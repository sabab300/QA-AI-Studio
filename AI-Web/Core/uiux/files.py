"""Safe handling of uploaded design files (PNG, JPEG, SVG, native .fig, Figma JSON exports)."""
from __future__ import annotations

import io
import json
import re
import zipfile

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_SVG_BYTES = 2 * 1024 * 1024
MAX_FIG_BYTES = 150 * 1024 * 1024
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 80_000_000
MAX_SIDE = 16384
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 600 * 1024 * 1024

ALLOWED_EXTENSIONS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".svg": "svg", ".fig": "fig", ".json": "figma_json"}


class UploadRejected(ValueError):
    """The upload is not acceptable. The message is safe to show to the user."""


def extension_of(name: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]{1,6})$", name or "")
    return m.group(1).lower() if m else ""


def sniff(data: bytes, filename: str) -> str:
    """Return the verified kind: png | jpeg | svg | fig | figma_json. Extension must match the content."""
    if not data:
        raise UploadRejected("The uploaded file is empty.")
    ext = extension_of(filename)
    declared = ALLOWED_EXTENSIONS.get(ext)
    if declared is None:
        raise UploadRejected("Unsupported file type. Supported: PNG, JPG/JPEG, SVG, .fig, or a Figma JSON export.")
    limit = {"png": MAX_IMAGE_BYTES, "jpeg": MAX_IMAGE_BYTES, "svg": MAX_SVG_BYTES, "fig": MAX_FIG_BYTES, "figma_json": MAX_JSON_BYTES}[declared]
    if len(data) > limit:
        raise UploadRejected(f"The file is {len(data) // 1024 // 1024} MB; the limit for this type is {limit // 1024 // 1024} MB.")
    head = data[:16]
    if declared == "png" and not head.startswith(b"\x89PNG\r\n\x1a\n"):
        raise UploadRejected("The file is named .png but its content is not a PNG image.")
    if declared == "jpeg" and not head.startswith(b"\xff\xd8\xff"):
        raise UploadRejected("The file is named .jpg/.jpeg but its content is not a JPEG image.")
    if declared == "svg":
        if b"<svg" not in data[:4096].lower() and b"<?xml" not in data[:64].lower():
            raise UploadRejected("The file is named .svg but its content is not SVG.")
    if declared == "fig" and not (head.startswith(b"fig-kiwi") or head.startswith(b"PK\x03\x04")):
        raise UploadRejected("The file is named .fig but is neither a native Figma file nor a Figma archive.")
    if declared == "figma_json":
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise UploadRejected("The JSON file could not be read.") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("document"), dict):
            raise UploadRejected("The JSON file is not a Figma file export (no 'document' node).")
    return declared


def validate_raster(data: bytes):
    """Decode-verify a PNG/JPEG and return (width, height). Guards against decompression bombs."""
    try:
        from PIL import Image
    except ImportError:
        raise UploadRejected("Image processing (Pillow) is not installed on the server; images cannot be validated.") from None
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            w, h = probe.size
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Exception:
        raise UploadRejected("The image is corrupt or cannot be decoded.") from None
    if w < 8 or h < 8 or w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
        raise UploadRejected(f"The image is {w}x{h}px; supported sizes are 8 to {MAX_SIDE}px per side.")
    return w, h


# ------------------------------------------------------------------------------ SVG

_FORBIDDEN_TAGS = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video", "canvas", "animate", "set", "animatetransform", "animatemotion"}
_URL_ATTRS = {"href", "xlink:href", "src"}


