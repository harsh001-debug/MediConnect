from .db import MongoStore, as_utc, utcnow
from .models import Appointment, Slot


def expire_hold(db: MongoStore, slot: Slot | None) -> bool:
    if not slot or slot.status != "held" or not slot.held_until or as_utc(slot.held_until) > utcnow():
        return False

    def release(tx):
        released = tx.update(Slot, {"_id": slot.id, "status": "held", "appointment_id": slot.appointment_id,
                                    "held_until": {"$lte": utcnow()}},
                             {"status": "available", "held_until": None, "appointment_id": None})
        if not released:
            return False
        if slot.appointment_id:
            tx.update(Appointment, {"_id": slot.appointment_id, "status": "pending_payment"}, {"status": "expired"})
        return True

    return db.run_transaction(release)


def expire_doctor_holds(db: MongoStore, doctor_id: int) -> None:
    for slot in db.find(Slot, {"doctor_id": doctor_id, "status": "held", "held_until": {"$lte": utcnow()}}):
        expire_hold(db, slot)
