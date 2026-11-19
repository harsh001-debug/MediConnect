"""Email-based account recovery, separate from contact verification."""

import hmac
import logging
import secrets

import httpx
from fastapi import BackgroundTasks, HTTPException
from pymongo.errors import DuplicateKeyError

from . import contact_verification as otp
from .db import MongoStore, utcnow
from .models import PasswordResetChallenge, User
from .security import hash_password, limit_requests

log = logging.getLogger(__name__)


def reset_key(secret: bytes, email: str) -> str:
    return otp._digest(secret, f"password-reset:{email}")


def code_digest(secret: bytes, key: str, nonce: str, code: str) -> str:
    return otp._digest(secret, f"password-reset-code:{key}:{nonce}:{code}")


def request_reset(db: MongoStore, email: str, source: str, background_tasks: BackgroundTasks) -> None:
    secret = otp._secret()
    key = reset_key(secret, email)
    limit_requests(db, "password-reset-send-source", source, limit=20, seconds=3600)
    limit_requests(db, "password-reset-send-address", key, limit=5, seconds=3600)
    if not otp.email_configured():
        raise HTTPException(503, "Password reset email is unavailable. Try again later.")
    # Keep account lookup and provider latency out of the public response.
    background_tasks.add_task(send_reset, db, email, secret, key)


def send_reset(db: MongoStore, email: str, secret: bytes, key: str) -> None:
    user = db.first(User, {"email": email, "email_verified_at": {"$type": "date"}, "suspended_at": None})
    if not user:
        return

    now = utcnow()
    code = f"{secrets.randbelow(1_000_000):06d}"
    nonce = secrets.token_hex(16)
    fields = {"user_id": user.id, "generation": user.session_generation, "nonce": nonce,
              "digest": code_digest(secret, key, nonce, code), "sent_at": now,
              "expires_at": now + otp.CODE_LIFETIME, "attempts": 0}
    try:
        db.insert(PasswordResetChallenge(key=key, **fields))
    except DuplicateKeyError:
        updated = db.update(PasswordResetChallenge, {"_id": key, "sent_at": {"$lte": now - otp.RESEND_INTERVAL}}, fields)
        if not updated:
            return

    try:
        otp.deliver_email(email, "Your MediConnect password reset code",
                          f"Your MediConnect password reset code is {code}. It expires in 10 minutes. "
                          "If you did not request a password reset, you can ignore this email.")
    except (httpx.HTTPError, HTTPException) as exc:
        log.warning("Password reset email delivery failed (%s)", type(exc).__name__)
        # Match the nonce so a late failure cannot invalidate a replacement code.
        db.delete_one(PasswordResetChallenge, {"_id": key, "nonce": nonce})


def reset_password(db: MongoStore, email: str, code: str, password: str, source: str) -> None:
    secret = otp._secret()
    key = reset_key(secret, email)
    limit_requests(db, "password-reset-confirm-source", source, limit=30, seconds=900)
    limit_requests(db, "password-reset-confirm-address", key, limit=15, seconds=3600)
    challenge = db.update(PasswordResetChallenge,
                          {"_id": key, "expires_at": {"$gt": utcnow()}, "attempts": {"$lt": otp.MAX_ATTEMPTS}},
                          {}, increments={"attempts": 1})
    invalid = HTTPException(400, "That code is invalid or has expired. Request a new code if needed.")
    if not challenge or not hmac.compare_digest(challenge.digest, code_digest(secret, key, challenge.nonce, code)):
        raise invalid

    password_hash = hash_password(password)

    def complete(tx: MongoStore) -> bool:
        now = utcnow()
        used = tx.delete_one(PasswordResetChallenge, {"_id": key, "nonce": challenge.nonce, "expires_at": {"$gt": now}})
        if not used:
            return False

        account = {"_id": challenge.user_id, "email": email, "email_verified_at": {"$type": "date"}, "suspended_at": None}
        if challenge.generation == 0:
            account["$or"] = [{"session_generation": 0}, {"session_generation": {"$exists": False}}]
        else:
            account["session_generation"] = challenge.generation
        return tx.update(User, account, {"password_hash": password_hash}, increments={"session_generation": 1}) is not None

    if not db.run_transaction(complete):
        raise invalid
