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
                page.insert_text((cx + 12.5, cy), o, fontsize=7.5, fontname="helv")
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
                    page.insert_text((cx + 12.5, cy), o, fontname="helv", fontsize=fs)
                    if o == f.get("other_option") and f.get("other_field"):
                        ow = fitz.get_text_length(o, fontname="helv", fontsize=8.5)
                        _text_widget(page, tname(n, f["other_field"]),
                                     (cx + 16 + ow, cy - 9, gx + col_w - 6, cy + 2), size=8)
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


# ============================================================================ Door-form style
# Same look as the company's Door / Bi-Pass / Screen forms: starts from the company Bi-Pass form
# (logo, Fax/Phone, Clear buttons, title bar, gray header table, Comments, Sales Representative,
# footer and Email Form button all stay), clears the door section and draws this product's sections.
DOOR_SOURCE = os.path.join("company", "OF-Bypass Door.pdf")
D_TOP, D_BOTTOM = 210, 679
D_L, D_R = 38.3, 562.1
D_ROW = 13
D_PHOTO = 92


class _Pen:
    """Measures (and, with a page, draws) door-style rows."""
    def __init__(self, page=None):
        from .form_patches import ARIAL, ARIAL_B, GAP
        self.page, self.reg, self.bold, self.gap = page, ARIAL, ARIAL_B, GAP
        self.size, self.box = 9.5, 9
        self.widgets = []
        self._f = {}

    def w(self, t, size=None, bold=False):
        # Helvetica has the same widths as Arial / Liberation Sans, and is built in (the app measures
        # with it on any PC; the font files are only needed when the form PDFs are rebuilt)
        return fitz.get_text_length(t, fontname="hebo" if bold else "helv", fontsize=size or self.size)

    def text(self, x, y, t, size=None, bold=False, color=(0, 0, 0)):
        if self.page:
            self.page.insert_text((x, y), t, fontsize=size or self.size, fontname="AISB" if bold else "AISR",
                                  fontfile=self.bold if bold else self.reg, color=color)
        return self.w(t, size, bold)

    def cb(self, name, x, y):
        if self.page:
            w = fitz.Widget()
            w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
            w.field_name = name
            w.rect = fitz.Rect(x, y - self.box + 1, x + self.box, y + 1)
            w.border_color, w.border_width, w.fill_color = (0, 0, 0), 0.8, (1, 1, 1)
            w.field_value = False
            self.widgets.append(w)

    def field(self, name, x0, y, x1):
        if self.page:
            self.page.draw_line((x0, y + 2), (x1, y + 2), color=(0, 0, 0), width=0.6)
            w = fitz.Widget()
            w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            w.field_name = name
            w.rect = fitz.Rect(x0, y - 10, x1, y + 2)
            w.text_font, w.text_fontsize = "Helv", 10
            self.widgets.append(w)

    def line(self, y, x1=D_R):
        if self.page:
            self.page.draw_line((D_L, y), (x1 + 2 if x1 < D_R else D_R, y), color=(0, 0, 0), width=0.75)


def _door_info_rows(pen, n, fields, y, x_max):
    """Quantity / Unit / Location row and one row per choice. Returns the y after the last row."""
    top, choices, meas, other, comments = _parts(fields)
    x = D_L + 4
    y += D_ROW
    widths = {"qty": 95, "item_no": 110, "unit": 110, "room": 200}
    labels = {"qty": "Quantity:", "item_no": "Item #:", "unit": "Unit #:", "room": "Location:"}
    for f in top:
        lw = pen.text(x, y, labels[f["key"]])
        pen.field(tname(n, f["key"]), x + lw + 4, y, min(x + widths[f["key"]] - 8, x_max - 4))
        x += widths[f["key"]]
    # short Yes/No-type questions share a row; longer lists get their own row(s)
    rows, cur, cur_w = [], [], 0
    for f in choices:
        small = len(_opts(f)) <= 3 and not f.get("other_field")
        fw = pen.w(f["label"] + ":") + 8 + sum(pen.box + pen.gap + pen.w(o) + 10 for o in _opts(f)) + 14
        if small and cur and cur_w + fw <= x_max - D_L - 8:
            cur.append(f)
            cur_w += fw
            continue
        if cur:
            rows.append(cur)
        cur, cur_w = ([f], fw) if small else ([], 0)
        if not small:
            rows.append([f])
    if cur:
        rows.append(cur)
    for row in rows:
        pen.line(y + 3.5, x_max)
        y += D_ROW
        if len(row) == 1 and not (len(_opts(row[0])) <= 3 and not row[0].get("other_field")) or len(row) == 1:
            f = row[0]
            pen.text(D_L + 4, y, f["label"] + ":")
            x = D_L + 130
            for o in _opts(f):
                ow = pen.box + pen.gap + pen.w(o) + 10
                if x + ow > x_max:
                    y += D_ROW - 1
                    x = D_L + 130
                pen.cb(cname(n, f["key"], o), x, y)
                x += pen.box + pen.gap
                x += pen.text(x, y, o) + 10
                if o == f.get("other_option") and f.get("other_field"):
                    pen.field(tname(n, f["other_field"]), x, y, min(x + 110, x_max - 4))
                    x += 118
        else:
            x = D_L + 4
            for f in row:
                x += pen.text(x, y, f["label"] + ":") + 8
                for o in _opts(f):
                    pen.cb(cname(n, f["key"], o), x, y)
                    x += pen.box + pen.gap
                    x += pen.text(x, y, o) + 10
                x += 14
    return y + 5


