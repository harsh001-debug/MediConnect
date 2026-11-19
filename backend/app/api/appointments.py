import json
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo.errors import DuplicateKeyError

from .. import payments, prescriptions
from ..db import MongoStore, as_utc, get_store, utcnow
from ..holds import expire_doctor_holds, expire_hold
from ..models import AdminAction, Appointment, DoctorProfile, PaymentAttempt, Refund, Review, Slot, User
from ..notifications import add_notice
from ..refunds import queue_refund, refund_dict
from ..schemas import BookingInput, CancelInput, ReviewInput, VerifyPaymentInput
from ..security import current_user, has_patient_access, require_role
from .common import visible_doctor_user

router = APIRouter(tags=["appointments and payments"])
HOLD_MINUTES = 15
URGENT_WINDOW = timedelta(hours=24)
PAYMENT_CHECK_WINDOW = timedelta(days=3)
log = logging.getLogger(__name__)


def appointment_dict(db: MongoStore, item: Appointment) -> dict:
    slot = db.get(Slot, item.slot_id)
    doctor = db.get(User, item.doctor_id)
    patient = db.get(User, item.patient_id) if item.patient_id else None
    profile = db.get(DoctorProfile, item.doctor_id)
    refund = db.first(Refund, {"appointment_id": item.id}, sort=[("created_at", 1)]) if item.status in {"cancelled", "expired", "payment_failed"} else None
    attempt = db.first(PaymentAttempt, {"appointment_id": item.id}) if item.status == "pending_payment" else None
    return {"id": item.id, "slot_id": item.slot_id, "doctor_id": item.doctor_id,
            "patient_id": item.patient_id, "doctor_name": doctor.full_name,
            "patient_name": patient.full_name if patient else item.guest_name, "mode": item.mode,
            "booking_kind": "urgent" if item.guest_patient_id else "scheduled", "status": item.status,
            "amount_rupees": item.amount_paise // 100, "amount_paise": item.amount_paise,
            "start_at": slot.start_at, "end_at": slot.end_at, "clinic_name": profile.clinic_name,
            "clinic_address": profile.clinic_address, "hold_expires_at": item.hold_expires_at,
            "can_cancel": can_cancel(item, slot, utcnow()),
            "payment_processing": bool(attempt and attempt.status == "authorized"),
            "cancelled_at": item.cancelled_at, "cancelled_by": item.cancelled_by,
            "cancellation_fee_paise": item.cancellation_fee_paise,
            "refund": refund_dict(refund) if refund else None}


def can_cancel(item: Appointment, slot: Slot | None, now) -> bool:
    return (slot is not None and as_utc(slot.start_at) > now and
            (item.status == "confirmed" or (item.status == "pending_payment" and as_utc(item.hold_expires_at) > now)))


def _release(db: MongoStore, slot_id: int, appointment_id: int) -> None:
    db.update(Slot, {"_id": slot_id, "status": "held", "appointment_id": appointment_id},
              {"status": "available", "held_until": None, "appointment_id": None})


