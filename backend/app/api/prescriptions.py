from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from .. import prescriptions
from ..db import MongoStore, get_store, utcnow
from ..models import Appointment, DoctorProfile, User
from ..security import current_user, limit_requests, require_role

router = APIRouter(prefix="/appointments/{appointment_id}/prescription", tags=["prior prescriptions"])

MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_PHOTOS * MAX_PHOTO_BYTES + 64 * 1024
MAX_STORED_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 24_000_000


class InMemoryUpload(MultiPartParser):
    # Keep prescription photos out of the system temporary directory.
    spool_max_size = MAX_REQUEST_BYTES
    max_file_size = MAX_REQUEST_BYTES  # Older supported Starlette versions use this name.


def normalize_photo(upload: UploadFile) -> bytes:
    data = upload.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Each prescription photo must be 8 MiB or smaller.")
    try:
        with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as source:
            if source.width * source.height > MAX_PIXELS:
                raise HTTPException(413, "Choose a photo under 24 megapixels.")
            if getattr(source, "is_animated", False):
                raise HTTPException(422, "Choose still JPEG, PNG, or WebP photos.")
            photo = ImageOps.exif_transpose(source)
            photo.thumbnail((3600, 3600))
            if photo.has_transparency_data:
                foreground = photo.convert("RGBA")
                photo = Image.new("RGB", foreground.size, "white")
                photo.paste(foreground, mask=foreground.getchannel("A"))
            else:
                photo = photo.convert("RGB")
            photo.info.clear()
            for quality in (92, 84, 76):
                output = BytesIO()
                photo.save(output, format="JPEG", quality=quality, optimize=True)
                if output.tell() <= MAX_STORED_BYTES:
                    return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        raise HTTPException(422, "Choose a valid JPEG, PNG, or WebP photo.") from None
    raise HTTPException(413, "This photo is too large after processing. Crop to the prescription and try again.")


async def limited_body(request: Request):
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_REQUEST_BYTES:
            raise MultiPartException("Upload too large")
        yield chunk


def check_upload(db: MongoStore, appointment_id: int, user: User) -> prescriptions.Settings:
    item = prescriptions.pending_appointment(db, appointment_id, user)
    config = prescriptions.settings()
    if not config or not prescriptions.upload_consent(db.get(DoctorProfile, item.doctor_id), config):
        raise HTTPException(404, "Prescription uploads are unavailable for this appointment.")
    limit_requests(db, "prescription-uploads", str(user.id), limit=12, seconds=900)
    return config


@router.put("")
async def upload(appointment_id: int, request: Request, user: User = Depends(require_role("patient")),
                 db: MongoStore = Depends(get_store)):
    config = await run_in_threadpool(check_upload, db, appointment_id, user)
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "multipart/form-data":
        raise HTTPException(415, "Send prescription photos as a file upload.")
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "Invalid upload length.") from None
    if length > MAX_REQUEST_BYTES:
        raise HTTPException(413, "Upload up to three photos of 8 MiB each.")
    try:
        form = await InMemoryUpload(request.headers, limited_body(request), max_files=MAX_PHOTOS,
                                    max_fields=1, max_part_size=128).parse()
    except MultiPartException as exc:
        if exc.message == "Upload too large":
            raise HTTPException(413, "Upload up to three photos of 8 MiB each.") from None
        raise HTTPException(422, "Send one to three photos and confirm consent.") from None
    try:
        if form.get("consent_version") != prescriptions.CONSENT_VERSION:
            raise HTTPException(422, "Confirm consent for AI processing and sharing before uploading.")
        files = form.getlist("photos")
        if len(form.multi_items()) != len(files) + 1 or not files or not all(isinstance(file, UploadFile) for file in files):
            raise HTTPException(422, "Select one to three prescription photos.")
        photos = await run_in_threadpool(lambda: [normalize_photo(file) for file in files])
        return await run_in_threadpool(prescriptions.save_draft, db, appointment_id, user, photos, config)
    finally:
        await form.close()


@router.delete("", status_code=204)
def delete(appointment_id: int, user: User = Depends(require_role("patient")), db: MongoStore = Depends(get_store)):
    prescriptions.delete_draft(db, appointment_id, user)
    return Response(status_code=204)


def readable_query(db: MongoStore, appointment_id: int, user: User) -> dict:
    item = db.get(Appointment, appointment_id)
    if not item or not item.prescription_revision or not prescriptions.can_read(item, user):
        raise HTTPException(404, "Previous prescription not found.")
    status = "draft" if item.status == "pending_payment" else {"$ne": "draft"}
    return {"_id": item.id, "status": status, "expires_at": {"$gt": utcnow()}}


@router.get("")
def detail(appointment_id: int, user: User = Depends(current_user), db: MongoStore = Depends(get_store)):
    query = readable_query(db, appointment_id, user)
    # Project the number of pages on the server to keep the image bytes out of status polling.
    row = next(iter(prescriptions.collection(db).aggregate([
        {"$match": query},
        {"$project": {"status": 1, "version": 1, "entries": 1, "audit_days": 1, "photo_count": {"$size": "$photos"}}},
    ])), None)
    if not row:
        raise HTTPException(404, "Previous prescription not found.")
    prescriptions.audit(db, row, "viewed", actor_id=user.id)
    return {field: row[field] for field in ("status", "version", "entries", "photo_count")}


@router.get("/photos/{version}/{page}")
def photo(appointment_id: int, version: str, page: int, user: User = Depends(current_user),
          db: MongoStore = Depends(get_store)):
    query = readable_query(db, appointment_id, user)
    if page < 1 or page > MAX_PHOTOS or len(version) != 32:
        raise HTTPException(404, "Prescription photo not found.")
    row = prescriptions.collection(db).find_one({**query, "version": version},
                                                {"audit_days": 1, "photos": {"$slice": [page - 1, 1]}})
    if not row or not row["photos"]:
        raise HTTPException(404, "Prescription photo not found.")
    prescriptions.audit(db, row, "viewed_photo", actor_id=user.id, page=page)
    return Response(row["photos"][0], media_type="image/jpeg", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin", "Referrer-Policy": "no-referrer",
    })