def _door_meas_rows(pen, n, fields, y):
    """Measurements three across, then other boxes two across, then the line's comments."""
    top, choices, meas, other, comments = _parts(fields)
    first = True
    for group, per in ((meas, 3), (other, 2)):
        for i in range(0, len(group), per):
            if not first:
                pen.line(y + 3.5)
            first = False
            y += D_ROW
            col = (D_R - D_L) / per
            for j, f in enumerate(group[i:i + per]):
                x0 = D_L + 4 + j * col
                lw = pen.text(x0, y, f["label"] + ":")
                pen.field(tname(n, f["key"]), x0 + lw + 4, y, x0 + col - 10)
    if comments:
        if not first:
            pen.line(y + 3.5)
        y += D_ROW
        lw = pen.text(D_L + 4, y, "Comments:")
        pen.field(tname(n, "line_comments"), D_L + 4 + lw + 4, y, D_R - 6)
    return y + 5


def _door_block(pen, n, fields, y, section, page=None):
    """One line item: '<Section>:' box (with the photo on the right) and 'Measurements:' box."""
    has_photo = any(f.get("photos") for f in fields)
    x_max = D_R - (D_PHOTO if has_photo else 4)
    pen.text(D_L + 1, y + 13, section + ":", 12, bold=True)
    top = y + 17
    end = _door_info_rows(pen, n, fields, top, x_max)
    _, _, meas, other, comments = _parts(fields)
    if comments and not (meas or other):   # nothing to measure: the line's comments go in this box
        pen.line(end - 1.5, x_max)
        end += D_ROW - 5
        lw = pen.text(D_L + 4, end, "Comments:")
        pen.field(tname(n, "line_comments"), D_L + 8 + lw, end, x_max - 6)
        end += 5
    if has_photo:
        end = max(end, top + 92)
        if page:
            page.draw_line((x_max + 2, top), (x_max + 2, end), color=(0, 0, 0), width=0.75)
            w = fitz.Widget()
            w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            w.field_name = tname(n, "photo")
            w.rect = fitz.Rect(x_max + 6, top + 4, D_R - 4, top + 88)
            w.field_flags = fitz.PDF_FIELD_IS_READ_ONLY
            pen.widgets.append(w)
    if page:
        page.draw_rect(fitz.Rect(D_L, top, D_R, end), color=(0, 0, 0), width=1.9)
    if meas or other:
        y = end + 4
        pen.text(D_L + 1, y + 13, "Measurements:", 12, bold=True)
        top = y + 17
        end = _door_meas_rows(pen, n, fields, top)
        if page:
            page.draw_rect(fitz.Rect(D_L, top, D_R, end), color=(0, 0, 0), width=1.9)
    return end


def door_block_height(fields):
    return _door_block(_Pen(), 0, fields, 0, "x")


def door_per_page(fields):
    return max(1, int((D_BOTTOM - D_TOP) // (door_block_height(fields) + 8)))


def build_door_style(spec, out_path, forms_dir):
    from .form_patches import _keep_clear_buttons  # noqa: F401  (same idea, done below)
    doc = fitz.open(os.path.join(forms_dir, DOOR_SOURCE))
    page = doc[0]
    # clear the Bi-Pass door sections (keep header, comments, sales rep, footer, buttons)
    for w in list(page.widgets()):
        if D_TOP - 4 < w.rect.y0 < 680 and w.field_type_string != "Button":
            page.delete_widget(w)
    page.draw_rect(fitz.Rect(30, D_TOP - 3, 582, 681), color=None, fill=(1, 1, 1))
    # title
    page.draw_rect(fitz.Rect(326, 55, 561.6, 70.1), color=None, fill=(0, 0, 0))
    title = spec["title"]
    size = 16
    while fitz.get_text_length(title, fontname="tiro", fontsize=size) > 228:
        size -= 0.5
    tw = fitz.get_text_length(title, fontname="tiro", fontsize=size)
    page.insert_text((326 + (235.6 - tw) / 2, 67.5), title, fontsize=size, fontname="tiro", color=(1, 1, 1))
    pen = _Pen(page)
    y = D_TOP
    section = spec.get("section", "Item Information")
    for n, fields in enumerate(spec["blocks"]):
        y = _door_block(pen, n, fields, y, section, page) + 8
    assert y <= D_BOTTOM + 8, (title, y)
    for w in pen.widgets:
        page.add_widget(w)
    # "Clear Name" button: its exclude-list must be this form's item fields
    names = [w.field_name for w in pen.widgets] + ["Comment1", "Comments2", "Comments3", "SR"]
    for x in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(x, "S") != ("name", "/ResetForm"):
                continue
            typ, val = doc.xref_get_key(x, "Fields")
        except Exception:   # noqa: BLE001
            continue
        if typ == "array" and "(Name)" not in val:
            doc.xref_set_key(x, "Fields", "[" + " ".join("(" + nm + ")" for nm in names) + "]")
    doc.set_metadata({"title": title, "creator": "Apartment Interior Supply"})
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()


def build_all():
    from .catalog import FORMS
    from .paths import resource_path
    forms_dir = resource_path("forms")
    for fk, spec in FORMS.items():
        if not spec.get("app_made"):
            continue
        out = os.path.join(forms_dir, spec["file"])
        if spec.get("style") == "door":
            build_door_style(spec, out, forms_dir)
        else:
            build(spec, out, os.path.join(forms_dir, "company", SOURCE))
        print("built", spec["file"])


if __name__ == "__main__":
    build_all()
