import hmac
import os
import secrets
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo.errors import DuplicateKeyError

from .. import contact_verification as otp
from ..db import MongoStore, as_utc, get_store, utcnow
from ..holds import expire_hold
from ..models import Appointment, DoctorProfile, GuestChallenge, GuestPatient, GuestSession, Slot
from ..schemas import CancelInput, GuestBookingInput, GuestCodeInput, GuestPhoneInput, VerifyPaymentInput
from ..security import limit_requests, request_source, token_hash
from . import appointments as booking
from .common import visible_doctor_user

router = APIRouter(prefix="/guest", tags=["guest booking"])
COOKIE = "mediconnect_guest"
COOKIE_PATH = "/api/v1/guest"
SESSION_SECONDS = 7 * 86400


def _guest_token(request: Request) -> str:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Verify your mobile number to manage guest bookings")
    return token_hash(token)


def current_guest(token: str = Depends(_guest_token), db: MongoStore = Depends(get_store)) -> GuestPatient:
    session = db.get(GuestSession, token)
    guest = db.get(GuestPatient, session.guest_id) if session and as_utc(session.expires_at) > utcnow() else None
    if not guest:
        raise HTTPException(401, "Verify your mobile number to manage guest bookings")
    return guest


@router.get("/availability")
def availability(db: MongoStore = Depends(get_store)):
    now = utcnow()
    start_range = {"$gt": now, "$lte": now + booking.URGENT_WINDOW}
    for slot in db.find(Slot, {"status": "held", "held_until": {"$lte": now}, "start_at": start_range}):
        expire_hold(db, slot)
    results = []
    doctors = {}
    for slot in db.find(Slot, {"status": "available", "start_at": start_range}, sort=[("start_at", 1), ("_id", 1)]):
        if slot.doctor_id not in doctors:
            profile = db.get(DoctorProfile, slot.doctor_id)
            user = visible_doctor_user(db, profile) if profile and profile.verification_status == "published" else None
            doctors[slot.doctor_id] = (profile, user)
        profile, user = doctors[slot.doctor_id]
        if not user:
            continue
        results.append({"id": slot.id, "doctor_name": user.full_name, "specialty": profile.specialty,
                        "mode": slot.mode, "start_at": slot.start_at, "city": profile.city, "clinic_name": profile.clinic_name,
                        "amount_paise": profile.online_fee_paise if slot.mode == "online" else profile.clinic_fee_paise})
        if len(results) == 50:
            break
    return results


@router.post("/otp/send")
def send_code(data: GuestPhoneInput, request: Request, db: MongoStore = Depends(get_store)):
    secret = otp._secret()
    key = otp._digest(secret, f"guest:{data.phone}")
    previous = db.get(GuestChallenge, key)
    now = utcnow()
    if previous and as_utc(previous.sent_at) + otp.RESEND_INTERVAL > now:
        raise HTTPException(429, "Wait one minute before requesting another code.")
    limit_requests(db, "otp-source", otp._digest(secret, request_source(request)), limit=30, seconds=3600)
    limit_requests(db, "otp-recipient-phone", otp._digest(secret, data.phone), limit=5, seconds=3600)
    token = secrets.token_urlsafe(32)
    hashed = token_hash(token)
    code = f"{otp.secrets.randbelow(1_000_000):06d}"
    digest = otp._digest(secret, f"guest-code:{key}:{hashed}:{code}")
    expires_at = now + otp.CODE_LIFETIME
    if previous:
        if not db.update(GuestChallenge, {"_id": key, "token_hash": previous.token_hash},
                         {"digest": digest, "token_hash": hashed, "sent_at": now, "expires_at": expires_at, "attempts": 0}):
            raise HTTPException(409, "Request a new code to continue")
    else:
        try:
            db.insert(GuestChallenge(key=key, digest=digest, token_hash=hashed, sent_at=now, expires_at=expires_at))
        except DuplicateKeyError:
            raise HTTPException(429, "Wait one minute before requesting another code.") from None
    try:
        otp.deliver_code("phone", data.phone, code)
    except (httpx.HTTPError, HTTPException):
        db.delete_one(GuestChallenge, {"_id": key, "token_hash": hashed})
        raise HTTPException(503, "Could not send the SMS code. Check the number and try again.") from None
    return {"challenge": token, "expires_at": expires_at, "resend_at": now + otp.RESEND_INTERVAL}


