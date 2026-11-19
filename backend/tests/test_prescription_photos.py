from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image
from starlette.datastructures import UploadFile

from app.api.prescriptions import normalize_photo


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_supported_photos_normalize_without_metadata(format):
    image = Image.new("RGB", (180, 240), "white")
    exif = Image.Exif()
    exif[270] = "private fixture metadata"
    content = BytesIO()
    image.save(content, format=format, exif=exif)
    result = normalize_photo(UploadFile(BytesIO(content.getvalue()), filename="untrusted-name"))
    with Image.open(BytesIO(result)) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.size == (180, 240)
        assert not normalized.getexif()
    assert b"private fixture metadata" not in result


def test_phone_photo_orientation_is_applied_before_metadata_removal():
    content = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (180, 240), "white").save(content, format="JPEG", exif=exif)
    with Image.open(BytesIO(normalize_photo(UploadFile(BytesIO(content.getvalue()))))) as photo:
        assert photo.size == (240, 180)
        assert not photo.getexif()


def test_transparent_prescription_has_white_background():
    content = BytesIO()
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(content, format="PNG")
    with Image.open(BytesIO(normalize_photo(UploadFile(BytesIO(content.getvalue()))))) as photo:
        assert photo.getpixel((20, 20)) == (255, 255, 255)


def test_animated_and_oversized_pixel_images_are_rejected():
    content = BytesIO()
    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
    frames[0].save(content, format="WEBP", save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(HTTPException) as error:
        normalize_photo(UploadFile(BytesIO(content.getvalue())))
    assert error.value.status_code == 422
    content = BytesIO()
    Image.new("RGB", (6000, 4001), "white").save(content, format="PNG")
    with pytest.raises(HTTPException) as error:
        normalize_photo(UploadFile(BytesIO(content.getvalue())))
    assert error.value.status_code == 413
