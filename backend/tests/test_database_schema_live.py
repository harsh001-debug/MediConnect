"""Opt-in Atlas validation checks. Every test transaction is aborted, never committed."""

import os
from dataclasses import asdict
from datetime import date, timedelta
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from app.database_schema import MODEL_COLLECTIONS
from app.db import _mongo_value, utcnow
from app.models import Appointment, DoctorProfile, LoginSession, Notification, PaymentAttempt, Review, Slot, User, VerificationCheck
from app.security import hash_password

pytestmark = pytest.mark.skipif(os.getenv("RUN_MONGO_SCHEMA_TESTS") != "1", reason="Opt-in MongoDB tests")


@pytest.fixture(scope="module")
def database():
    with MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=5000, tz_aware=True) as client:
        yield client[os.getenv("MONGODB_DB_NAME", "mediconnect")]


def examples():
    now = utcnow()
    suffix = uuid4().hex
    return [
        User(id=9000000000000001, email=f"schema-{suffix}@example.com", full_name="Schema Demo", phone="0000000000", password_hash=hash_password("SchemaDemo@2026"), role="patient"),
        LoginSession(token_hash=suffix * 2, user_id=9000000000000001, expires_at=now + timedelta(hours=1)),
        DoctorProfile(user_id=9000000000000002, council="Schema demo", registration_number=suffix, registration_date=date(2020, 1, 1), qualifications="Demo MBBS", specialty="General Medicine", experience_years=5, clinic_name="Schema demo", clinic_address="Fictional demo address", city="Kolkata", clinic_fee_paise=50000, online_fee_paise=40000, council_key="schemademo", registration_key=suffix),
        VerificationCheck(id=9000000000000003, doctor_id=9000000000000002, result="unavailable", source_url=""),
        Slot(id=9000000000000004, doctor_id=9000000000000002, mode="clinic", start_at=now, end_at=now + timedelta(minutes=30)),
        Appointment(id=9000000000000005, slot_id=9000000000000004, doctor_id=9000000000000002, patient_id=9000000000000001, mode="clinic", amount_paise=50000, hold_expires_at=now + timedelta(minutes=15)),
        PaymentAttempt(id=9000000000000006, appointment_id=9000000000000005, razorpay_order_id=f"order_schema_{suffix}"),
        Notification(id=9000000000000007, user_id=9000000000000001, body="Schema transaction test"),
        Review(id=9000000000000008, appointment_id=9000000000000005, doctor_id=9000000000000002, patient_id=9000000000000001, rating=5),
    ]


def document(model):
    collection, key = MODEL_COLLECTIONS[type(model).__name__]
    data = {field: _mongo_value(value) for field, value in asdict(model).items()}
    data["_id"] = data.pop(key)
    return collection, data


def test_valid_models_are_accepted_by_real_mongodb(database):
    with database.client.start_session() as session:
        session.start_transaction()
        try:
            for model in examples():
                collection, data = document(model)
                database[collection].insert_one(data, session=session)
        finally:
            session.abort_transaction()


@pytest.mark.parametrize("index,changes", [
    (0, {"role": "superuser"}),
    (1, {"expires_at": "not-a-date"}),
    (2, {"clinic_fee_paise": -100}),
    (4, {"end_at": _mongo_value(utcnow() - timedelta(days=1))}),
    (5, {"status": "unknown"}),
    (6, {"status": "unknown"}),
    (8, {"rating": 6}),
    (8, {"rating": 3.5}),
])
def test_mongodb_rejects_invalid_records(database, index, changes):
    collection, data = document(examples()[index])
    data.update(changes)
    with database.client.start_session() as session:
        session.start_transaction()
        try:
            with pytest.raises(OperationFailure) as error:
                database[collection].insert_one(data, session=session)
            assert error.value.code == 121
        finally:
            session.abort_transaction()