@router.post("/otp/verify")
def verify_code(data: GuestCodeInput, request: Request, response: Response, db: MongoStore = Depends(get_store)):
    secret = otp._secret()
    key = otp._digest(secret, f"guest:{data.phone}")
    hashed = token_hash(data.challenge)
    active = {"_id": key, "token_hash": hashed, "expires_at": {"$gt": utcnow()}}
    attempt = db.update(GuestChallenge, {**active, "attempts": {"$lt": otp.MAX_ATTEMPTS}}, {}, increments={"attempts": 1})
    if not attempt:
        raise HTTPException(400, "This code is no longer usable. Request a new code.")
    expected = otp._digest(secret, f"guest-code:{key}:{hashed}:{data.code}")
    if not hmac.compare_digest(attempt.digest, expected):
        if attempt.attempts >= otp.MAX_ATTEMPTS:
            raise HTTPException(429, "Too many incorrect codes. Request a new code.")
        raise HTTPException(400, "That code is incorrect. Check the message and try again.")
    active["expires_at"] = {"$gt": utcnow()}
    if not db.database.guestchallenges.delete_one(active).deleted_count:
        raise HTTPException(409, "Verification has changed. Request a new code.")
    guest = db.first(GuestPatient, {"phone": data.phone})
    if not guest:
        try:
            guest = db.insert(GuestPatient(phone=data.phone))
        except DuplicateKeyError:
            guest = db.first(GuestPatient, {"phone": data.phone})
    previous_token = request.cookies.get(COOKIE)
    if previous_token:
        db.database.guestsessions.delete_one({"_id": token_hash(previous_token)})
    token = secrets.token_urlsafe(32)
    db.insert(GuestSession(token_hash=token_hash(token), guest_id=guest.id,
                           expires_at=utcnow() + timedelta(seconds=SESSION_SECONDS)))
    response.set_cookie(COOKIE, token, path=COOKIE_PATH, httponly=True, samesite="lax",
                        secure=os.getenv("APP_ENV") == "production", max_age=SESSION_SECONDS)
    return {"phone": guest.phone}


@router.get("/session")
def session(guest: GuestPatient = Depends(current_guest)):
    return {"phone": guest.phone}


@router.post("/sign-out")
def sign_out(response: Response, token: str = Depends(_guest_token), db: MongoStore = Depends(get_store)):
    db.database.guestsessions.delete_one({"_id": token})
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return {"ok": True}


@router.post("/appointments", status_code=201)
def book(data: GuestBookingInput, guest: GuestPatient = Depends(current_guest), db: MongoStore = Depends(get_store)):
    return booking.book_slot(db, data.slot_id, guest_id=guest.id, guest_name=data.full_name,
                             expected_amount_paise=data.expected_amount_paise)


@router.get("/appointments")
def appointments(guest: GuestPatient = Depends(current_guest), db: MongoStore = Depends(get_store)):
    owner = {"guest_patient_id": guest.id}
    for item in db.find(Appointment, {**owner, "status": "pending_payment"}):
        expire_hold(db, db.get(Slot, item.slot_id))
    return [booking.appointment_dict(db, item) for item in db.find(Appointment, owner, sort=[("created_at", -1)])]


def owned_booking(appointment_id: int, guest: GuestPatient = Depends(current_guest), db: MongoStore = Depends(get_store)):
    return booking.patient_item(db, appointment_id, guest_id=guest.id)


@router.get("/appointments/{appointment_id}/checkout")
def checkout(item: Appointment = Depends(owned_booking), db: MongoStore = Depends(get_store)):
    return booking.checkout_for(db, item)


@router.post("/appointments/{appointment_id}/verify-payment")
def verify_payment(data: VerifyPaymentInput, item: Appointment = Depends(owned_booking), db: MongoStore = Depends(get_store)):
    return booking.verify_checkout(db, item, data)


@router.post("/appointments/{appointment_id}/payment-failed")
def payment_failed(item: Appointment = Depends(owned_booking), db: MongoStore = Depends(get_store)):
    return booking.record_payment_failure(db, item)


@router.get("/appointments/{appointment_id}/cancellation-quote")
def cancellation_quote(item: Appointment = Depends(owned_booking), db: MongoStore = Depends(get_store)):
    return booking.quote_cancellation(item, db.get(Slot, item.slot_id), "guest", utcnow())


@router.post("/appointments/{appointment_id}/cancel")
def cancel(data: CancelInput, item: Appointment = Depends(owned_booking), db: MongoStore = Depends(get_store)):
    return booking.cancel_booking(db, item, "guest", data)


@router.get("/appointments/{appointment_id}/meeting")
def meeting(item: Appointment = Depends(owned_booking), db: MongoStore = Depends(get_store)):
    return booking.meeting_for(db, item)
