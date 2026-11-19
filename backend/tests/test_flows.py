import hashlib
import hmac
import json
import os
from datetime import timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
import mongomock
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo
from pymongo.errors import DuplicateKeyError

from app import contact_verification as otp, payments, prescriptions, refunds
from app.api import appointments, auth
from app.api import prescriptions as prescription_routes
from app.db import MongoStore, ensure_indexes, get_store, utcnow
from app.main import app
from app.models import Appointment, DoctorProfile, GuestChallenge, GuestPatient, Notification, PasswordResetChallenge, PaymentAttempt, Refund, Slot, User
from app.registry import RegistryRecord, RegistryResult, interpret_result, lookup_registration
from app.security import hash_password


@pytest.fixture
def setup(monkeypatch):
    database = mongomock.MongoClient(tz_aware=True)["mediconnect_test"]
    ensure_indexes(database)
    mongo = MongoStore(database)

    def session():
        yield mongo

    app.dependency_overrides[get_store] = session
    monkeypatch.setenv("OTP_SECRET", "isolated tests only " * 3)
    monkeypatch.setattr(otp.secrets, "randbelow", lambda maximum: 123456)
    monkeypatch.setattr(otp, "deliver_code", lambda *args: None)
    monkeypatch.setattr(auth, "lookup_registration", lambda *args: RegistryResult("unavailable", detail="Site unavailable"))
    monkeypatch.setattr(payments, "create_order", lambda amount, receipt: {"id": "order_test_123"})
    monkeypatch.setattr(payments, "credentials", lambda: ("rzp_test_key", "secret"))
    monkeypatch.setattr(payments, "verify_signature", lambda order, payment, signature: signature == "valid")
    monkeypatch.setattr(payments, "fetch_payment", lambda payment: {"order_id": "order_test_123", "amount": 50000, "currency": "INR", "status": "captured"})
    mongo.insert(User(email="admin@example.com", full_name="Admin", phone="0000000000", password_hash=hash_password("testpass123"), role="admin"))
    yield mongo
    app.dependency_overrides.clear()


def verify_contacts(client: TestClient):
    for channel in ("email", "phone"):
        sent = client.post(f"/api/v1/auth/contacts/{channel}/send")
        assert sent.status_code == 200, sent.text
        verified = client.post(f"/api/v1/auth/contacts/{channel}/verify", json={"code": "123456"})
        assert verified.status_code == 200, verified.text
    assert verified.json()["verification_required"] is False


def patient(client: TestClient, *, verified=True):
    response = client.post("/api/v1/auth/patient-signup", json={"email": "patient@example.com", "password": "testpass123", "full_name": "Demo Patient", "phone": "9999999999"})
    assert response.status_code == 201, response.text
    if verified:
        verify_contacts(client)


REGISTRATION = {"full_name": "Demo Doctor", "council": "Maharashtra Medical Council", "registration_number": "MMC-123"}


def doctor(client: TestClient, *, verified=True, confirm_registry_match=True):
    data = {**REGISTRATION, "email": "doctor@example.com", "password": "testpass123", "phone": "9999999998", "registration_date": "2020-01-01", "qualifications": "MBBS", "specialty": "General Medicine", "experience_years": 5, "clinic_name": "Health Clinic", "clinic_address": "123 Station Road", "city": "Pune", "bio": "Care for all", "clinic_fee_rupees": 400, "online_fee_rupees": 500}
    if confirm_registry_match is not None:
        data["confirm_registry_match"] = confirm_registry_match
    response = client.post("/api/v1/auth/doctor-signup", json=data)
    assert response.status_code == 201, response.text
    if verified:
        verify_contacts(client)
    return response.json()["id"]


def login(client: TestClient, email: str):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "testpass123"})
    assert response.status_code == 200, response.text


def test_password_reset_uses_email_and_revokes_old_sessions(setup, monkeypatch):
    delivered = []
    monkeypatch.setattr(otp, "email_configured", lambda: True)
    monkeypatch.setattr(otp, "deliver_email", lambda destination, subject, text: delivered.append((destination, subject, text)))
    base = "/api/v1/auth"
    with TestClient(app) as existing, TestClient(app) as recovery:
        patient(existing)
        account = setup.first(User, {"email": "patient@example.com"})
        # Existing documents may predate the session generation field.
        setup.database.users.update_one({"_id": account.id}, {"$unset": {"session_generation": ""}})

        response = recovery.post(f"{base}/password-reset/request", json={"email": "PATIENT@example.com"})
        assert response.status_code == 202 and response.json() == {"ok": True}
        assert response.headers["cache-control"] == "no-store"
        assert len(delivered) == 1
        assert delivered[0][:2] == ("patient@example.com", "Your MediConnect password reset code")
        assert "123456" in delivered[0][2]
        challenge, = setup.find(PasswordResetChallenge)
        assert challenge.digest != "123456"

        details = {"email": account.email, "password": "updatedpass123", "code": "123456"}
        assert recovery.post(f"{base}/password-reset/confirm", json={**details, "code": "000001"}).status_code == 400
        assert existing.get(f"{base}/me").status_code == 200
        assert recovery.post(f"{base}/password-reset/confirm", json=details).status_code == 204
        assert recovery.post(f"{base}/password-reset/confirm", json=details).status_code == 400
        assert existing.get(f"{base}/me").status_code == 401
        assert recovery.post(f"{base}/login", json={"email": account.email, "password": "testpass123"}).status_code == 401
        signed_in = recovery.post(f"{base}/login", json={"email": account.email, "password": details["password"]})
        assert signed_in.status_code == 200
        assert signed_in.json()["verification_required"] is False


def test_password_reset_request_hides_account_eligibility(setup, monkeypatch):
    delivered = []
    monkeypatch.setattr(otp, "email_configured", lambda: True)
    monkeypatch.setattr(otp, "deliver_email", lambda *args: delivered.append(args))
    base = "/api/v1/auth"
    with TestClient(app) as client:
        patient(client, verified=False)
        setup.update(User, {"email": "admin@example.com"}, {"email_verified_at": utcnow(), "suspended_at": utcnow()})
        for email in ("patient@example.com", "unknown@example.com", "admin@example.com"):
            response = client.post(f"{base}/password-reset/request", json={"email": email})
            assert response.status_code == 202 and response.json() == {"ok": True}
            confirmed = client.post(f"{base}/password-reset/confirm", json={
                "email": email, "code": "123456", "password": "updatedpass123",
            })
            assert confirmed.status_code == 400
        assert not delivered
        assert not setup.find(PasswordResetChallenge)


def test_only_one_concurrent_request_can_use_a_password_reset_code(setup, monkeypatch):
    monkeypatch.setattr(otp, "email_configured", lambda: True)
    monkeypatch.setattr(otp, "deliver_email", lambda *args: None)
    base = "/api/v1/auth/password-reset"
    with TestClient(app) as client:
        patient(client)
        email = "patient@example.com"
        assert client.post(f"{base}/request", json={"email": email}).status_code == 202

        def confirm(password):
            result = client.post(f"{base}/confirm", json={"email": email, "password": password, "code": "123456"})
            return result.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(confirm, ("replacement-one", "replacement-two")))
        assert sorted(statuses) == [204, 400]
        assert setup.first(User, {"email": email}).session_generation == 1


def test_password_reset_resend_limits_attempts_and_invalidates_old_codes(setup, monkeypatch):
    delivered = []
    monkeypatch.setattr(otp, "email_configured", lambda: True)
    monkeypatch.setattr(otp, "deliver_email", lambda _destination, _subject, text: delivered.append(text))
    base = "/api/v1/auth/password-reset"
    with TestClient(app) as client:
        patient(client)
        email = {"email": "patient@example.com"}
        codes = iter((100000, 200000, 300000))
        monkeypatch.setattr(otp.secrets, "randbelow", lambda _maximum: next(codes))
        assert client.post(f"{base}/request", json=email).status_code == 202
        assert client.post(f"{base}/request", json=email).status_code == 202
        assert len(delivered) == 1
        for _ in range(5):
            response = client.post(f"{base}/confirm", json={**email, "password": "updatedpass123", "code": "000001"})
            assert response.status_code == 400
        assert client.post(f"{base}/confirm", json={**email, "password": "updatedpass123", "code": "100000"}).status_code == 400

        challenge, = setup.find(PasswordResetChallenge)
        setup.update(PasswordResetChallenge, {"_id": challenge.key}, {"sent_at": utcnow() - timedelta(minutes=2)})
        assert client.post(f"{base}/request", json=email).status_code == 202
        assert len(delivered) == 2 and "300000" in delivered[1]
        assert client.post(f"{base}/confirm", json={**email, "password": "updatedpass123", "code": "100000"}).status_code == 400
        setup.update(PasswordResetChallenge, {"_id": challenge.key}, {"expires_at": utcnow() - timedelta(seconds=1)})
        assert client.post(f"{base}/confirm", json={**email, "password": "updatedpass123", "code": "300000"}).status_code == 400


