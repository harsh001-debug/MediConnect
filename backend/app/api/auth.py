from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import EmailStr, TypeAdapter, ValidationError
from pymongo.errors import DuplicateKeyError

from .. import password_reset
from ..contact_verification import check_code, contact_status, send_code
from ..db import MongoStore, get_store
from ..models import DoctorProfile, LoginSession, User, VerificationCheck
from ..notifications import add_notice
from ..registry import RegistryResult, lookup_registration, normalized
from ..schemas import (ContactChannel, ContactCodeInput, ContactUpdate, DoctorRegistrationLookup, DoctorSignup,
                       LoginInput, PasswordResetInput, PasswordResetRequest, PatientSignup, normalize_mobile)
from ..security import (COOKIE_NAME, contact_verification_required, hash_password, issue_session, limit_requests,
                        request_source, session_user, token_hash, verify_password)

router = APIRouter(prefix="/auth", tags=["accounts"])
DUMMY_PASSWORD_HASH = hash_password("unused password for unknown accounts")
EMAIL_ADDRESS = TypeAdapter(EmailStr)


def user_dict(user: User, db: MongoStore) -> dict:
    profile = db.get(DoctorProfile, user.id) if user.role == "doctor" else None
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "phone": user.phone,
            "role": "admin" if user.admin_access else user.role,
            "verification_status": profile.verification_status if profile else None,
            "verification_required": contact_verification_required(user),
            "contacts": {"email": contact_status(user, "email"), "phone": contact_status(user, "phone")}}


@router.post("/patient-signup", status_code=201)
def patient_signup(data: PatientSignup, request: Request, response: Response, db: MongoStore = Depends(get_store)):
    limit_requests(db, "signup-source", request_source(request), limit=12, seconds=3600)
    user = User(email=data.email.lower(), full_name=data.full_name.strip(), phone=data.phone.strip(),
                password_hash=hash_password(data.password), role="patient")
    try:
        db.insert(user)
    except DuplicateKeyError:
        raise HTTPException(409, "Email is already registered") from None
    issue_session(response, db, user)
    return user_dict(user, db)


@router.post("/doctor-registration-lookup", response_model=RegistryResult, response_model_exclude={"matched_name"})
def doctor_registration_lookup(data: DoctorRegistrationLookup, request: Request, db: MongoStore = Depends(get_store)):
    limit_requests(db, "doctor-registration-lookup", request_source(request), limit=12, seconds=600)
    if not normalized(data.council) or not normalized(data.registration_number):
        raise HTTPException(422, "Enter a council and registration number containing letters or digits")
    return lookup_registration(data.registration_number, data.council, data.full_name)


@router.post("/doctor-signup", status_code=201)
def doctor_signup(data: DoctorSignup, request: Request, response: Response, db: MongoStore = Depends(get_store)):
    limit_requests(db, "signup-source", request_source(request), limit=12, seconds=3600)
    council_key = normalized(data.council)
    registration_key = normalized(data.registration_number)
    if not council_key or not registration_key:
        raise HTTPException(422, "Enter a council and registration number containing letters or digits")
    if db.first(DoctorProfile, {"council_key": council_key, "registration_key": registration_key}):
        raise HTTPException(409, "This registration is already in use")
    result = lookup_registration(data.registration_number, data.council, data.full_name) if data.confirm_registry_match else None
    matched = result is not None and result.status == "matched"
    user = User(email=data.email.lower(), full_name=data.full_name.strip(), phone=data.phone.strip(),
                password_hash=hash_password(data.password), role="doctor")
    try:
        with db.transaction() as tx:
            tx.insert(user)
            tx.insert(DoctorProfile(user_id=user.id, council=data.council.strip(), registration_number=data.registration_number.strip(),
                                    registration_date=data.registration_date, qualifications=data.qualifications.strip(),
                                    specialty=data.specialty.strip(), experience_years=data.experience_years,
                                    clinic_name=data.clinic_name.strip(), clinic_address=data.clinic_address.strip(),
                                    city=data.city.strip(), bio=data.bio.strip(), clinic_fee_paise=data.clinic_fee_rupees * 100,
                                    online_fee_paise=data.online_fee_rupees * 100,
                                    verification_status="published" if matched else "pending",
                                    verification_method="registry_matched" if matched else "",
                                    council_key=council_key, registration_key=registration_key,
                                    hide_until_contacts_verified=True))
            if result is not None:
                tx.insert(VerificationCheck(doctor_id=user.id, result=result.status, source_url=result.source_url,
                                            matched_name=result.matched_name, detail=result.detail))
            add_notice(tx, user.id, "registration_matched" if matched else "application_pending",
                       "Your registration matched the NMC register." if matched else "Your profile is pending admin review.")
    except DuplicateKeyError:
        raise HTTPException(409, "Email or medical registration is already in use") from None
    issue_session(response, db, user)
    return user_dict(user, db)


