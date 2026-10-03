"""PDFs for products with no official paper form (roller shades, trim, hardware, cabinets, closet/shower).

The app draws these itself in the same layout as the company forms and embeds the order data
(JSON) in the file, so an emailed-back copy can be read straight into an order again.
"""
import json

import pymupdf as fitz

from .catalog import FORMS, HEADER_FIELD_LABELS
from . import option_photos as photos

EMBED_NAME = "ais-order.json"
PAGE = fitz.paper_rect("letter")
MARGIN = 36

FOOTER = ("Submit completed form: email to orders@apartmentinterior.net  (cc taylor@apartmentinterior.net)  "
          "\u00b7  Fax (480) 964-7610  \u00b7  Phone (480) 964-7600")
ACCENT = (0.78, 0.35, 0.16)
INK = (0.13, 0.13, 0.13)
GREY = (0.42, 0.42, 0.42)


def _visible(f, b):
    from .pdf_fill import _visible as vis
    return vis(f, b)


def _fit(text, width, size, font="helv"):
    while size > 5 and fitz.get_text_length(text, fontname=font, fontsize=size) > width:
        size -= 0.25
    return size


def _cell(page, x, y, w, label, value):
    page.insert_text((x + 3, y + 8), label, fontsize=_fit(label, w - 6, 6.5), fontname="hebo", color=GREY)
    v = str(value)
    page.insert_text((x + 3, y + 19), v, fontsize=_fit(v, w - 6, 9.5), fontname="helv", color=INK)
    page.draw_rect(fitz.Rect(x, y, x + w, y + 23), color=(0.75, 0.75, 0.75), width=0.5)


def _line_items(fields, b):
    out, pics = [], []
    for f in fields:
        if not _visible(f, b) or f["key"] == "line_comments":
            continue
        v = b.get(f["key"])
        if v in (None, ""):
            continue
        if any(x.get("other_field") == f["key"] for x in fields):
            continue   # shown with its dropdown
        if f.get("other_field") and v == f.get("other_option") and b.get(f["other_field"]):
            v = f"Other: {b[f['other_field']]}"
        out.append((f["label"], v))
        if f.get("photos"):
            p = photos.find(f["photos"], v)
            if p:
                pics.append(p)
    if b.get("_room") and not b.get("room"):
        out.insert(0, ("Room", b["_room"]))
    if b.get("line_comments"):
        out.append(("Comments", b["line_comments"]))
    return out, pics


class _Pager:
    def __init__(self, doc, title):
        self.doc, self.title, self.page, self.n = doc, title, None, 0
        self.new()

    def new(self):
        self.page = self.doc.new_page(width=PAGE.width, height=PAGE.height)
        self.n += 1
        pg = self.page
        pg.draw_rect(fitz.Rect(0, 0, PAGE.width, 8), color=None, fill=ACCENT)
        pg.insert_text((MARGIN, MARGIN + 14), self.title, fontsize=17, fontname="hebo", color=ACCENT)
        pg.insert_text((MARGIN, MARGIN + 28), "Apartment Interior Supply  \u00b7  Tempe, AZ", fontsize=8.5,
                       fontname="helv", color=GREY)
        pg.insert_text((MARGIN, PAGE.height - 22), FOOTER, fontsize=_fit(FOOTER, PAGE.width - 2 * MARGIN, 7.5),
                       fontname="helv", color=GREY)
        self.y = MARGIN + 40

    def need(self, h):
        if self.y + h > PAGE.height - 40:
            self.new()