def test_otp_verifies_each_contact_and_restores_access_after_login(setup, monkeypatch):
    codes = iter((123, 654321))
    delivered = []
    monkeypatch.setattr(otp.secrets, "randbelow", lambda maximum: next(codes))
    monkeypatch.setattr(otp, "deliver_code", lambda *args: delivered.append(args))
    base = "/api/v1/auth"
    with TestClient(app) as client:
        patient(client, verified=False)
        start = client.get(f"{base}/me")
        assert start.headers["cache-control"] == "no-store"
        assert start.json()["verification_required"] is True
        assert start.json()["phone"] == "+919999999999"
        assert client.get("/api/v1/appointments").status_code == 403
        assert client.post(f"{base}/contacts/email/send").status_code == 200
        assert client.post(f"{base}/contacts/email/send").status_code == 429
        assert client.post(f"{base}/contacts/phone/send").status_code == 200
        stored = setup.get(User, start.json()["id"])
        assert stored.email_code["digest"] != "000123"
        assert "digest" not in client.get(f"{base}/me").text
        assert delivered == [("email", "patient@example.com", "000123"), ("phone", "+919999999999", "654321")]
        assert client.post(f"{base}/contacts/phone/verify", json={"code": "000123"}).status_code == 400
        assert client.post(f"{base}/contacts/email/verify", json={"code": "000123"}).json()["verification_required"] is True
        assert client.get("/api/v1/appointments").status_code == 403
        assert client.post(f"{base}/logout").status_code == 200
        assert client.post(f"{base}/contacts/phone/verify", json={"code": "654321"}).status_code == 401
        login(client, "patient@example.com")
        assert client.post(f"{base}/contacts/phone/verify", json={"code": "654321"}).json()["verification_required"] is False
        assert setup.get(User, stored.id).phone_code is None
        assert client.get("/api/v1/appointments").status_code == 200


def test_otp_exhaustion_expiry_contact_edits_and_provider_failure(setup, monkeypatch):
    base = "/api/v1/auth/contacts"
    with TestClient(app) as client:
        patient(client, verified=False)
        user = setup.first(User, {"email": "patient@example.com"})
        assert client.post(f"{base}/phone/send").status_code == 200
        for expected in [400, 400, 400, 400, 429]:
            assert client.post(f"{base}/phone/verify", json={"code": "000000"}).status_code == expected
        assert client.post(f"{base}/phone/verify", json={"code": "123456"}).status_code == 429
        assert client.post(f"{base}/email/send").status_code == 200
        setup.update(User, {"_id": user.id}, {"email_code.expires_at": utcnow() - timedelta(seconds=1)})
        assert client.post(f"{base}/email/verify", json={"code": "123456"}).status_code == 400
        assert client.put(f"{base}/phone", json={"value": "98765 43210", "password": "wrong"}).status_code == 401
        edited = client.put(f"{base}/phone", json={"value": "98765 43210", "password": "testpass123"})
        assert edited.json()["phone"] == "+919876543210"
        assert setup.get(User, user.id).phone_code is None

        def delivery_unavailable(*args):
            raise otp.httpx.ReadTimeout("provider timed out")

        monkeypatch.setattr(otp, "deliver_code", delivery_unavailable)
        assert client.post(f"{base}/phone/send").status_code == 503
        assert setup.get(User, user.id).phone_code is None
        assert client.get("/api/v1/appointments").status_code == 403


def test_unverified_doctor_is_hidden_until_both_codes_are_confirmed(setup, monkeypatch):
    monkeypatch.setattr(auth, "lookup_registration", lambda *args: RegistryResult("matched", matched_name="Demo Doctor"))
    with TestClient(app) as client:
        doctor_id = doctor(client, verified=False)
        assert client.get(f"/api/v1/doctors/{doctor_id}").status_code == 404
        assert client.get(f"/api/v1/doctors/{doctor_id}/slots").status_code == 404
        assert client.get("/api/v1/doctor/profile").status_code == 403
        assert client.get("/api/v1/doctors").json() == []
        verify_contacts(client)
        assert client.get(f"/api/v1/doctors/{doctor_id}").status_code == 200


def test_search_fee_date_and_visit_type_match_the_same_india_time_slot(setup, monkeypatch):
    monkeypatch.setattr(auth, "lookup_registration", lambda *args: RegistryResult("matched", matched_name="Demo Doctor"))
    with TestClient(app) as client:
        doctor_id = doctor(client)
        start = (utcnow() + timedelta(days=2)).replace(hour=19, minute=0, second=0, microsecond=0)
        setup.insert(Slot(doctor_id=doctor_id, mode="online", start_at=start, end_at=start + timedelta(minutes=30)))
        setup.insert(Slot(doctor_id=doctor_id, mode="clinic", start_at=start - timedelta(hours=2), end_at=start - timedelta(hours=1)))
        india_day = (start + timedelta(days=1)).date().isoformat()
        route = f"/api/v1/doctors?available_on={india_day}"
        assert client.get(f"{route}&max_fee=400").json() == []
        assert client.get(f"{route}&mode=clinic").json() == []
        assert client.get(f"{route}&max_fee=500").json()[0]["matching_modes"] == ["online"]


def test_registry_fallback_and_admin_approval(setup):
    with TestClient(app) as doc, TestClient(app) as admin:
        doctor_id = doctor(doc)
        assert doc.get("/api/v1/doctor/profile").json()["verification_status"] == "pending"
        assert admin.get("/api/v1/doctors").json() == []
        login(admin, "admin@example.com")
        pending = admin.get("/api/v1/admin/pending-doctors").json()
        assert pending[0]["check"]["result"] == "unavailable"
        response = admin.post(f"/api/v1/admin/doctors/{doctor_id}/decision", json={"decision": "approved", "note": "Checked official register manually"})
        assert response.status_code == 200, response.text
        assert len(admin.get("/api/v1/doctors").json()) == 1
        inbox = doc.get("/api/v1/notifications/inbox").json()
        assert inbox["unread_count"] == 2
        assert all(item["target"] == {"type": "doctor_profile"} for item in inbox["items"])
        assert "Checked official register manually" in inbox["items"][0]["body"]


def test_exact_match_auto_publishes(setup, monkeypatch):
    monkeypatch.setattr(auth, "lookup_registration", lambda *args: RegistryResult("matched", matched_name="Demo Doctor"))
    with TestClient(app) as client:
        doctor(client)
        profile = client.get("/api/v1/doctor/profile").json()
        assert profile["verification_status"] == "published"
        assert profile["verification_method"] == "registry_matched"


def test_registry_lookup_previews_match_and_rechecks_on_confirmation(setup, monkeypatch):
    record = RegistryRecord(**REGISTRATION, qualifications="MBBS")
    calls = []

    def search(*details):
        calls.append(details)
        if len(calls) == 1:
            return RegistryResult("matched", matched_name=record.full_name, record=record)
        return RegistryResult("review", detail="Record no longer matches")

    monkeypatch.setattr(auth, "lookup_registration", search)
    with TestClient(app) as client:
        lookup = client.post("/api/v1/auth/doctor-registration-lookup", json=REGISTRATION)
        assert lookup.status_code == 200, lookup.text
        assert lookup.json()["status"] == "matched"
        assert lookup.json()["record"] == {**REGISTRATION, "qualifications": "MBBS"}
        assert lookup.json()["source_url"].startswith("https://nmc.org.in/")
        doctor_id = doctor(client)
        expected = (record.registration_number, record.council, record.full_name)
        assert calls == [expected, expected]
        assert client.get(f"/api/v1/doctors/{doctor_id}").status_code == 404
        assert client.get("/api/v1/doctor/profile").json()["verification_status"] == "pending"


@pytest.mark.parametrize("confirmation", [False, None])
def test_doctor_can_skip_registry_lookup(setup, monkeypatch, confirmation):
    def unexpected_lookup(*details):
        pytest.fail("Signup contacted NMC without confirmation")

    monkeypatch.setattr(auth, "lookup_registration", unexpected_lookup)
    with TestClient(app) as client, TestClient(app) as admin:
        doctor_id = doctor(client, verified=False, confirm_registry_match=confirmation)
        login(admin, "admin@example.com")
        assert admin.get("/api/v1/admin/pending-doctors").json() == []
        verify_contacts(client)
        pending = admin.get("/api/v1/admin/pending-doctors").json()
        assert [(item["id"], item["check"]) for item in pending] == [(doctor_id, None)]


def test_registry_lookup_validates_and_limits_requests(setup, monkeypatch):
    import app.security as security

    time_snapshot = utcnow()
    monkeypatch.setattr(security, "utcnow", lambda: time_snapshot)
    with TestClient(app) as client:
        path = "/api/v1/auth/doctor-registration-lookup"
        assert client.post(path, json={**REGISTRATION, "registration_number": "  "}).status_code == 422
        for _ in range(12):
            response = client.post(path, json=REGISTRATION)
            assert response.status_code == 200
            assert response.json()["status"] == "unavailable"
            assert response.json()["record"] is None
        assert client.post(path, json=REGISTRATION).status_code == 429