def book_slot(db: MongoStore, slot_id: int, *, patient_id: int | None = None,
              guest_id: int | None = None, guest_name: str | None = None,
              expected_amount_paise: int | None = None, patient_generation: int | None = None) -> dict:
    slot = db.get(Slot, slot_id)
    if not slot:
        raise HTTPException(404, "Slot not found")
    expire_hold(db, slot)
    slot = db.get(Slot, slot_id)
    if not slot:
        raise HTTPException(409, "Slot is no longer available")
    now = utcnow()
    start = as_utc(slot.start_at)
    profile = db.get(DoctorProfile, slot.doctor_id)
    if (not profile or profile.verification_status != "published" or not visible_doctor_user(db, profile)
            or slot.status != "available" or start <= now or (guest_id and start > now + URGENT_WINDOW)):
        raise HTTPException(409, "Slot is no longer available")
    if guest_id:
        pending = db.first(Appointment, {"guest_patient_id": guest_id, "status": "pending_payment"})
        if pending:
            expire_hold(db, db.get(Slot, pending.slot_id))
            if db.get(Appointment, pending.id).status == "pending_payment":
                raise HTTPException(409, "Finish or cancel your pending guest booking before reserving another time")
    amount = profile.online_fee_paise if slot.mode == "online" else profile.clinic_fee_paise
    if expected_amount_paise is not None and expected_amount_paise != amount:
        raise HTTPException(409, "The consultation fee changed. Review the updated price before booking.")
    appointment = Appointment(id=db.next_id(Appointment), slot_id=slot.id, doctor_id=slot.doctor_id,
                              patient_id=patient_id, guest_patient_id=guest_id, guest_name=guest_name,
                              mode=slot.mode, amount_paise=amount,
                              hold_expires_at=min(start, now + timedelta(minutes=HOLD_MINUTES)),
                              room_name="MediConnect-" + secrets.token_urlsafe(24) if slot.mode == "online" else None)
    def reserve(tx):
        # Account writes serialize reservations with a concurrent suspension.
        participants = [(slot.doctor_id, "doctor"), (patient_id, "patient")] if patient_id else [(slot.doctor_id, "doctor")]
        for account_id, role in sorted(participants):
            eligible = {"role": "doctor"} if role == "doctor" else {
                "$or": [{"role": {"$in": ["patient", "admin"]}}, {"admin_access": True}]}
            account = tx.update(User, {"_id": account_id, **eligible, "suspended_at": None},
                                {}, increments={"booking_version": 1})
            if not account or (role == "patient" and patient_generation is not None
                               and account.session_generation != patient_generation):
                raise HTTPException(409, "An account for this booking is no longer active")
        claimed = tx.update(Slot, {"_id": slot.id, "status": "available", "start_at": {"$gt": utcnow()}},
                            {"status": "held", "held_until": appointment.hold_expires_at, "appointment_id": appointment.id})
        if not claimed:
            raise HTTPException(409, "Slot is no longer available")
        tx.insert(appointment)

    try:
        db.run_transaction(reserve)
    except DuplicateKeyError:
        _release(db, slot.id, appointment.id)
        raise HTTPException(409, "Booking already exists. Open your appointments to manage it.") from None

    try:
        order = payments.create_order(amount, f"mc-{appointment.id}-{secrets.token_hex(8)}")

        def save_order(tx):
            held = tx.get(Slot, slot.id)
            current = tx.get(Appointment, appointment.id)
            if (current.status != "pending_payment" or not held or held.status != "held"
                    or held.appointment_id != appointment.id or as_utc(held.held_until) <= utcnow()):
                raise HTTPException(409, "Slot hold expired while creating payment order")
            tx.insert(PaymentAttempt(appointment_id=appointment.id, razorpay_order_id=order["id"],
                                     next_check_at=utcnow() + timedelta(seconds=30)))

        db.run_transaction(save_order)
    except Exception:
        def abandon(tx):
            if tx.first(PaymentAttempt, {"appointment_id": appointment.id}):
                return
            tx.update(Appointment, {"_id": appointment.id, "status": "pending_payment"}, {"status": "payment_failed"})
            _release(tx, slot.id, appointment.id)

        db.run_transaction(abandon)
        raise
    return {"appointment": appointment_dict(db, appointment), "checkout": checkout_dict(appointment, order["id"])}


@router.post("/appointments", status_code=201)
def book(data: BookingInput, user: User = Depends(require_role("patient")), db: MongoStore = Depends(get_store)):
    return book_slot(db, data.slot_id, patient_id=user.id, patient_generation=user.session_generation)


