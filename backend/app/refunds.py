import hashlib
import secrets
from datetime import timedelta

import httpx
from fastapi import HTTPException

from . import payments
from .db import MongoStore, utcnow
from .models import Appointment, Refund
from .notifications import add_notice


def queue_refund(db: MongoStore, appointment_id: int, payment_id: str, amount_paise: int) -> None:
    if amount_paise > 0 and not db.get(Refund, payment_id):
        db.insert(Refund(payment_id=payment_id, appointment_id=appointment_id, amount_paise=amount_paise))


def refund_dict(refund: Refund) -> dict:
    return {"amount_paise": refund.amount_paise, "status": refund.status,
            "reference": refund.provider_refund_id}


def _provider_refund(refund: Refund) -> dict:
    if refund.provider_refund_id:
        return payments.fetch_refund(refund.payment_id, refund.provider_refund_id)
    receipt = "mc-" + hashlib.sha256(refund.payment_id.encode()).hexdigest()[:32]
    try:
        return payments.create_refund(refund.payment_id, refund.amount_paise, receipt)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {400, 409, 422}:
            existing = payments.find_refund(refund.payment_id, receipt)
            if existing:
                return existing
        raise


def _save_result(db: MongoStore, payment_id: str, lease: str, changes: dict) -> None:
    owned = {"_id": payment_id, "lease_token": lease}
    if changes.get("status") not in {"processed", "needs_review"}:
        db.update(Refund, owned, changes)
        return

    def save(tx):
        updated = tx.update(Refund, owned, changes)
        if not updated:
            return
        appointment = tx.get(Appointment, updated.appointment_id)
        if appointment:
            message = ("Your test refund was processed." if updated.status == "processed"
                       else "Your test refund needs review. Check your appointment for the latest status.")
            add_notice(tx, appointment.patient_id, f"refund_{updated.status}", message, appointment_id=appointment.id)

    db.run_transaction(save)


def process_refunds(db: MongoStore, *, limit: int = 10) -> None:
    now = utcnow()
    due = {"status": {"$in": ["queued", "processing"]}, "next_attempt_at": {"$lte": now},
           "$or": [{"lease_until": None}, {"lease_until": {"$lte": now}}]}
    # Claim shortly before each provider call. A lease lost on restart expires automatically.
    for candidate in db.find(Refund, due, sort=[("next_attempt_at", 1)], limit=limit):
        lease = secrets.token_hex(16)
        refund = db.update(Refund, {**due, "_id": candidate.payment_id},
                           {"lease_token": lease, "lease_until": utcnow() + timedelta(seconds=60)},
                           increments={"attempts": 1})
        if not refund:
            continue
        changes = {"lease_token": None, "lease_until": None, "error": None}
        try:
            actual = _provider_refund(refund)
            if (actual.get("payment_id") != refund.payment_id or actual.get("amount") != refund.amount_paise
                    or actual.get("currency") != "INR" or not isinstance(actual.get("id"), str)
                    or actual["id"] != (refund.provider_refund_id or actual["id"])):
                changes.update(status="needs_review", error="Provider refund details did not match")
            else:
                provider_status = actual.get("status")
                changes["provider_refund_id"] = actual["id"]
                if provider_status == "processed":
                    changes["status"] = "processed"
                elif provider_status == "pending":
                    changes.update(status="processing", next_attempt_at=utcnow() + timedelta(minutes=1))
                else:
                    changes.update(status="needs_review", error="Provider reported an unsuccessful refund")
        except (httpx.HTTPError, ValueError, HTTPException, KeyError) as exc:
            http_status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            retryable = http_status is None or http_status in {408, 409, 429} or http_status >= 500
            if retryable and (refund.attempts < 8 or refund.provider_refund_id):
                delay = min(3600, 15 * (2 ** min(refund.attempts, 8)))
                changes["next_attempt_at"] = utcnow() + timedelta(seconds=delay)
            else:
                changes.update(status="needs_review", error="Refund provider could not complete the request")
        _save_result(db, refund.payment_id, lease, changes)
