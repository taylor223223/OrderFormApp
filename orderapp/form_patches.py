"""Adds our newer product options onto the company's own order form PDFs.

The company PDFs are kept untouched in forms/company/. This script writes the updated copies
the app prints on (same file names, in forms/). Everything else on the company forms stays as is:
new checkboxes and boxes are added in matching fonts, next to the options already there.
Run `python -m orderapp.form_patches` after changing anything here.
"""
import os
import re
import shutil

import pymupdf as fitz

from .paths import resource_path

FONTS = "/usr/share/fonts/truetype"
ARIAL = os.path.join(FONTS, "liberation", "LiberationSans-Regular.ttf")       # same metrics as Arial
ARIAL_B = os.path.join(FONTS, "liberation", "LiberationSans-Bold.ttf")
CARLITO = os.path.join(FONTS, "crosextra", "Carlito-Regular.ttf")              # same metrics as Calibri
CARLITO_B = os.path.join(FONTS, "crosextra", "Carlito-Bold.ttf")
GAP = 4            # points between a checkbox and its words
CYAN = (0.655, 1.0, 1.0)

NEW_DOOR_TYPES = ["5 Panel", "2 Panel", "2 Panel Arch", "2 Panel Arch Plank", "5 Panel Shaker",
                  "3 Panel Shaker Equal", "3 Panel Shaker Craftsman", "2 Panel Shaker", "1 Panel Shaker",
                  "HC Primecoat"]
HARDWARE_STYLES = ["Marina", "Soma", "Lombard", "Sea Cliff", "Tiburon"]
HARDWARE_TYPES = ["Passage", "Privacy", "Entry"]
HARDWARE_FINISHES = ["Satin Nickel", "Matte Black"]
NEW_BLIND_STYLES = ['Basswood 2"', 'Basswood 2-1/2"', '1" Metal Plus Mini', '2" Metal Plus']
SUNSCREEN_COLORS = ["Black", "Brown", "Beige", "Stucco", "Gray", "Dark Bronze"]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


# widget names used by catalog.py
def door_style_name(t):
    return f"DoorStyle {slug(t)}.0"


def hw_name(group, t):
    return f"Hardware {group} {slug(t)}.0"


class Patcher:
    def __init__(self, page, regular, bold, size=10, box=10):
        self.page, self.size, self.box = page, size, box
        self.reg, self.bold = regular, bold
        self.widgets = []
        self.new_names = []

    def text(self, x, y, s, size=None, bold=False):
        size = size or self.size
        font = self.bold if bold else self.reg
        self.page.insert_text((x, y), s, fontsize=size, fontname="AISB" if bold else "AISR", fontfile=font)
        return fitz.Font(fontfile=font).text_length(s, size)

    def width(self, s, size=None, bold=False):
        return fitz.Font(fontfile=self.bold if bold else self.reg).text_length(s, size or self.size)

    def checkbox(self, name, x, y_base, size=None):
        """Box with its bottom on the text baseline (like the company forms)."""
        b = self.box if size is None else size
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.field_name = name
        w.rect = fitz.Rect(x, y_base - b + 1, x + b, y_base + 1)
        w.border_color = (0, 0, 0)
        w.border_width = 0.8
        w.fill_color = (1, 1, 1)
        w.field_value = False
        self.widgets.append(w)
        self.new_names.append(name)
        return b

    def field(self, name, rect, fill=None, size=10, underline=True):
        if underline and not fill:
            r = fitz.Rect(rect)
            self.page.draw_line((r.x0, r.y1), (r.x1, r.y1), color=(0, 0, 0), width=0.6)
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.rect = fitz.Rect(rect)
        w.text_font = "Helv"
        w.text_fontsize = size
        if fill:
            w.fill_color = fill
        self.widgets.append(w)
        self.new_names.append(name)

    def option(self, name, x, y, label, size=None):
        b = self.checkbox(name, x, y)
        return b + GAP + self.text(x + b + GAP, y, label, size) + 9

    def flow(self, x, y, items, x_max, size=None):
        """Lay out a row of ("label", text) / ("cb", name, text) / ("field", name, width) items,
        shrinking the type if the row would run past x_max."""
        size = size or self.size
        while True:
            need = 0
            for it in items:
                if it[0] == "label":
                    need += self.width(it[1], size) + 6
                elif it[0] == "cb":
                    need += self.box + GAP + self.width(it[2], size) + 9
                else:
                    need += it[2] + 8
            if x + need <= x_max or size <= 7:
                break
            size -= 0.5
        for it in items:
            if it[0] == "label":
                x += self.text(x, y, it[1], size) + 6
            elif it[0] == "cb":
                x += self.option(it[1], x, y, it[2], size)
            else:
                self.field(it[1], (x, y - 10, x + it[2], y + 2))
                x += it[2] + 8
        return x

    def finish(self, doc):
        for w in self.widgets:
            self.page.add_widget(w)
        _keep_clear_buttons(doc, self.new_names)