@router.post("/login")
def login(data: LoginInput, request: Request, response: Response, db: MongoStore = Depends(get_store)):
    email = data.email.lower()
    source = request_source(request)
    limit_requests(db, "login-source", source, limit=60, seconds=600)
    limit_requests(db, "login-account-source", f"{source}:{email}", limit=10, seconds=600)
    user = db.first(User, {"email": email})
    if not verify_password(data.password, user.password_hash if user else DUMMY_PASSWORD_HASH) or not user:
        raise HTTPException(401, "Invalid email or password")
    if user.suspended_at:
        raise HTTPException(403, "This account is suspended. Contact MediConnect for assistance.")
    issue_session(response, db, user)
    return user_dict(user, db)


@router.post("/logout")
def logout(request: Request, response: Response, db: MongoStore = Depends(get_store)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        login_session = db.get(LoginSession, token_hash(token))
        if login_session:
            db.delete(login_session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/password-reset/request", status_code=202)
def request_password_reset(data: PasswordResetRequest, request: Request, background_tasks: BackgroundTasks,
                           db: MongoStore = Depends(get_store)):
    password_reset.request_reset(db, data.email.lower(), request_source(request), background_tasks)
    return {"ok": True}


@router.post("/password-reset/confirm", status_code=204)
def confirm_password_reset(data: PasswordResetInput, request: Request, db: MongoStore = Depends(get_store)):
    password_reset.reset_password(db, data.email.lower(), data.code, data.password, request_source(request))


@router.get("/me")
def me(user: User = Depends(session_user), db: MongoStore = Depends(get_store)):
    return user_dict(user, db)


def require_contact_onboarding(user: User = Depends(session_user)) -> User:
    if user.role not in {"patient", "doctor"}:
        raise HTTPException(403, "Contact verification is for patient and doctor accounts")
    return user


@router.post("/contacts/{channel}/send")
def send_contact_code(channel: ContactChannel, request: Request, user: User = Depends(require_contact_onboarding),
                      db: MongoStore = Depends(get_store)):
    return user_dict(send_code(db, user, channel, request_source(request)), db)


@router.post("/contacts/{channel}/verify")
def verify_contact_code(channel: ContactChannel, data: ContactCodeInput, user: User = Depends(require_contact_onboarding),
                        db: MongoStore = Depends(get_store)):
    return user_dict(check_code(db, user, channel, data.code), db)


@router.put("/contacts/{channel}")
def edit_contact(channel: ContactChannel, data: ContactUpdate, request: Request,
                 user: User = Depends(require_contact_onboarding), db: MongoStore = Depends(get_store)):
    field = f"{channel}_verified_at"
    if getattr(user, field):
        raise HTTPException(409, "This contact has already been verified")
    try:
        value = EMAIL_ADDRESS.validate_python(data.value).lower() if channel == "email" else normalize_mobile(data.value)
    except ValidationError:
        raise HTTPException(422, "Enter a valid email address") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    limit_requests(db, "contact-edit", f"{request_source(request)}:{user.id}", limit=10, seconds=600)
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Your password is incorrect")
    try:
        updated = db.update(User, {"_id": user.id, channel: getattr(user, channel), field: None},
                            {channel: value, f"{channel}_code": None})
    except DuplicateKeyError:
        raise HTTPException(409, "Email is already registered") from None
    if not updated:
        raise HTTPException(409, "Verification has changed. Refresh the page and try again.")
    return user_dict(updated, db)