@router.get("/appointments")
def appointments(user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    personal = has_patient_access(user)
    if personal:
        doctor_ids = {row.doctor_id for row in db.find(Appointment, {"patient_id": user.id, "status": "pending_payment"})}
        for doctor_id in doctor_ids:
            expire_doctor_holds(db, doctor_id)
        rows = db.find(Appointment, {"patient_id": user.id}, sort=[("created_at", -1)])
    elif user.role == "doctor":
        expire_doctor_holds(db, user.id)
        rows = db.find(Appointment, {"doctor_id": user.id}, sort=[("created_at", -1)])
    else:
        rows = []
    completed = [item.id for item in rows if item.status == "completed"] if personal else []
    reviewed = {entry.appointment_id for entry in db.find(Review, {"appointment_id": {"$in": completed}})} if completed else set()
    prescription_statuses = prescriptions.visible_statuses(db, rows, "patient" if personal else user.role)
    config = prescriptions.settings() if personal else None
    upload_consents = {}
    if config:
        doctor_ids = {item.doctor_id for item in rows if item.status == "pending_payment"} & config.doctor_ids
        if doctor_ids:
            upload_consents = {profile.user_id: prescriptions.upload_consent(profile, config)
                               for profile in db.find(DoctorProfile, {"_id": {"$in": list(doctor_ids)}})}
    return [{**appointment_dict(db, item), "reviewed": item.id in reviewed,
             "prescription_status": prescription_statuses.get(item.id),
             "prescription_consent": upload_consents.get(item.doctor_id) if item.status == "pending_payment" else None}
            for item in rows]


def checkout_dict(item: Appointment, order_id: str) -> dict:
    return {"key": payments.credentials()[0], "order_id": order_id, "amount": item.amount_paise, "currency": "INR"}


def patient_item(db, appointment_id: int, *, patient_id: int | None = None, guest_id: int | None = None) -> Appointment:
    item = db.get(Appointment, appointment_id)
    if not item or not ((patient_id is not None and item.patient_id == patient_id) or
                        (guest_id is not None and item.guest_patient_id == guest_id)):
        raise HTTPException(404, "Appointment not found")
    return item


def checkout_for(db: MongoStore, item: Appointment) -> dict:
    if item.status != "pending_payment" or as_utc(item.hold_expires_at) <= utcnow():
        raise HTTPException(409, "Payment hold has expired")
    attempt = db.first(PaymentAttempt, {"appointment_id": item.id})
    if not attempt:
        raise HTTPException(409, "Payment order unavailable")
    if attempt.status == "authorized":
        raise HTTPException(409, "Your payment is processing. Refresh the appointment status.")
    return checkout_dict(item, attempt.razorpay_order_id)


@router.get("/appointments/{appointment_id}/checkout")
def resume_checkout(appointment_id: int, user: User = Depends(require_role("patient")), db: MongoStore = Depends(get_store)):
    return checkout_for(db, patient_item(db, appointment_id, patient_id=user.id))


def record_payment_failure(db: MongoStore, item: Appointment) -> dict:
    def fail(tx):
        attempt = tx.first(PaymentAttempt, {"appointment_id": item.id})
        if attempt and attempt.status == "authorized":
            raise HTTPException(409, "Your payment is processing. Refresh the appointment status.")
        changed = tx.update(Appointment, {"_id": item.id, "status": "pending_payment"}, {"status": "payment_failed"})
        if not changed:
            raise HTTPException(409, "This appointment is no longer awaiting payment")
        _release(tx, item.slot_id, item.id)
        tx.update(PaymentAttempt, {"appointment_id": item.id}, {"status": "failed"})
        if changed.prescription_revision:
            prescriptions.remove(tx, {"_id": item.id}, "purged")
        return changed

    return appointment_dict(db, db.run_transaction(fail))


@router.post("/appointments/{appointment_id}/payment-failed")
def payment_failed(appointment_id: int, user: User = Depends(require_role("patient")), db: MongoStore = Depends(get_store)):
    return record_payment_failure(db, patient_item(db, appointment_id, patient_id=user.id))


def reconcile_capture(db: MongoStore, order_id: str, payment_id: str, amount_paise: int, currency: str):
    def reconcile(tx):
        attempt = tx.first(PaymentAttempt, {"razorpay_order_id": order_id})
        if not attempt:
            return None, False
        current = tx.get(Appointment, attempt.appointment_id)
        if not current or current.amount_paise != amount_paise or currency != "INR":
            raise HTTPException(409, "Payment amount or currency does not match the booking")
        if attempt.razorpay_payment_id == payment_id and attempt.status in {"captured", "paid_after_expiry"}:
            return current, attempt.status != "captured"
        if tx.get(Refund, payment_id):
            return current, True
        now = utcnow()
        slot = tx.get(Slot, current.slot_id)
        payable = (current.status == "pending_payment" and slot and as_utc(slot.start_at) > now
                   and as_utc(current.hold_expires_at) > now)
        booked = tx.update(Slot, {"_id": current.slot_id, "status": "held", "appointment_id": current.id,
                                  "held_until": {"$gt": now}}, {"status": "booked", "held_until": None}) if payable else None
        if not booked:
            if not attempt.razorpay_payment_id or attempt.status == "authorized":
                tx.update(PaymentAttempt, {"_id": attempt.id},
                          {"status": "paid_after_expiry", "razorpay_payment_id": payment_id, "next_check_at": None})
            if current.status == "pending_payment":
                current = tx.update(Appointment, {"_id": current.id, "status": "pending_payment"}, {"status": "expired"})
                _release(tx, current.slot_id, current.id)
            queue_refund(tx, current.id, payment_id, amount_paise)
            add_notice(tx, current.patient_id, "late_payment",
                       "A payment arrived after booking closed. A full test refund has been queued.", appointment_id=current.id)
            return current, True
        current = tx.update(Appointment, {"_id": current.id, "status": "pending_payment"},
                            {"status": "confirmed", "paid_at": now})
        tx.update(PaymentAttempt, {"_id": attempt.id},
                  {"status": "captured", "razorpay_payment_id": payment_id, "next_check_at": None})
        add_notice(tx, current.patient_id, "booking_confirmed", "Your appointment was confirmed.", appointment_id=current.id)
        has_prescription = prescriptions.share_draft(tx, current, now)
        notice = ("A new appointment has been booked. Open the appointment to see the attached prescription."
                  if has_prescription else "A new appointment has been booked.")
        add_notice(tx, current.doctor_id, "new_booking", notice, appointment_id=current.id)
        return current, False

    return db.run_transaction(reconcile)


def record_authorization(db: MongoStore, attempt: PaymentAttempt, payment_id: str) -> None:
    db.update(PaymentAttempt, {"_id": attempt.id, "status": "created"},
              {"status": "authorized", "razorpay_payment_id": payment_id,
               "next_check_at": utcnow() + timedelta(seconds=30)})


def reconcile_due_orders(db: MongoStore, *, limit: int = 10) -> None:
    now = utcnow()
    due = {"status": {"$in": ["created", "authorized", "failed"]},
           "created_at": {"$gte": now - PAYMENT_CHECK_WINDOW},
           "$or": [{"next_check_at": None}, {"next_check_at": {"$lte": now}}]}
    for candidate in db.find(PaymentAttempt, due, sort=[("next_check_at", 1)], limit=limit):
        item = db.get(Appointment, candidate.appointment_id)
        if not item:
            continue
        active = item.status == "pending_payment" and as_utc(item.hold_expires_at) > now
        interval = timedelta(seconds=30) if active else timedelta(minutes=10)
        attempt = db.update(PaymentAttempt, {**due, "_id": candidate.id}, {"next_check_at": utcnow() + interval})
        if not attempt:
            continue
        try:
            authorized = None
            rejected_authorization = False
            for actual in payments.fetch_order_payments(attempt.razorpay_order_id):
                payment_id = actual.get("id")
                if (not isinstance(payment_id, str) or actual.get("order_id") != attempt.razorpay_order_id
                        or actual.get("amount") != item.amount_paise or actual.get("currency") != "INR"):
                    continue
                if actual.get("status") == "captured":
                    reconcile_capture(db, attempt.razorpay_order_id, payment_id, item.amount_paise, "INR")
                elif active and actual.get("status") == "authorized":
                    authorized = payment_id
                elif actual.get("status") == "failed" and payment_id == attempt.razorpay_payment_id:
                    rejected_authorization = True
            if rejected_authorization:
                db.update(PaymentAttempt, {"_id": attempt.id, "status": "authorized",
                                          "razorpay_payment_id": attempt.razorpay_payment_id},
                          {"status": "created", "razorpay_payment_id": None})
            if authorized:
                record_authorization(db, attempt, authorized)
        except HTTPException as exc:
            log.warning("Order reconciliation failed for appointment %s (status %s)", item.id, exc.status_code)


def verify_checkout(db: MongoStore, item: Appointment, data: VerifyPaymentInput) -> dict:
    attempt = db.first(PaymentAttempt, {"appointment_id": item.id})
    if not attempt or attempt.razorpay_order_id != data.razorpay_order_id:
        raise HTTPException(400, "Order does not match appointment")
    if not payments.verify_signature(data.razorpay_order_id, data.razorpay_payment_id, data.razorpay_signature):
        raise HTTPException(400, "Invalid payment signature")
    if attempt.razorpay_payment_id == data.razorpay_payment_id and attempt.status == "captured":
        return appointment_dict(db, db.get(Appointment, item.id))
    actual = payments.fetch_payment(data.razorpay_payment_id)
    if (actual.get("order_id") != attempt.razorpay_order_id or actual.get("amount") != item.amount_paise
            or actual.get("currency") != "INR"):
        raise HTTPException(409, "Payment amount or currency does not match the booking")
    if actual.get("status") == "authorized":
        record_authorization(db, attempt, data.razorpay_payment_id)
        return appointment_dict(db, db.get(Appointment, item.id))
    if actual.get("status") != "captured":
        raise HTTPException(409, "Payment is not captured for this booking")
    current, late = reconcile_capture(db, attempt.razorpay_order_id, data.razorpay_payment_id, item.amount_paise, "INR")
    if late:
        raise HTTPException(409, "This booking is closed. Check its status for payment and refund details.")
    return appointment_dict(db, current)


@router.post("/appointments/{appointment_id}/verify-payment")
def verify_payment(appointment_id: int, data: VerifyPaymentInput,
                   user: User = Depends(require_role("patient")), db: MongoStore = Depends(get_store)):
    return verify_checkout(db, patient_item(db, appointment_id, patient_id=user.id), data)


@router.post("/payments/razorpay/webhook")
async def razorpay_webhook(request: Request, db: MongoStore = Depends(get_store)):
    from starlette.concurrency import run_in_threadpool

    body = await request.body()
    if not payments.verify_webhook(body, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(400, "Invalid webhook signature")
    try:
        event = json.loads(body)
        if event["event"] != "payment.captured":
            return {"ok": True}
        entity = event["payload"]["payment"]["entity"]
        order_id, payment_id, amount, currency = (entity[key] for key in ("order_id", "id", "amount", "currency"))
        if entity["status"] != "captured" or not all(isinstance(value, str) and value for value in (order_id, payment_id, currency)) or type(amount) is not int:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Invalid webhook payload") from None
    await run_in_threadpool(reconcile_capture, db, order_id, payment_id, amount, currency)
    return {"ok": True}


def quote_cancellation(item: Appointment, slot: Slot, actor: str, now) -> dict:
    if not can_cancel(item, slot, now):
        raise HTTPException(409, "This appointment can no longer be cancelled")
    paid = item.amount_paise if item.status == "confirmed" else 0
    notice = as_utc(slot.start_at) - now
    percent = 0
    if paid and actor in {"patient", "guest"} and notice < timedelta(hours=24):
        percent = 25 if notice >= timedelta(hours=2) else 50
    fee = paid * percent // 100
    return {"paid_paise": paid, "fee_percent": percent, "fee_paise": fee, "refund_paise": paid - fee}


def participant(db: MongoStore, appointment_id: int, user: User) -> tuple[Appointment, str]:
    item = db.get(Appointment, appointment_id)
    if item and has_patient_access(user) and item.patient_id == user.id:
        return item, "patient"
    if item and user.role == "doctor" and item.doctor_id == user.id:
        return item, "doctor"
    raise HTTPException(404, "Appointment not found")


def cancel_in_transaction(tx: MongoStore, appointment_id: int, actor: str, expected: CancelInput | None,
                          *, admin_id: int | None = None, reason: str = "", now=None) -> Appointment:
    current = tx.get(Appointment, appointment_id)
    if not current:
        raise HTTPException(404, "Appointment not found")
    if current.status == "cancelled":
        return current
    slot = tx.get(Slot, current.slot_id)
    now = now or utcnow()
    quote = quote_cancellation(current, slot, actor, now)
    if (expected and (expected.paid_paise != quote["paid_paise"] or expected.refund_paise != quote["refund_paise"])) or (not expected and quote["paid_paise"] and admin_id is None):
        raise HTTPException(409, {"code": "quote_changed", "message": "Review the current cancellation amount before confirming.", "quote": quote})
    attempt = tx.first(PaymentAttempt, {"appointment_id": current.id})
    if quote["paid_paise"] and (not attempt or attempt.status != "captured" or not attempt.razorpay_payment_id):
        raise HTTPException(409, "The captured payment could not be verified. Review the payment record before retrying.")
    changed = tx.update(Appointment, {"_id": current.id, "status": current.status},
                        {"status": "cancelled", "cancelled_at": now, "cancelled_by": actor,
                         "cancellation_fee_paise": quote["fee_paise"]})
    if not changed:
        latest = tx.get(Appointment, current.id)
        if latest.status == "cancelled":
            return latest
        raise HTTPException(409, "Appointment state changed. Refresh and try again.")
    if changed.prescription_revision:
        actor_id = admin_id if admin_id is not None else (current.doctor_id if actor == "doctor" else current.patient_id)
        prescriptions.remove(tx, {"_id": appointment_id}, "cancelled", actor_id=actor_id)
    tx.update(Slot, {"_id": slot.id, "appointment_id": current.id},
              {"status": "available", "held_until": None, "appointment_id": None})
    if quote["refund_paise"]:
        queue_refund(tx, current.id, attempt.razorpay_payment_id, quote["refund_paise"])
    patient_message = "Your appointment was cancelled." + (" Your test refund has been queued." if quote["refund_paise"] else "")
    add_notice(tx, current.patient_id, "booking_cancelled", patient_message, appointment_id=current.id)
    doctor_message = ("MediConnect cancelled an appointment." if admin_id is not None
                      else "An appointment was cancelled. The time is available again.")
    add_notice(tx, current.doctor_id, "doctor_booking_cancelled", doctor_message, appointment_id=current.id)
    if admin_id is not None:
        tx.insert(AdminAction(admin_id=admin_id, target_id=current.id, action="cancel_appointment",
                              reason=reason, created_at=now))
    return changed


def cancel_booking(db: MongoStore, item: Appointment, actor: str, expected: CancelInput | None) -> dict:
    return appointment_dict(db, db.run_transaction(lambda tx: cancel_in_transaction(tx, item.id, actor, expected)))


@router.get("/appointments/{appointment_id}/cancellation-quote")
def cancellation_quote(appointment_id: int, user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    item, actor = participant(db, appointment_id, user)
    return quote_cancellation(item, db.get(Slot, item.slot_id), actor, utcnow())


@router.post("/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int, data: CancelInput | None = None, user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    item, actor = participant(db, appointment_id, user)
    return cancel_booking(db, item, actor, data)


@router.post("/appointments/{appointment_id}/complete")
def complete(appointment_id: int, user: User = Depends(require_role("doctor")), db: MongoStore = Depends(get_store)):
    item = db.get(Appointment, appointment_id)
    if not item or item.doctor_id != user.id:
        raise HTTPException(404, "Appointment not found")
    slot = db.get(Slot, item.slot_id)
    if item.status != "confirmed" or as_utc(slot.start_at) > utcnow():
        raise HTTPException(409, "Only started, confirmed appointments can be completed")
    with db.transaction() as tx:
        changed = tx.update(Appointment, {"_id": item.id, "doctor_id": user.id, "status": "confirmed"},
                            {"status": "completed"})
        if not changed:
            raise HTTPException(409, "Appointment is no longer confirmed")
        add_notice(tx, item.patient_id, "visit_completed", "Your appointment is complete. You can leave a review.",
                   appointment_id=item.id)
    return appointment_dict(db, changed)


def meeting_for(db: MongoStore, item: Appointment) -> dict:
    if item.mode != "online":
        raise HTTPException(404, "Online appointment not found")
    slot = db.get(Slot, item.slot_id)
    now = utcnow()
    if item.status != "confirmed":
        raise HTTPException(403, "This appointment needs to be confirmed before you can join")
    if now < as_utc(slot.start_at) - timedelta(minutes=15):
        raise HTTPException(403, "The meeting opens 15 minutes before the appointment")
    if now > as_utc(slot.end_at) + timedelta(minutes=30):
        raise HTTPException(403, "The meeting window has closed")
    return {"room_name": item.room_name, "domain": "meet.jit.si"}


@router.get("/appointments/{appointment_id}/meeting")
def meeting(appointment_id: int, user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    item, _ = participant(db, appointment_id, user)
    return meeting_for(db, item)


@router.post("/appointments/{appointment_id}/review", status_code=201)
def review(appointment_id: int, data: ReviewInput, user: User = Depends(require_role("patient")), db: MongoStore = Depends(get_store)):
    item = db.get(Appointment, appointment_id)
    if not item or item.patient_id != user.id or item.status != "completed":
        raise HTTPException(403, "Only completed appointments can be reviewed")
    try:
        db.insert(Review(appointment_id=item.id, doctor_id=item.doctor_id, patient_id=user.id,
                         rating=data.rating, comment=data.comment.strip()))
    except DuplicateKeyError:
        raise HTTPException(409, "Already reviewed") from None
    return {"ok": True}