def fill_generated(form_key, data):
    spec = FORMS[form_key]
    doc = fitz.open()
    pg = _Pager(doc, spec["title"])
    W = PAGE.width - 2 * MARGIN
    h = data.get("header") or {}
    # customer box: 3 columns
    items = [(HEADER_FIELD_LABELS[k], h.get(k, "")) for k in
             ["name", "acct", "po", "address", "city", "date", "mgmt", "phone", "state", "zip"]]
    items.append(("Sales Rep", data.get("sales_rep") or ""))
    widths = [W * 0.45, W * 0.30, W * 0.25]
    for r in range(0, len(items), 3):
        x = MARGIN
        for (lab, val), w in zip(items[r:r + 3], widths):
            _cell(pg.page, x, pg.y, w, lab, val)
            x += w
        pg.y += 23
    pg.y += 10
    fields = spec["blocks"][0]
    blocks = [b for b in (data.get("blocks") or []) if any(str(v).strip() for v in b.values() if v is not None)]
    PIC_W = 92
    for n, b in enumerate(blocks, 1):
        cells, pics = _line_items(fields, b)
        cols = 3
        cw = (W - (PIC_W + 6 if pics else 0)) / cols
        rows = max(1, -(-len(cells) // cols))
        pic_h = min(len(pics), 2) * 84
        hgt = 16 + max(rows * 23, pic_h)
        pg.need(hgt + 6)
        page = pg.page
        page.draw_rect(fitz.Rect(MARGIN, pg.y, MARGIN + W, pg.y + 14), color=None, fill=ACCENT)
        page.insert_text((MARGIN + 4, pg.y + 10.5), f"Line {n}", fontsize=9, fontname="hebo", color=(1, 1, 1))
        top = pg.y + 16
        for i, (lab, val) in enumerate(cells):
            _cell(page, MARGIN + (i % cols) * cw, top + (i // cols) * 23, cw, lab, val)
        for i, p in enumerate(pics[:2]):
            r = fitz.Rect(MARGIN + W - PIC_W, top + i * 84, MARGIN + W, top + i * 84 + 80)
            try:
                page.insert_image(r, filename=p, keep_proportion=True)
            except Exception:
                pass
        pg.y += hgt + 8
    if not blocks:
        pg.page.insert_text((MARGIN, pg.y + 10), "No line items.", fontsize=9, fontname="helv", color=GREY)
        pg.y += 20
    comments = str(data.get("comments") or "")
    pg.need(70)
    page = pg.page
    page.draw_rect(fitz.Rect(MARGIN, pg.y, MARGIN + W, pg.y + 14), color=None, fill=ACCENT)
    page.insert_text((MARGIN + 4, pg.y + 10.5), "Comments", fontsize=9, fontname="hebo", color=(1, 1, 1))
    box = fitz.Rect(MARGIN, pg.y + 14, MARGIN + W, pg.y + 64)
    page.draw_rect(box, color=(0.75, 0.75, 0.75), width=0.5)
    warns = []
    if comments:
        size = 9
        while size >= 6 and page.insert_textbox(box + (4, 3, -4, -2), comments, fontsize=size,
                                                 fontname="helv", color=INK) < 0:
            size -= 1
        if size < 6:
            warns.append("Comments too long for the box; shortened on the PDF (full text is saved in the app).")
    if pg.n > 1:
        for i, p in enumerate(doc):
            p.insert_text((PAGE.width - MARGIN - 50, MARGIN + 14), f"Page {i + 1} of {pg.n}",
                          fontsize=7.5, fontname="helv", color=GREY)
    payload = {"form_key": form_key, "data": {k: data.get(k) for k in ("header", "sales_rep", "comments", "blocks")}}
    doc.embfile_add(EMBED_NAME, json.dumps(payload).encode("utf-8"), filename=EMBED_NAME)
    doc.set_metadata({"title": spec["title"], "creator": "AIS Sales Support"})
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out, warns


def read_generated(doc):
    """(form_key, data) from a PDF this module made, else None."""
    try:
        if EMBED_NAME not in doc.embfile_names():
            return None
        payload = json.loads(doc.embfile_get(EMBED_NAME).decode("utf-8"))
        fk = payload.get("form_key")
        if fk not in FORMS:
            return None
        d = payload.get("data") or {}
        d.setdefault("header", {})
        d.setdefault("blocks", [])
        return fk, d
    except Exception:
        return None
