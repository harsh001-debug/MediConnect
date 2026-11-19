from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import OperationFailure

from ..db import MongoStore, as_utc, get_store, utcnow
from ..models import AdminAction, Appointment, DoctorProfile, Refund, Slot, User, VerificationCheck
from ..notifications import add_notice
from ..refunds import refund_dict
from ..schemas import AdminAccountStatus, AdminCancellation, AdminDecision, AdminGrant, SlotInput
from ..security import require_role
from . import admin_reporting as reporting
from .appointments import can_cancel, cancel_in_transaction, quote_cancellation
from .common import doctor_dict, slot_dict, visible_doctor_user
from .doctors import add_doctor_slot, remove_doctor_slot

admin_only = require_role("admin")
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(admin_only)])
Store = Annotated[MongoStore, Depends(get_store)]
Admin = Annotated[User, Depends(admin_only)]


class DateRange:
    def __init__(self, period: Literal["week", "today", "last7", "custom"] = "week",
                 start: date | None = None, end: date | None = None):
        self.start, self.end = reporting.report_dates(period, start, end)


class Listing:
    def __init__(self, q: str = Query("", max_length=120), page: int = Query(1, ge=1, le=100000),
                 limit: int = Query(25, ge=1, le=100)):
        self.q, self.page, self.limit = q.strip(), page, limit

    def documents(self, collection, match, sort):
        return reporting.page_documents(collection, match, sort, self.page, self.limit)

    def response(self, items, total):
        return reporting.page_data(items, total, self.page, self.limit)


Range = Annotated[DateRange, Depends()]
ListQuery = Annotated[Listing, Depends()]


@router.get("/overview")
def overview(db: Store, dates: Range):
    return reporting.overview(db, dates.start, dates.end)


@router.get("/accounts")
def accounts(db: Store, listing: ListQuery, role: Literal["patient", "doctor", "admin"] | None = None,
             status: Literal["active", "suspended"] | None = None, eligible: bool = False):
    match = {}
    if role == "admin":
        match["$and"] = [{"$or": [{"role": "admin"}, {"admin_access": True}]}]
    elif role:
        match.update(role=role, admin_access={"$ne": True})
    if status:
        match["suspended_at"] = None if status == "active" else {"$ne": None}
    if listing.q:
        pattern = reporting.search_expression(listing.q)
        match["$or"] = [{"full_name": pattern}, {"email": pattern}]
    if eligible:
        candidates = db.find(DoctorProfile, {"verification_status": "published"})
        match["_id"] = {"$in": [profile.user_id for profile in candidates if visible_doctor_user(db, profile)]}
    rows, total = listing.documents(db.database.users, match, [("created_at", -1), ("_id", -1)])
    doctor_ids = [row["_id"] for row in rows if row["role"] == "doctor"]
    profiles = {profile.user_id: profile for profile in db.find(DoctorProfile, {"_id": {"$in": doctor_ids}})}
    return listing.response([
        {"id": row["_id"], "full_name": row["full_name"], "email": row["email"],
         "role": "admin" if row.get("admin_access") else row["role"],
         "created_at": row["created_at"], "suspended_at": row.get("suspended_at"),
         "doctor_status": profiles[row["_id"]].verification_status if row["_id"] in profiles else None,
         "specialty": profiles[row["_id"]].specialty if row["_id"] in profiles else None}
        for row in rows
    ], total)


@router.put("/accounts/{account_id}/status")
def change_account_status(account_id: int, data: AdminAccountStatus, admin: Admin, db: Store):
    def change(tx):
        account = tx.get(User, account_id)
        if not account or account.admin_access or account.role not in {"patient", "doctor"}:
            raise HTTPException(404, "Patient or doctor account not found")
        if bool(account.suspended_at) == data.suspended:
            return {"suspended": data.suspended, "cancelled_appointments": 0}
        now = utcnow()
        # Reservations write these same user records before claiming a slot.
        tx.update(User, {"_id": account_id}, {"suspended_at": now if data.suspended else None},
                  increments={"session_generation": 1} if data.suspended else None)
        tx.insert(AdminAction(admin_id=admin.id, target_id=account_id, reason=data.reason,
                              action="suspend" if data.suspended else "reactivate", created_at=now))
        count = 0
        if data.suspended:
            field = "doctor_id" if account.role == "doctor" else "patient_id"
            active = {"$or": [{"status": "confirmed"}, {"status": "pending_payment", "hold_expires_at": {"$gt": now}}]}
            for item in tx.find(Appointment, {field: account_id, **active}, sort=[("_id", 1)]):
                slot = tx.get(Slot, item.slot_id)
                if slot and can_cancel(item, slot, now):
                    cancel_in_transaction(tx, item.id, "admin", None, admin_id=admin.id, reason=data.reason, now=now)
                    count += 1
        return {"suspended": data.suspended, "cancelled_appointments": count}

    try:
        return db.run_transaction(change)
    except OperationFailure as exc:
        if exc.code not in {50, 112, 251, 290}:
            raise
        raise HTTPException(409, "The account change could not finish. Refresh and try again.") from None


