import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage

import httpx
from fastapi import HTTPException

from .db import MongoStore, as_utc, utcnow
from .models import ContactCode, User
from .schemas import ContactChannel, normalize_mobile
from .security import limit_requests

CODE_LIFETIME = timedelta(minutes=10)
RESEND_INTERVAL = timedelta(minutes=1)
MAX_ATTEMPTS = 5
VERIFICATION_EMAIL_SUBJECT = "Your MediConnect verification code"
log = logging.getLogger(__name__)


def _secret() -> bytes:
    secret = os.getenv("OTP_SECRET", "").encode()
    if len(secret) < 32:
        raise HTTPException(503, "Code delivery is unavailable. Try again later.")
    return secret


def _digest(secret: bytes, value: str) -> str:
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


def _code_digest(secret: bytes, user: User, channel: ContactChannel, nonce: str, code: str) -> str:
    return _digest(secret, f"otp:{user.id}:{channel}:{getattr(user, channel)}:{nonce}:{code}")


def _check_delivery_response(response: httpx.Response, reference_key: str, *,
                             accepted_statuses: tuple[str, ...] | None = None) -> None:
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get(reference_key), str) or not payload[reference_key].strip():
        raise HTTPException(503, "The delivery provider could not accept the code")
    status = payload.get("status")
    if status in ("failed", "undelivered", "canceled") or (accepted_statuses is not None and status not in accepted_statuses):
        raise HTTPException(503, "The delivery provider could not accept the code")


def deliver_smtp_email(destination: str, message: str, *, subject: str, message_id: str | None) -> None:
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    sender = os.getenv("OTP_EMAIL_FROM", "").strip() or username
    if not username or not password:
        raise HTTPException(503, "Email delivery is unavailable. Configure the SMTP account.")
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        if port not in {465, 587}:
            raise ValueError("Unsupported secure SMTP port")
        email = EmailMessage()
        email["From"] = sender
        email["To"] = destination
        email["Subject"] = subject
        if message_id:
            email["Message-ID"] = message_id
        email.set_content(message)
        host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
        context = ssl.create_default_context()
        if port == 465:
            connection = smtplib.SMTP_SSL(host, port, timeout=8, context=context)
        else:
            connection = smtplib.SMTP(host, port, timeout=8)
        with connection as smtp:
            smtp.ehlo()
            if port == 587:
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(username, password)
            if smtp.send_message(email):
                raise HTTPException(503, "The email provider rejected the recipient.")
    except (smtplib.SMTPException, OSError, ValueError):
        raise HTTPException(503, "The email provider could not accept the message.") from None


def email_provider() -> str:
    return os.getenv("OTP_EMAIL_PROVIDER", "resend").strip().lower()


def email_configured() -> bool:
    if email_provider() == "smtp":
        return all(os.getenv(name, "").strip() for name in ("SMTP_USERNAME", "SMTP_PASSWORD"))
    return email_provider() == "resend" and all(
        os.getenv(name, "").strip() for name in ("RESEND_API_KEY", "OTP_EMAIL_FROM"))


def deliver_email(destination: str, subject: str, message: str, *, idempotency_key: str | None = None) -> None:
    provider = email_provider()
    if provider == "smtp":
        message_id = f"<{idempotency_key}@mediconnect.local>" if idempotency_key else None
        deliver_smtp_email(destination, message, subject=subject, message_id=message_id)
        return
    if provider == "resend" and email_configured():
        headers = {"Authorization": f"Bearer {os.environ['RESEND_API_KEY'].strip()}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = httpx.post("https://api.resend.com/emails", headers=headers,
                              json={"from": os.environ["OTP_EMAIL_FROM"].strip(), "to": [destination],
                                    "subject": subject, "text": message}, timeout=8)
        _check_delivery_response(response, "id")
        return
    raise HTTPException(503, "Email delivery provider is not configured.")


def deliver_code(channel: ContactChannel, destination: str, code: str) -> None:
    message = f"Your MediConnect verification code is {code}. It expires in 10 minutes. Do not share this code."
    if channel == "email":
        deliver_email(destination, VERIFICATION_EMAIL_SUBJECT, message)
        return
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    service = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "").strip()
    if not (sid and token and service):
        raise HTTPException(503, "SMS delivery is unavailable. Try again later.")
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, token),
        data={"MessagingServiceSid": service, "To": destination, "Body": message,
              "ValidityPeriod": int(CODE_LIFETIME.total_seconds())},
        timeout=8,
    )
    _check_delivery_response(response, "sid", accepted_statuses=("accepted", "queued", "sending", "sent", "delivered"))


