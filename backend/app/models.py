"""Small typed records stored as MongoDB documents."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TypedDict

from .db import utcnow


class ContactCode(TypedDict):
    nonce: str
    digest: str
    attempts: int
    sent_at: datetime
    expires_at: datetime


@dataclass(kw_only=True)
class User:
    email: str
    full_name: str
    phone: str
    password_hash: str
    role: str
    id: int | None = None
    created_at: datetime = field(default_factory=utcnow)
    slot_version: int = 0
    email_verified_at: datetime | None = None
    phone_verified_at: datetime | None = None
    email_code: ContactCode | None = None
    phone_code: ContactCode | None = None
    suspended_at: datetime | None = None
    session_generation: int = 0
    booking_version: int = 0
    admin_access: bool = False


@dataclass(kw_only=True)
class LoginSession:
    token_hash: str
    user_id: int
    expires_at: datetime
    generation: int = 0


@dataclass(kw_only=True)
class AdminAction:
    admin_id: int
    target_id: int
    action: str
    reason: str
    id: int | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass(kw_only=True)
class AuthRateLimit:
    key: str
    count: int
    expires_at: datetime


@dataclass(kw_only=True)
class PasswordResetChallenge:
    key: str
    user_id: int
    generation: int
    nonce: str
    digest: str
    sent_at: datetime
    expires_at: datetime
    attempts: int = 0


@dataclass(kw_only=True)
class GuestPatient:
    phone: str
    id: int | None = None


@dataclass(kw_only=True)
class GuestSession:
    token_hash: str
    guest_id: int
    expires_at: datetime


@dataclass(kw_only=True)
class GuestChallenge:
    key: str
    digest: str
    token_hash: str
    sent_at: datetime
    expires_at: datetime
    attempts: int = 0


@dataclass(kw_only=True)
class DoctorProfile:
    user_id: int
    council: str
    registration_number: str
    registration_date: date
    qualifications: str
    specialty: str
    experience_years: int
    clinic_name: str
    clinic_address: str
    city: str
    clinic_fee_paise: int
    online_fee_paise: int
    bio: str = ""
    verification_status: str = "pending"
    verification_method: str = ""
    review_note: str = ""
    council_key: str = ""
    registration_key: str = ""
    hide_until_contacts_verified: bool = False


@dataclass(kw_only=True)
class VerificationCheck:
    doctor_id: int
    result: str
    source_url: str
    id: int | None = None
    matched_name: str = ""
    detail: str = ""
    checked_at: datetime = field(default_factory=utcnow)


@dataclass(kw_only=True)
class Slot:
    doctor_id: int
    mode: str
    start_at: datetime
    end_at: datetime
    id: int | None = None
    status: str = "available"
    held_until: datetime | None = None
    appointment_id: int | None = None


@dataclass(kw_only=True)
class Appointment:
    slot_id: int
    doctor_id: int
    patient_id: int | None
    mode: str
    amount_paise: int
    hold_expires_at: datetime
    id: int | None = None
    status: str = "pending_payment"
    room_name: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    paid_at: datetime | None = None
    guest_patient_id: int | None = None
    guest_name: str | None = None
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None
    cancellation_fee_paise: int | None = None
    prescription_revision: int = 0


@dataclass(kw_only=True)
class PaymentAttempt:
    appointment_id: int
    razorpay_order_id: str
    id: int | None = None
    razorpay_payment_id: str | None = None
    status: str = "created"
    created_at: datetime = field(default_factory=utcnow)
    next_check_at: datetime | None = None


@dataclass(kw_only=True)
class Refund:
    payment_id: str
    appointment_id: int
    amount_paise: int
    status: str = "queued"
    provider_refund_id: str | None = None
    attempts: int = 0
    next_attempt_at: datetime = field(default_factory=utcnow)
    lease_until: datetime | None = None
    lease_token: str | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)


class NotificationEmail(TypedDict):
    key: str
    status: str
    attempts: int
    next_attempt_at: datetime
    lease_until: datetime | None
    lease_token: str | None


@dataclass(kw_only=True)
class Notification:
    user_id: int
    body: str
    id: int | None = None
    created_at: datetime = field(default_factory=utcnow)
    read_at: datetime | None = None
    kind: str | None = None
    appointment_id: int | None = None
    email: NotificationEmail | None = None


@dataclass(kw_only=True)
class Review:
    appointment_id: int
    doctor_id: int
    patient_id: int
    rating: int
    id: int | None = None
    comment: str = ""
    created_at: datetime = field(default_factory=utcnow)