@router.post("/accounts/{account_id}/make-admin")
def make_admin(account_id: int, data: AdminGrant, admin: Admin, db: Store):
    def grant(tx):
        account = tx.get(User, account_id)
        if not account:
            raise HTTPException(404, "Account not found")
        if account.suspended_at:
            raise HTTPException(409, "Reactivate this account before granting administrator access.")
        if account.role == "admin" or account.admin_access:
            return {"id": account.id, "role": "admin"}
        tx.update(User, {"_id": account.id}, {"admin_access": True}, increments={"session_generation": 1})
        tx.insert(AdminAction(admin_id=admin.id, target_id=account.id, action="grant_admin", reason=data.reason))
        return {"id": account.id, "role": "admin"}

    return db.run_transaction(grant)


@router.get("/slots")
def slots(db: Store, dates: Range, listing: ListQuery,
          status: Literal["available", "held", "booked"] | None = None, doctor_id: int | None = None):
    now = utcnow()
    match = {"start_at": reporting.date_query(dates.start, dates.end)}
    if status == "available":
        match["$or"] = [{"status": "available"}, {"status": "held", "held_until": {"$lte": now}}]
    elif status:
        match["status"] = status
        if status == "held":
            match["held_until"] = {"$gt": now}
    if doctor_id is not None:
        match["doctor_id"] = doctor_id
    if listing.q:
        matched_ids = reporting.find_account_ids(db, listing.q, role="doctor")
        match["doctor_id"] = {"$in": matched_ids if doctor_id is None else [key for key in matched_ids if key == doctor_id]}
    rows, total = listing.documents(db.database.slots, match, [("start_at", 1), ("_id", 1)])
    doctors = reporting.by_id(db, User, (row["doctor_id"] for row in rows))
    slot_ids = [row["_id"] for row in rows]
    history = set(db.database.appointments.distinct("slot_id", {"slot_id": {"$in": slot_ids}}))
    items = []
    for row in rows:
        slot = db._decode(Slot, row)
        doctor = doctors.get(slot.doctor_id)
        expired = slot.status == "held" and slot.held_until and as_utc(slot.held_until) <= now
        items.append({**slot_dict(slot), "status": "available" if expired else slot.status,
                      "doctor_name": doctor.full_name if doctor else None,
                      "can_remove": slot.status == "available" and slot.id not in history and as_utc(slot.start_at) > now,
                      "doctor_suspended": bool(doctor and doctor.suspended_at)})
    return listing.response(items, total)


@router.post("/doctors/{doctor_id}/slots", status_code=201)
def create_slot(doctor_id: int, data: SlotInput, db: Store):
    return add_doctor_slot(db, doctor_id, data)


@router.delete("/slots/{slot_id}")
def remove_slot(slot_id: int, db: Store):
    return remove_doctor_slot(db, slot_id)


