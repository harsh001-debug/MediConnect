"""Versioned MongoDB collection contracts. Existing collection names stay stable."""

from pymongo import ASCENDING as ASC, DESCENDING as DESC, IndexModel

SCHEMA_VERSION = 9
MODEL_COLLECTIONS = {
    "User": ("users", "id"),
    "AdminAction": ("adminactions", "id"),
    "LoginSession": ("loginsessions", "token_hash"),
    "AuthRateLimit": ("authratelimits", "key"),
    "PasswordResetChallenge": ("passwordresetchallenges", "key"),
    "GuestPatient": ("guestpatients", "id"),
    "GuestSession": ("guestsessions", "token_hash"),
    "GuestChallenge": ("guestchallenges", "key"),
    "DoctorProfile": ("doctorprofiles", "user_id"),
    "VerificationCheck": ("verificationchecks", "id"),
    "Slot": ("slots", "id"),
    "Appointment": ("appointments", "id"),
    "PaymentAttempt": ("paymentattempts", "id"),
    "Refund": ("refunds", "payment_id"),
    "Notification": ("notifications", "id"),
    "Review": ("reviews", "id"),
}


def text(minimum=0, maximum=None):
    result = {"bsonType": "string", "minLength": minimum}
    if maximum is not None:
        result["maxLength"] = maximum
    return result


def integer(minimum=0, maximum=None):
    result = {"bsonType": ["int", "long"], "minimum": minimum}
    if maximum is not None:
        result["maximum"] = maximum
    return result


def choice(*values):
    return {"bsonType": "string", "enum": list(values)}


def nullable(schema):
    return {"anyOf": [schema, {"bsonType": "null"}]}


ID = integer(1)
DATE = {"bsonType": "date"}
PAGE = integer(1, 3)
PRESCRIPTION_ENTRY_FIELDS = {
    "kind": choice("date", "medication", "note", "unreadable"),
    "text": text(1, 240), "page": PAGE,
}
MODE = choice("clinic", "online")
CONTACT_CODE_FIELDS = {
    "nonce": text(32, 32), "digest": text(64, 64), "attempts": integer(0, 5),
    "sent_at": DATE, "expires_at": DATE,
}
CONTACT_CODE = nullable({"bsonType": "object", "required": list(CONTACT_CODE_FIELDS),
                         "additionalProperties": False, "properties": CONTACT_CODE_FIELDS})
