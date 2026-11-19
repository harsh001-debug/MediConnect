from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from ..db import MongoStore, as_utc, get_store, utcnow
from ..models import Notification, User
from ..notifications import APPLICATION_KINDS, DOCTOR_BOOKING_KINDS
from ..security import current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])
NEWEST = [("created_at", -1), ("_id", -1)]
PAGE_SIZE = 30
MAX_ID = (1 << 63) - 1


class ReadAll(BaseModel):
    through_id: Annotated[int, Field(gt=0, le=MAX_ID)]


def notice_dict(item: Notification) -> dict:
    return {"id": item.id, "body": item.body, "created_at": as_utc(item.created_at),
            "read_at": as_utc(item.read_at) if item.read_at else None}


def notice_target(item: Notification, user: User) -> dict | None:
    doctor_dashboard = user.role == "doctor" and not user.admin_access
    if item.kind in APPLICATION_KINDS and doctor_dashboard:
        return {"type": "doctor_profile"}
    if item.appointment_id and (item.kind not in DOCTOR_BOOKING_KINDS or doctor_dashboard):
        return {"type": "appointment", "appointment_id": item.appointment_id}
    return None


@router.get("")
def notifications(user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    rows = db.find(Notification, {"user_id": user.id}, sort=NEWEST, limit=PAGE_SIZE)
    return [notice_dict(item) for item in rows]


@router.get("/inbox")
def inbox(before_id: Annotated[int | None, Query(gt=0, le=MAX_ID)] = None,
          user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    owned = {"user_id": user.id}
    latest = db.database.notifications.find_one(owned, projection={"_id": True}, sort=[("_id", -1)])
    through_id = latest["_id"] if latest else None
    scope = {**owned, "_id": {"$lte": through_id}} if through_id is not None else {**owned, "_id": None}
    page = dict(scope)
    if before_id is not None:
        cursor = db.first(Notification, {**owned, "_id": before_id})
        if cursor is None:
            raise HTTPException(404, "Notification not found")
        page["$or"] = [{"created_at": {"$lt": cursor.created_at}},
                       {"created_at": cursor.created_at, "_id": {"$lt": before_id}}]
    rows = db.find(Notification, page, sort=NEWEST, limit=PAGE_SIZE + 1)
    return {"items": [{**notice_dict(item), "target": notice_target(item, user)} for item in rows[:PAGE_SIZE]],
            "unread_count": db.database.notifications.count_documents({**scope, "read_at": None}),
            "next_before_id": rows[PAGE_SIZE - 1].id if len(rows) > PAGE_SIZE else None,
            "through_id": through_id}


@router.post("/read-all")
def read_all(data: ReadAll, user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    owned = {"user_id": user.id}
    if not db.database.notifications.find_one({**owned, "_id": data.through_id}, projection={"_id": True}):
        raise HTTPException(404, "Notification not found")
    db.database.notifications.update_many({**owned, "_id": {"$lte": data.through_id}, "read_at": None},
                                          {"$set": {"read_at": utcnow()}})
    return {"ok": True}


@router.post("/{notification_id}/read")
def read_notification(notification_id: Annotated[int, Path(gt=0, le=MAX_ID)],
                      user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    owned = {"_id": notification_id, "user_id": user.id}
    updated = db.update(Notification, {**owned, "read_at": None}, {"read_at": utcnow()})
    if not updated and not db.first(Notification, owned):
        raise HTTPException(404, "Notification not found")
    return {"ok": True}
