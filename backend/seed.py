"""Create an admin login and optional fictional demonstration accounts."""

import os
from datetime import timedelta

from app.db import store, utcnow
from app.models import DoctorProfile, Slot, User
from app.registry import normalized
from app.security import hash_password


def add_user(db, role: str, prefix: str):
    email = os.getenv(prefix + "_EMAIL", "").strip().lower()
    password = os.getenv(prefix + "_PASSWORD", "")
    if not email or not password:
        return
    if len(password) < 8:
        raise ValueError(prefix + "_PASSWORD must have at least 8 characters")
    if db.first(User, {"email": email}):
        print(f"{role} already exists: {email}")
        return
    db.insert(User(email=email, full_name=os.getenv(prefix + "_NAME", role.title()), phone="0000000000",
                   password_hash=hash_password(password), role=role))
    print(f"Created {role}: {email}")


def add_demo_doctors(db):
    if os.getenv("SEED_DEMO_DATA") != "1":
        return
    password = os.getenv("SEED_DOCTOR_PASSWORD", "")
    if len(password) < 8:
        raise ValueError("Set SEED_DOCTOR_PASSWORD to at least 8 characters")
    examples = [
        ("Aarav Mehta", "General Medicine", "Pune", "Cedar Clinic", 450, 550),
        ("Nisha Rao", "Dermatology", "Mumbai", "Harbor Health", 650, 700),
        ("Rohan Shah", "Cardiology", "Delhi", "Northside Clinic", 800, 950),
    ]
    for index, (name, specialty, city, clinic, clinic_fee, online_fee) in enumerate(examples, start=1):
        email = f"demo.doctor{index}@example.com"
        registration = f"DEMO-{index:03d}"
        keys = {"council_key": normalized("Demo Council"), "registration_key": normalized(registration)}
        if db.first(DoctorProfile, keys) or db.first(User, {"email": email}):
            continue
        with db.transaction() as tx:
            user = tx.insert(User(email=email, full_name=name, phone="0000000000",
                                  password_hash=hash_password(password), role="doctor"))
            tx.insert(DoctorProfile(user_id=user.id, council="Demo Council", registration_number=registration,
                                    registration_date=utcnow().date() - timedelta(days=365 * 8), qualifications="MBBS · Demo data",
                                    specialty=specialty, experience_years=8 + index, clinic_name=clinic,
                                    clinic_address="Demo address, Central Area", city=city,
                                    bio="Fictional profile created for the MediConnect semester demonstration.",
                                    clinic_fee_paise=clinic_fee * 100, online_fee_paise=online_fee * 100,
                                    verification_status="published", verification_method="demo_seeded",
                                    **keys))
            for day in range(1, 7):
                start = (utcnow() + timedelta(days=day)).replace(hour=5 + index, minute=0, second=0, microsecond=0)
                tx.insert(Slot(doctor_id=user.id, mode="clinic", start_at=start, end_at=start + timedelta(minutes=30)))
                tx.insert(Slot(doctor_id=user.id, mode="online", start_at=start + timedelta(hours=1), end_at=start + timedelta(hours=1, minutes=30)))
        print(f"Created fictional demo doctor: {email}")


if __name__ == "__main__":
    database = store()
    add_user(database, "admin", "SEED_ADMIN")
    add_user(database, "patient", "SEED_PATIENT")
    add_demo_doctors(database)
