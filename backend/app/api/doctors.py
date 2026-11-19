from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import DuplicateKeyError, OperationFailure

from .. import prescriptions
from ..db import MongoStore, as_utc, get_store, utcnow
from ..holds import expire_doctor_holds
from ..models import Appointment, DoctorProfile, Review, Slot, User
from ..schemas import DoctorUpdate, SlotInput
from ..security import require_role
from .common import doctor_dict, slot_dict, visible_doctor_user

router = APIRouter(tags=["doctors and slots"])
INDIA_TIME = timezone(timedelta(hours=5, minutes=30))


@router.get("/doctors")
def list_doctors(specialty: str = "", city: str = "", max_fee: int | None = Query(default=None, ge=1),
                 available_on: date | None = None, mode: Literal["clinic", "online"] | None = None,
                 db: MongoStore = Depends(get_store)):
    profiles = db.find(DoctorProfile, {"verification_status": "published"})
    specialty, city = specialty.strip().casefold(), city.strip().casefold()
    now = utcnow()
    start_range = {"$gt": now}
    if available_on:
        if available_on < now.astimezone(INDIA_TIME).date():
            return []
        start = as_utc(datetime.combine(available_on, time.min, INDIA_TIME))
        start_range.update({"$gte": start, "$lt": start + timedelta(days=1)})
    items = []
    for profile in profiles:
        if specialty not in profile.specialty.casefold() or city not in profile.city.casefold():
            continue
        user = visible_doctor_user(db, profile)
        if not user:
            continue
        fees = {"clinic": profile.clinic_fee_paise, "online": profile.online_fee_paise}
        modes = [kind for kind, fee in fees.items()
                 if (mode is None or mode == kind) and (max_fee is None or fee <= max_fee * 100)]
        if modes and (mode or available_on):
            expire_doctor_holds(db, profile.user_id)
            slots = db.find(Slot, {"doctor_id": profile.user_id, "status": "available", "start_at": start_range,
                                  "mode": {"$in": modes}})
            scheduled_modes = {slot.mode for slot in slots}
            modes = [kind for kind in modes if kind in scheduled_modes]
        if modes:
            items.append({**doctor_dict(db, profile, user), "matching_modes": modes})
    return items


@router.get("/doctors/{doctor_id}")
def doctor_detail(doctor_id: int, db: MongoStore = Depends(get_store)):
    profile = db.get(DoctorProfile, doctor_id)
    user = visible_doctor_user(db, profile) if profile and profile.verification_status == "published" else None
    if not user:
        raise HTTPException(404, "Doctor not found")
    reviews = db.find(Review, {"doctor_id": doctor_id}, sort=[("created_at", -1)])
    result = doctor_dict(db, profile, user)
    result["registration_date"] = profile.registration_date
    result["prescription_consent"] = prescriptions.upload_consent(profile, prescriptions.settings())
    result["reviews"] = [{"id": item.id, "rating": item.rating, "comment": item.comment, "created_at": item.created_at} for item in reviews]
    return result


@router.get("/doctors/{doctor_id}/slots")
def public_slots(doctor_id: int, db: MongoStore = Depends(get_store)):
    profile = db.get(DoctorProfile, doctor_id)
    if not profile or profile.verification_status != "published" or not visible_doctor_user(db, profile):
        raise HTTPException(404, "Doctor not found")
    expire_doctor_holds(db, doctor_id)
    slots = db.find(Slot, {"doctor_id": doctor_id, "status": "available"}, sort=[("start_at", 1)])
    return [slot_dict(slot) for slot in slots if as_utc(slot.start_at) > utcnow()]


@router.get("/doctor/profile")
def own_profile(user: User = Depends(require_role("doctor")), db: MongoStore = Depends(get_store)):
    profile = db.get(DoctorProfile, user.id)
    result = doctor_dict(db, profile)
    result["registration_date"] = profile.registration_date
    result["review_note"] = profile.review_note
    return result


