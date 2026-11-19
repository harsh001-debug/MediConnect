import json
from datetime import timedelta

import httpx
import mongomock
import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from app import notifications, payments, refunds
from app.db import MongoStore, ensure_indexes, get_store, utcnow
from app.main import app
from app.models import Appointment, Notification, Refund, User
from app.security import COOKIE_NAME, issue_session


@pytest.fixture
def db(monkeypatch):
    database = mongomock.MongoClient(tz_aware=True)["notification_tests"]
    ensure_indexes(database)
    store = MongoStore(database)
    monkeypatch.setitem(app.dependency_overrides, get_store, lambda: store)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("NOTIFICATION_EMAIL_ENABLED", "0")
    return store


def account(db, name, *, verified=True):
    verified_at = utcnow() if verified else None
    return db.insert(User(email=f"{name}@example.com", full_name=name, phone="9999999999", password_hash="unused",
                          role="patient", email_verified_at=verified_at, phone_verified_at=verified_at))


def authenticate(db, client, user):
    response = Response()
    issue_session(response, db, user)
    client.cookies.set(COOKIE_NAME, response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1])


def test_inbox_paginates_legacy_rows_and_bulk_read_uses_the_viewed_boundary(db):
    patient = account(db, "patient")
    other = account(db, "other")
    now = utcnow()
    entries = [db.insert(Notification(user_id=patient.id, body=f"Update {i}", created_at=now)) for i in range(32)]
    db.database.notifications.update_many({}, {"$unset": {"kind": 1, "appointment_id": 1, "email": 1, "read_at": 1}})
    foreign = db.insert(Notification(user_id=other.id, body="Another account's notice"))

    with TestClient(app) as client:
        path = "/api/v1/notifications"
        assert client.get(f"{path}/inbox").status_code == 401
        authenticate(db, client, patient)
        response = client.get(f"{path}/inbox")
        assert response.headers["Cache-Control"] == "no-store"
        first = response.json()
        assert first["unread_count"] == 32
        assert [item["id"] for item in first["items"]] == [item.id for item in reversed(entries[2:])]
        assert first["items"][0]["target"] is None
        second = client.get(f"{path}/inbox", params={"before_id": first["next_before_id"]}).json()
        assert [item["id"] for item in second["items"]] == [entries[1].id, entries[0].id]
        assert second["next_before_id"] is None
        assert len(client.get(path).json()) == 30
        assert client.get(f"{path}/inbox", params={"before_id": foreign.id}).status_code == 404
        assert client.post(f"{path}/{foreign.id}/read").status_code == 404
        assert client.post(f"{path}/read-all", json={"through_id": foreign.id}).status_code == 404

        individual = f"{path}/{entries[0].id}/read"
        assert client.post(individual).json() == {"ok": True}
        first_read = db.get(Notification, entries[0].id).read_at
        assert client.post(individual).status_code == 200
        fresh = db.insert(Notification(user_id=patient.id, body="Newly arrived"))
        assert client.post(f"{path}/read-all", json={"through_id": first["through_id"]}).status_code == 200
        assert db.get(Notification, entries[0].id).read_at == first_read
        assert db.get(Notification, fresh.id).read_at is None
        assert db.get(Notification, foreign.id).read_at is None
        assert client.get(f"{path}/inbox").json()["unread_count"] == 1
        authenticate(db, client, other)
        assert [item["id"] for item in client.get(f"{path}/inbox").json()["items"]] == [foreign.id]