def test_register_response_requires_unique_exact_match():
    row = {"registration_no": "MMC-123", "state_medical_council": "Maharashtra Medical Council", "name": "Demo Doctor",
           "qualification": " MBBS ", "additional_qualifications": [{"qualification": "MD", "year": 2019}], "removed_status": "0"}
    payload = {"success": True, "data": [row], "pagination": {"total": 1}}
    match = interpret_result(payload, "MMC-123", "maharashtra medical council", "Demo  Doctor")
    assert match.status == "matched"
    assert match.record == RegistryRecord(**REGISTRATION, qualifications="MBBS, MD")
    assert interpret_result(payload, "MMC-123", "Another Council", "Demo Doctor").status == "review"
    assert interpret_result(payload, "MMC123", "Maharashtra Medical Council", "Demo Doctor").status == "review"
    assert interpret_result({**payload, "pagination": {"total": 2}}, "MMC-123", "Maharashtra Medical Council", "Demo Doctor").status == "review"
    for other_row in (row, {**row, "name": None}):
        ambiguous = {**payload, "data": [row, other_row], "pagination": {"total": 2}}
        assert interpret_result(ambiguous, "MMC-123", "Maharashtra Medical Council", "Demo Doctor").status == "review"
    removed = {**payload, "data": [{**row, "removed_status": "1"}]}
    assert interpret_result(removed, "MMC-123", "Maharashtra Medical Council", "Demo Doctor").status == "review"
    assert interpret_result({"success": False}, "MMC-123", "Maharashtra Medical Council", "Demo Doctor").status == "unavailable"


def test_public_register_request_uses_number_search(monkeypatch):
    import app.registry as registry

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"success": True, "data": [{"registration_no": "MMC-123", "state_medical_council": "Maharashtra Medical Council", "name": "Demo Doctor"}], "pagination": {"total": 1}}

    def fake_get(url, **kwargs):
        assert url == registry.SEARCH_URL
        assert kwargs["params"]["search_type"] == "reg_no"
        assert kwargs["params"]["reg_no"] == "MMC-123"
        return Response()

    monkeypatch.setattr(registry.httpx, "get", fake_get)
    assert lookup_registration("MMC-123", "Maharashtra Medical Council", "Demo Doctor").status == "matched"


def test_booking_payment_review_and_permissions(setup):
    with TestClient(app) as doc, TestClient(app) as admin, TestClient(app) as pt, TestClient(app) as other:
        doctor_id = doctor(doc)
        login(admin, "admin@example.com")
        admin.post(f"/api/v1/admin/doctors/{doctor_id}/decision", json={"decision": "approved", "note": "Official register checked"})
        start = utcnow() + timedelta(minutes=30)
        end = start + timedelta(minutes=30)
        created = doc.post("/api/v1/doctor/slots", json={"mode": "online", "start_at": start.isoformat(), "end_at": end.isoformat()})
        assert created.status_code == 201, created.text
        slot_id = created.json()["id"]
        patient(pt)
        booked = pt.post("/api/v1/appointments", json={"slot_id": slot_id})
        assert booked.status_code == 201, booked.text
        appointment_id = booked.json()["appointment"]["id"]
        assert pt.get(f"/api/v1/appointments/{appointment_id}/checkout").json()["order_id"] == "order_test_123"
        assert other.post("/api/v1/appointments", json={"slot_id": slot_id}).status_code == 401
        assert doc.post("/api/v1/appointments", json={"slot_id": slot_id}).status_code == 403
        assert pt.post("/api/v1/appointments", json={"slot_id": slot_id}).status_code == 409
        bad = pt.post(f"/api/v1/appointments/{appointment_id}/verify-payment", json={"razorpay_order_id": "order_test_123", "razorpay_payment_id": "pay_1", "razorpay_signature": "bad"})
        assert bad.status_code == 400
        assert pt.get(f"/api/v1/appointments/{appointment_id}/meeting").status_code == 403
        paid = pt.post(f"/api/v1/appointments/{appointment_id}/verify-payment", json={"razorpay_order_id": "order_test_123", "razorpay_payment_id": "pay_1", "razorpay_signature": "valid"})
        assert paid.status_code == 200, paid.text
        assert paid.json()["status"] == "confirmed"
        assert pt.post(f"/api/v1/appointments/{appointment_id}/review", json={"rating": 5, "comment": "Helpful"}).status_code == 403
        assert other.get(f"/api/v1/appointments/{appointment_id}/meeting").status_code == 401
        setup.update(Slot, {"_id": slot_id}, {"start_at": utcnow() - timedelta(minutes=10),
                                              "end_at": utcnow() + timedelta(minutes=20)})
        assert pt.get(f"/api/v1/appointments/{appointment_id}/meeting").status_code == 200
        assert doc.post(f"/api/v1/appointments/{appointment_id}/complete").status_code == 200
        assert pt.post(f"/api/v1/appointments/{appointment_id}/review", json={"rating": 5, "comment": "Helpful"}).status_code == 201
        assert pt.post(f"/api/v1/appointments/{appointment_id}/review", json={"rating": 5}).status_code == 409
        assert pt.get(f"/api/v1/doctors/{doctor_id}").json()["rating"] == 5
        notices = setup.find(Notification, {"appointment_id": appointment_id}, sort=[("_id", 1)])
        patient_id = pt.get("/api/v1/auth/me").json()["id"]
        assert [(item.user_id, item.kind) for item in notices] == [
            (patient_id, "booking_confirmed"), (doctor_id, "new_booking"), (patient_id, "visit_completed")]
        assert all(item["target"] == {"type": "appointment", "appointment_id": appointment_id}
                   for item in pt.get("/api/v1/notifications/inbox").json()["items"])


