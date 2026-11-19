import json
import re
import smtplib
from base64 import b64decode
from datetime import timedelta
from urllib.parse import parse_qs
from unittest.mock import MagicMock

import httpx
import mongomock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import contact_verification as otp
from app.contact_verification import deliver_code, deliver_email
from app.db import MongoStore, ensure_indexes, get_store, utcnow
from app.main import app
from app.models import GuestChallenge, User


@pytest.fixture
def twilio_config(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "1" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fictional-token")
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", "MG" + "2" * 32)


def test_email_and_sms_use_provider_contracts_and_the_entered_destination(monkeypatch, twilio_config):
    monkeypatch.setenv("OTP_EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_fictional_test")
    monkeypatch.setenv("OTP_EMAIL_FROM", "MediConnect <accounts@example.com>")
    reached = []

    def handle(request):
        reached.append(request.url.host)
        assert request.method == "POST"
        if request.url.host == "api.resend.com":
            assert request.url.path == "/emails"
            assert request.headers["Authorization"] == "Bearer re_fictional_test"
            data = json.loads(request.content)
            assert data["from"] == "MediConnect <accounts@example.com>"
            assert data["to"] == ["patient@example.com"]
            assert "381256" in data["text"]
            return httpx.Response(200, json={"id": "sandbox-email"})
        assert request.url.host == "api.twilio.com"
        assert request.url.path == "/2010-04-01/Accounts/AC" + "1" * 32 + "/Messages.json"
        scheme, credentials = request.headers["Authorization"].split()
        assert scheme == "Basic"
        assert b64decode(credentials).decode() == "AC" + "1" * 32 + ":fictional-token"
        data = parse_qs(request.content.decode())
        assert data["MessagingServiceSid"] == ["MG" + "2" * 32]
        assert data["To"] == ["+919876543210"]
        assert "752839" in data["Body"][0]
        assert data["ValidityPeriod"] == ["600"]
        return httpx.Response(201, json={"sid": "SM" + "3" * 32, "status": "queued"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        deliver_code("email", "patient@example.com", "381256")
        deliver_code("phone", "+919876543210", "752839")
    assert reached == ["api.resend.com", "api.twilio.com"]


@pytest.mark.parametrize("payload", [
    {"status": "accepted"}, {"sid": " ", "status": "accepted"}, {"sid": "SMdemo"},
    {"sid": "SMdemo", "status": "failed"}, {"sid": "SMdemo", "status": "undelivered"},
    {"sid": "SMdemo", "status": ["accepted"]},
])
def test_sms_requires_provider_acceptance(monkeypatch, twilio_config, payload):
    transport = httpx.MockTransport(lambda _request: httpx.Response(201, json=payload))
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        with pytest.raises(HTTPException) as exc:
            deliver_code("phone", "+919876543210", "752839")
    assert exc.value.status_code == 503


def test_sms_missing_service_fails_before_connecting(monkeypatch, twilio_config):
    monkeypatch.setenv("TWILIO_MESSAGING_SERVICE_SID", " ")
    send = MagicMock()
    monkeypatch.setattr(httpx, "post", send)
    with pytest.raises(HTTPException) as exc:
        deliver_code("phone", "+919876543210", "752839")
    assert exc.value.status_code == 503
    send.assert_not_called()


@pytest.fixture
def sms_api(monkeypatch, twilio_config):
    database = mongomock.MongoClient(tz_aware=True)["mediconnect_sms_test"]
    ensure_indexes(database)
    store = MongoStore(database)
    monkeypatch.setitem(app.dependency_overrides, get_store, lambda: store)
    monkeypatch.setenv("OTP_SECRET", "isolated tests only " * 3)
    with TestClient(app, base_url="https://testserver") as client:
        yield client, store


def test_account_verifies_sms_code_sent_through_twilio(monkeypatch, sms_api):
    client, store = sms_api
    signup = client.post("/api/v1/auth/patient-signup", json={
        "email": "patient@example.com", "password": "testpass123", "full_name": "Demo Patient", "phone": "98765 43210",
    })
    assert signup.status_code == 201
    user_id = signup.json()["id"]
    store.update(User, {"_id": user_id}, {"email_verified_at": utcnow()})
    codes = iter([1234, 654321])
    monkeypatch.setattr(otp.secrets, "randbelow", lambda maximum: next(codes))
    sent = []

    def handle(request):
        fields = parse_qs(request.content.decode())
        assert fields["To"] == ["+919876543210"]
        code, = re.findall(r"\b[0-9]{6}\b", fields["Body"][0])
        sent.append(code)
        return httpx.Response(201, json={"sid": "SM" + "3" * 32, "status": "accepted"})

    path = "/api/v1/auth/contacts/phone"
    with httpx.Client(transport=httpx.MockTransport(handle)) as provider:
        monkeypatch.setattr(httpx, "post", provider.post)
        first = client.post(f"{path}/send")
        assert first.status_code == 200
        assert sent == ["001234"]
        assert sent[0] not in first.text
        assert store.get(User, user_id).phone_code["digest"] != sent[0]
        assert client.post(f"{path}/send").status_code == 429
        store.update(User, {"_id": user_id}, {"phone_code.sent_at": utcnow() - timedelta(minutes=2)})
        assert client.post(f"{path}/send").status_code == 200
        assert sent == ["001234", "654321"]

    assert client.post(f"{path}/verify", json={"code": sent[0]}).status_code == 400
    verified = client.post(f"{path}/verify", json={"code": sent[1]})
    assert verified.status_code == 200
    assert verified.json()["contacts"]["phone"]["verified"] is True
    assert verified.json()["verification_required"] is False
    assert store.get(User, user_id).phone_code is None


def test_guest_retries_a_twilio_failure_and_uses_the_code_once(monkeypatch, sms_api):
    client, store = sms_api
    codes = iter([123456, 752839])
    monkeypatch.setattr(otp.secrets, "randbelow", lambda maximum: next(codes))
    sent = []

    def handle(request):
        fields = parse_qs(request.content.decode())
        assert fields["To"] == ["+919876543211"]
        code, = re.findall(r"\b[0-9]{6}\b", fields["Body"][0])
        sent.append(code)
        if len(sent) == 1:
            return httpx.Response(400, json={"message": "private provider details"})
        return httpx.Response(201, json={"sid": "SM" + "3" * 32, "status": "accepted"})

    phone = {"phone": "98765 43211"}
    path = "/api/v1/guest/otp"
    with httpx.Client(transport=httpx.MockTransport(handle)) as provider:
        monkeypatch.setattr(httpx, "post", provider.post)
        failed = client.post(f"{path}/send", json=phone)
        assert failed.status_code == 503
        assert "private provider details" not in failed.text
        assert store.find(GuestChallenge) == []
        delivered = client.post(f"{path}/send", json=phone)
        assert delivered.status_code == 200

    assert sent == ["123456", "752839"]
    challenge = delivered.json()
    assert challenge.keys() == {"challenge", "expires_at", "resend_at"}
    verify = {**phone, "challenge": challenge["challenge"], "code": sent[-1]}
    assert client.post(f"{path}/verify", json=verify).json() == {"phone": "+919876543211"}
    assert client.get("/api/v1/guest/session").status_code == 200
    assert client.post(f"{path}/verify", json=verify).status_code == 400


@pytest.fixture
def smtp_account(monkeypatch):
    monkeypatch.setenv("OTP_EMAIL_PROVIDER", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "sender@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "synthetic-app-password")
    monkeypatch.setenv("OTP_EMAIL_FROM", "MediConnect <sender@gmail.com>")
    factory = MagicMock()
    smtp = factory.return_value.__enter__.return_value
    smtp.send_message.return_value = {}
    monkeypatch.setattr("app.contact_verification.smtplib.SMTP", factory)
    monkeypatch.setattr("app.contact_verification.smtplib.SMTP_SSL", factory)
    return factory, smtp


@pytest.mark.parametrize("port", [587, 465])
def test_smtp_authenticates_over_tls_and_sends_to_entered_email(monkeypatch, smtp_account, port):
    factory, smtp = smtp_account
    monkeypatch.setenv("SMTP_PORT", str(port))
    deliver_code("email", "recipient@example.com", "381256")
    assert factory.call_args.args == ("smtp.gmail.com", port)
    assert factory.call_args.kwargs["timeout"] == 8
    if port == 587:
        smtp.starttls.assert_called_once()
        assert [call[0] for call in smtp.method_calls] == ["ehlo", "starttls", "ehlo", "login", "send_message"]
        assert smtp.starttls.call_args.kwargs["context"].check_hostname
    else:
        assert factory.call_args.kwargs["context"].check_hostname
        smtp.starttls.assert_not_called()
    smtp.login.assert_called_once_with("sender@gmail.com", "synthetic-app-password")
    email = smtp.send_message.call_args.args[0]
    assert str(email["To"]) == "recipient@example.com"
    assert str(email["From"]) == "MediConnect <sender@gmail.com>"
    assert "381256" in email.get_content()


@pytest.mark.parametrize("failure", ["authentication", "tls", "timeout", "recipient"])
def test_smtp_failures_return_safe_error(smtp_account, failure):
    _, smtp = smtp_account
    if failure == "authentication":
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private provider details")
    elif failure == "tls":
        smtp.starttls.side_effect = smtplib.SMTPNotSupportedError("TLS unavailable")
    elif failure == "timeout":
        smtp.send_message.side_effect = TimeoutError("private request details")
    else:
        smtp.send_message.return_value = {"recipient@example.com": (550, b"rejected")}
    with pytest.raises(HTTPException) as exc:
        deliver_code("email", "recipient@example.com", "381256")
    assert exc.value.status_code == 503
    assert "private" not in exc.value.detail


def test_smtp_missing_password_fails_before_connecting(monkeypatch, smtp_account):
    factory, _ = smtp_account
    monkeypatch.setenv("SMTP_PASSWORD", "")
    with pytest.raises(HTTPException) as exc:
        deliver_code("email", "recipient@example.com", "381256")
    assert exc.value.status_code == 503
    factory.assert_not_called()


def test_notification_email_uses_smtp_with_its_own_subject_and_message_id(smtp_account):
    _, smtp = smtp_account
    deliver_email("recipient@example.com", "MediConnect update", "An appointment was cancelled.",
                  idempotency_key="notice-key")
    email = smtp.send_message.call_args.args[0]
    assert email["Subject"] == "MediConnect update"
    assert email["Message-ID"] == "<notice-key@mediconnect.local>"
    assert email.get_content().strip() == "An appointment was cancelled."
