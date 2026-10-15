"""Order photos: phone/camera pictures of field orders, measurements, damage, etc."""
import io
import os
import re
import uuid

from .db import now, q, x
from .paths import data_dir

MAX_SIDE = 3200          # px - full detail for order reference; only bigger camera shots are scaled
JPEG_QUALITY = 90
ALLOWED = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff")



def photo_dir(order_id):
    d = os.path.join(data_dir(), "photos", str(int(order_id)))
    os.makedirs(d, exist_ok=True)
    return d


def _safe(name):
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name or "photo")[:80]


def shrink(raw):
    """Return (bytes, ext). A JPEG that's already upright and not oversized is kept exactly as taken (no
    re-compression); otherwise it is turned upright per EXIF, scaled to MAX_SIDE and saved as a high-quality JPEG."""
    try:
        from PIL import Image, ImageOps
        try:  # iPhone HEIC support if pillow-heif is installed
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        im = Image.open(io.BytesIO(raw))
        try:
            orient = im.getexif().get(0x0112, 1)
        except Exception:  # noqa: BLE001
            orient = 1
        if im.format == "JPEG" and orient in (1, None) and max(im.size) <= MAX_SIDE:
            return raw, ".jpg"
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, subsampling=0)
        return out.getvalue(), ".jpg"
    except Exception:  # noqa: BLE001 - unknown format: keep as-is
        return raw, None


def add_photo(order_id, filename, raw, caption=""):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext and ext not in ALLOWED:
        raise ValueError(f"{filename}: not a photo")
    data, new_ext = shrink(raw)
    base = os.path.splitext(_safe(filename))[0] or "photo"
    fn = f"{base}-{uuid.uuid4().hex[:6]}{new_ext or ext or '.jpg'}"
    path = os.path.join(photo_dir(order_id), fn)
    with open(path, "wb") as fh:
        fh.write(data)
    return x("INSERT INTO order_photos(order_id, filename, path, caption, created) VALUES (?,?,?,?,?)",
             (order_id, filename or fn, path, caption, now()))


def photos_for(order_id):
    return [p for p in q("SELECT * FROM order_photos WHERE order_id=? ORDER BY id", (order_id,))
            if os.path.exists(p["path"])]


def photos_pdf(order_id, out_path, title=""):
    """Combine an order's photos into one PDF (one photo per letter-size page)."""
    import pymupdf as fitz
    doc = fitz.open()
    for p in photos_for(order_id):
        page = doc.new_page(width=612, height=792)
        top = 36
        if title or p["caption"]:
            page.insert_text((36, 30), " - ".join(x for x in [title, p["caption"]] if x)[:110], fontsize=10)
            top = 42
        try:
            page.insert_image(fitz.Rect(36, top, 576, 756), filename=p["path"], keep_proportion=True)
        except Exception:  # noqa: BLE001
            page.insert_text((36, 100), f"(could not embed {p['filename']})", fontsize=10)
    if doc.page_count == 0:
        return None
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path


def append_photo_pages(pdf_bytes, order_id, title=""):
    """Add a "PHOTO REFERENCE" section to the end of an order PDF: one photo per page, as large as the page
    allows (page turns sideways for wide photos). Photos are embedded as saved - no re-compression."""
    photos = photos_for(order_id)
    if not photos:
        return pdf_bytes
    import pymupdf as fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n = len(photos)
    for i, p in enumerate(photos, 1):
        w, h = 612, 792
        try:
            from PIL import Image
            with Image.open(p["path"]) as im:
                if im.size[0] > im.size[1]:
                    w, h = 792, 612
        except Exception:  # noqa: BLE001
            pass
        page = doc.new_page(width=w, height=h)
        page.draw_rect(fitz.Rect(0, 0, w, 40), color=None, fill=(0.93, 0.93, 0.93))
        page.insert_text((28, 25), "PHOTO REFERENCE", fontsize=13, fontname="hebo")
        page.insert_text((175, 25), f"{title}   -   Photo {i} of {n}"[:95], fontsize=10)
        top, bottom = 50, h - 24
        if p["caption"]:
            page.insert_text((28, h - 12), p["caption"][:130], fontsize=10)
            bottom = h - 28
        try:
            page.insert_image(fitz.Rect(24, top, w - 24, bottom), filename=p["path"], keep_proportion=True)
        except Exception:  # noqa: BLE001
            page.insert_text((28, 100), f"(could not add {p['filename']})", fontsize=10)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
