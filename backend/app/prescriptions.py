"""Private appointment attachments and their durable processing queue."""

import logging
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from pymongo import ReturnDocument

from .db import MongoStore, as_utc, utcnow
from .models import Appointment, DoctorProfile, Slot, User
from .prescription_ai import ExtractionError, extract_prescription, provider
from .security import has_patient_access

CONSENT_VERSION = "v1"
SHARED_STATES = {"confirmed", "completed"}
PURGE_GRACE = timedelta(hours=23)
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Settings:
    project: str
    retention_days: int
    audit_days: int
    doctor_ids: frozenset[int]


def approved_doctors() -> frozenset[int]:
    try:
        return frozenset(int(value) for value in os.getenv("PRESCRIPTION_DOCTOR_IDS", "").split(",") if value.strip())
    except ValueError:
        return frozenset()


def settings() -> Settings | None:
    if os.getenv("PRESCRIPTIONS_ENABLED") != "1":
        return None
    project = os.getenv("PRESCRIPTION_GOOGLE_PROJECT", "").strip()
    doctor_ids = approved_doctors()
    try:
        retention_days = int(os.getenv("PRESCRIPTION_RETENTION_DAYS", ""))
        audit_days = int(os.getenv("PRESCRIPTION_AUDIT_RETENTION_DAYS", ""))
    except ValueError:
        return None
    configured = False
    if provider() == "gemini":
        configured = bool(os.getenv("GEMINI_API_KEY", "").strip())
    elif provider() == "vertex":
        configured = bool(re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project))
    if not (configured and doctor_ids
            and min(doctor_ids) > 0 and 1 <= retention_days <= 36500 and 1 <= audit_days <= 36500):
        return None
    return Settings(project, retention_days, audit_days, doctor_ids)


def upload_consent(profile: DoctorProfile | None, config: Settings | None) -> str | None:
    demo_testing = os.getenv("APP_ENV") == "development" and os.getenv("PRESCRIPTION_TEST_MODE") == "1"
    if (config and profile and profile.user_id in config.doctor_ids
            and profile.verification_status == "published"
            and (profile.verification_method != "demo_seeded" or demo_testing)):
        return CONSENT_VERSION
    return None


def collection(db: MongoStore):
    return db.database["prescriptions"]


def audit(db: MongoStore, row: dict, action: str, *, actor_id: int | None = None, page: int | None = None):
    now = utcnow()
    db.database["prescriptionaudit"].insert_one({
        "appointment_id": row["_id"], "actor_id": actor_id, "action": action, "page": page,
        "created_at": now, "expires_at": now + timedelta(days=row["audit_days"]),
    }, session=db.session)


def visible_statuses(db: MongoStore, appointments: list[Appointment], role: str) -> dict[int, str]:
    now = utcnow()
    approved = approved_doctors() if role == "doctor" else frozenset()
    shared, drafts = [], []
    for item in appointments:
        if not item.patient_id or not item.prescription_revision:
            continue
        if item.status in SHARED_STATES and (role == "patient" or item.doctor_id in approved):
            shared.append(item.id)
        elif role == "patient" and item.status == "pending_payment" and as_utc(item.hold_expires_at) > now:
            drafts.append(item.id)
    if not (shared or drafts):
        return {}
    rows = collection(db).find({
        "expires_at": {"$gt": now},
        "$or": [{"_id": {"$in": shared}, "status": {"$ne": "draft"}},
                {"_id": {"$in": drafts}, "status": "draft"}],
    }, {"status": 1})
    return {row["_id"]: row["status"] for row in rows}


def can_read(item: Appointment, user: User) -> bool:
    if not item.patient_id:
        return False
    if has_patient_access(user) and user.id == item.patient_id:
        return item.status in SHARED_STATES or (
            item.status == "pending_payment" and as_utc(item.hold_expires_at) > utcnow())
    return (user.role == "doctor" and user.id == item.doctor_id and item.doctor_id in approved_doctors()
            and item.status in SHARED_STATES)


def pending_appointment(db: MongoStore, appointment_id: int, user: User) -> Appointment:
    item = db.get(Appointment, appointment_id)
    if not item or item.patient_id != user.id:
        raise HTTPException(404, "Appointment not found")
    if item.status != "pending_payment" or as_utc(item.hold_expires_at) <= utcnow():
        raise HTTPException(409, "Prescription photos can be changed only while the payment hold is active.")
    return item


def lock_draft(db: MongoStore, appointment_id: int, user: User) -> Appointment:
    # Touch the same appointment document that payment confirmation and cancellation write.
    item = db.update(Appointment, {
        "_id": appointment_id, "patient_id": user.id, "status": "pending_payment",
        "hold_expires_at": {"$gt": utcnow()},
    }, {}, increments={"prescription_revision": 1})
    if not item:
        raise HTTPException(409, "Prescription photos can be changed only while the payment hold is active.")
    return item


