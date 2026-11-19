"""Add fictional West Bengal doctors without replacing existing accounts or bookings."""

import os
from datetime import datetime, time, timedelta, timezone

from app.db import as_utc, store, utcnow
from app.models import DoctorProfile, Slot, User
from app.registry import normalized
from app.security import hash_password

IST = timezone(timedelta(hours=5, minutes=30))
DOCTORS = [
    ("ananya.sen", "Ananya Sen", "General Medicine", "Kolkata", "Salt Lake", "MBBS, MD (General Medicine)", 10, 500, 400),
    ("soumyajit.das", "Soumyajit Das", "Cardiology", "Howrah", "Shibpur", "MBBS, MD, DM (Cardiology)", 14, 900, 700),
    ("ishita.roy", "Ishita Roy", "Dermatology", "Siliguri", "Pradhan Nagar", "MBBS, MD (Dermatology)", 8, 600, 500),
    ("arindam.ghosh", "Arindam Ghosh", "Orthopedics", "Durgapur", "City Centre", "MBBS, MS (Orthopedics)", 12, 700, 550),
    ("madhumita.pal", "Madhumita Pal", "Pediatrics", "Asansol", "Ushagram", "MBBS, MD (Pediatrics)", 9, 450, 350),
    ("ritam.biswas", "Ritam Biswas", "ENT", "Malda", "English Bazar", "MBBS, MS (ENT)", 7, 500, 400),
]


def seed_west_bengal(db, password):
    if not 8 <= len(password) <= 128:
        raise ValueError("Set WB_DEMO_PASSWORD to between 8 and 128 characters")
    now = utcnow()
    local_today = now.astimezone(IST).date()
    results = []
    for index, (slug, name, specialty, city, area, qualifications, years, clinic_fee, online_fee) in enumerate(DOCTORS, 1):
        email = f"demo.wb.{slug}@example.com"
        registration = f"FICTIONAL-WB-{index:03d}"
        council = "Fictional West Bengal Demo Council"
        keys = {"council_key": normalized(council), "registration_key": normalized(registration)}
        with db.transaction() as tx:
            profile = tx.first(DoctorProfile, keys)
            user = tx.get(User, profile.user_id) if profile else tx.first(User, {"email": email})
            if profile and not user:
                raise ValueError(f"Demo profile {registration} has no account; refusing to modify it")
            created = user is None
            if created:
                user = tx.insert(User(email=email, full_name=f"Dr. {name} (Demo)", phone="0000000000",
                                      password_hash=hash_password(password), role="doctor"))
                tx.insert(DoctorProfile(
                    user_id=user.id, council=council, registration_number=registration,
                    registration_date=local_today - timedelta(days=365 * years), qualifications=f"{qualifications} — fictional demo",
                    specialty=specialty, experience_years=years, city=city,
                    clinic_name=f"{city} Demo {specialty} Clinic",
                    clinic_address=f"Fictional demo clinic, {area}, {city}, West Bengal — not a real medical facility",
                    bio="Fictional doctor for testing MediConnect. This profile, qualifications, registration and clinic are demo data only.",
                    clinic_fee_paise=clinic_fee * 100, online_fee_paise=online_fee * 100,
                    verification_status="published", verification_method="demo_seeded",
                    **keys))
            profile = tx.get(DoctorProfile, user.id)
            if user.role != "doctor" or not profile or profile.registration_number != registration or profile.verification_method != "demo_seeded":
                raise ValueError(f"Account {email} is not the expected demo doctor; refusing to modify it")
            added = 0
            slots = tx.find(Slot, {"doctor_id": user.id})
            for day in range(1, 8):
                for mode, hour in (("clinic", 10), ("online", 18)):
                    start = datetime.combine(local_today + timedelta(days=day), time(hour, 0), tzinfo=IST).astimezone(timezone.utc)
                    end = start + timedelta(minutes=30)
                    if any(as_utc(slot.start_at) < end and as_utc(slot.end_at) > start for slot in slots):
                        continue
                    slots.append(tx.insert(Slot(doctor_id=user.id, mode=mode, start_at=start, end_at=end)))
                    added += 1
            results.append({"email": email, "city": city, "specialty": specialty, "created": created, "slots_added": added})
    return results


if __name__ == "__main__":
    for result in seed_west_bengal(store(), os.getenv("WB_DEMO_PASSWORD", "")):
        print(f"{result['email']} | {result['city']} | {result['specialty']} | "
              f"{'created' if result['created'] else 'existing password preserved'} | {result['slots_added']} slots added")
