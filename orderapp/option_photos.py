"""Small product photos shown next to a picked option (door style, trim profile, hardware...).

Bundled defaults (cropped from the AIS brochure) live in static/option_photos/<group>/<slug>.jpg.
Photos the user uploads in Settings go to <data_dir>/option_photos/<group>/<slug>.<ext> and win.
"""
import os
import re

from .paths import data_dir, resource_path

EXTS = (".jpg", ".jpeg", ".png", ".webp")

GROUP_LABELS = {
    "door_style": "Door types",
    "hardware_style": "Door hardware",
    "bath_collection": "Bath hardware",
    "cabinet_style": "Cabinet door styles",
    "trim_profile": "Baseboard & casing",
    "closet_type": "Closet / wardrobe / shower doors",
    "mirror_frame": "Mirror frame",
    "patio_trim": "Patio screen door trim colors",
    "sunscreen_trim": "Window / sunscreen trim colors",
    "sunscreen_color": "Sunscreen fabric colors",
}


def slug(label):
    """'6 Panel (Colonist)' -> '6-panel', 'A: #103 Casing...' -> 'a', 'Soma (Matte Black)' -> 'soma'."""
    s = str(label or "").split(" (")[0].split(":")[0]
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def user_dir(group=None):
    d = os.path.join(data_dir(), "option_photos")
    return os.path.join(d, group) if group else d


def bundled_dir(group=None):
    d = resource_path(os.path.join("static", "option_photos"))
    return os.path.join(d, group) if group else d


def _safe(part):
    return re.fullmatch(r"[a-z0-9_-]+", part or "") is not None


def find(group, label_or_slug):
    """Path of the photo for an option, or None."""
    sl = slug(label_or_slug)
    if not (_safe(group) and _safe(sl)):
        return None
    for d in (user_dir(group), bundled_dir(group)):
        for ext in EXTS:
            p = os.path.join(d, sl + ext)
            if os.path.isfile(p):
                return p
    return None


def is_custom(group, label_or_slug):
    p = find(group, label_or_slug)
    return bool(p and p.startswith(user_dir()))


def save_upload(group, label, file_storage):
    sl = slug(label)
    if not (_safe(group) and _safe(sl)):
        raise ValueError("bad photo name")
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    if ext not in EXTS:
        raise ValueError("Use a JPG, PNG or WEBP picture.")
    d = user_dir(group)
    os.makedirs(d, exist_ok=True)
    for e in EXTS:   # replace any older upload for this option
        try:
            os.remove(os.path.join(d, sl + e))
        except OSError:
            pass
    file_storage.save(os.path.join(d, sl + ext))


def remove_upload(group, label):
    sl = slug(label)
    if not (_safe(group) and _safe(sl)):
        return
    for e in EXTS:
        try:
            os.remove(os.path.join(user_dir(group), sl + e))
        except OSError:
            pass


def catalog_groups():
    """{group: [option labels]} for every option list in the catalog that has photos."""
    from .catalog import FORMS, editor_fields
    out = {}
    for fk in FORMS:
        for f in editor_fields(fk):
            g = f.get("photos")
            if g and f.get("options"):
                lst = out.setdefault(g, [])
                for o in f["options"]:
                    lab = o[0] if isinstance(o, (list, tuple)) else o
                    if lab not in ("Other", "No") and lab not in lst:
                        lst.append(lab)
    order = list(GROUP_LABELS)
    return dict(sorted(out.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))