def sanitize_svg(data: bytes) -> tuple[bytes, list]:
    """Parse with entity/DTD expansion disabled and strip active or external content.

    Returns (sanitized_bytes, removed_notes). Raises UploadRejected for DTD/entity use or invalid XML.
    """
    try:
        from defusedxml import ElementTree as DET
        from defusedxml.common import DefusedXmlException
    except ImportError:
        raise UploadRejected("Safe SVG processing needs the 'defusedxml' package, which is not installed on the server.") from None
    import xml.etree.ElementTree as ET
    try:
        root = DET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException:
        raise UploadRejected("The SVG uses a DTD, entities or external references, which are not allowed.") from None
    except ET.ParseError:
        raise UploadRejected("The SVG is not valid XML.") from None
    removed = []

    def local(tag):
        return tag.rsplit("}", 1)[-1].lower()

    def clean(node):
        for child in list(node):
            if local(child.tag) in _FORBIDDEN_TAGS:
                node.remove(child)
                removed.append(f"removed <{local(child.tag)}>")
                continue
            clean(child)
        for name in list(node.attrib):
            lname = name.rsplit("}", 1)[-1].lower()
            value = node.attrib[name].strip().lower()
            if lname.startswith("on"):
                del node.attrib[name]
                removed.append(f"removed attribute {lname}")
            elif lname in ("href", "src") and value and not value.startswith("#") and not value.startswith("data:image/"):
                del node.attrib[name]
                removed.append(f"removed external reference {lname}")
            elif "javascript:" in value or ("url(" in value and re.search(r"url\(\s*['\"]?(?!#)", value)):
                del node.attrib[name]
                removed.append(f"removed attribute {lname} with external or scripted URL")
        if local(node.tag) == "style" and node.text:
            node.text = re.sub(r"@import[^;]*;?", "", node.text)
            node.text = re.sub(r"url\(\s*['\"]?(?!#|data:image/)[^)]*\)", "url()", node.text)

    clean(root)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    out = ET.tostring(root, encoding="utf-8")
    return out, removed


# ------------------------------------------------------------------------------ .fig

def inspect_fig(data: bytes) -> dict:
    """Describe a native .fig file honestly.

    A .fig is either a proprietary 'fig-kiwi' binary or a ZIP archive whose scene graph is stored in the same
    proprietary binary (canvas.fig). Decoding that scene graph is not implemented, so structural properties are
    NEVER produced from it. What can be read safely is reported: the container, the archive's entry names,
    the optional meta.json fields and the thumbnail image.
    """
    result = {"container": "unknown", "structural_available": False, "entries": [], "meta": {}, "thumbnail": None, "notes": []}
    if data.startswith(b"fig-kiwi"):
        result["container"] = "fig-kiwi"
        result["notes"].append("Native binary Figma file (fig-kiwi). Its scene graph cannot be decoded here.")
    elif data.startswith(b"PK\x03\x04"):
        result["container"] = "zip"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ZIP_ENTRIES:
                    raise UploadRejected("The .fig archive contains too many entries.")
                if sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
                    raise UploadRejected("The .fig archive expands beyond the safe size limit.")
                result["entries"] = [{"name": i.filename[:200], "size": i.file_size} for i in infos[:200]]
                names = {i.filename: i for i in infos}
                if "meta.json" in names and names["meta.json"].file_size < 1_000_000:
                    try:
                        meta = json.loads(archive.read("meta.json").decode("utf-8", "replace"))
                        for key in ("file_name", "client_meta", "version", "exported_at"):
                            if key in meta and isinstance(meta[key], (str, int, float)):
                                result["meta"][key] = str(meta[key])[:200]
                    except ValueError:
                        result["notes"].append("meta.json in the archive could not be parsed.")
                if "thumbnail.png" in names and names["thumbnail.png"].file_size <= 10 * 1024 * 1024:
                    thumb = archive.read("thumbnail.png")
                    if thumb.startswith(b"\x89PNG\r\n\x1a\n"):
                        result["thumbnail"] = thumb
                if "canvas.fig" not in names:
                    result["notes"].append("The archive has no canvas.fig scene graph.")
        except zipfile.BadZipFile:
            raise UploadRejected("The .fig file looks like an archive but is corrupt.") from None
    result["notes"].append("Structural analysis (sizes, colours, typography) is unavailable for native .fig files; "
                           "connect the design by Figma URL or upload a Figma JSON export / PNG export to enable it.")
    return result