def _keep_clear_buttons(doc, new_names):
    """The company forms' "Clear Name" button resets every field EXCEPT a listed set of item fields.
    Add our new item fields to that list so clearing the name doesn't wipe them."""
    for x in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(x, "S") != ("name", "/ResetForm"):
                continue
            typ, val = doc.xref_get_key(x, "Fields")
        except Exception:   # noqa: BLE001
            continue
        if typ != "array" or "(Name)" in val:
            continue   # that's the Clear Items button (it excludes the header) - nothing to do
        extra = " ".join("(" + n.replace("(", "\\(").replace(")", "\\)") + ")" for n in new_names)
        doc.xref_set_key(x, "Fields", val[:-1] + " " + extra + "]")


def _heading_box(p, y_head, y_top, y_bot, title):
    p.text(39, y_head, title, 12, bold=True)
    p.page.draw_rect(fitz.Rect(38.3, y_top, 562.1, y_bot), color=(0, 0, 0), width=1.9)


# --------------------------------------------------------------------------- forms
DOOR_SPLIT = 275.9      # just under the company's door style rows
DOOR_ROW = 11.5
DOOR_STYLE_COLS = [     # same checkbox columns as the company's door types
    (53.1, ["2 Panel", "5 Panel", "2 Panel Arch", "2 Panel Arch Plank", "HC Primecoat"]),
    (237.0, ["1 Panel Shaker", "2 Panel Shaker", "3 Panel Shaker Equal", "3 Panel Shaker Craftsman",
             "5 Panel Shaker"]),
]
def door_added_height():
    """How far the company Door / Pre-Hung forms' lower half moved down (catalog overlays use it)."""
    rows_style = max(len(c[1]) for c in DOOR_STYLE_COLS)
    return rows_style * DOOR_ROW + 2 + 3 * DOOR_ROW + 2.5


PHOTO_X = 462          # photo column (door photo on top, hardware photo under it)
COMMENTS_DROP = 10     # the Comments / Sales Rep boxes move down a little to make room


