"""Store account notices with their events and deliver email after commit."""

import logging
import os
import secrets
from datetime import timedelta

import httpx
from fastapi import HTTPException

from .contact_verification import deliver_email, email_configured
from .db import MongoStore, as_utc, utcnow
from .models import Notification, NotificationEmail, User

log = logging.getLogger(__name__)
EMAIL_SUBJECT = "You have a MediConnect update"
EMAIL_LIFETIME = timedelta(hours=24)
EMAIL_LEASE = timedelta(minutes=2)
VERIFICATION_RECHECK = timedelta(minutes=5)
RETRY_DELAYS = (60, 300, 900, 3600)
EMAIL_MESSAGES = {
    "booking_confirmed": "Your appointment was confirmed.",
    "new_booking": "You have a new confirmed appointment.",
    "booking_cancelled": "Your appointment was cancelled.",
    "doctor_booking_cancelled": "An appointment was cancelled.",
    "visit_completed": "Your appointment was marked complete.",
    "late_payment": "A payment arrived after booking closed. A full test refund has been queued.",
    "refund_processed": "Your test refund was processed.",
    "refund_needs_review": "Your test refund needs review.",
    "registration_matched": "Your doctor registration matched the NMC register.",
    "application_pending": "Your doctor application is pending review.",
    "application_approved": "Your doctor application was approved.",
    "application_rejected": "Your doctor application was rejected.",
}
APPLICATION_KINDS = {"registration_matched", "application_pending", "application_approved", "application_rejected"}
DOCTOR_BOOKING_KINDS = {"new_booking", "doctor_booking_cancelled"}


def email_enabled() -> bool:
    return os.getenv("NOTIFICATION_EMAIL_ENABLED") == "1"


def add_notice(db: MongoStore, user_id: int | None, kind: str, body: str, *, appointment_id: int | None = None) -> None:
    if user_id is None:
        return
    now = utcnow()
    email: NotificationEmail | None = None
    if email_enabled():
        email = {"key": secrets.token_hex(16), "status": "queued", "attempts": 0, "next_attempt_at": now,
                 "lease_until": None, "lease_token": None}
    db.insert(Notification(user_id=user_id, kind=kind, body=body, appointment_id=appointment_id,
                           created_at=now, email=email))


def _retryable(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 409:
            try:
                payload = error.response.json()
            except ValueError:
                return False
            return isinstance(payload, dict) and payload.get("name") == "concurrent_idempotent_requests"
        return status in {408, 429} or status >= 500
    return isinstance(error, httpx.RequestError) or (isinstance(error, HTTPException) and error.status_code == 503)


def _deliver_notice(db: MongoStore, notice: Notification, lease: str) -> dict:
    job = notice.email
    now = utcnow()
    recipient = db.get(User, notice.user_id)
    body = EMAIL_MESSAGES.get(notice.kind or "")
    deadline = as_utc(notice.created_at) + EMAIL_LIFETIME
    if not job or not recipient or not body or now >= deadline:
        return {"email.status": "skipped"}
    if not recipient.email_verified_at:
        return {"email.next_attempt_at": min(now + VERIFICATION_RECHECK, deadline)}
    if job["attempts"] > len(RETRY_DELAYS):
        log.warning("Notification email exhausted its retries (notice %s)", notice.id)
        return {"email.status": "failed"}

    attempt = db.update(Notification, {"_id": notice.id, "email.lease_token": lease,
                                       "email.lease_until": {"$gt": now}}, {}, increments={"email.attempts": 1})
    if attempt is None:
        return {}
    try:
        deliver_email(recipient.email, EMAIL_SUBJECT, f"{body}\n\nSign in to MediConnect for details.",
                      idempotency_key=f"notification-{job['key']}")
    except (httpx.HTTPError, HTTPException) as exc:
        retry_index = job["attempts"]
        if retry_index < len(RETRY_DELAYS) and _retryable(exc):
            next_attempt = utcnow() + timedelta(seconds=RETRY_DELAYS[retry_index])
            if next_attempt < deadline:
                return {"email.next_attempt_at": next_attempt}
        log.warning("Notification email could not be sent (notice %s, %s)", notice.id, type(exc).__name__)
        return {"email.status": "failed"}
    return {"email.status": "sent"}


def process_notification_email(db: MongoStore, *, limit: int = 10) -> None:
    if not email_enabled() or not email_configured():
        return
    now = utcnow()
    due = {"email.status": "queued", "email.next_attempt_at": {"$lte": now},
           "$or": [{"email.lease_until": None}, {"email.lease_until": {"$lte": now}}]}
    for candidate in db.find(Notification, due, sort=[("email.next_attempt_at", 1)], limit=limit):
        lease = secrets.token_hex(16)
        claim = db.update(Notification, {**due, "_id": candidate.id},
                          {"email.lease_token": lease, "email.lease_until": utcnow() + EMAIL_LEASE})
        if claim:
            changes = _deliver_notice(db, claim, lease)
            if changes:
                db.update(Notification, {"_id": claim.id, "email.lease_token": lease},
                          {**changes, "email.lease_until": None, "email.lease_token": None})
