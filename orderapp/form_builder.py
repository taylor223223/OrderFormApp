"""Builds fillable PDF order forms for products that have no official company form,
in the same look as the company's Blind Order Form: AIS logo, black title bar, cyan
fill-in boxes, navy line bars, checkbox lists, comments, and the same footer with the
"Email Form" button. The logo and footer are copied straight from the company form.

Widget names follow a fixed pattern so catalog.py can map to them without coordinates:
  text:      L{n}.{key}           checkbox: L{n}.{key}.{option-slug}
  photo:     L{n}.photo           (empty read-only box; the app drops the product photo in)
Run `python -m orderapp.form_builder` to rebuild the PDFs after changing GENERATED in catalog.py.
"""
import os
import re

import pymupdf as fitz

SOURCE = "OF-Horizontal Blind Order Form.pdf"   # logo + footer come from here
L, R = 27, 585                                     # frame left/right
TOP_LINES, BOTTOM_LINES = 186, 676                 # area for line items
CYAN = (0.655, 1.0, 1.0)
NAVY = (0.0, 0.0, 0.4)
GREY = (0.851, 0.851, 0.851)
PHOTO_W = 92
ROW = 12.5            # checkbox row height
FIELD_H = 15
EMAIL_JS = ('this.mailDoc({bUI: true, cTo: "orders@apartmentinterior.net", cCc: "Taylor@ApartmentInterior.net", '
            'cSubject: "Order Form - " + this.documentFileName, cMsg: "Completed order form attached."});')

HEADER_NAMES = {"name": "Name", "acct": "Acct #", "address": "Addr", "city": "City", "state": "State",
                "zip": "Zip", "mgmt": "Mgmt", "phone": "Phone", "po": "PO", "date": "Date"}
TOP_KEYS = ("qty", "item_no", "unit", "room")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def tname(n, key):
    return f"L{n}.{key}"


def cname(n, key, opt):
    return f"L{n}.{key}.{slug(opt)}"


def _opts(f):
    return [o[0] if isinstance(o, (list, tuple)) else o for o in f.get("options", [])]


def _parts(fields):
    """Split a line's fields into the form's sections."""
    other_fields = {f.get("other_field") for f in fields if f.get("other_field")}
    top = [f for f in fields if f["key"] in TOP_KEYS and f["kind"] == "text"]
    choices = [f for f in fields if f["kind"] == "choice"]
    meas = [f for f in fields if f["kind"] == "text" and f.get("measure") and f["key"] not in TOP_KEYS]
    other = [f for f in fields if f["kind"] == "text" and not f.get("measure") and f["key"] not in TOP_KEYS
             and f["key"] not in other_fields]
    comments = next((f for f in fields if f["key"] == "line_comments"), None)
    return top, choices, meas, other, comments


def _wide(f):
    return any(len(o) > 34 for o in _opts(f))