def patch_door(doc, prehung=False):
    """New door types go right under the company's door types (same box), then pre-hung and door
    hardware, with a photo column on the right. Everything below moves down to make room."""
    page = doc[0]
    src = fitz.open(doc.name)
    p = Patcher(page, ARIAL, ARIAL_B, size=10.02, box=10)
    q = Patcher(page, ARIAL, ARIAL_B, size=9.5, box=9)
    rows_style = max(len(c[1]) for c in DOOR_STYLE_COLS)
    y0 = DOOR_SPLIT
    y_hw = y0 + rows_style * DOOR_ROW + 2
    add = door_added_height()
    bottom = y0 + add
    # 1) clear everything below the style rows; it is redrawn lower down at the end
    page.draw_rect(fitz.Rect(30, y0, 580, 600), color=None, fill=(1, 1, 1))
    page.draw_rect(fitz.Rect(30, 676, 580, 766), color=None, fill=(1, 1, 1))
    # 2) more door types, in the same box and columns as the company's
    for x, types in DOOR_STYLE_COLS:
        for i, t in enumerate(types):
            p.option(door_style_name(t), x, y0 + 10 + i * DOOR_ROW, t)
    page.draw_line((37.4, y_hw), (PHOTO_X, y_hw), color=(0, 0, 0), width=0.75)
    # 3) pre-hung + door hardware
    row = []
    if not prehung:
        row = [("label", "Pre-Hung:"), ("cb", "PreHung yes.0", "Yes"), ("cb", "PreHung no.0", "No"),
               ("label", "Threshold (in):"), ("field", "Threshold.0", 45), ("label", "")]
    row += [("label", "Door Hardware:"), ("cb", "Hardware yes.0", "Yes"), ("cb", "Hardware no.0", "No")]
    q.flow(40, y_hw + 9.5, row, PHOTO_X - 4)
    q.flow(40, y_hw + 9.5 + DOOR_ROW, [("label", "Hardware Style:")] +
           [("cb", hw_name("style", t), t) for t in HARDWARE_STYLES], PHOTO_X - 4)
    q.flow(40, y_hw + 9.5 + 2 * DOOR_ROW, [("label", "Hardware Type:")] + [("cb", hw_name("type", t), t)
           for t in HARDWARE_TYPES] + [("label", " Finish:")] +
           [("cb", hw_name("finish", t), t) for t in HARDWARE_FINISHES], PHOTO_X - 4)
    page.draw_line((37.4, bottom - 0.5), (561.1, bottom - 0.5), color=(0, 0, 0), width=0.75)
    page.draw_rect(fitz.Rect(35.5, y0 - 1, 37.4, bottom), color=None, fill=(0, 0, 0))
    page.draw_rect(fitz.Rect(561.1, y0 - 1, 563.0, bottom), color=None, fill=(0, 0, 0))
    page.draw_line((PHOTO_X, y0), (PHOTO_X, bottom - 0.5), color=(0, 0, 0), width=0.75)
    for name, r in (("Photo door_style.0", (PHOTO_X + 4, y0 + 2, 557, bottom - 27)),
                    ("Photo hardware_style.0", (PHOTO_X + 4, bottom - 25, 557, bottom - 3))):
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.rect = fitz.Rect(r)
        w.field_flags = fitz.PDF_FIELD_IS_READ_ONLY
        q.widgets.append(w)
    # 4) the rest of the company form, moved down
    for w in list(page.widgets()):
        r = w.rect
        dy = add if y0 - 1 <= r.y0 < 600 else (COMMENTS_DROP if 676 <= r.y0 < 760 else 0)
        if dy:
            w.rect = fitz.Rect(r.x0, r.y0 + dy, r.x1, r.y1 + dy)
            w.update()
    page.show_pdf_page(fitz.Rect(0, y0 + add, 612, 594 + add), src, 0, clip=fitz.Rect(0, y0, 612, 594))
    page.show_pdf_page(fitz.Rect(0, 676 + COMMENTS_DROP, 612, 756 + COMMENTS_DROP), src, 0,
                       clip=fitz.Rect(0, 676, 612, 756))
    for w in q.widgets:
        page.add_widget(w)
    p.new_names += q.new_names
    p.finish(doc)


def patch_bypass(doc):
    page = doc[0]
    p = Patcher(page, ARIAL, ARIAL_B, size=9.5, box=9)
    _heading_box(p, 645, 649, 680, "Mirror Frame:")
    for i, y in enumerate((661, 675)):
        p.flow(42, y, [("label", f"Door {i + 1}:"), ("cb", f"Mirror yes.{i}", "Yes"), ("cb", f"Mirror no.{i}", "No"),
                       ("label", "Length (in):"), ("field", f"Mirror length.{i}", 55),
                       ("label", "Height (in):"), ("field", f"Mirror height.{i}", 55),
                       ("label", "Finish:"), ("field", f"Mirror finish.{i}", 120)], 556)
        if i == 0:
            page.draw_line((38.3, 665), (562.1, 665), color=(0, 0, 0), width=0.75)
    p.finish(doc)


SCREEN_DOOR_SUFFIX = [".0", ".1.0", ".1.1.0", ".1.1.1"]
WS_SUFFIX = [".0", ".1.0", ".1.1"]


def patch_screen_door(doc):
    page = doc[0]
    p = Patcher(page, ARIAL, ARIAL_B, size=10.02, box=7.8)
    ws = {w.field_name: w.rect for w in page.widgets()}
    for s in SCREEN_DOOR_SUFFIX:
        rb, rw = ws[f"Bronze{s}"], ws[f"White{s}"]
        p.option(f"Tan{s}", 490, rb.y1 - 1, "Tan")
        p.option(f"Gray{s}", 490, rw.y1 - 1, "Gray")
    p.finish(doc)


def patch_window_screen(doc):
    page = doc[0]
    p = Patcher(page, ARIAL, ARIAL_B, size=10.02, box=9)
    ws = {w.field_name: w for w in page.widgets()}
    for s in WS_SUFFIX:
        ra = ws[f"FinishAlmond{s}"].rect
        p.option(f"FinishTan{s}", 404, ra.y1 - 0.5, "Tan")
        # second "Other Modifications" line becomes the sunscreen line
        r2 = ws[f"OtherModTXT2{s}"].rect
        page.delete_widget(ws[f"OtherModTXT2{s}"])
        y = r2.y1 - 2
        p.flow(40, y, [("label", "Sunscreen:"), ("cb", f"Suntex80{s}", "Suntex 80"), ("cb", f"Suntex90{s}", "Suntex 90"),
                       ("label", "Color:")] + [("cb", f"SunCol {slug(c)}{s}", c) for c in SUNSCREEN_COLORS], 560)
        ws = {w.field_name: w for w in page.widgets()}
    p.finish(doc)