def test_expired_hold_returns_to_search(setup):
    with TestClient(app) as doc, TestClient(app) as admin, TestClient(app) as pt:
        doctor_id = doctor(doc)
        login(admin, "admin@example.com")
        admin.post(f"/api/v1/admin/doctors/{doctor_id}/decision", json={"decision": "approved", "note": "Official register checked"})
        start = utcnow() + timedelta(days=1)
        slot_id = doc.post("/api/v1/doctor/slots", json={"mode": "clinic", "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=30)).isoformat()}).json()["id"]
        patient(pt)
        appointment_id = pt.post("/api/v1/appointments", json={"slot_id": slot_id}).json()["appointment"]["id"]
        expired = utcnow() - timedelta(seconds=1)
        setup.update(Slot, {"_id": slot_id}, {"held_until": expired})
        setup.update(Appointment, {"_id": appointment_id}, {"hold_expires_at": expired})
        assert pt.get(f"/api/v1/doctors/{doctor_id}/slots").json()[0]["id"] == slot_id
        assert pt.get("/api/v1/appointments").json()[0]["status"] == "expired"
        assert pt.get(f"/api/v1/appointments/{appointment_id}/checkout").status_code == 409


def test_failed_payment_releases_slot(setup):
    with TestClient(app) as doc, TestClient(app) as admin, TestClient(app) as pt:
        doctor_id = doctor(doc)
        login(admin, "admin@example.com")
        admin.post(f"/api/v1/admin/doctors/{doctor_id}/decision", json={"decision": "approved", "note": "Official register checked"})
        start = utcnow() + timedelta(days=1)
        slot_id = doc.post("/api/v1/doctor/slots", json={"mode": "online", "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=30)).isoformat()}).json()["id"]
        patient(pt)
        appointment_id = pt.post("/api/v1/appointments", json={"slot_id": slot_id}).json()["appointment"]["id"]
        failed = pt.post(f"/api/v1/appointments/{appointment_id}/payment-failed")
        assert failed.status_code == 200, failed.text
        assert failed.json()["status"] == "payment_failed"
        assert pt.get(f"/api/v1/doctors/{doctor_id}/slots").json()[0]["id"] == slot_id
        assert pt.get(f"/api/v1/appointments/{appointment_id}/checkout").status_code == 409


def test_mongo_indexes_and_atomic_slot_claim(setup):
    setup.insert(PaymentAttempt(appointment_id=1, razorpay_order_id="order_a"))
    setup.insert(PaymentAttempt(appointment_id=2, razorpay_order_id="order_b"))
    with pytest.raises(DuplicateKeyError):
        setup.insert(PaymentAttempt(appointment_id=3, razorpay_order_id="order_a"))

    start = utcnow() + timedelta(days=1)
    slot = setup.insert(Slot(doctor_id=99, mode="clinic", start_at=start, end_at=start + timedelta(minutes=30)))

    def claim(appointment_id):
        return setup.update(Slot, {"_id": slot.id, "status": "available"},
                            {"status": "held", "appointment_id": appointment_id,
                             "held_until": utcnow() + timedelta(minutes=15)})

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, [101, 102]))
    assert sum(item is not None for item in outcomes) == 1


def test_slot_with_cancelled_appointment_history_cannot_be_deleted(setup):
    with TestClient(app) as doc, TestClient(app) as admin, TestClient(app) as pt:
        doctor_id = doctor(doc)
        login(admin, "admin@example.com")
        admin.post(f"/api/v1/admin/doctors/{doctor_id}/decision", json={"decision": "approved", "note": "Demo application checked"})
        start = utcnow() + timedelta(days=1)
        response = doc.post("/api/v1/doctor/slots", json={"mode": "clinic", "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=30)).isoformat()})
        slot_id = response.json()["id"]
        patient(pt)
        booking = pt.post("/api/v1/appointments", json={"slot_id": slot_id})
        assert booking.status_code == 201
        assert pt.post(f"/api/v1/appointments/{booking.json()['appointment']['id']}/cancel").status_code == 200
        assert doc.delete(f"/api/v1/doctor/slots/{slot_id}").status_code == 409
        assert setup.get(Slot, slot_id) is not None
        assert pt.get("/api/v1/appointments").status_code == 200


def test_failed_order_creation_releases_mongo_hold(setup, monkeypatch):
    from fastapi import HTTPException

    with TestClient(app) as doc, TestClient(app) as admin, TestClient(app) as pt:
        doctor_id = doctor(doc)
        login(admin, "admin@example.com")
        admin.post(f"/api/v1/admin/doctors/{doctor_id}/decision", json={"decision": "approved", "note": "Official register checked"})
        start = utcnow() + timedelta(days=1)
        slot_id = doc.post("/api/v1/doctor/slots", json={"mode": "clinic", "start_at": start.isoformat(),
                                                        "end_at": (start + timedelta(minutes=30)).isoformat()}).json()["id"]
        patient(pt)
        monkeypatch.setattr(payments, "create_order", lambda *args: (_ for _ in ()).throw(HTTPException(502, "Payment unavailable")))
        assert pt.post("/api/v1/appointments", json={"slot_id": slot_id}).status_code == 502
        assert setup.get(Slot, slot_id).status == "available"
        assert setup.get(Slot, slot_id).appointment_id is None


def test_cancellation_allows_rebooking_without_reusing_old_payment(setup, monkeypatch):
    monkeypatch.setattr(payments, "create_order", lambda amount, receipt: {"id": f"order_{receipt}"})
    with TestClient(app) as doc, TestClient(app) as admin, TestClient(app) as pt:
        doctor_id = doctor(doc)
        login(admin, "admin@example.com")
        admin.post(f"/api/v1/admin/doctors/{doctor_id}/decision", json={"decision": "approved", "note": "Official register checked"})
        start = utcnow() + timedelta(days=1)
        slot_id = doc.post("/api/v1/doctor/slots", json={"mode": "clinic", "start_at": start.isoformat(),
                                                        "end_at": (start + timedelta(minutes=30)).isoformat()}).json()["id"]
        patient(pt)
        first = pt.post("/api/v1/appointments", json={"slot_id": slot_id})
        assert first.status_code == 201, first.text
        first_id = first.json()["appointment"]["id"]
        assert pt.post(f"/api/v1/appointments/{first_id}/cancel").status_code == 200
        second = pt.post("/api/v1/appointments", json={"slot_id": slot_id})
        assert second.status_code == 201, second.text
        assert second.json()["appointment"]["id"] != first_id
        assert setup.get(Appointment, first_id).status == "cancelled"
        assert setup.get(Slot, slot_id).appointment_id == second.json()["appointment"]["id"]


def verify_guest(client: TestClient, phone: str):
    send = client.post("/api/v1/guest/otp/send", json={"phone": phone})
    assert send.status_code == 200, send.text
    payload = {"phone": phone, "code": "123456", "challenge": send.json()["challenge"]}
    verified = client.post("/api/v1/guest/otp/verify", json=payload)
    assert verified.status_code == 200, verified.text
    assert "HttpOnly" in verified.headers["set-cookie"]
    assert client.post("/api/v1/guest/otp/verify", json=payload).status_code == 400


def available_guest_slot(db, doctor_id, *, starts_in):
    start = utcnow() + starts_in
    return db.insert(Slot(doctor_id=doctor_id, mode="online", start_at=start, end_at=start + timedelta(minutes=30)))


def signed_capture(client, monkeypatch, order_id, payment_id="pay_capture", *, amount=50000):
    secret = "isolated webhook test secret"
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", secret)
    body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": order_id, "amount": amount, "currency": "INR", "status": "captured"}}}}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    url = "/api/v1/payments/razorpay/webhook"
    assert client.post(url, content=body, headers={"X-Razorpay-Signature": "wrong"}).status_code == 400
    return client.post(url, content=body, headers={"X-Razorpay-Signature": signature})


def test_guest_checks_out_recovers_and_cancels_with_a_partial_refund(setup, monkeypatch):
    monkeypatch.setattr(auth, "lookup_registration", lambda *args: RegistryResult("matched"))
    with TestClient(app) as doc, TestClient(app) as guest, TestClient(app) as recovery, TestClient(app) as other:
        doctor_id = doctor(doc)
        slot = available_guest_slot(setup, doctor_id, starts_in=timedelta(minutes=10))
        future = available_guest_slot(setup, doctor_id, starts_in=timedelta(hours=25))
        base = "/api/v1/guest"
        assert [item["id"] for item in guest.get(f"{base}/availability").json()] == [slot.id]
        booking = {"slot_id": slot.id, "full_name": "Guest Patient"}
        assert guest.post(f"{base}/appointments", json=booking).status_code == 401
        verify_guest(guest, "9876543210")
        assert guest.get("/api/v1/auth/me").status_code == 401
        assert guest.post(f"{base}/appointments", json={**booking, "slot_id": future.id}).status_code == 409
        stale_price = guest.post(f"{base}/appointments", json={**booking, "expected_amount_paise": 45000})
        assert stale_price.status_code == 409
        assert setup.get(Slot, slot.id).status == "available"
        response = guest.post(f"{base}/appointments", json=booking)
        assert response.status_code == 201, response.text
        item = response.json()["appointment"]
        path = f"{base}/appointments/{item['id']}"
        assert item["booking_kind"] == "urgent" and item["patient_id"] is None
        assert item["hold_expires_at"] == item["start_at"]
        verify_guest(other, "9876543211")
        assert other.post(f"{base}/appointments", json=booking).status_code == 409
        assert other.get(f"{path}/checkout").status_code == 404
        assert other.post(f"{path}/cancel", json={"paid_paise": 0, "refund_paise": 0}).status_code == 404
        assert guest.get(f"{path}/checkout").json() == response.json()["checkout"]
        assert guest.post(f"{base}/sign-out").status_code == 200
        assert guest.get(f"{base}/appointments").status_code == 401
        verify_guest(recovery, "+91 98765 43210")
        assert recovery.get(f"{base}/appointments").json()[0]["id"] == item["id"]

        assert signed_capture(recovery, monkeypatch, "order_test_123").status_code == 200
        assert signed_capture(recovery, monkeypatch, "order_test_123").status_code == 200
        assert doc.get("/api/v1/appointments").json()[0]["patient_name"] == "Guest Patient"
        assert recovery.get(f"{path}/meeting").status_code == 200
        stale = recovery.post(f"{path}/cancel", json={"paid_paise": 0, "refund_paise": 0})
        assert stale.status_code == 409
        quote = recovery.get(f"{path}/cancellation-quote").json()
        assert stale.json()["detail"]["quote"] == quote
        assert quote == {"paid_paise": 50000, "fee_percent": 50, "fee_paise": 25000, "refund_paise": 25000}
        cancelled = recovery.post(f"{path}/cancel", json=quote)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["refund"]["amount_paise"] == 25000
        assert recovery.post(f"{path}/cancel", json=quote).json()["status"] == "cancelled"
        assert setup.database.refunds.count_documents({}) == 1
        assert setup.get(Slot, slot.id).status == "available"
        assert recovery.get(f"{path}/meeting").status_code == 403
        assert setup.database.notifications.count_documents({"user_id": doctor_id, "body": {"$regex": "cancelled"}}) == 1
        notices = setup.find(Notification, {"appointment_id": item["id"]}, sort=[("_id", 1)])
        assert [(notice.user_id, notice.kind) for notice in notices] == [
            (doctor_id, "new_booking"), (doctor_id, "doctor_booking_cancelled")]


def test_guest_otp_expires_and_throttles(setup, monkeypatch):
    sent_codes = []
    monkeypatch.setattr(otp, "deliver_code", lambda *args: sent_codes.append(args))
    with TestClient(app) as guest:
        data = {"phone": "9876543212"}
        url = "/api/v1/guest/otp"
        challenge = guest.post(f"{url}/send", json=data).json()["challenge"]
        assert sent_codes == [("phone", "+919876543212", "123456")]
        assert guest.post(f"{url}/send", json=data).status_code == 429
        for expected in [400, 400, 400, 400, 429]:
            assert guest.post(f"{url}/verify", json={**data, "challenge": challenge, "code": "000000"}).status_code == expected
        assert guest.post(f"{url}/verify", json={**data, "challenge": challenge, "code": "123456"}).status_code == 400
        setup.update(GuestChallenge, {}, {"sent_at": utcnow() - timedelta(minutes=2)})
        new = guest.post(f"{url}/send", json=data).json()["challenge"]
        setup.update(GuestChallenge, {}, {"expires_at": utcnow() - timedelta(seconds=1)})
        assert guest.post(f"{url}/verify", json={**data, "challenge": new, "code": "123456"}).status_code == 400


def test_cancellation_time_boundaries():
    start = utcnow()
    item = Appointment(slot_id=1, doctor_id=1, patient_id=2, mode="clinic", amount_paise=10003,
                       hold_expires_at=start, status="confirmed")
    slot = Slot(doctor_id=1, mode="clinic", start_at=start, end_at=start + timedelta(minutes=30))
    for notice, deduction in [(timedelta(hours=24), 0), (timedelta(hours=24, microseconds=-1), 25),
                              (timedelta(hours=2), 25), (timedelta(hours=2, microseconds=-1), 50)]:
        quote = appointments.quote_cancellation(item, slot, "patient", start - notice)
        assert quote["fee_percent"] == deduction
        assert quote["refund_paise"] == 10003 - (10003 * deduction // 100)
    assert appointments.quote_cancellation(item, slot, "doctor", start - timedelta(seconds=1))["refund_paise"] == 10003
    with pytest.raises(HTTPException, match="can no longer be cancelled"):
        appointments.quote_cancellation(item, slot, "patient", start)


def test_late_payment_after_unpaid_cancellation_is_refunded_once(setup, monkeypatch):
    monkeypatch.setattr(auth, "lookup_registration", lambda *args: RegistryResult("matched"))
    with TestClient(app) as doc, TestClient(app) as pt:
        doctor_id = doctor(doc)
        slot = available_guest_slot(setup, doctor_id, starts_in=timedelta(minutes=30))
        patient(pt)
        reservation = pt.post("/api/v1/appointments", json={"slot_id": slot.id}).json()
        path = f"/api/v1/appointments/{reservation['appointment']['id']}"
        assert pt.post(f"{path}/cancel", json={"paid_paise": 0, "refund_paise": 0}).json()["refund"] is None
        for _ in range(2):
            assert signed_capture(pt, monkeypatch, reservation["checkout"]["order_id"], "pay_late").status_code == 200
        assert setup.get(Refund, "pay_late").amount_paise == 50000
        assert setup.database.refunds.count_documents({}) == 1
        assert setup.get(Slot, slot.id).status == "available"
        assert pt.get("/api/v1/appointments").json()[0]["status"] == "cancelled"
        notifications = setup.find(Notification, {"body": {"$regex": "payment arrived"}})
        assert len(notifications) == 1


def test_refund_worker_retries_with_same_key_polls_and_surfaces_rejections(setup, monkeypatch):
    setup.insert(Refund(payment_id="pay_partial", appointment_id=1, amount_paise=25000))
    requests = []

    def create(payment_id, amount_paise, receipt):
        requests.append((payment_id, amount_paise, receipt))
        if len(requests) == 1:
            raise httpx.ReadTimeout("response lost")
        return {"id": "rfnd_demo", "payment_id": payment_id, "amount": amount_paise, "currency": "INR", "status": "pending"}

    monkeypatch.setattr(payments, "create_refund", create)
    refunds.process_refunds(setup)
    assert setup.get(Refund, "pay_partial").status == "queued"
    setup.update(Refund, {"_id": "pay_partial"}, {"next_attempt_at": utcnow() - timedelta(seconds=1)})
    refunds.process_refunds(setup)
    assert requests[0] == requests[1]
    assert setup.get(Refund, "pay_partial").status == "processing"

    def fetch(payment_id, refund_id):
        return {"id": refund_id, "payment_id": payment_id, "amount": 25000, "currency": "INR", "status": "failed"}

    setup.update(Refund, {"_id": "pay_partial"}, {"next_attempt_at": utcnow() - timedelta(seconds=1)})
    monkeypatch.setattr(payments, "fetch_refund", fetch)
    refunds.process_refunds(setup)
    assert setup.get(Refund, "pay_partial").status == "needs_review"
    with TestClient(app) as admin:
        assert admin.get("/api/v1/admin/refunds").status_code == 401
        login(admin, "admin@example.com")
        assert admin.get("/api/v1/admin/refunds").json()[0]["reference"] == "rfnd_demo"


@pytest.mark.parametrize("closed_before_capture", [False, True])
def test_reconciles_delayed_capture_without_browser_or_webhook(setup, monkeypatch, closed_before_capture):
    monkeypatch.setattr(auth, "lookup_registration", lambda *args: RegistryResult("matched"))
    with TestClient(app) as doc, TestClient(app) as guest:
        slot = available_guest_slot(setup, doctor(doc), starts_in=timedelta(hours=3))
        verify_guest(guest, "9876543210")
        base = "/api/v1/guest/appointments"
        booking = guest.post(base, json={"slot_id": slot.id, "full_name": "Fictional Patient"}).json()
        item_id = booking["appointment"]["id"]
        order = booking["checkout"]["order_id"]
        pay = {"id": "pay_delayed", "order_id": order, "amount": 50000, "currency": "INR", "status": "authorized"}
        monkeypatch.setattr(payments, "fetch_payment", lambda _id: pay)
        checkout = {"razorpay_order_id": order, "razorpay_payment_id": pay["id"], "razorpay_signature": "valid"}
        response = guest.post(f"{base}/{item_id}/verify-payment", json=checkout)
        assert response.status_code == 200
        assert response.json()["payment_processing"] is True
        assert response.json()["status"] == "pending_payment"
        assert guest.get(f"{base}/{item_id}/checkout").status_code == 409
        if closed_before_capture:
            cancelled = guest.post(f"{base}/{item_id}/cancel", json={"paid_paise": 0, "refund_paise": 0})
            assert cancelled.json()["cancellation_fee_paise"] == 0
        guest.post("/api/v1/guest/sign-out")
        pay["status"] = "captured"
        monkeypatch.setattr(payments, "fetch_order_payments", lambda _order: [pay])
        setup.update(PaymentAttempt, {"appointment_id": item_id}, {"next_check_at": utcnow() - timedelta(seconds=1)})
        appointments.reconcile_due_orders(setup)
        appointments.reconcile_due_orders(setup)
        if closed_before_capture:
            assert setup.get(Appointment, item_id).status == "cancelled"
            assert setup.get(Slot, slot.id).status == "available"
            assert setup.get(Refund, pay["id"]).amount_paise == 50000
            assert setup.database.refunds.count_documents({}) == 1
        else:
            assert setup.get(Appointment, item_id).status == "confirmed"
            assert setup.get(Slot, slot.id).status == "booked"
            assert setup.get(Refund, pay["id"]) is None


@pytest.fixture
def prescription_booking(setup, monkeypatch):
    monkeypatch.setenv("PRESCRIPTION_AI_PROVIDER", "vertex")
    monkeypatch.setattr(auth, "lookup_registration", lambda *args: RegistryResult("matched"))
    monkeypatch.setenv("PRESCRIPTIONS_ENABLED", "1")
    monkeypatch.setenv("PRESCRIPTION_GOOGLE_PROJECT", "test-prescriptions")
    monkeypatch.setenv("PRESCRIPTION_RETENTION_DAYS", "30")
    monkeypatch.setenv("PRESCRIPTION_AUDIT_RETENTION_DAYS", "10")
    with TestClient(app) as doc, TestClient(app) as pt, TestClient(app) as other:
        doctor_id = doctor(doc)
        monkeypatch.setenv("PRESCRIPTION_DOCTOR_IDS", str(doctor_id))
        patient(pt)
        login(other, "admin@example.com")
        slot = available_guest_slot(setup, doctor_id, starts_in=timedelta(hours=3))
        booked = pt.post("/api/v1/appointments", json={"slot_id": slot.id})
        assert booked.status_code == 201, booked.text
        appointment_id = booked.json()["appointment"]["id"]
        yield doc, pt, other, f"/api/v1/appointments/{appointment_id}", appointment_id


@pytest.mark.skipif(os.getenv("RUN_GEMINI_LIVE_TESTS") != "1", reason="Opt-in Gemini test with synthetic photos")
def test_live_gemini_prescription_through_upload_payment_and_worker(setup, prescription_booking, monkeypatch):
    doc, pt, other, base, appointment_id = prescription_booking
    monkeypatch.setenv("PRESCRIPTION_AI_PROVIDER", "gemini")
    monkeypatch.delenv("PRESCRIPTION_GOOGLE_PROJECT", raising=False)
    assert os.getenv("GEMINI_API_KEY"), "Configure a Gemini key for the live test"
    image = Image.new("RGB", (1200, 600), "white")
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 32)
    except OSError:
        font = ImageFont.load_default(size=32)
    ImageDraw.Draw(image).multiline_text((40, 40),
        "FICTIONAL PRESCRIPTION FOR SOFTWARE TESTING\nDate: 19 November 2026\n"
        "DemoTablet 10 mg - one tablet daily for 3 days\nFollow-up: after 7 days",
        fill="black", font=font, spacing=20)
    content = BytesIO()
    image.save(content, format="JPEG")
    endpoint = base + "/prescription"
    uploaded = pt.put(endpoint, files=[("photos", ("synthetic.jpg", content.getvalue(), "image/jpeg"))],
                      data={"consent_version": "v1"})
    assert uploaded.status_code == 200, uploaded.text
    assert doc.get(endpoint).status_code == other.get(endpoint).status_code == 404
    confirmed = pt.post(base + "/verify-payment", json={
        "razorpay_order_id": "order_test_123", "razorpay_payment_id": "pay_live_gemini",
        "razorpay_signature": "valid",
    })
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    prescriptions.process_prescriptions(setup)
    result = pt.get(endpoint).json()
    assert result["status"] == "ready", result
    assert any("DemoTablet" in entry["text"] for entry in result["entries"]), result
    assert any("10 mg" in entry["text"] for entry in result["entries"]), result
    assert all(entry["page"] == 1 for entry in result["entries"])
    assert doc.get(endpoint).json() == result
    assert doc.get(f"{endpoint}/photos/{result['version']}/1").status_code == 200
    assert other.get(endpoint).status_code == 404
    assert setup.database.prescriptions.find_one({"_id": appointment_id})["attempts"] == 1


def prescription_photo():
    content = BytesIO()
    metadata = PngInfo()
    metadata.add_text("comment", "sample private metadata")
    Image.new("RGB", (130, 160), "white").save(content, format="PNG", pnginfo=metadata)
    return ("photos", ("sample.png", content.getvalue(), "image/png"))


@pytest.mark.parametrize("confirmation", ["checkout", "webhook"])
def test_prescription_is_shared_only_after_confirmation(setup, prescription_booking, monkeypatch, confirmation):
    doc, pt, other, base, appointment_id = prescription_booking
    endpoint = base + "/prescription"
    file = prescription_photo()
    consent = {"consent_version": "v1"}
    assert pt.put(endpoint, files=[file]).status_code == 422
    invalid = ("photos", ("invalid.png", b"invalid image", "image/png"))
    assert pt.put(endpoint, files=[invalid], data=consent).status_code == 422
    assert pt.put(endpoint, files=[file] * 4, data=consent).status_code == 422
    oversized = ("photos", ("large.png", b"0" * (prescription_routes.MAX_PHOTO_BYTES + 1), "image/png"))
    assert pt.put(endpoint, files=[oversized], data=consent).status_code == 413
    uploaded = pt.put(endpoint, files=[file], data=consent)
    assert uploaded.status_code == 200, uploaded.text
    old_page = f"{endpoint}/photos/{uploaded.json()['version']}/1"
    replacement = pt.put(endpoint, files=[file, file], data=consent).json()
    assert replacement["photo_count"] == 2
    assert pt.get(old_page).status_code == 404
    image_url = f"{endpoint}/photos/{replacement['version']}/1"
    assert doc.get(endpoint).status_code == doc.get(image_url).status_code == 404
    assert other.get(endpoint).status_code == other.get(image_url).status_code == 404
    assert pt.get("/api/v1/appointments").json()[0]["prescription_status"] == "draft"
    assert doc.get("/api/v1/appointments").json()[0]["prescription_status"] is None
    assert pt.get(base + "/checkout").status_code == 200
    monkeypatch.setattr(prescriptions, "extract_prescription", lambda *args: [
        {"kind": "note", "text": "Fictional prescription excerpt", "page": 2},
    ])
    prescriptions.process_prescriptions(setup)
    assert pt.get(endpoint).json()["status"] == "draft"

    payment = {"id": "pay_prescription", "order_id": "order_test_123", "amount": 50000, "currency": "INR"}
    checkout = {"razorpay_order_id": payment["order_id"], "razorpay_payment_id": payment["id"],
                "razorpay_signature": "valid"}
    if confirmation == "checkout":
        monkeypatch.setattr(payments, "fetch_payment", lambda _id: {**payment, "status": "authorized"})
        assert pt.post(base + "/verify-payment", json=checkout).json()["status"] == "pending_payment"
        assert doc.get(endpoint).status_code == 404
        monkeypatch.setattr(payments, "fetch_payment", lambda _id: {**payment, "status": "captured"})
        assert pt.post(base + "/verify-payment", json=checkout).json()["status"] == "confirmed"
        assert pt.post(base + "/verify-payment", json=checkout).json()["status"] == "confirmed"
    else:
        monkeypatch.setattr(payments, "verify_webhook", lambda *args: True)
        event = {"event": "payment.captured", "payload": {"payment": {"entity": {**payment, "status": "captured"}}}}
        for _ in range(2):
            received = pt.post("/api/v1/payments/razorpay/webhook", json=event)
            assert received.status_code == 200, received.text

    assert doc.get(endpoint).json()["status"] == "queued"
    response = doc.get(image_url)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "image/jpeg"
    assert b"sample private metadata" not in response.content
    assert doc.get(image_url[:-1] + "3").status_code == 404
    assert other.get(image_url).status_code == 404
    assert pt.put(endpoint, files=[file], data=consent).status_code == 409
    assert pt.delete(endpoint).status_code == 409
    prescriptions.process_prescriptions(setup)
    assert doc.get(endpoint).json() == pt.get(endpoint).json()
    assert doc.get(endpoint).json()["entries"][0]["page"] == 2
    assert setup.database.prescriptionaudit.count_documents({"appointment_id": appointment_id, "action": "shared"}) == 1
    assert setup.database.prescriptions.find_one({"_id": appointment_id})["attempts"] == 1
    notices = doc.get("/api/v1/notifications").json()
    assert sum("attached prescription" in notice["body"] for notice in notices) == 1
    assert all("Fictional prescription excerpt" not in notice["body"] for notice in notices)


def test_prescription_removal_and_confirmation_race(setup, prescription_booking, monkeypatch):
    doc, pt, _, base, appointment_id = prescription_booking
    endpoint = base + "/prescription"
    assert pt.put(endpoint, files=[prescription_photo()], data={"consent_version": "v1"}).status_code == 200
    assert pt.delete(endpoint).status_code == 204
    assert pt.get(endpoint).status_code == 404
    normalize = prescription_routes.normalize_photo

    def confirm_during_upload(file):
        current, late = appointments.reconcile_capture(setup, "order_test_123", "pay_race", 50000, "INR")
        assert current.status == "confirmed" and not late
        return normalize(file)

    monkeypatch.setattr(prescription_routes, "normalize_photo", confirm_during_upload)
    response = pt.put(endpoint, files=[prescription_photo()], data={"consent_version": "v1"})
    assert response.status_code == 409, response.text
    assert doc.get(endpoint).status_code == 404
    assert setup.database.prescriptions.find_one({"_id": appointment_id}) is None


def test_prescription_worker_retry_and_cancellation(setup, prescription_booking, monkeypatch):
    doc, pt, _, base, appointment_id = prescription_booking
    endpoint = base + "/prescription"
    assert pt.put(endpoint, files=[prescription_photo()], data={"consent_version": "v1"}).status_code == 200
    appointments.reconcile_capture(setup, "order_test_123", "pay_worker", 50000, "INR")

    def unavailable(*args):
        raise prescriptions.ExtractionError("provider connection", retryable=True)

    monkeypatch.setattr(prescriptions, "extract_prescription", unavailable)
    for expected in (1, 2, 3):
        setup.database.prescriptions.update_one({"_id": appointment_id}, {"$set": {"next_attempt_at": utcnow()}})
        prescriptions.process_prescriptions(setup)
        row = setup.database.prescriptions.find_one({"_id": appointment_id})
        assert row["attempts"] == expected
    result = doc.get(endpoint).json()
    assert result["status"] == "unavailable"
    assert doc.get(f"{endpoint}/photos/{result['version']}/1").status_code == 200

    def cancel_while_extracting(*args):
        cancelled = doc.post(base + "/cancel", json={"paid_paise": 50000, "refund_paise": 50000})
        assert cancelled.status_code == 200, cancelled.text
        return [{"kind": "note", "text": "Discard this response", "page": 1}]

    setup.database.prescriptions.update_one({"_id": appointment_id},
                                            {"$set": {"attempts": 0, "status": "queued", "next_attempt_at": utcnow()}})
    monkeypatch.setattr(prescriptions, "extract_prescription", cancel_while_extracting)
    prescriptions.process_prescriptions(setup)
    assert setup.database.prescriptions.find_one({"_id": appointment_id}) is None
    assert doc.get(endpoint).status_code == pt.get(endpoint).status_code == 404
    assert setup.database.prescriptionaudit.count_documents({"appointment_id": appointment_id, "action": "cancelled"}) == 1


def test_expired_prescription_draft_loses_access_and_is_purged(setup, prescription_booking, monkeypatch):
    doc, pt, _, base, appointment_id = prescription_booking
    endpoint = base + "/prescription"
    monkeypatch.setenv("PRESCRIPTIONS_ENABLED", "0")
    assert pt.put(endpoint, files=[prescription_photo()], data={"consent_version": "v1"}).status_code == 404
    monkeypatch.setenv("PRESCRIPTIONS_ENABLED", "1")
    assert pt.put(endpoint, files=[prescription_photo()], data={"consent_version": "v1"}).status_code == 200
    past = utcnow() - timedelta(minutes=1)
    setup.update(Appointment, {"_id": appointment_id}, {"hold_expires_at": past})
    setup.database.prescriptions.update_one({"_id": appointment_id}, {"$set": {"expires_at": past}})
    assert pt.get(endpoint).status_code == doc.get(endpoint).status_code == 404
    prescriptions.process_prescriptions(setup)
    assert setup.database.prescriptions.find_one({"_id": appointment_id}) is None
    assert setup.database.prescriptionaudit.count_documents({"appointment_id": appointment_id, "action": "purged"}) == 1


def test_prescription_completed_visit_revocation_and_retention(setup, prescription_booking, monkeypatch):
    doc, pt, _, base, appointment_id = prescription_booking
    endpoint = base + "/prescription"
    uploaded = pt.put(endpoint, files=[prescription_photo()], data={"consent_version": "v1"})
    assert uploaded.status_code == 200
    image_url = f"{endpoint}/photos/{uploaded.json()['version']}/1"
    appointments.reconcile_capture(setup, "order_test_123", "pay_retention", 50000, "INR")
    item = setup.get(Appointment, appointment_id)
    setup.update(Slot, {"_id": item.slot_id}, {"start_at": utcnow() - timedelta(minutes=5)})
    assert doc.post(base + "/complete").status_code == 200
    assert doc.get(image_url).status_code == pt.get(image_url).status_code == 200
    monkeypatch.setenv("PRESCRIPTION_DOCTOR_IDS", "999999")
    assert doc.get(endpoint).status_code == doc.get(image_url).status_code == 404
    assert pt.get(endpoint).status_code == 200
    monkeypatch.setenv("PRESCRIPTIONS_ENABLED", "0")
    setup.database.prescriptions.update_one({"_id": appointment_id},
                                           {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}})
    assert pt.get(endpoint).status_code == 404
    prescriptions.process_prescriptions(setup)
    assert setup.database.prescriptions.find_one({"_id": appointment_id}) is None


def test_worker_lease_survives_restart_and_prevents_duplicate_extraction(setup, prescription_booking, monkeypatch):
    _, pt, _, base, appointment_id = prescription_booking
    assert pt.put(base + "/prescription", files=[prescription_photo()], data={"consent_version": "v1"}).status_code == 200
    appointments.reconcile_capture(setup, "order_test_123", "pay_lease", 50000, "INR")
    calls = []

    def extract(*args):
        calls.append(True)
        # A concurrent worker must skip this worker's current lease.
        prescriptions.process_prescriptions(setup)
        return [{"kind": "unreadable", "text": "Some prescription text could not be read.", "page": 1}]

    monkeypatch.setattr(prescriptions, "extract_prescription", extract)
    setup.database.prescriptions.update_one({"_id": appointment_id}, {"$set": {
        "status": "processing", "attempts": 1, "lease_token": "a" * 32,
        "lease_until": utcnow() + timedelta(minutes=1),
    }})
    prescriptions.process_prescriptions(setup)
    assert not calls
    setup.database.prescriptions.update_one({"_id": appointment_id},
                                           {"$set": {"lease_until": utcnow() - timedelta(seconds=1)}})
    prescriptions.process_prescriptions(setup)
    prescriptions.process_prescriptions(setup)
    assert len(calls) == 1
    row = setup.database.prescriptions.find_one({"_id": appointment_id})
    assert row["attempts"] == 2 and row["status"] == "unreadable"
    assert row["lease_token"] is None


def test_upload_ownership_origin_and_partial_failure_preserve_saved_photos(setup, prescription_booking, monkeypatch):
    doc, pt, other, base, appointment_id = prescription_booking
    endpoint = base + "/prescription"
    files = [prescription_photo()]
    consent = {"consent_version": "v1"}
    uploaded = pt.put(endpoint, files=files, data=consent).json()
    original = uploaded["version"]
    assert doc.put(endpoint, files=files, data=consent).status_code == 403
    assert other.delete(endpoint).status_code == 404
    assert pt.put(endpoint, files=files, data=consent, headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    invalid = ("photos", ("corrupted.png", b"corrupt data", "image/png"))
    assert pt.put(endpoint, files=[prescription_photo(), invalid], data=consent).status_code == 422
    assert pt.get(endpoint).json()["version"] == original
    assert pt.put(endpoint, content=b"garbage", headers={"Content-Type": "multipart/form-data"}).status_code == 422
    assert pt.put(endpoint, content=b"garbage", headers={"Content-Type": "application/json"}).status_code == 415
    # Another signed-in patient must not be able to discover or change the attachment.
    user = setup.insert(User(email="unrelated@example.com", full_name="Unrelated Patient", phone="9999999997",
                             password_hash=hash_password("testpass123"), role="patient",
                             email_verified_at=utcnow(), phone_verified_at=utcnow()))
    login(other, user.email)
    assert other.get(endpoint).status_code == 404
    assert other.put(endpoint, files=files, data=consent).status_code == 404
    assert other.delete(endpoint).status_code == 404
    pt.post("/api/v1/auth/logout")
    # Clearing the cookie exercises a stale photo link after signing out.
    pt.cookies.clear()
    assert pt.get(f"{endpoint}/photos/{original}/1").status_code == 401


def test_admin_routes_share_login_and_forbid_other_roles(setup):
    paths = ("overview", "accounts", "appointments", "slots", "transactions", "pending-doctors", "refunds")
    with TestClient(app) as client:
        for path in paths:
            assert client.get(f"/api/v1/admin/{path}").status_code == 401
        patient(client)
        for path in paths:
            assert client.get(f"/api/v1/admin/{path}").status_code == 403
        assert client.put("/api/v1/admin/accounts/1/status", json={"suspended": True, "reason": "Test reason"}).status_code == 403
        login(client, "admin@example.com")
        for path in paths:
            response = client.get(f"/api/v1/admin/{path}")
            assert response.status_code == 200, response.text
            assert response.headers["Cache-Control"] == "no-store"
        assert client.get("/api/v1/admin/overview?period=custom&start=2026-04-01&end=2026-11-01").status_code == 422
        row = client.get("/api/v1/admin/accounts?q=Demo").json()["items"][0]
        assert row["role"] == "patient" and "password_hash" not in row and "phone" not in row


@pytest.mark.parametrize("role", ["patient", "doctor"])
def test_admin_can_grant_access_preserving_account_history(setup, role):
    with TestClient(app) as owner, TestClient(app) as target:
        if role == "patient":
            patient(target)
            email = "patient@example.com"
        else:
            doctor(target)
            email = "doctor@example.com"
        user = setup.first(User, {"email": email})
        original_hash = user.password_hash
        path = f"/api/v1/admin/accounts/{user.id}/make-admin"
        payload = {"reason": "Trusted colleague appointed administrator"}
        assert target.post(path, json=payload).status_code == 403
        assert owner.post(path, json=payload).status_code == 401
        login(owner, "admin@example.com")
        assert owner.post(path, json={"reason": ""}).status_code == 422
        assert owner.post(path, json=payload).json()["role"] == "admin"
        assert owner.post(path, json=payload).status_code == 200
        changed = setup.get(User, user.id)
        assert changed.role == role and changed.admin_access
        assert changed.password_hash == original_hash
        assert changed.session_generation == user.session_generation + 1
        if role == "doctor":
            assert setup.get(DoctorProfile, user.id)
        assert setup.database.adminactions.count_documents({"action": "grant_admin", "target_id": user.id}) == 1
        assert target.get("/api/v1/auth/me").status_code == 401
        logged_in = target.post("/api/v1/auth/login", json={"email": email, "password": "testpass123"})
        assert logged_in.json()["role"] == "admin"
        assert logged_in.json()["verification_required"] is False
        assert target.get("/api/v1/admin/accounts").status_code == 200
        admins = owner.get("/api/v1/admin/accounts", params={"role": "admin", "q": "Demo"}).json()["items"]
        assert any(row["id"] == user.id and row["role"] == "admin" for row in admins)
        original_role_rows = owner.get("/api/v1/admin/accounts", params={"role": role}).json()["items"]
        assert all(row["id"] != user.id for row in original_role_rows)
        assert owner.put(f"/api/v1/admin/accounts/{user.id}/status",
                         json={"suspended": True, "reason": "Cannot suspend an administrator"}).status_code == 404


def test_admin_grant_rejects_suspended_and_missing_accounts(setup):
    with TestClient(app) as admin:
        login(admin, "admin@example.com")
        user = setup.insert(User(email="suspended@example.com", full_name="Suspended Account",
                                 phone="0000000000", password_hash="unchanged", role="patient",
                                 suspended_at=utcnow()))
        payload = {"reason": "Attempted administrator appointment"}
        assert admin.post(f"/api/v1/admin/accounts/{user.id}/make-admin", json=payload).status_code == 409
        assert admin.post("/api/v1/admin/accounts/999999/make-admin", json=payload).status_code == 404
        assert not setup.get(User, user.id).admin_access


@pytest.mark.parametrize("promoted", [False, True])
def test_admin_personal_booking_prescription_payment_and_cancellation(setup, prescription_booking, monkeypatch, promoted):
    doc, pt, admin, unrelated_base, _ = prescription_booking
    provider = setup.first(User, {"email": "doctor@example.com"})
    if promoted:
        # Keep the unrelated patient separate from the administrator's personal bookings.
        user = setup.insert(User(email="personal-admin@example.com", full_name="Personal Admin",
                                 phone="0000000000", password_hash=hash_password("testpass123"),
                                 role="patient", admin_access=True))
        login(admin, user.email)
    else:
        user = setup.first(User, {"email": "admin@example.com"})
    slot = available_guest_slot(setup, provider.id, starts_in=timedelta(hours=4))
    monkeypatch.setattr(payments, "create_order", lambda amount, receipt: {"id": "order_admin_personal"})
    monkeypatch.setattr(payments, "fetch_payment", lambda payment: {
        "order_id": "order_admin_personal", "amount": 50000, "currency": "INR", "status": "captured"})
    booked = admin.post("/api/v1/appointments", json={"slot_id": slot.id})
    assert booked.status_code == 201, booked.text
    item_id = booked.json()["appointment"]["id"]
    base = f"/api/v1/appointments/{item_id}"
    assert admin.get(base + "/checkout").status_code == 200
    assert admin.get(unrelated_base + "/checkout").status_code == 404
    assert admin.get(unrelated_base + "/cancellation-quote").status_code == 404
    assert admin.get(unrelated_base + "/meeting").status_code == 404
    uploaded = admin.put(base + "/prescription", files=[prescription_photo()], data={"consent_version": "v1"})
    assert uploaded.status_code == 200, uploaded.text
    assert admin.get(base + "/prescription").json()["status"] == "draft"
    assert pt.get(base + "/prescription").status_code == 404
    payment = admin.post(base + "/verify-payment", json={
        "razorpay_order_id": "order_admin_personal", "razorpay_payment_id": "pay_admin_personal",
        "razorpay_signature": "valid",
    })
    assert payment.status_code == 200 and payment.json()["status"] == "confirmed"
    listing = admin.get("/api/v1/appointments").json()
    assert [item["id"] for item in listing] == [item_id]
    assert listing[0]["prescription_status"] == "queued"
    assert admin.get("/api/v1/admin/accounts").status_code == 200
    quote = admin.get(base + "/cancellation-quote").json()
    assert quote["fee_percent"] == 25 and quote["refund_paise"] == 37500
    cancelled = admin.post(base + "/cancel", json={k: quote[k] for k in ("paid_paise", "refund_paise")})
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled_by"] == "patient"
    assert setup.get(Appointment, item_id).patient_id == user.id
    assert setup.get(Refund, "pay_admin_personal").amount_paise == 37500
    assert admin.get(base + "/prescription").status_code == 404


def test_admin_can_join_own_visit_and_review_after_completion(setup, prescription_booking, monkeypatch):
    doc, _, admin, unrelated, _ = prescription_booking
    provider = setup.first(User, {"email": "doctor@example.com"})
    slot = available_guest_slot(setup, provider.id, starts_in=timedelta(hours=4))
    monkeypatch.setattr(payments, "create_order", lambda *args: {"id": "order_admin_review"})
    result = admin.post("/api/v1/appointments", json={"slot_id": slot.id})
    assert result.status_code == 201
    item_id = result.json()["appointment"]["id"]
    appointments.reconcile_capture(setup, "order_admin_review", "pay_admin_review", 50000, "INR")
    setup.update(Slot, {"_id": slot.id}, {"start_at": utcnow() - timedelta(minutes=5),
                                       "end_at": utcnow() + timedelta(minutes=25)})
    base = f"/api/v1/appointments/{item_id}"
    assert admin.get(base + "/meeting").status_code == 200
    assert admin.post(unrelated + "/review", json={"rating": 5}).status_code == 403
    assert admin.post(base + "/review", json={"rating": 5}).status_code == 403
    assert doc.post(base + "/complete").status_code == 200
    assert admin.post(base + "/review", json={"rating": 5, "comment": "Synthetic visit review"}).status_code == 201
    assert admin.post(base + "/review", json={"rating": 5}).status_code == 409
    assert admin.get("/api/v1/appointments").json()[0]["reviewed"] is True


def test_fictional_prescriptions_require_explicit_local_test_mode(setup, monkeypatch):
    from app.prescriptions import Settings, upload_consent
    with TestClient(app) as client:
        doctor_id = doctor(client)
    profile = setup.get(DoctorProfile, doctor_id)
    profile.verification_status = "published"
    profile.verification_method = "demo_seeded"
    config = Settings(project="", retention_days=7, audit_days=30, doctor_ids=frozenset({doctor_id}))
    monkeypatch.setenv("PRESCRIPTION_TEST_MODE", "1")
    monkeypatch.setenv("APP_ENV", "development")
    assert upload_consent(profile, config) == "v1"
    monkeypatch.setenv("APP_ENV", "production")
    assert upload_consent(profile, config) is None
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("PRESCRIPTION_TEST_MODE", "0")
    assert upload_consent(profile, config) is None


def test_admin_manages_slots_and_cancels_an_urgent_booking(setup):
    with TestClient(app) as doc, TestClient(app) as admin:
        doctor_id = doctor(doc)
        login(admin, "admin@example.com")
        base = "/api/v1/admin"
        assert admin.post(f"{base}/doctors/{doctor_id}/decision",
                          json={"decision": "approved", "note": "Reviewed registration"}).status_code == 200
        start = utcnow() + timedelta(hours=2)
        window = {"mode": "online", "start_at": start.isoformat(), "end_at": (start + timedelta(minutes=30)).isoformat()}
        created = admin.post(f"{base}/doctors/{doctor_id}/slots", json=window)
        assert created.status_code == 201, created.text
        assert admin.post(f"{base}/doctors/{doctor_id}/slots", json=window).status_code == 409
        assert admin.delete(f"{base}/slots/{created.json()['id']}").status_code == 200
        slot = setup.get(Slot, admin.post(f"{base}/doctors/{doctor_id}/slots", json=window).json()["id"])
        guest = setup.insert(GuestPatient(phone="9999999990"))
        booking = appointments.book_slot(setup, slot.id, guest_id=guest.id, guest_name="Urgent Demo")
        appointment_id = booking["appointment"]["id"]
        appointments.reconcile_capture(setup, booking["checkout"]["order_id"], "pay_admin", 50000, "INR")
        when = start.astimezone(timezone(timedelta(hours=5, minutes=30))).date()
        listing = admin.get(f"{base}/appointments", params={"kind": "urgent", "q": "Urgent Demo",
                            "period": "custom", "start": when.isoformat(), "end": when.isoformat()})
        assert listing.json()["items"][0]["id"] == appointment_id
        assert admin.get(f"{base}/slots", params={"period": "custom", "start": when.isoformat(),
                        "end": when.isoformat()}).json()["items"][0]["status"] == "booked"
        path = f"{base}/appointments/{appointment_id}"
        quote = admin.get(path + "/cancellation-quote").json()
        assert quote["refund_paise"] == 50000
        payload = {**quote, "reason": "Clinic services interrupted"}
        assert admin.post(path + "/cancel", json={**payload, "refund_paise": 0}).status_code == 409
        assert admin.post(path + "/cancel", json=payload).json()["status"] == "cancelled"
        assert admin.post(path + "/cancel", json=payload).status_code == 200
        assert setup.get(Refund, "pay_admin").amount_paise == 50000
        assert setup.database.adminactions.count_documents({"action": "cancel_appointment"}) == 1
        assert admin.delete(f"{base}/slots/{slot.id}").status_code == 409
        notices = setup.find(Notification, {"appointment_id": appointment_id}, sort=[("_id", 1)])
        assert [(notice.user_id, notice.kind) for notice in notices] == [
            (doctor_id, "new_booking"), (doctor_id, "doctor_booking_cancelled")]


def test_doctor_suspension_cancels_future_bookings_and_revokes_old_sessions(setup, monkeypatch):
    with TestClient(app) as doc, TestClient(app) as admin:
        doctor_id = doctor(doc)
        login(admin, "admin@example.com")
        base = "/api/v1/admin"
        admin.post(f"{base}/doctors/{doctor_id}/decision", json={"decision": "approved", "note": "Reviewed registration"})
        slots = [available_guest_slot(setup, doctor_id, starts_in=timedelta(hours=hour)) for hour in (2, 3)]
        guest = setup.insert(GuestPatient(phone="9999999990"))
        orders = iter(["order_confirmed", "order_pending"])
        monkeypatch.setattr(payments, "create_order", lambda amount, receipt: {"id": next(orders)})
        patient_user = setup.insert(User(email="admin-test-patient@example.com", full_name="Demo Patient",
                                        phone="9999999999", password_hash="unused", role="patient"))
        confirmed = appointments.book_slot(setup, slots[0].id, patient_id=patient_user.id)
        pending = appointments.book_slot(setup, slots[1].id, guest_id=guest.id, guest_name="Urgent Demo")
        appointments.reconcile_capture(setup, "order_confirmed", "pay_confirmed", 50000, "INR")
        path = f"{base}/accounts/{doctor_id}/status"
        payload = {"suspended": True, "reason": "Temporarily unavailable"}
        response = admin.put(path, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["cancelled_appointments"] == 2
        assert admin.put(path, json=payload).json()["cancelled_appointments"] == 0
        assert doc.get("/api/v1/auth/me").status_code == 401
        assert doc.get(f"/api/v1/doctors/{doctor_id}").status_code == 404
        assert doc.get("/api/v1/guest/availability").json() == []
        assert doc.post("/api/v1/auth/login", json={"email": "doctor@example.com", "password": "testpass123"}).status_code == 403
        for booking in (confirmed, pending):
            item = setup.get(Appointment, booking["appointment"]["id"])
            assert (item.status, item.cancelled_by) == ("cancelled", "admin")
        appointments.reconcile_capture(setup, "order_pending", "pay_late", 50000, "INR")
        assert setup.get(Refund, "pay_confirmed").amount_paise == 50000
        assert setup.get(Refund, "pay_late").amount_paise == 50000
        assert admin.put(path, json={"suspended": False, "reason": "Returned to practice"}).status_code == 200
        assert doc.get("/api/v1/auth/me").status_code == 401
        login(doc, "doctor@example.com")
        assert doc.get(f"/api/v1/doctors/{doctor_id}").status_code == 200
        assert setup.database.adminactions.count_documents({"action": {"$in": ["suspend", "reactivate"]}}) == 2