NOTIFICATION_EMAIL_FIELDS = {
    "key": text(32, 32), "status": choice("queued", "sent", "failed", "skipped"),
    "attempts": integer(), "next_attempt_at": DATE, "lease_until": nullable(DATE),
    "lease_token": nullable(text(32, 32)),
}
FIELDS = {
    "users": {
        "_id": ID, "email": text(3, 320), "full_name": text(2, 120), "phone": text(10, 20),
        "password_hash": text(1), "role": choice("patient", "doctor", "admin"),
        "created_at": DATE, "slot_version": integer(),
        "email_verified_at": nullable(DATE), "phone_verified_at": nullable(DATE),
        "email_code": CONTACT_CODE, "phone_code": CONTACT_CODE,
        "suspended_at": nullable(DATE), "session_generation": integer(), "booking_version": integer(),
        "admin_access": {"bsonType": "bool"},
    },
    "loginsessions": {
        "_id": {"bsonType": "string", "pattern": "^[a-f0-9]{64}$"}, "user_id": ID, "expires_at": DATE,
        "generation": integer(),
    },
    "adminactions": {
        "_id": ID, "admin_id": ID, "target_id": ID, "action": choice("suspend", "reactivate", "cancel_appointment", "grant_admin"),
        "reason": text(5, 500), "created_at": DATE,
    },
    "authratelimits": {"_id": text(64, 64), "count": integer(1), "expires_at": DATE},
    "passwordresetchallenges": {"_id": text(64, 64), "user_id": ID, "generation": integer(), **CONTACT_CODE_FIELDS},
    "guestpatients": {"_id": ID, "phone": text(9, 16)},
    "guestsessions": {"_id": text(64, 64), "guest_id": ID, "expires_at": DATE},
    "guestchallenges": {"_id": text(64, 64), "digest": text(64, 64), "token_hash": text(64, 64),
                        "sent_at": DATE, "expires_at": DATE, "attempts": integer(0, 5)},
    "doctorprofiles": {
        "_id": ID, "council": text(3, 120), "registration_number": text(3, 80),
        "registration_date": {"bsonType": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "qualifications": text(3, 300), "specialty": text(3, 100), "experience_years": integer(0, 70),
        "clinic_name": text(2, 120), "clinic_address": text(5, 300), "city": text(2, 80),
        "clinic_fee_paise": integer(100, 10000000), "online_fee_paise": integer(100, 10000000),
        "bio": text(0, 1000), "verification_status": choice("pending", "published", "rejected"),
        "verification_method": choice("", "registry_matched", "admin_review", "demo_seeded"),
        "review_note": text(0, 500), "council_key": text(1), "registration_key": text(1),
        "hide_until_contacts_verified": {"bsonType": "bool"},
    },
    "verificationchecks": {
        "_id": ID, "doctor_id": ID, "result": choice("matched", "review", "unavailable"),
        "source_url": text(), "matched_name": text(), "detail": text(), "checked_at": DATE,
    },
    "slots": {
        "_id": ID, "doctor_id": ID, "mode": MODE, "start_at": DATE, "end_at": DATE,
        "status": choice("available", "held", "booked"), "held_until": nullable(DATE),
        "appointment_id": nullable(ID),
    },
    "appointments": {
        "_id": ID, "slot_id": ID, "doctor_id": ID, "patient_id": nullable(ID), "mode": MODE,
        "amount_paise": integer(100, 10000000), "hold_expires_at": DATE,
        "status": choice("pending_payment", "confirmed", "completed", "cancelled", "expired", "payment_failed"),
        "room_name": nullable(text(1)), "created_at": DATE, "paid_at": nullable(DATE),
        "guest_patient_id": nullable(ID), "guest_name": nullable(text(2, 120)),
        "cancelled_at": nullable(DATE), "cancelled_by": nullable(choice("patient", "guest", "doctor", "admin")),
        "cancellation_fee_paise": nullable(integer()),
        "prescription_revision": integer(),
    },
    "prescriptions": {
        "_id": ID, "version": text(32, 32),
        "photos": {"bsonType": "array", "minItems": 1, "maxItems": 3, "items": {"bsonType": "binData"}},
        "status": choice("draft", "queued", "processing", "ready", "unreadable", "unavailable"),
        "entries": {"bsonType": "array", "maxItems": 40, "items": {
            "bsonType": "object", "required": list(PRESCRIPTION_ENTRY_FIELDS),
            "additionalProperties": False, "properties": PRESCRIPTION_ENTRY_FIELDS,
        }},
        "consent_version": text(1, 32), "consented_at": DATE,
        "expires_at": DATE, "purge_at": DATE, "retain_until": DATE, "audit_days": integer(1),
        "attempts": integer(0, 3), "next_attempt_at": nullable(DATE),
        "lease_until": nullable(DATE), "lease_token": nullable(text(32, 32)),
    },
    "prescriptionaudit": {
        "_id": {"bsonType": "objectId"}, "appointment_id": ID, "actor_id": nullable(ID),
        "action": choice("uploaded", "replaced", "removed", "shared", "viewed", "viewed_photo", "cancelled", "purged"),
        "page": nullable(PAGE), "created_at": DATE, "expires_at": DATE,
    },
    "paymentattempts": {
        "_id": ID, "appointment_id": ID, "razorpay_order_id": text(1),
        "razorpay_payment_id": nullable(text(1)),
        "status": choice("created", "authorized", "failed", "captured", "paid_after_expiry"), "created_at": DATE,
        "next_check_at": nullable(DATE),
    },
    "refunds": {
        "_id": text(1), "appointment_id": ID, "amount_paise": integer(1),
        "status": choice("queued", "processing", "processed", "needs_review"),
        "provider_refund_id": nullable(text(1)), "attempts": integer(), "next_attempt_at": DATE,
        "lease_until": nullable(DATE), "lease_token": nullable(text(1)), "error": nullable(text(1)), "created_at": DATE,
    },
    "notifications": {
        "_id": ID, "user_id": ID, "body": text(1), "created_at": DATE, "read_at": nullable(DATE),
        "kind": nullable(text(1)), "appointment_id": nullable(ID),
        "email": nullable({"bsonType": "object", "required": list(NOTIFICATION_EMAIL_FIELDS),
                           "additionalProperties": False, "properties": NOTIFICATION_EMAIL_FIELDS}),
    },
    "reviews": {
        "_id": ID, "appointment_id": ID, "doctor_id": ID, "patient_id": ID,
        "rating": integer(1, 5), "comment": text(0, 500), "created_at": DATE,
    },
    "counters": {"_id": {"bsonType": "string", "enum": [name for name, (_, key) in MODEL_COLLECTIONS.items() if key == "id"]}, "value": integer()},
    "schema_migrations": {"_id": integer(1), "applied_at": DATE},
}

OPTIONAL_FIELDS = {
    "users": {"email_verified_at", "phone_verified_at", "email_code", "phone_code", "suspended_at",
              "session_generation", "booking_version", "admin_access"},
    "loginsessions": {"generation"},
    "doctorprofiles": {"hide_until_contacts_verified"},
    "appointments": {"guest_patient_id", "guest_name", "cancelled_at", "cancelled_by", "cancellation_fee_paise",
                     "prescription_revision"},
    "paymentattempts": {"next_check_at"},
    "notifications": {"read_at", "kind", "appointment_id", "email"},
}
VALIDATORS = {
    name: {"$jsonSchema": {"bsonType": "object", "required": [field for field in fields if field not in OPTIONAL_FIELDS.get(name, ())],
                           "additionalProperties": False, "properties": fields}}
    for name, fields in FIELDS.items()
}
# A date type alone would still permit negative-duration appointments.
VALIDATORS["slots"] = {"$and": [VALIDATORS["slots"], {"$expr": {"$gt": ["$end_at", "$start_at"]}}]}
VALIDATORS["appointments"] = {"$and": [VALIDATORS["appointments"], {"$or": [
    {"patient_id": {"$ne": None}, "guest_patient_id": None, "guest_name": None},
    {"patient_id": None, "guest_patient_id": {"$ne": None}, "guest_name": {"$ne": None}},
]}]}

# Preserve the names/options of existing indexes so setup is safe to repeat.
INDEXES = {
    "users": [IndexModel("email", unique=True), IndexModel("role")],
    "adminactions": [IndexModel([("admin_id", ASC), ("created_at", DESC)])],
    "loginsessions": [IndexModel("user_id"), IndexModel("expires_at", expireAfterSeconds=0)],
    "authratelimits": [IndexModel("expires_at", expireAfterSeconds=0)],
    "passwordresetchallenges": [IndexModel("expires_at", expireAfterSeconds=0)],
    "guestpatients": [IndexModel("phone", unique=True)],
    "guestsessions": [IndexModel("expires_at", expireAfterSeconds=0)],
    "guestchallenges": [IndexModel("expires_at", expireAfterSeconds=0)],
    "doctorprofiles": [
        IndexModel([("council_key", ASC), ("registration_key", ASC)], unique=True),
        IndexModel([("verification_status", ASC), ("specialty", ASC), ("city", ASC)]),
    ],
    "verificationchecks": [IndexModel([("doctor_id", ASC), ("checked_at", DESC)])],
    "slots": [
        IndexModel([("start_at", ASC), ("_id", ASC)]),
        IndexModel([("doctor_id", ASC), ("start_at", ASC)], unique=True),
        IndexModel([("doctor_id", ASC), ("status", ASC), ("start_at", ASC)]),
        IndexModel([("doctor_id", ASC), ("status", ASC), ("held_until", ASC)]),
        IndexModel([("status", ASC), ("start_at", ASC)]),
    ],
    "appointments": [
        IndexModel("paid_at"),
        IndexModel("cancelled_at"),
        IndexModel([("patient_id", ASC), ("created_at", DESC)]),
        IndexModel([("doctor_id", ASC), ("created_at", DESC)]),
        IndexModel([("slot_id", ASC), ("status", ASC)]),
        IndexModel([("guest_patient_id", ASC), ("created_at", DESC)]),
        IndexModel("guest_patient_id", name="guest_pending_hold", unique=True, partialFilterExpression={
            "guest_patient_id": {"$type": "number"}, "status": "pending_payment"}),
    ],
    "paymentattempts": [
        IndexModel("appointment_id", unique=True), IndexModel("razorpay_order_id", unique=True),
        IndexModel("razorpay_payment_id", unique=True, partialFilterExpression={"razorpay_payment_id": {"$type": "string"}}),
        IndexModel([("status", ASC), ("next_check_at", ASC), ("created_at", ASC)]),
    ],
    "refunds": [IndexModel("appointment_id"), IndexModel("created_at"), IndexModel([("status", ASC), ("next_attempt_at", ASC)])],
    "prescriptions": [
        IndexModel("expires_at"), IndexModel("purge_at", expireAfterSeconds=0),
        IndexModel([("status", ASC), ("next_attempt_at", ASC)]),
    ],
    "prescriptionaudit": [IndexModel("appointment_id"), IndexModel("expires_at", expireAfterSeconds=0)],
    "reviews": [IndexModel("appointment_id", unique=True), IndexModel("doctor_id"),
                IndexModel([("doctor_id", ASC), ("created_at", DESC)])],
    "notifications": [
        IndexModel([("user_id", ASC), ("created_at", DESC), ("_id", DESC)]),
        IndexModel([("user_id", ASC), ("_id", DESC)]),
        IndexModel([("user_id", ASC), ("read_at", ASC), ("_id", ASC)]),
        IndexModel([("email.status", ASC), ("email.next_attempt_at", ASC)]),
    ],
}

# These are application-level relationships, not MongoDB foreign keys.
REFERENCES = [
    ("doctorprofiles", "_id", "users", "doctor"),
    ("loginsessions", "user_id", "users", None),
    ("passwordresetchallenges", "user_id", "users", None),
    ("adminactions", "admin_id", "users", "admin"),
    ("guestsessions", "guest_id", "guestpatients", None),
    ("verificationchecks", "doctor_id", "doctorprofiles", None),
    ("slots", "doctor_id", "doctorprofiles", None),
    ("appointments", "doctor_id", "doctorprofiles", None),
    ("appointments", "patient_id", "users", "patient"),
    ("appointments", "guest_patient_id", "guestpatients", None),
    ("appointments", "slot_id", "slots", None),
    ("prescriptions", "_id", "appointments", None),
    ("prescriptionaudit", "appointment_id", "appointments", None),
    ("paymentattempts", "appointment_id", "appointments", None),
    ("refunds", "appointment_id", "appointments", None),
    ("notifications", "user_id", "users", None),
    ("reviews", "appointment_id", "appointments", None),
    ("reviews", "doctor_id", "doctorprofiles", None),
    ("reviews", "patient_id", "users", "patient"),
]


def ensure_indexes(database):
    for name, indexes in INDEXES.items():
        for index in indexes:
            options = dict(index.document)
            keys = list(options.pop("key").items())
            database[name].create_index(keys, **options)


def audit_database(database):
    """Read-only preflight; report counts, never document contents or secrets."""
    issues = []
    counts = {}
    for name, validator in VALIDATORS.items():
        counts[name] = database[name].count_documents({})
        invalid = database[name].count_documents({"$nor": [validator]})
        if invalid:
            issues.append(f"{name}: {invalid} documents do not match the schema")
    for source, field, target, role in REFERENCES:
        matches = {"related": {"$size": 0}}
        if role:
            matches = {"$or": [matches, {"related.role": {"$ne": role}}]}
        if role == "admin":
            matches = {"related": {"$not": {"$elemMatch": {"$or": [{"role": "admin"}, {"admin_access": True}]}}}}
        elif role == "patient":
            matches = {"related": {"$not": {"$elemMatch": {
                "$or": [{"role": {"$in": ["patient", "admin"]}}, {"admin_access": True}]}}}}
        applicable = [{"$match": {field: {"$ne": None}}}] if source == "appointments" and field in {"patient_id", "guest_patient_id"} else []
        result = list(database[source].aggregate([*applicable,
            {"$lookup": {"from": target, "localField": field, "foreignField": "_id", "as": "related"}},
            {"$match": matches}, {"$count": "count"},
        ]))
        if result:
            issues.append(f"{source}.{field}: {result[0]['count']} missing or incorrect {target} references")
    for name, indexes in INDEXES.items():
        for index in indexes:
            spec = index.document
            if not spec.get("unique"):
                continue
            pipeline = [{"$match": spec["partialFilterExpression"]}] if "partialFilterExpression" in spec else []
            pipeline += [
                {"$group": {"_id": {key: f"${key}" for key in spec["key"]}, "count": {"$sum": 1}}},
                {"$match": {"count": {"$gt": 1}}}, {"$count": "count"},
            ]
            duplicate = list(database[name].aggregate(pipeline))
            if duplicate:
                issues.append(f"{name}: {duplicate[0]['count']} duplicate groups for {spec['name']}")
    return {"schema_version": SCHEMA_VERSION, "counts": counts, "issues": issues}


def apply_schema(database):
    """Audit first. Never rewrite, rename or drop existing business documents."""
    from datetime import datetime, timezone

    report = audit_database(database)
    if report["issues"]:
        raise ValueError("Schema not applied; fix the audit findings first: " + "; ".join(report["issues"]))
    existing = set(database.list_collection_names())
    for name, validator in VALIDATORS.items():
        options = {"validator": validator, "validationLevel": "strict", "validationAction": "error"}
        if name in existing:
            database.command({"collMod": name, **options})
        else:
            database.create_collection(name, **options)
    ensure_indexes(database)
    database.schema_migrations.update_one(
        {"_id": SCHEMA_VERSION}, {"$setOnInsert": {"applied_at": datetime.now(timezone.utc)}}, upsert=True)
    return report