BLIND_TOP = {1: 193.8, 2: 393.3, 3: 602.6}   # top of the first Style checkbox in each block
BLIND_LOC = ["Bedroom - __________", "Kitchen", "Living Room", "Other ___________________"]
BLIND_LOC_KEYS = ["Bedroom", "Kitchen", "Living Room", "Other"]


def patch_blind(doc):
    """Style / Location / Color: every box sits under its heading with a few points of air before
    its words; Style gets the new blinds as a second column; one clean measurement box."""
    page = doc[0]
    p = Patcher(page, CARLITO, CARLITO_B, size=12, box=8.5)
    white = (1, 1, 1)
    old = ["All Vinyl", "Vinyl Plus", "All Metal", "Faux Wood"]
    shift = 4.6
    for n, y0 in BLIND_TOP.items():
        rows = [y0 + 15 * i for i in range(4)]
        # cover the old boxes and words (they are printed on the page), then redraw them spaced out
        page.draw_rect(fitz.Rect(62, y0 - 7, 240, y0 + 54), color=None, fill=white)
        page.draw_rect(fitz.Rect(294, y0 - 7, 449.2, y0 + 54), color=None, fill=white)
        page.draw_rect(fitz.Rect(440, y0 - 7, 520, y0 + 25), color=None, fill=white)
        for col, (x, labels) in enumerate(((64, old), (140, NEW_BLIND_STYLES))):
            for i, lab in enumerate(labels):
                p.option(f"Blind {n} Style {lab}", x, rows[i] + 8.5, lab)
        for i, (lab, key) in enumerate(zip(BLIND_LOC, BLIND_LOC_KEYS)):
            p.checkbox(f"Blind {n} Location {key}", 296, rows[i] + 8.5)
            p.text(296 + 8.5 + GAP, rows[i] + 8.5 - 1.2, lab)
        for i, lab in enumerate(["Alabaster", "White"]):
            p.checkbox(f"Blind {n} Color {lab}", 443, rows[i] + 8.5)
            p.text(443 + 8.5 + GAP, rows[i] + 8.5 - 1.2, lab)
    for w in list(page.widgets()):
        nm = w.field_name
        if w.field_type_string == "CheckBox" and any(k in nm for k in (" Style ", " Location ", " Color ")):
            page.delete_widget(w)
    for w in list(page.widgets()):
        if w.field_name.endswith("Bedroom #") or w.field_name.endswith("Other (describe)"):
            r = w.rect
            w.rect = fitz.Rect(r.x0 + shift, r.y0, r.x1 + shift, r.y1)
            w.update()
    # one clean measurement box (no line through the middle)
    for d in page.get_drawings():
        r = d["rect"]
        if d.get("fill") is None and r.height < 0.5 and r.width < 70 and \
                (abs(r.x0 - 26.1) < 1 or abs(r.x0 - 240.8) < 1):
            if any("Window" in w.field_name and w.rect.y0 + 5 < r.y0 < w.rect.y1 - 5 for w in page.widgets()):
                page.draw_rect(fitz.Rect(r.x0 + 1.2, r.y0 - 0.9, r.x1 - 1.2, r.y0 + 0.9), color=None, fill=CYAN)
    p.new_names = [x for x in p.new_names if " Style " in x and any(t in x for t in NEW_BLIND_STYLES)]
    p.finish(doc)


PATCHES = {
    "OF-Door.pdf": patch_door,
    "OF-Pre-Hung.pdf": lambda d: patch_door(d, prehung=True),
    "OF-Bypass Door.pdf": patch_bypass,
    "OF-Screen Door.pdf": patch_screen_door,
    "OF-Window Screen.pdf": patch_window_screen,
    "OF-Horizontal Blind Order Form.pdf": patch_blind,
}


def build_all():
    forms = resource_path("forms")
    company = os.path.join(forms, "company")
    os.makedirs(company, exist_ok=True)
    for fn, fix in PATCHES.items():
        src = os.path.join(company, fn)
        if not os.path.exists(src):          # first run: keep the untouched company original
            shutil.copy(os.path.join(forms, fn), src)
        doc = fitz.open(src)
        fix(doc)
        doc.save(os.path.join(forms, fn), garbage=3, deflate=True)
        doc.close()
        print("updated", fn)


if __name__ == "__main__":
    build_all()
