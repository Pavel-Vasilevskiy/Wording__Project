"""Persistent storage and safe image normalization for admin-managed artwork."""

import io
import os
import posixpath
import uuid
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename


MAX_ASSET_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FORMAT_MIME = {"PNG": {"image/png"}, "JPEG": {"image/jpeg", "image/jpg"}, "WEBP": {"image/webp"}}
EXTENSION_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


class AssetError(ValueError):
    """A safe, user-displayable asset validation error."""


def asset_storage_directory(config):
    configured = config.get("CYBERPUNK_ASSET_DIR")
    environment = config.get("FLASK_ENV", "development")
    normalized = posixpath.normpath(str(configured or ""))
    if environment == "production" and (not configured or not normalized.startswith("/data/") or normalized == "/data"):
        raise RuntimeError("CYBERPUNK_ASSET_DIR must point to persistent storage under /data in production.")
    if configured:
        root = Path(configured).expanduser().resolve()
    elif environment == "production":
        raise RuntimeError("CYBERPUNK_ASSET_DIR must point to persistent storage in production.")
    else:
        root = Path(config.get("INSTANCE_PATH", "instance")) / "cyberpunk_warehouse" / "assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_storage_reference(reference):
    if not isinstance(reference, str) or not reference or "\\" in reference or "\x00" in reference or ":" in reference:
        raise AssetError("Invalid asset path.")
    path = PurePosixPath(reference)
    if path.is_absolute() or len(path.parts) != 1 or any(part in {"", ".", ".."} for part in path.parts):
        raise AssetError("Invalid asset path.")
    return path


def process_uploaded_image(upload, max_bytes=MAX_ASSET_BYTES):
    """Decode allowed images and normalize to metadata-free WebP."""
    filename = secure_filename(upload.filename or "")
    if not filename or Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise AssetError("Unsupported file format. Use PNG, JPG, or WebP.")
    upload.stream.seek(0, os.SEEK_END)
    size = upload.stream.tell()
    upload.stream.seek(0)
    if size > max_bytes:
        raise AssetError("Image must be 5 MB or smaller.")
    if not size:
        raise AssetError("The selected file is empty.")
    try:
        with Image.open(upload.stream) as source:
            if source.format not in FORMAT_MIME:
                raise AssetError("Unsupported image format.")
            if EXTENSION_FORMATS.get(Path(filename).suffix.lower()) != source.format:
                raise AssetError("File extension does not match the image content.")
            if upload.mimetype and upload.mimetype.lower() not in FORMAT_MIME[source.format] | {"application/octet-stream"}:
                raise AssetError("File content does not match its image type.")
            if source.width < 1 or source.height < 1 or source.width * source.height > MAX_IMAGE_PIXELS:
                raise AssetError("Image dimensions are too large to process safely.")
            source.verify()
        upload.stream.seek(0)
        with Image.open(upload.stream) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
            dimensions = (image.width, image.height)
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=88, method=5, exact=True)
    except AssetError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise AssetError("Unable to process this image.") from exc
    return output.getvalue(), dimensions


def save_asset_bytes(directory, content):
    root = Path(directory).resolve()
    filename = f"{uuid.uuid4().hex}.webp"
    target = (root / filename).resolve()
    if target.parent != root:
        raise AssetError("Invalid asset destination.")
    with target.open("xb") as output:
        output.write(content)
    return filename


def remove_asset_file(directory, reference):
    path = validate_storage_reference(reference)
    root = Path(directory).resolve()
    target = (root / Path(*path.parts)).resolve()
    if target.parent != root:
        raise AssetError("Invalid asset path.")
    try:
        target.unlink()
    except FileNotFoundError:
        pass