@router.put("/doctor/profile")
def update_profile(data: DoctorUpdate, user: User = Depends(require_role("doctor")), db: MongoStore = Depends(get_store)):
    updates = {name: value.strip() if isinstance(value, str) else value
               for name, value in data.model_dump(exclude={"clinic_fee_rupees", "online_fee_rupees"}).items()}
    updates["clinic_fee_paise"] = data.clinic_fee_rupees * 100
    updates["online_fee_paise"] = data.online_fee_rupees * 100
    profile = db.update(DoctorProfile, {"_id": user.id}, updates)
    return doctor_dict(db, profile)


@router.get("/doctor/slots")
def own_slots(user: User = Depends(require_role("doctor")), db: MongoStore = Depends(get_store)):
    expire_doctor_holds(db, user.id)
    slots = db.find(Slot, {"doctor_id": user.id}, sort=[("start_at", -1)])
    return [slot_dict(slot) for slot in slots]


@router.post("/doctor/slots", status_code=201)
def create_slot(data: SlotInput, user: User = Depends(require_role("doctor")), db: MongoStore = Depends(get_store)):
    return add_doctor_slot(db, user.id, data)


def add_doctor_slot(db: MongoStore, doctor_id: int, data: SlotInput):
    profile = db.get(DoctorProfile, doctor_id)
    if not profile or profile.verification_status != "published" or not visible_doctor_user(db, profile):
        raise HTTPException(403, "Select an active doctor with a published profile")
    start, end = as_utc(data.start_at), as_utc(data.end_at)
    if start <= utcnow() or end - start < timedelta(minutes=15) or end - start > timedelta(hours=2):
        raise HTTPException(422, "Choose a future slot lasting 15 minutes to two hours")
    try:
        with db.transaction() as tx:
            # Write the doctor document so concurrent slot-creation transactions conflict.
            doctor = tx.update(User, {"_id": doctor_id, "role": "doctor", "suspended_at": None},
                               {}, increments={"slot_version": 1})
            if not doctor:
                raise HTTPException(409, "The doctor account is no longer active")
            overlap = tx.first(Slot, {"doctor_id": doctor_id, "start_at": {"$lt": end.replace(tzinfo=None)},
                                      "end_at": {"$gt": start.replace(tzinfo=None)}})
            if overlap:
                raise HTTPException(409, "Slot overlaps an existing slot")
            slot = tx.insert(Slot(doctor_id=doctor_id, mode=data.mode, start_at=start, end_at=end))
    except (DuplicateKeyError, OperationFailure) as exc:
        if isinstance(exc, OperationFailure) and exc.code != 112:
            raise
        raise HTTPException(409, "Slot overlaps an existing slot; retry if needed") from None
    return slot_dict(slot)


@router.delete("/doctor/slots/{slot_id}")
def remove_slot(slot_id: int, user: User = Depends(require_role("doctor")), db: MongoStore = Depends(get_store)):
    return remove_doctor_slot(db, slot_id, doctor_id=user.id)


def remove_doctor_slot(db: MongoStore, slot_id: int, *, doctor_id: int | None = None):
    slot = db.get(Slot, slot_id)
    if not slot or (doctor_id is not None and slot.doctor_id != doctor_id):
        raise HTTPException(404, "Slot not found")
    if slot.status != "available":
        raise HTTPException(409, "Booked slots cannot be removed")
    try:
        with db.transaction() as tx:
            if tx.first(Appointment, {"slot_id": slot.id}):
                raise HTTPException(409, "This slot has appointment history and must be kept")
            query = {"_id": slot.id, "doctor_id": slot.doctor_id, "status": "available"}
            if doctor_id is None:
                query["start_at"] = {"$gt": utcnow()}
            removed = tx.database.slots.delete_one(query, **tx._options())
            if removed.deleted_count != 1:
                raise HTTPException(409, "Slot is no longer available")
    except OperationFailure as exc:
        if exc.code != 112:
            raise
        raise HTTPException(409, "Slot changed while removing it; refresh and try again") from None
    return {"ok": True}