def _choice_rows(choices, area_w):
    """Pack checkbox groups 3 across; groups with long option names get a full row (2 columns)."""
    rows, cur = [], []
    for f in choices:
        if _wide(f):
            if cur:
                rows.append(cur)
                cur = []
            rows.append([f])
        else:
            cur.append(f)
            if len(cur) == 3:
                rows.append(cur)
                cur = []
    if cur:
        rows.append(cur)

    def h(row):
        if len(row) == 1 and _wide(row[0]):
            return 14 + ROW * -(-len(_opts(row[0])) // 2)
        return 14 + ROW * max(len(_opts(f)) for f in row)
    return [(row, h(row)) for row in rows]


def block_height(fields):
    top, choices, meas, other, comments = _parts(fields)
    area_w = R - L - PHOTO_W
    h = 10 + 22                                   # navy bar + quantity row
    h += sum(rh + 4 for _, rh in _choice_rows(choices, area_w))
    h += -(-len(meas) // 3) * 19 + (12 if meas else 0)
    h += -(-len(other) // 2) * 19
    h += 20 if comments else 0
    return h + 6


def per_page(fields):
    return max(1, int((BOTTOM_LINES - TOP_LINES) // block_height(fields)))


# ---------------------------------------------------------------- drawing helpers
def _label(page, x, y, text, size=9, bold=True, underline=False):
    font = "hebo" if bold else "helv"
    page.insert_text((x, y), text, fontsize=size, fontname=font, color=(0, 0, 0))
    if underline:
        w = fitz.get_text_length(text, fontname=font, fontsize=size)
        page.draw_line((x, y + 1.5), (x + w, y + 1.5), color=(0, 0, 0), width=0.8)
    return fitz.get_text_length(text, fontname=font, fontsize=size)


_PENDING = []   # widgets are added after all printed text (keeps the page font intact)


def _text_widget(page, name, rect, size=9, readonly=False, fill=CYAN):
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.field_name = name
    w.rect = fitz.Rect(rect)
    w.text_font = "Helv"
    w.text_fontsize = size
    w.fill_color = fill
    w.border_color = (0, 0, 0) if fill else None
    w.border_width = 0.5 if fill else 0
    if readonly:
        w.field_flags = fitz.PDF_FIELD_IS_READ_ONLY
    _PENDING.append(w)


def _check_widget(page, name, x, y):
    w = fitz.Widget()
    w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
    w.field_name = name
    w.rect = fitz.Rect(x, y - 8, x + 8.5, y + 0.5)
    w.fill_color = (1, 1, 1)
    w.border_color = (0, 0, 0)
    w.border_width = 0.6
    w.field_value = False
    _PENDING.append(w)


def _field(page, x, y, label, name, width):
    """LABEL: [cyan box] on one baseline."""
    lw = _label(page, x, y, label.upper() + ":", 8.5, underline=True)
    _text_widget(page, name, (x + lw + 4, y - 10, x + width - 6, y + 3))


def _page_frame(page, src, title, n_page, total):
    page.draw_rect(fitz.Rect(L, 7, R, 755), color=(0, 0, 0), width=1.5)
    page.draw_rect(fitz.Rect(386, 7, R, 38), color=None, fill=(0, 0, 0))
    size = 18
    while fitz.get_text_length(title, fontname="hebo", fontsize=size) > R - 386 - 10:
        size -= 0.5
    tw = fitz.get_text_length(title, fontname="hebo", fontsize=size)
    page.insert_text((386 + (R - 386 - tw) / 2, 29), title, fontsize=size, fontname="hebo", color=(1, 1, 1))
    page.draw_line((386, 38), (386, 130), color=(0, 0, 0), width=0.8)
    for i, (key, lab) in enumerate((("name", "CUSTOMER NAME:"), ("po", "P.O. NUMBER:"), ("date", "DATE:"))):
        y = 50 + i * 30
        _label(page, 388, y, lab, 10)
        _text_widget(page, HEADER_NAMES[key], (386, y + 3, R, y + 18))
    # rest of the customer info (the Door form has these too)
    page.draw_rect(fitz.Rect(L, 132, R, 142), color=None, fill=NAVY)
    y = 156
    x = L + 3
    for key, lab, w in (("acct", "Acct #", 100), ("address", "Address", 270), ("mgmt", "Mgmt Co.", 188)):
        _field(page, x, y, lab, HEADER_NAMES[key], w)
        x += w
    y = 175
    x = L + 3
    for key, lab, w in (("city", "City", 170), ("state", "State", 80), ("zip", "Zip", 110), ("phone", "Phone", 198)):
        _field(page, x, y, lab, HEADER_NAMES[key], w)
        x += w
    if total > 1:
        page.insert_text((L + 3, 18), f"Page {n_page} of {total}", fontsize=7, fontname="helv")


def _draw_block(page, n, fields, y0):
    top, choices, meas, other, comments = _parts(fields)
    area_w = R - L - PHOTO_W
    page.draw_rect(fitz.Rect(L, y0, R, y0 + 9), color=None, fill=NAVY)
    y = y0 + 23
    x = L + 3
    widths = {"qty": 120, "item_no": 120, "unit": 120, "room": 190}
    for f in top:
        lab = {"qty": "Quantity", "item_no": "Item #", "unit": "Unit #", "room": "Location"}[f["key"]]
        _field(page, x, y, lab, tname(n, f["key"]), widths[f["key"]])
        x += widths[f["key"]]
    y += 5
    page.draw_rect(fitz.Rect(L, y, R, y + 4), color=None, fill=GREY)
    photo_top = y + 6
    y += 4
    col_w = area_w / 3
    for row, rh in _choice_rows(choices, area_w):
        if len(row) == 1 and _wide(row[0]):
            f = row[0]
            _label(page, L + 3, y + 11, f["label"].upper() + ":", 8.5, underline=True)
            opts = _opts(f)
            half = -(-len(opts) // 2)
            for i, o in enumerate(opts):
                cx = L + 14 + (i // half) * (area_w / 2)
                cy = y + 14 + ROW * (i % half) + 9
                _check_widget(page, cname(n, f["key"], o), cx, cy)
                page.insert_text((cx + 11, cy), o, fontsize=7.5, fontname="helv")
        else:
            for c, f in enumerate(row):
                gx = L + 3 + c * col_w
                _label(page, gx, y + 11, f["label"].upper() + ":", 8.5, underline=True)
                for i, o in enumerate(_opts(f)):
                    cx, cy = gx + 12, y + 14 + ROW * i + 9
                    _check_widget(page, cname(n, f["key"], o), cx, cy)
                    fs = 8.5
                    while fitz.get_text_length(o, fontname="helv", fontsize=fs) > col_w - 30 and fs > 6:
                        fs -= 0.5
                    page.insert_text((cx + 11, cy), o, fontname="helv", fontsize=fs)
                    if o == f.get("other_option") and f.get("other_field"):
                        ow = fitz.get_text_length(o, fontname="helv", fontsize=8.5)
                        _text_widget(page, tname(n, f["other_field"]),
                                     (cx + 14 + ow, cy - 9, gx + col_w - 6, cy + 2), size=8)
        y += rh + 4
    if meas:
        page.draw_rect(fitz.Rect(L, y, L + area_w, y + 3), color=None, fill=GREY)
        _label(page, L + 3, y + 12, "MEASUREMENTS:", 8.5, underline=True)
        y += 12
        for i, f in enumerate(meas):
            mx = L + 3 + (i % 3) * col_w
            my = y + 17 + (i // 3) * 19
            _text_widget(page, tname(n, f["key"]), (mx, my - 11, mx + 52, my + 3))
            lab = f["label"]
            size = 8
            while fitz.get_text_length(lab, fontname="helv", fontsize=size) > col_w - 62 and size > 5.5:
                size -= 0.5
            page.insert_text((mx + 56, my - 1), lab, fontsize=size, fontname="helv")
        y += -(-len(meas) // 3) * 19
    for i, f in enumerate(other):
        ox = L + 3 + (i % 2) * (area_w / 2)
        oy = y + 15 + (i // 2) * 19
        _field(page, ox, oy, f["label"].replace(" *", "*"), tname(n, f["key"]), area_w / 2)
    y += -(-len(other) // 2) * 19
    if any(f.get("photos") for f in fields):
        _text_widget(page, tname(n, "photo"), (R - PHOTO_W + 4, photo_top, R - 4, photo_top + 84),
                     readonly=True, fill=None)
    if comments:
        y += 16
        lw = _label(page, L + 3, y, "COMMENTS:", 8.5, underline=True)
        _text_widget(page, tname(n, "line_comments"), (L + lw + 10, y - 11, R, y + 3))
    return y + 6


def build(spec, out_path, src_path):
    """Draw one form template (one page) from its catalog spec."""
    src = fitz.open(src_path)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    _page_frame(page, src, spec.get("form_title") or spec["title"].upper(), 1, 1)
    y = TOP_LINES
    for n, fields in enumerate(spec["blocks"]):
        y = _draw_block(page, n, fields, y)
    assert y <= BOTTOM_LINES + 2, (spec["title"], y)
    # sales rep + general comments (like the Door form)
    y = BOTTOM_LINES + 14
    lw = _label(page, L + 3, y, "SALES REP:", 8.5, underline=True)
    _text_widget(page, "Sales Rep", (L + lw + 8, y - 11, L + 200, y + 3))
    lw2 = _label(page, L + 210, y, "COMMENTS:", 8.5, underline=True)
    for i in range(3):
        _text_widget(page, f"Comments{i + 1}", (L + 216 + lw2, y - 11 + i * 22, R - 4, y + 3 + i * 22))
    # real "Email Form" button, same script as the company forms
    b = fitz.Widget()
    b.field_type = fitz.PDF_WIDGET_TYPE_BUTTON
    b.field_name = "Email Completed Form Button"
    b.rect = fitz.Rect(505, 768, 596, 788)
    b.button_caption = "Email Form"
    b.fill_color = (0.2, 0.4, 0.7)
    b.text_color = (1, 1, 1)
    b.text_fontsize = 8
    b.script = EMAIL_JS
    _PENDING.append(b)
    # logo + footer copied from the company form last (copying first breaks the page's own fonts)
    page.show_pdf_page(fitz.Rect(68, 22, 337, 121), src, 0, clip=fitz.Rect(68, 22, 337, 121))
    page.show_pdf_page(fitz.Rect(0, 760, 500, 792), src, 0, clip=fitz.Rect(0, 760, 500, 792))
    while _PENDING:
        page.add_widget(_PENDING.pop(0))
    doc.set_metadata({"title": spec["title"], "creator": "Apartment Interior Supply"})
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    src.close()


def build_all():
    from .catalog import FORMS
    from .paths import resource_path
    forms_dir = resource_path("forms")
    for fk, spec in FORMS.items():
        if spec.get("app_made"):
            build(spec, os.path.join(forms_dir, spec["file"]), os.path.join(forms_dir, SOURCE))
            print("built", spec["file"])


if __name__ == "__main__":
    build_all()
