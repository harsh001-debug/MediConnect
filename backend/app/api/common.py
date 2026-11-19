from ..db import MongoStore

from ..models import DoctorProfile, Review, Slot, User


def visible_doctor_user(db: MongoStore, profile: DoctorProfile) -> User | None:
    user = db.get(User, profile.user_id)
    if not user or user.suspended_at:
        return None
    if profile.hide_until_contacts_verified and user and not (user.email_verified_at and user.phone_verified_at):
        return None
    return user


def doctor_dict(db: MongoStore, profile: DoctorProfile, user: User | None = None) -> dict:
    user = user or db.get(User, profile.user_id)
    reviews = db.find(Review, {"doctor_id": profile.user_id})
    return {
        "id": profile.user_id, "full_name": user.full_name, "council": profile.council,
        "registration_number": profile.registration_number, "qualifications": profile.qualifications,
        "specialty": profile.specialty, "experience_years": profile.experience_years,
        "clinic_name": profile.clinic_name, "clinic_address": profile.clinic_address,
        "city": profile.city, "bio": profile.bio,
        "clinic_fee_rupees": profile.clinic_fee_paise // 100,
        "online_fee_rupees": profile.online_fee_paise // 100,
        "verification_status": profile.verification_status,
        "verification_method": profile.verification_method,
        "rating": round(sum(item.rating for item in reviews) / len(reviews), 1) if reviews else None,
        "review_count": len(reviews),
    }


def slot_dict(slot: Slot) -> dict:
    return {"id": slot.id, "doctor_id": slot.doctor_id, "mode": slot.mode,
            "start_at": slot.start_at, "end_at": slot.end_at, "status": slot.status}
