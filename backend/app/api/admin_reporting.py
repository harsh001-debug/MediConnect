"""Read-only admin reporting over booking, slot and payment records."""

import re
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException

from ..db import MongoStore, as_utc, utcnow
from ..models import Appointment, PaymentAttempt, Refund, Slot, User
from .appointments import can_cancel

IST = timezone(timedelta(hours=5, minutes=30))


def report_dates(period: str, start: date | None, end: date | None):
    today = utcnow().astimezone(IST).date()
    if period == "today":
        return today, today
    if period == "last7":
        return today - timedelta(days=6), today
    if period == "week":
        monday = today - timedelta(days=today.weekday())
        return monday, monday + timedelta(days=6)
    if not start or not end or not 0 <= (end - start).days < 90 or start == date.min or end == date.max:
        raise HTTPException(422, "Choose a start and end date within a 90-day range")
    return start, end


def date_query(start: date, end: date) -> dict:
    return {"$gte": datetime.combine(start, time.min, IST).astimezone(timezone.utc),
            "$lt": datetime.combine(end + timedelta(days=1), time.min, IST).astimezone(timezone.utc)}


def local_day(timestamp: datetime) -> date:
    return as_utc(timestamp).astimezone(IST).date()


def search_expression(query: str) -> dict:
    return {"$regex": re.escape(query.strip()), "$options": "i"}


def find_account_ids(db: MongoStore, query: str, *, role: str | None = None) -> list[int]:
    pattern = search_expression(query)
    match = {"$or": [{"full_name": pattern}, {"email": pattern}]}
    if role:
        match["role"] = role
    return [row["_id"] for row in db.database.users.find(match, {"_id": 1})]


def page_documents(collection, match: dict, sort: list, page: int, limit: int) -> tuple[list, int]:
    return list(collection.find(match).sort(sort).skip((page - 1) * limit).limit(limit)), collection.count_documents(match)


def page_data(items: list, total: int, page: int, limit: int) -> dict:
    return {"items": items, "total": total, "page": page, "limit": limit}


def by_id(db: MongoStore, model, ids) -> dict:
    keys = set(ids) - {None}
    return {row.id: row for row in db.find(model, {"_id": {"$in": list(keys)}})} if keys else {}


def appointment_row(item: Appointment, slot: Slot, users: dict, now: datetime) -> dict:
    doctor, patient = users.get(item.doctor_id), users.get(item.patient_id)
    expired = item.status == "pending_payment" and as_utc(item.hold_expires_at) <= now
    return {"id": item.id, "doctor_id": item.doctor_id, "doctor_name": doctor.full_name if doctor else None,
            "patient_name": patient.full_name if patient else item.guest_name,
            "booking_kind": "urgent" if item.guest_patient_id else "scheduled", "mode": item.mode,
            "status": "expired" if expired else item.status, "start_at": slot.start_at,
            "amount_paise": item.amount_paise, "paid_at": item.paid_at, "cancelled_by": item.cancelled_by,
            "can_cancel": can_cancel(item, slot, now)}


def transaction_rows(db: MongoStore, start: date, end: date) -> list[dict]:
    # An unmatched capture always queues a full refund. Its record is also the
    # capture receipt for extra payments that share an order with a normal capture.
    bounds = date_query(start, end)
    confirmed = db.find(Appointment, {"paid_at": bounds})
    recent_refunds = db.find(Refund, {"created_at": bounds})
    attempts = db.find(PaymentAttempt, {"appointment_id": {"$in": list(
        {item.id for item in confirmed} | {refund.appointment_id for refund in recent_refunds})}})
    attempts_by_booking = {attempt.appointment_id: attempt for attempt in attempts}
    captured_ids = {attempt.razorpay_payment_id for attempt in attempts if attempt.status == "captured"}
    refunds = {row.payment_id: row for row in db.find(Refund, {"_id": {"$in": list(captured_ids - {None})}})}

    def receipt(appointment_id, timestamp, amount, attempt, payment_id, refund, late):
        return {"appointment_id": appointment_id, "captured_at": timestamp, "amount_paise": amount,
                "payment_id": payment_id, "order_id": attempt.razorpay_order_id if attempt else None,
                "late_capture": late, "refund_amount_paise": refund.amount_paise if refund else 0,
                "refund_status": refund.status if refund else None,
                "refund_reference": refund.provider_refund_id if refund else None}

    rows = []
    for item in confirmed:
        attempt = attempts_by_booking.get(item.id)
        payment_id = attempt.razorpay_payment_id if attempt and attempt.status == "captured" else None
        rows.append(receipt(item.id, item.paid_at, item.amount_paise, attempt, payment_id,
                            refunds.get(payment_id), False))
    for refund in recent_refunds:
        if refund.payment_id not in captured_ids:
            rows.append(receipt(refund.appointment_id, refund.created_at, refund.amount_paise,
                                attempts_by_booking.get(refund.appointment_id), refund.payment_id, refund, True))
    return rows


COUNTERS = ("bookings", "cancellations", "slots", "booked_slots", "payments",
            "gross_paise", "refunded_paise", "pending_refund_paise", "review_refund_paise", "net_paise")


def overview(db: MongoStore, start: date, end: date) -> dict:
    now = utcnow()
    bounds = date_query(start, end)
    daily = {start + timedelta(days=offset): dict.fromkeys(COUNTERS, 0) for offset in range((end - start).days + 1)}
    for row in db.database.appointments.find({"cancelled_at": bounds}, {"cancelled_at": 1}):
        daily[local_day(row["cancelled_at"])]["cancellations"] += 1
    for row in db.database.slots.find({"start_at": bounds}, {"start_at": 1, "status": 1}):
        entry = daily[local_day(row["start_at"])]
        entry["slots"] += 1
        entry["booked_slots"] += row["status"] == "booked"

    for row in transaction_rows(db, start, end):
        day = daily[local_day(row["captured_at"])]
        day["bookings"] += not row["late_capture"]
        day["payments"] += 1
        day["gross_paise"] += row["amount_paise"]
        status = row["refund_status"]
        counter = {"processed": "refunded_paise", "queued": "pending_refund_paise",
                   "processing": "pending_refund_paise", "needs_review": "review_refund_paise"}.get(status)
        if counter:
            day[counter] += row["refund_amount_paise"]
    for day in daily.values():
        day["net_paise"] = day["gross_paise"] - day["refunded_paise"]

    accounts = db.database.users
    ready_profiles = list(db.database.doctorprofiles.find({"verification_status": "published"},
                          {"hide_until_contacts_verified": 1}))
    all_public = [row["_id"] for row in ready_profiles if not row.get("hide_until_contacts_verified")]
    need_contacts = [row["_id"] for row in ready_profiles if row.get("hide_until_contacts_verified")]
    published = accounts.count_documents({"role": "doctor", "suspended_at": None, "$or": [
        {"_id": {"$in": all_public}},
        {"_id": {"$in": need_contacts}, "email_verified_at": {"$ne": None}, "phone_verified_at": {"$ne": None}},
    ]})
    return {"start": start, "end": end, "as_of": now, "accounts": accounts.count_documents({}),
            "patients": accounts.count_documents({"role": "patient"}),
            "doctors": accounts.count_documents({"role": "doctor"}), "published_doctors": published,
            "totals": {key: sum(row[key] for row in daily.values()) for key in COUNTERS},
            "days": [{"date": day, **values} for day, values in daily.items()]}
