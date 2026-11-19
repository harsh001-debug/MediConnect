from datetime import date, datetime, timedelta, timezone

import mongomock

from app.api import admin_reporting
from app.db import MongoStore
from app.models import Appointment, PaymentAttempt, Refund, Slot


def test_report_uses_ist_boundaries_and_reconciles_capture_cohorts(monkeypatch):
    monday_ist = datetime(2026, 11, 15, 18, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(admin_reporting, "utcnow", lambda: monday_ist)
    db = MongoStore(mongomock.MongoClient(tz_aware=True).mediconnect_test)
    start, end = admin_reporting.report_dates("week", None, None)
    assert (start, end) == (date(2026, 11, 16), date(2026, 11, 22))
    slot = db.insert(Slot(doctor_id=1, mode="clinic", start_at=monday_ist,
                          end_at=monday_ist + timedelta(minutes=30), status="booked"))

    before = monday_ist - timedelta(milliseconds=1)
    previous = db.insert(Appointment(doctor_id=1, slot_id=slot.id, patient_id=2, mode="clinic",
                                     amount_paise=75000, hold_expires_at=before, paid_at=before))
    paid = db.insert(Appointment(doctor_id=1, slot_id=slot.id, patient_id=2, mode="clinic", status="cancelled",
                                 amount_paise=50000, hold_expires_at=monday_ist, paid_at=monday_ist,
                                 cancelled_at=monday_ist + timedelta(hours=1)))
    db.insert(PaymentAttempt(appointment_id=previous.id, razorpay_order_id="order_previous",
                             razorpay_payment_id="pay_previous", status="captured"))
    db.insert(PaymentAttempt(appointment_id=paid.id, razorpay_order_id="order_paid",
                             razorpay_payment_id="pay_paid", status="captured"))
    db.insert(Refund(appointment_id=previous.id, payment_id="pay_previous", amount_paise=75000,
                     status="processed", created_at=monday_ist))
    db.insert(Refund(appointment_id=paid.id, payment_id="pay_paid", amount_paise=25000,
                     status="processed", created_at=monday_ist + timedelta(days=1)))
    db.insert(Refund(appointment_id=paid.id, payment_id="pay_extra", amount_paise=50000,
                     status="needs_review", created_at=monday_ist + timedelta(days=1)))

    report = admin_reporting.overview(db, start, end)
    assert report["days"][0]["bookings"] == 1
    assert report["days"][0]["cancellations"] == 1
    assert report["days"][0]["booked_slots"] == 1
    assert report["days"][0]["refunded_paise"] == 25000
    assert report["days"][1]["review_refund_paise"] == 50000
    assert report["totals"]["payments"] == 2
    assert report["totals"]["gross_paise"] == 100000
    assert report["totals"]["net_paise"] == 75000
    assert report["days"][6]["gross_paise"] == 0
    rows = admin_reporting.transaction_rows(db, start, end)
    assert [(row["payment_id"], row["late_capture"]) for row in rows] == [
        ("pay_paid", False), ("pay_extra", True),
    ]