def contact_status(user: User, channel: ContactChannel) -> dict:
    verified = getattr(user, f"{channel}_verified_at") is not None
    challenge: ContactCode | None = getattr(user, f"{channel}_code")
    now = utcnow()
    active = challenge and challenge["attempts"] < MAX_ATTEMPTS and as_utc(challenge["expires_at"]) > now
    return {"verified": verified,
            "expires_at": as_utc(challenge["expires_at"]) if active else None,
            "resend_at": as_utc(challenge["sent_at"]) + RESEND_INTERVAL if challenge else None}


def send_code(db: MongoStore, user: User, channel: ContactChannel, source: str) -> User:
    field = f"{channel}_code"
    verified_field = f"{channel}_verified_at"
    if getattr(user, verified_field):
        return user
    contact = getattr(user, channel)
    try:
        destination = normalize_mobile(contact) if channel == "phone" else contact
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    secret = _secret()
    now = utcnow()
    previous: ContactCode | None = getattr(user, field)
    if previous and as_utc(previous["sent_at"]) + RESEND_INTERVAL > now:
        raise HTTPException(429, "Wait one minute before requesting another code.")
    limit_requests(db, "otp-source", _digest(secret, source), limit=30, seconds=3600)
    limit_requests(db, f"otp-account-{channel}", str(user.id), limit=5, seconds=3600)
    limit_requests(db, f"otp-recipient-{channel}", _digest(secret, destination.casefold()), limit=5, seconds=3600)
    code = f"{secrets.randbelow(1_000_000):06d}"
    nonce = secrets.token_hex(16)
    challenge: ContactCode = {"nonce": nonce, "digest": _code_digest(secret, user, channel, nonce, code),
                              "attempts": 0, "sent_at": now, "expires_at": now + CODE_LIFETIME}
    previous_version = {f"{field}.nonce": previous["nonce"]} if previous else {field: None}
    updated = db.update(User, {"_id": user.id, channel: contact, verified_field: None, **previous_version},
                        {field: challenge})
    if not updated:
        raise HTTPException(409, "Verification has changed. Refresh the page and try again.")
    try:
        deliver_code(channel, destination, code)
    except (httpx.HTTPError, HTTPException) as exc:
        db.update(User, {"_id": user.id, f"{field}.nonce": nonce}, {field: None})
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        log.warning("Contact delivery failed for channel %s (%s, HTTP %s)", channel, type(exc).__name__, status)
        raise HTTPException(503, "Could not send the code. Check your contact details and try again.") from None
    return updated


def check_code(db: MongoStore, user: User, channel: ContactChannel, code: str) -> User:
    verified_field = f"{channel}_verified_at"
    if getattr(user, verified_field):
        return user
    field = f"{channel}_code"
    challenge: ContactCode | None = getattr(user, field)
    if not challenge or as_utc(challenge["expires_at"]) <= utcnow():
        raise HTTPException(400, "This code has expired. Request a new code.")
    secret = _secret()
    nonce = challenge["nonce"]
    active = {"_id": user.id, channel: getattr(user, channel), verified_field: None,
              f"{field}.nonce": nonce, f"{field}.expires_at": {"$gt": utcnow()}}
    attempt = db.update(User, {**active, f"{field}.attempts": {"$lt": MAX_ATTEMPTS}}, {},
                        increments={f"{field}.attempts": 1})
    if not attempt:
        raise HTTPException(429, "This code is no longer usable. Request a new code.")
    if not hmac.compare_digest(_code_digest(secret, user, channel, nonce, code), challenge["digest"]):
        if getattr(attempt, field)["attempts"] >= MAX_ATTEMPTS:
            raise HTTPException(429, "Too many incorrect codes. Request a new code.")
        raise HTTPException(400, "That code is incorrect. Check the message and try again.")
    confirmed = db.update(User, {**active, f"{field}.expires_at": {"$gt": utcnow()}},
                          {verified_field: utcnow(), field: None})
    if not confirmed:
        raise HTTPException(409, "Verification has changed. Refresh the page and try again.")
    return confirmed