@router.get("/appointments")
def appointments(db: Store, dates: Range, listing: ListQuery,
                 status: Literal["pending_payment", "confirmed", "completed", "cancelled", "expired", "payment_failed"] | None = None,
                 kind: Literal["scheduled", "urgent"] | None = None):
    now = utcnow()
    slot_ids = db.database.slots.distinct("_id", {"start_at": reporting.date_query(dates.start, dates.end)})
    match = {"slot_id": {"$in": slot_ids}}
    clauses = []
    if status == "expired":
        clauses.append({"$or": [{"status": "expired"}, {"status": "pending_payment", "hold_expires_at": {"$lte": now}}]})
    elif status:
        match["status"] = status
        if status == "pending_payment":
            match["hold_expires_at"] = {"$gt": now}
    if kind:
        match["guest_patient_id"] = {"$ne": None} if kind == "urgent" else None
    if listing.q:
        ids = reporting.find_account_ids(db, listing.q)
        terms = [{"doctor_id": {"$in": ids}}, {"patient_id": {"$in": ids}},
                 {"guest_name": reporting.search_expression(listing.q)}]
        if listing.q.isascii() and listing.q.isdigit() and len(listing.q) <= 18:
            terms.append({"_id": int(listing.q)})
        clauses.append({"$or": terms})
    if clauses:
        match["$and"] = clauses
    rows, total = listing.documents(db.database.appointments, match, [("created_at", -1), ("_id", -1)])
    users = reporting.by_id(db, User, (key for row in rows for key in (row["doctor_id"], row["patient_id"])))
    slots_by_id = reporting.by_id(db, Slot, (row["slot_id"] for row in rows))
    return listing.response([
        reporting.appointment_row(db._decode(Appointment, row), slots_by_id[row["slot_id"]], users, now)
        for row in rows if row["slot_id"] in slots_by_id
    ], total)


@router.get("/appointments/{appointment_id}/cancellation-quote")
def cancellation_quote(appointment_id: int, db: Store):
    item = db.get(Appointment, appointment_id)
    if not item:
        raise HTTPException(404, "Appointment not found")
    return quote_cancellation(item, db.get(Slot, item.slot_id), "admin", utcnow())


@router.post("/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int, data: AdminCancellation, admin: Admin, db: Store):
    item = db.run_transaction(lambda tx: cancel_in_transaction(
        tx, appointment_id, "admin", data, admin_id=admin.id, reason=data.reason))
    return {"id": item.id, "status": item.status}


@router.get("/transactions")
def transactions(db: Store, dates: Range, listing: ListQuery,
                 status: Literal["queued", "processing", "processed", "needs_review", "no_refund"] | None = None):
    rows = reporting.transaction_rows(db, dates.start, dates.end)
    if status:
        rows = [row for row in rows if (row["refund_status"] or "no_refund") == status]
    if listing.q:
        needle = listing.q.casefold()
        rows = [row for row in rows if any(needle in str(row[key] or "").casefold()
                for key in ("appointment_id", "payment_id", "order_id", "refund_reference"))]
    rows.sort(key=lambda row: (row["captured_at"], row["appointment_id"], row["payment_id"] or ""), reverse=True)
    offset = (listing.page - 1) * listing.limit
    return listing.response(rows[offset:offset + listing.limit], len(rows))


@router.get("/refunds")
def refunds(db: Store):
    rows = db.find(Refund, {"status": "needs_review"}, sort=[("created_at", -1)], limit=100)
    return [{**refund_dict(row), "appointment_id": row.appointment_id, "payment_id": row.payment_id,
             "error": row.error} for row in rows]


@router.get("/pending-doctors")
def pending_doctors(db: Store):
    profiles = db.find(DoctorProfile, {"verification_status": "pending"})
    result = []
    for profile in profiles:
        user = visible_doctor_user(db, profile)
        if not user:
            continue
        item = doctor_dict(db, profile, user)
        item["registration_date"] = profile.registration_date
        check = db.first(VerificationCheck, {"doctor_id": profile.user_id}, sort=[("checked_at", -1)])
        item["check"] = {"result": check.result, "detail": check.detail, "source_url": check.source_url} if check else None
        result.append(item)
    return result


@router.post("/doctors/{doctor_id}/decision")
def decide(doctor_id: int, data: AdminDecision, db: Store):
    profile = db.get(DoctorProfile, doctor_id)
    if not profile or profile.verification_status != "pending" or not visible_doctor_user(db, profile):
        raise HTTPException(404, "Pending application not found")
    status = "published" if data.decision == "approved" else "rejected"
    with db.transaction() as tx:
        changed = tx.update(DoctorProfile, {"_id": doctor_id, "verification_status": "pending"},
                            {"verification_status": status, "verification_method": "admin_review" if data.decision == "approved" else "",
                             "review_note": data.note.strip()})
        if not changed:
            raise HTTPException(404, "Pending application not found")
        add_notice(tx, doctor_id, f"application_{data.decision}",
                   f"Your doctor application was {data.decision}. {data.note.strip()}".strip())
    return {"status": status}