def save_draft(db: MongoStore, appointment_id: int, user: User, photos: list[bytes], config: Settings) -> dict:
    version = uuid4().hex

    def save(tx):
        item = lock_draft(tx, appointment_id, user)
        slot = tx.get(Slot, item.slot_id)
        row = {
            "_id": item.id, "version": version, "photos": photos, "status": "draft", "entries": [],
            "consent_version": CONSENT_VERSION, "consented_at": utcnow(),
            "expires_at": item.hold_expires_at, "purge_at": item.hold_expires_at + PURGE_GRACE,
            "retain_until": slot.end_at + timedelta(days=config.retention_days), "audit_days": config.audit_days,
            "attempts": 0, "next_attempt_at": None, "lease_until": None, "lease_token": None,
        }
        result = collection(tx).replace_one({"_id": item.id}, row, upsert=True, session=tx.session)
        audit(tx, row, "replaced" if result.matched_count else "uploaded", actor_id=user.id)
        return {"status": "draft", "version": version, "photo_count": len(photos), "entries": []}

    return db.run_transaction(save)


def remove(db: MongoStore, query: dict, action: str, *, actor_id: int | None = None) -> None:
    row = collection(db).find_one_and_delete(query, projection={"audit_days": 1}, session=db.session)
    if row:
        audit(db, row, action, actor_id=actor_id)


def delete_draft(db: MongoStore, appointment_id: int, user: User) -> None:
    pending_appointment(db, appointment_id, user)

    def delete(tx):
        lock_draft(tx, appointment_id, user)
        remove(tx, {"_id": appointment_id, "status": "draft"}, "removed", actor_id=user.id)

    db.run_transaction(delete)


def share_draft(db: MongoStore, item: Appointment, now) -> bool:
    if not item.prescription_revision:
        return False
    rows = collection(db)
    row = rows.find_one({"_id": item.id, "status": "draft", "expires_at": {"$gt": now}},
                        {"retain_until": 1, "audit_days": 1}, session=db.session)
    if not row:
        return False
    rows.update_one({"_id": item.id}, {"$set": {
        "status": "queued", "next_attempt_at": now,
        "expires_at": row["retain_until"], "purge_at": row["retain_until"] + PURGE_GRACE,
    }}, session=db.session)
    audit(db, row, "shared")
    return True


def process_prescriptions(db: MongoStore, *, limit: int = 4) -> None:
    rows = collection(db)
    now = utcnow()
    expired = {"expires_at": {"$lte": now}}
    for candidate in rows.find(expired, {"version": 1}).sort("expires_at").limit(50):
        db.run_transaction(lambda tx: remove(tx, {**expired, **candidate}, "purged"))

    config = settings()
    if not config:
        return
    due = {"status": {"$in": ["queued", "processing"]}, "expires_at": {"$gt": now},
           "next_attempt_at": {"$lte": now}, "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}]}
    for candidate in rows.find(due, {"attempts": 1}).sort("next_attempt_at").limit(limit):
        job = {**due, "_id": candidate["_id"]}
        if candidate["attempts"] >= MAX_ATTEMPTS:
            rows.update_one(job, {"$set": {"status": "unavailable", "next_attempt_at": None,
                                          "lease_token": None, "lease_until": None}})
            continue
        lease = uuid4().hex
        claimed = rows.find_one_and_update({**job, "attempts": {"$lt": MAX_ATTEMPTS}}, {
            "$set": {"status": "processing", "lease_token": lease, "lease_until": utcnow() + timedelta(minutes=3)},
            "$inc": {"attempts": 1},
        }, return_document=ReturnDocument.AFTER)
        if not claimed:
            continue
        item = db.get(Appointment, claimed["_id"])
        changes = {"lease_token": None, "lease_until": None, "next_attempt_at": None}
        if not item or item.status not in SHARED_STATES or item.doctor_id not in config.doctor_ids:
            changes["status"] = "unavailable"
        else:
            try:
                entries = extract_prescription(config.project, claimed["photos"])
                status = "ready" if any(entry["kind"] != "unreadable" for entry in entries) else "unreadable"
                changes.update(status=status, entries=entries)
            except ExtractionError as exc:
                logging.getLogger(__name__).warning("Prescription extraction failed (%s)", exc.reason)
                if exc.retryable and claimed["attempts"] < MAX_ATTEMPTS:
                    delay = 15 * (2 ** (claimed["attempts"] - 1))
                    changes.update(status="queued", next_attempt_at=utcnow() + timedelta(seconds=delay))
                else:
                    changes["status"] = "unavailable"
        # Cancellation and retention can delete the attachment while the provider is running.
        rows.update_one({"_id": claimed["_id"], "lease_token": lease, "expires_at": {"$gt": utcnow()}},
                        {"$set": changes})