def enable_resend(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_EMAIL_ENABLED", "1")
    monkeypatch.setenv("OTP_EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "fictional-key")
    monkeypatch.setenv("OTP_EMAIL_FROM", "MediConnect <sender@example.com>")


def test_email_waits_for_verification_retries_safely_and_keeps_private_copy_in_app(db, monkeypatch):
    user = account(db, "fictional", verified=False)
    db.insert(Notification(user_id=user.id, body="Legacy row"))
    notifications.add_notice(db, user.id, "application_pending", "Email delivery was disabled at creation.")
    enable_resend(monkeypatch)
    notifications.add_notice(db, user.id, "new_booking", "Private fictional patient and prescription", appointment_id=71)
    job = db.first(Notification, {"kind": "new_booking"})
    now = utcnow() + timedelta(seconds=1)
    monkeypatch.setattr(notifications, "utcnow", lambda: now)
    requests = []

    def provider(request):
        requests.append((request.headers["Idempotency-Key"], json.loads(request.content)))
        if len(requests) == 1:
            raise httpx.ReadTimeout("response not received")
        return httpx.Response(200, json={"id": "fictional-provider-id"})

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        notifications.process_notification_email(db)
        assert db.get(Notification, job.id).email["attempts"] == 0
        assert not requests

        db.update(User, {"_id": user.id}, {"email": "verified@example.com", "email_verified_at": now})
        now += timedelta(minutes=5)
        notifications.process_notification_email(db)
        assert db.get(Notification, job.id).email["status"] == "queued"
        assert db.get(Notification, job.id).email["attempts"] == 1
        now += timedelta(seconds=60)
        notifications.process_notification_email(db)
        notifications.process_notification_email(db)

    assert len(requests) == 2 and requests[0] == requests[1]
    key, payload = requests[0]
    assert key == f"notification-{job.email['key']}"
    assert payload["to"] == ["verified@example.com"]
    assert payload["text"] == "You have a new confirmed appointment.\n\nSign in to MediConnect for details."
    assert db.get(Notification, job.id).email["status"] == "sent"
    assert db.get(Notification, job.id).read_at is None


def test_email_respects_active_leases_and_expires_unverified_jobs(db, monkeypatch):
    enable_resend(monkeypatch)
    verified = account(db, "verified")
    unverified = account(db, "unverified", verified=False)
    notifications.add_notice(db, unverified.id, "application_pending", "Pending")
    notifications.add_notice(db, verified.id, "booking_confirmed", "Confirmed")
    waiting = db.first(Notification, {"user_id": unverified.id})
    leased = db.first(Notification, {"user_id": verified.id})
    now = utcnow() + timedelta(seconds=1)
    monkeypatch.setattr(notifications, "utcnow", lambda: now)
    calls = []
    monkeypatch.setattr(notifications, "deliver_email", lambda recipient, *args, **kwargs: calls.append(recipient))
    db.update(Notification, {"_id": leased.id}, {"email.lease_token": "old-lease", "email.lease_until": now + timedelta(seconds=30)})
    db.update(Notification, {"_id": waiting.id}, {"created_at": now - timedelta(days=2)})
    notifications.process_notification_email(db)
    assert calls == []
    assert db.get(Notification, waiting.id).email["status"] == "skipped"
    now += timedelta(seconds=31)
    notifications.process_notification_email(db)
    assert calls == [verified.email]
    assert db.get(Notification, leased.id).email["lease_token"] is None


@pytest.mark.parametrize("provider_status, kind", [("processed", "refund_processed"), ("failed", "refund_needs_review")])
def test_terminal_refund_creates_one_notice_for_the_patient(db, monkeypatch, provider_status, kind):
    patient = account(db, "patient")
    doctor = account(db, "doctor")
    item = db.insert(Appointment(slot_id=1, doctor_id=doctor.id, patient_id=patient.id, mode="clinic",
                                 amount_paise=50000, hold_expires_at=utcnow(), status="cancelled"))
    refunds.queue_refund(db, item.id, "payment-id", 50000)
    monkeypatch.setattr(payments, "create_refund", lambda *_: {"id": "refund-id", "payment_id": "payment-id",
                        "amount": 50000, "currency": "INR", "status": provider_status})
    refunds.process_refunds(db)
    refunds.process_refunds(db)
    assert db.get(Refund, "payment-id").status == kind.removeprefix("refund_")
    assert [(notice.user_id, notice.kind, notice.appointment_id) for notice in db.find(Notification)] == [
        (patient.id, kind, item.id)]
