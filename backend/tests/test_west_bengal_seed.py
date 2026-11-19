import mongomock
import pytest
from fastapi.testclient import TestClient

from app.db import MongoStore, ensure_indexes, get_store, utcnow
from app.main import app
from app.models import Slot, User
from app.security import verify_password
from seed_west_bengal import DOCTORS, seed_west_bengal


def test_demo_doctors_login_slots_and_repeat_safety(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    db = MongoStore(mongomock.MongoClient(tz_aware=True).mediconnect_test)
    ensure_indexes(db.database)
    first = seed_west_bengal(db, "OriginalDemo@2026")
    assert len(first) == len(DOCTORS) == 6
    assert sum(row["slots_added"] for row in first) == 84
    slot = db.first(Slot)
    db.update(Slot, {"_id": slot.id}, {"status": "booked"})
    second = seed_west_bengal(db, "DifferentDemo@2026")
    assert all(not row["created"] and row["slots_added"] == 0 for row in second)
    assert db.get(Slot, slot.id).status == "booked"
    assert db.database.slots.count_documents({}) == 84

    app.dependency_overrides[get_store] = lambda: db
    try:
        with TestClient(app) as client:
            assert len(client.get("/api/v1/doctors").json()) == 6
            for row in first:
                user = db.first(User, {"email": row["email"]})
                assert verify_password("OriginalDemo@2026", user.password_hash)
                response = client.post("/api/v1/auth/login", json={"email": row["email"], "password": "OriginalDemo@2026"})
                assert response.status_code == 200
                assert response.json()["role"] == "doctor"
                assert response.json()["verification_required"] is True
                assert client.get("/api/v1/doctor/profile").status_code == 403
                db.update(User, {"_id": user.id}, {"email_verified_at": utcnow(), "phone_verified_at": utcnow()})
                profile = client.get("/api/v1/doctor/profile").json()
                assert profile["city"] == row["city"]
                available = client.get(f"/api/v1/doctors/{user.id}/slots").json()
                assert {item["mode"] for item in available} == {"clinic", "online"}
                assert len(client.get("/api/v1/doctor/slots").json()) == 14
                client.post("/api/v1/auth/logout")
        renamed = db.first(User, {"email": first[0]["email"]})
        db.update(User, {"_id": renamed.id}, {"email": "verified-demo@example.com"})
        assert all(not row["created"] for row in seed_west_bengal(db, "DifferentDemo@2026"))
        assert db.database.users.count_documents({}) == 6
    finally:
        app.dependency_overrides.clear()


def test_seed_refuses_to_take_over_existing_account():
    db = MongoStore(mongomock.MongoClient(tz_aware=True).mediconnect_test)
    db.insert(User(email=f"demo.wb.{DOCTORS[0][0]}@example.com", full_name="Existing patient",
                   phone="0000000000", password_hash="unchanged", role="patient"))
    with pytest.raises(ValueError, match="refusing to modify"):
        seed_west_bengal(db, "DemoPassword@2026")
    assert db.first(User).role == "patient"
    assert db.database.slots.count_documents({}) == 0
