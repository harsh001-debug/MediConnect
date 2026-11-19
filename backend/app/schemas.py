from datetime import date, datetime
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, EmailStr, Field, field_validator


CleanText = Annotated[str, BeforeValidator(lambda value: value.strip() if isinstance(value, str) else value)]


def normalize_mobile(value: str) -> str:
    number = re.sub(r"[ ().-]", "", value)
    if re.fullmatch(r"[6-9][0-9]{9}", number):
        number = "+91" + number
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", number):
        raise ValueError("Enter a 10-digit Indian mobile number or include your country code")
    if number.startswith("+91") and not re.fullmatch(r"\+91[6-9][0-9]{9}", number):
        raise ValueError("Enter a valid Indian mobile number")
    return number


ContactChannel = Literal["email", "phone"]


class PatientSignup(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: CleanText = Field(min_length=2, max_length=120)
    phone: str = Field(max_length=32)

    _normalize_phone = field_validator("phone")(normalize_mobile)


class DoctorSignup(PatientSignup):
    council: CleanText = Field(min_length=3, max_length=120)
    registration_number: CleanText = Field(min_length=3, max_length=80)
    confirm_registry_match: bool = False
    registration_date: date
    qualifications: CleanText = Field(min_length=3, max_length=300)
    specialty: CleanText = Field(min_length=3, max_length=100)
    experience_years: int = Field(ge=0, le=70)
    clinic_name: CleanText = Field(min_length=2, max_length=120)
    clinic_address: CleanText = Field(min_length=5, max_length=300)
    city: CleanText = Field(min_length=2, max_length=80)
    bio: str = Field(default="", max_length=1000)
    clinic_fee_rupees: int = Field(ge=1, le=100000)
    online_fee_rupees: int = Field(ge=1, le=100000)

    @field_validator("registration_date")
    @classmethod
    def registration_not_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Registration date cannot be in the future")
        return value


class DoctorRegistrationLookup(BaseModel):
    full_name: CleanText = Field(min_length=2, max_length=120)
    council: CleanText = Field(min_length=3, max_length=120)
    registration_number: CleanText = Field(min_length=3, max_length=80)


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ContactCodeInput(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$", min_length=6, max_length=6)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetInput(PasswordResetRequest, ContactCodeInput):
    password: str = Field(min_length=8, max_length=128)


class ContactUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class DoctorUpdate(BaseModel):
    qualifications: CleanText = Field(min_length=3, max_length=300)
    specialty: CleanText = Field(min_length=3, max_length=100)
    experience_years: int = Field(ge=0, le=70)
    clinic_name: CleanText = Field(min_length=2, max_length=120)
    clinic_address: CleanText = Field(min_length=5, max_length=300)
    city: CleanText = Field(min_length=2, max_length=80)
    bio: str = Field(max_length=1000)
    clinic_fee_rupees: int = Field(ge=1, le=100000)
    online_fee_rupees: int = Field(ge=1, le=100000)


class SlotInput(BaseModel):
    mode: Literal["clinic", "online"]
    start_at: datetime
    end_at: datetime

    @field_validator("start_at", "end_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Include a timezone offset")
        return value


class BookingInput(BaseModel):
    slot_id: int


class GuestPhoneInput(BaseModel):
    phone: str = Field(max_length=32)

    _normalize_phone = field_validator("phone")(normalize_mobile)


class GuestCodeInput(GuestPhoneInput, ContactCodeInput):
    challenge: str = Field(min_length=40, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")


class GuestBookingInput(BookingInput):
    full_name: CleanText = Field(min_length=2, max_length=120)
    expected_amount_paise: int | None = Field(default=None, ge=100)


class CancelInput(BaseModel):
    paid_paise: int = Field(ge=0)
    refund_paise: int = Field(ge=0)


class VerifyPaymentInput(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class ReviewInput(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


class AdminDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    note: CleanText = Field(min_length=5, max_length=500)


class AdminCancellation(CancelInput):
    reason: CleanText = Field(min_length=5, max_length=500)


class AdminAccountStatus(BaseModel):
    suspended: bool
    reason: CleanText = Field(min_length=5, max_length=500)


class AdminGrant(BaseModel):
    reason: CleanText = Field(min_length=5, max_length=500)
