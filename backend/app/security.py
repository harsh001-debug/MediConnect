import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from pymongo.errors import DuplicateKeyError

from .db import MongoStore, as_utc, get_store, utcnow
from .models import AuthRateLimit, LoginSession, User

COOKIE_NAME = "mediconnect_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt_text, digest_text = stored.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_text), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, base64.b64decode(digest_text))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(response: Response, db: MongoStore, user: User) -> None:
    token = secrets.token_urlsafe(32)
    db.insert(LoginSession(token_hash=token_hash(token), user_id=user.id, expires_at=utcnow() + timedelta(days=7),
                           generation=user.session_generation))
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=os.getenv("APP_ENV") == "production", samesite="lax", max_age=7 * 86400, path="/")


def session_token(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in")
    return token


def session_user(token: str = Depends(session_token), db: MongoStore = Depends(get_store)) -> User:
    login = db.get(LoginSession, token_hash(token))
    if not login or as_utc(login.expires_at) <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in")
    user = db.get(User, login.user_id)
    if not user or user.suspended_at or login.generation != user.session_generation:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in")
    return user


def contact_verification_required(user: User) -> bool:
    return user.role in {"patient", "doctor"} and not user.admin_access and not (user.email_verified_at and user.phone_verified_at)


def current_user(user: User = Depends(session_user)) -> User:
    if contact_verification_required(user):
        raise HTTPException(403, "Verify your email and mobile number to continue")
    return user


def limit_requests(db: MongoStore, scope: str, identity: str, *, limit: int, seconds: int) -> None:
    now = int(utcnow().timestamp())
    ends_at = now - now % seconds + seconds
    key = token_hash(f"{scope}:{ends_at}:{identity}")
    try:
        db.database["authratelimits"].update_one(
            {"_id": key, "count": {"$lt": limit}},
            {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": datetime.fromtimestamp(ends_at, timezone.utc)}},
            upsert=True,
        )
    except DuplicateKeyError:
        # Concurrent first requests can both try the insert. Retry against the existing counter.
        updated = db.database["authratelimits"].update_one({"_id": key, "count": {"$lt": limit}}, {"$inc": {"count": 1}})
        if not updated.modified_count:
            raise HTTPException(429, "Too many attempts. Try again later.",
                                headers={"Retry-After": str(ends_at - now)}) from None


def request_source(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def has_patient_access(user: User) -> bool:
    return user.role in {"patient", "admin"} or user.admin_access


def require_role(role: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if (user.role != role and not (role == "admin" and user.admin_access)
                and not (role == "patient" and has_patient_access(user))):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
        return user
    return dependency
