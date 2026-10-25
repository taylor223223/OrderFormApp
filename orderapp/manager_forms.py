"""Customer Order Forms: simple fillable PDFs a property's maintenance manager fills out.

Not the internal rep forms. Each item is a picture of what to measure with the fill-in
boxes sitting right on the arrows, plus simple check-one choices (with pictures where it
helps). The manager emails it to the rep and the order can go in the same day.

    python -m orderapp.manager_forms            -> forms/manager/*.pdf (blank, fillable)
"""
import math
import os

import pymupdf as fitz

HERE = os.path.dirname(__file__)
COMPANY = os.path.join(HERE, "forms", "company", "OF-Horizontal Blind Order Form.pdf")
PHOTOS = os.path.join(HERE, "static", "option_photos")
OUT_DIR = os.path.join(HERE, "forms", "manager")

CYAN = (0.655, 1.0, 1.0)
NAVY = (0.0, 0.0, 0.4)
GREY = (0.85, 0.85, 0.85)
DARK = (0.3, 0.3, 0.3)
WALL = (0.90, 0.88, 0.84)
GLASS = (0.80, 0.91, 0.98)
WOOD = (0.97, 0.97, 0.95)
RED = (0.80, 0.10, 0.10)
BLUE = (0.10, 0.40, 0.80)
GREEN = (0.05, 0.50, 0.25)
L, R = 18, 594
BOTTOM = 756                      # cards stop here (footer below)
CX = L + 378                      # left edge of the choices column
RS = 12.5                         # row spacing of the check-one lists
FRACTIONS = [" ", "1/8", "1/4", "3/8", "1/2", "5/8", "3/4", "7/8"]
REP_EMAIL = "Taylor@ApartmentInterior.net"
EMAIL_JS = ('this.mailDoc({bUI: true, cTo: "%s", cSubject: "Customer Order - " + this.documentFileName, '
            'cMsg: "Measurements attached."});' % REP_EMAIL)

ROOMS = ["Bedroom", "Kitchen", "Living Room", "Other"]
NOT_SURE = "Not sure - match what's there"


class Page:
    def __init__(self, page, dry=False):
        self.p = page
        self.dry = dry
        self.widgets = []          # added last so page fonts stay intact

    # ---- printed things
    def text(self, x, y, s, size=8, bold=False, color=(0, 0, 0), center=False):
        font = "hebo" if bold else "helv"
        tw = fitz.get_text_length(s, fontname=font, fontsize=size)
        if center:
            x -= tw / 2
        self.p.insert_text((x, y), s, fontsize=size, fontname=font, color=color)
        return tw

    def fit(self, s, width, size, bold=False):
        font = "hebo" if bold else "helv"
        while fitz.get_text_length(s, fontname=font, fontsize=size) > width and size > 5.5:
            size -= 0.25
        return size

    def wrap(self, x, y, s, width, size=7.5, lead=9.0):
        words, line, n = s.split(), "", 0
        for w in words:
            t = (line + " " + w).strip()
            if fitz.get_text_length(t, fontname="helv", fontsize=size) > width:
                self.text(x, y + n * lead, line, size)
                line, n = w, n + 1
            else:
                line = t
        if line:
            self.text(x, y + n * lead, line, size)
        return y + (n + 1) * lead

    def rect(self, r, color=DARK, fill=None, width=0.6, dashes=None):
        self.p.draw_rect(fitz.Rect(r), color=color, fill=fill, width=width, dashes=dashes)

    def line(self, a, b, color=DARK, width=0.6, dashes=None):
        self.p.draw_line(fitz.Point(a), fitz.Point(b), color=color, width=width, dashes=dashes)

    def bar(self, y, title, h=14):
        self.rect((L, y, R, y + h), color=None, fill=NAVY)
        self.text(L + 6, y + h - 4, title, 9, bold=True, color=(1, 1, 1))

    def arrow(self, a, b, color=RED, width=1.1, both=True):
        a, b = fitz.Point(a), fitz.Point(b)
        self.p.draw_line(a, b, color=color, width=width)
        for tip, tail in ((a, b), (b, a)) if both else ((b, a),):
            d = tail - tip
            n = abs(d) or 1
            u = fitz.Point(d.x / n, d.y / n)
            v = fitz.Point(-u.y, u.x)
            pts = [tip, tip + u * 6 + v * 3, tip + u * 6 - v * 3, tip]
            self.p.draw_polyline(pts, color=color, fill=color, width=0.5)

    def photo(self, rect, path):
        if os.path.isfile(path):
            self.p.insert_image(fitz.Rect(rect), filename=path, keep_proportion=True)
        self.rect(rect, color=GREY, width=0.5)

    # ---- fill-in things
    def field(self, name, rect, size=9, align=0):
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.rect = fitz.Rect(rect)
        w.text_font, w.text_fontsize = "Helv", size
        w.fill_color, w.border_color, w.border_width = CYAN, (0, 0, 0), 0.6
        w.text_format = fitz.TEXT_ALIGN_CENTER if align else fitz.TEXT_ALIGN_LEFT
        self.widgets.append(w)

    def fraction(self, name, rect):
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_COMBOBOX
        w.field_name = name
        w.rect = fitz.Rect(rect)
        w.choice_values = FRACTIONS
        w.field_value = " "
        w.text_font, w.text_fontsize = "Helv", 8.5
        w.fill_color, w.border_color, w.border_width = CYAN, (0, 0, 0), 0.6
        self.widgets.append(w)

    def inches(self, name, cx, cy, tag=None, tag_color=RED):
        """[ whole ] [ frac v ] "   centered on (cx, cy), optional label above"""
        x = cx - 33
        if tag:
            tw = fitz.get_text_length(tag, fontname="hebo", fontsize=6.5)
            self.rect((cx - tw / 2 - 2, cy - 17.5, cx + tw / 2 + 2, cy - 9), color=None, fill=(1, 1, 1))
            self.text(cx, cy - 11, tag, 6.5, bold=True, color=tag_color, center=True)
        self.rect((x - 2, cy - 8, x + 68, cy + 8), color=None, fill=(1, 1, 1))
        self.field(name, (x, cy - 7, x + 26, cy + 7), size=9, align=1)
        self.fraction(name + " frac", (x + 28, cy - 7, x + 61, cy + 7))
        self.text(x + 62.5, cy + 3, '"', 9, bold=True)

    def check(self, name, x, y, label, size=8, maxw=None):
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.field_name = name
        w.rect = fitz.Rect(x, y - 8.5, x + 9.5, y + 1)
        w.fill_color, w.border_color, w.border_width = (1, 1, 1), (0, 0, 0), 0.8
        w.field_value = False
        self.widgets.append(w)
        if maxw:
            size = self.fit(label, maxw - 13, size)
        return self.text(x + 13, y, label, size)

    def labeled(self, x, y, label, name, width, size=8.5):
        lw = self.text(x, y, label, size, bold=True)
        self.field(name, (x + lw + 4, y - 10, x + width, y + 3))

    def finish(self):
        for w in self.widgets:
            self.p.add_widget(w)


# --------------------------------------------------------------------------- shared parts
def header(pg, subtitle):
    pg.rect((L, 8, R, 760), color=(0, 0, 0), width=1.5)
    pg.rect((352, 8, R, 52), color=None, fill=(0, 0, 0))
    pg.text(473, 26, "CUSTOMER ORDER FORM", 14, bold=True, color=(1, 1, 1), center=True)
    pg.text(473, 44, subtitle.upper(), pg.fit(subtitle.upper(), R - 362, 12.5, True), bold=True,
            color=(1, 1, 1), center=True)
    y = 66
    for lab, name in (("PROPERTY:", "Property"), ("YOUR NAME:", "Manager Name"),
                      ("PHONE:", "Manager Phone"), ("EMAIL:", "Manager Email")):
        pg.labeled(356, y, lab, name, R - 360)
        y += 15
    pg.labeled(356, y, "DATE:", "Date", 470 - 360)
    pg.labeled(478, y, "PO #:", "PO Number", R - 482)


def continued(pg, subtitle):
    pg.rect((L, 8, R, 760), color=(0, 0, 0), width=1.5)
    pg.rect((L, 8, R, 30), color=None, fill=(0, 0, 0))
    pg.text(L + 8, 23, f"CUSTOMER ORDER FORM - {subtitle.upper()}  (continued)", 11, bold=True, color=(1, 1, 1))
    pg.text(R - 128, 23, "Need more? Use another form.", 7, color=(1, 1, 1))


def logo(pg, src):
    # same AIS logo as the company forms (copied last so fonts on this page aren't disturbed)
    pg.p.show_pdf_page(fitz.Rect(40, 18, 309, 117), src, 0, clip=fitz.Rect(68, 22, 337, 121))


def footer(pg, page_no, pages):
    pg.text(L + 4, 773, f"WHEN DONE: click \"Email Form\" (or save and email it) to {REP_EMAIL}.  "
            "Questions? Call (480) 964-7600.", 8, bold=True, color=NAVY)
    pg.text(L + 4, 784, "Apartment Interior Supply  ·  5325 S. Kyrene Rd., Suite 103, Tempe, AZ 85283  ·  "
            f"Page {page_no} of {pages}", 7, color=(0.35, 0.35, 0.35))
    b = fitz.Widget()
    b.field_type = fitz.PDF_WIDGET_TYPE_BUTTON
    b.field_name = f"Email Form {page_no}"
    b.rect = fitz.Rect(505, 765, 594, 787)
    b.button_caption = "Email Form"
    b.fill_color, b.text_color, b.text_fontsize = (0.2, 0.4, 0.7), (1, 1, 1), 9
    b.script = EMAIL_JS
    pg.widgets.append(b)


def how_to(pg, y, steps):
    """4 tiles: (heading, text or None, picture(pg, x, y, w)) - pictures sit at the bottom of the tile."""
    pg.bar(y, "HOW TO MEASURE  (about 2 minutes each)")
    y += 12
    tw = (R - L) / len(steps)
    th = 100
    for i in range(1, len(steps)):
        pg.line((L + i * tw, y), (L + i * tw, y + th), color=GREY, width=1)
    for i, (head, body, pic) in enumerate(steps):
        x = L + i * tw + 8
        pg.text(x, y + 13, head, pg.fit(head, tw - 14, 8.5, True), bold=True, color=NAVY)
        if body:
            pg.wrap(x, y + 25, body, tw - 16, 7.2, 8.6)
        if pic:
            pic(pg, L + i * tw, y + 52, tw)
    return y + th + 4


def card_bar(pg, y, label, k):
    pg.bar(y, label, 18)
    x = max(96, L + 6 + fitz.get_text_length(label, fontname="hebo", fontsize=9) + 14)
    pg.text(x, y + 13, "UNIT #", 8.5, bold=True, color=(1, 1, 1))
    pg.field(f"{k} Unit", (x + 32, y + 3, x + 100, y + 15))
    x += 110
    pg.text(x, y + 13, "HOW MANY OF THIS SAME SIZE?", 8.5, bold=True, color=(1, 1, 1))
    pg.field(f"{k} Qty", (x + 152, y + 3, x + 186, y + 15), align=1)
    pg.text(x + 196, y + 13, "(usually 1)", 7.5, color=(1, 1, 1))
    return y + 18


def choices(pg, k, x, y, groups, width=R - CX - 4):
    """Check-one lists. group = (title, options, cols) or dict(title, opts, cols, other, name).
    Long options (and "Not sure - match what's there") get a row of their own."""
    for g in groups:
        if isinstance(g, tuple):
            g = dict(zip(("title", "opts", "cols"), g))
        cols = g.get("cols", 2)
        cw = width / cols
        y += RS
        pg.text(x, y, g["title"], 8.5, bold=True)
        name = g.get("name", g["title"].split("(")[0].strip(" :").title())
        row, col = 0, 0
        for o in g["opts"]:
            wide = o == NOT_SURE or len(o) > 26
            if wide and col:
                row, col = row + 1, 0
            ox = x + col * cw
            oy = y + RS + row * RS
            span = width if wide else (cw if col < cols - 1 else width - col * cw)
            lw = pg.check(f"{k} {name} {o}", ox, oy, o, 7.8, maxw=span - 2)
            if o in g.get("other", ()):                  # "Other ____"
                pg.field(f"{k} {name} {o} (describe)", (ox + 16 + lw, oy - 9, ox + span - 4, oy + 2), size=8)
            if wide:
                row, col = row + 1, 0
            else:
                col += 1
                if col == cols:
                    row, col = row + 1, 0
        y += (row + (1 if col else 0)) * RS + 3
    return y


def hinge_cut_rows(pg, k, x, y, x1=R - 6):
    """Cabinet doors: concealed hinge bore locations (to the hinge CENTER) and the cup size."""
    for label, name in (("TOP & BOTTOM HINGES - door edge to center:", "Top Hinge"),
                        ("BOTTOM - only if it's different:", "Bottom Hinge"),
                        ("CENTER HINGE (if any) - top of door to center:", "Center Hinge"),
                        ("CUP DIAMETER (if needed):", "Hinge Cup Diameter")):
        y += RS
        lw = pg.text(x, y, label, 7, bold=True)
        pg.field(f"{k} {name}", (x + lw + 4, y - 9, x1, y + 3), size=8)
    pg.text(x, y + 10, 'To hinge center. Same both ends (3" from top = 3" from bottom).', 6.2, color=DARK)
    return y + 14


def notes_row(pg, k, y, x1=CX - 10):
    pg.text(L + 8, y + 3, "NOTES:", 8.5, bold=True)
    pg.field(f"{k} Notes", (L + 46, y - 7, x1, y + 7), size=8.5)
    return y + 10


# --------------------------------------------------------------------------- little pictures
def window(pg, r, inset=12, mullion=True, sill=True):
    """Wall with a window opening in it; returns the opening rect."""
    r = fitz.Rect(r)
    pg.rect(r, color=(0.5, 0.5, 0.5), fill=WALL)
    o = fitz.Rect(r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset - (6 if sill else 0))
    pg.rect(o, color=DARK, fill=(1, 1, 1), width=1)
    g = fitz.Rect(o.x0 + 5, o.y0 + 5, o.x1 - 5, o.y1 - 5)
    pg.rect(g, color=(0.55, 0.55, 0.55), fill=GLASS)
    if mullion:
        pg.line((g.x0 + g.width / 2, g.y0), (g.x0 + g.width / 2, g.y1), color=(0.55, 0.55, 0.55))
    if sill:
        pg.rect((o.x0 - 6, o.y1, o.x1 + 6, o.y1 + 5), color=(0.4, 0.4, 0.4), fill=(0.75, 0.72, 0.68), width=0.5)
    return o


def ruler(pg, x, y, w):
    """1 inch of tape measure with 1/8 marks, showing 35-3/8"."""
    pg.rect((x, y, x + w, y + 22), fill=(1.0, 0.86, 0.2))
    step = (w - 16) / 8
    for i in range(9):
        h = 12 if i in (0, 8) else 8 if i == 4 else 6 if i % 2 == 0 else 4
        xx = x + 8 + i * step
        pg.line((xx, y), (xx, y + h), color=(0, 0, 0))
        if 0 < i < 8:
            frac = {1: "1/8", 2: "1/4", 3: "3/8", 4: "1/2", 5: "5/8", 6: "3/4", 7: "7/8"}[i]
            pg.text(xx, y + 20, frac, 4.6, center=True, color=(0.25, 0.25, 0.25))
    pg.text(x + 8, y + 20, "35", 6.5, bold=True, center=True)
    pg.text(x + 8 + 8 * step, y + 20, "36", 6.5, bold=True, center=True)
    xx = x + 8 + 3 * step
    pg.line((xx, y - 5), (xx, y + 13), color=RED, width=1.2)
    pg.text(xx, y - 7, 'write it as  35  3/8"', 7, bold=True, color=RED, center=True)


def side_view(pg, x, y, w, h, labels=True):
    """Cross-section of the wall: the arrow is the depth of the window opening."""
    pg.rect((x, y, x + w * 0.62, y + h), color=(0.5, 0.5, 0.5), fill=WALL)
    gx = x + w * 0.62
    pg.rect((gx, y - 4, gx + 6, y + h + 4), fill=GLASS)
    if labels:
        pg.text(gx + 9, y + h / 2 + 2, "window", 6, color=DARK)
        pg.text(x + 2, y - 3, "room side", 6, color=DARK)
    pg.arrow((x + 1, y + h / 2), (gx - 1, y + h / 2))


def tile_ruler(pg, x, y, w):
    ruler(pg, x + 18, y + 4, w - 36)


def door_slab(pg, r, hinge_left=True, glass=None, blinds=False, deadbolt=False, peep=False, photo=None,
              casing=True, knob=True, hinge_count=3):
    """Front of a door (seen from the side the hinges show on). Returns dict of key points."""
    r = fitz.Rect(r)
    if casing:
        pg.rect((r.x0 - 7, r.y0 - 7, r.x1 + 7, r.y1), color=(0.5, 0.5, 0.5), fill=WALL)
        pg.line((r.x0 - 30, r.y1), (r.x1 + 30, r.y1), color=DARK, width=1.2)
    pg.rect(r, color=DARK, fill=WOOD, width=1)
    if photo and os.path.isfile(photo):
        pg.p.insert_image(r, filename=photo, keep_proportion=False)
        pg.rect(r, color=DARK, width=1)
    h = r.height
    hx = r.x0 if hinge_left else r.x1 - 3
    hinges = []
    spots = (0.10, 0.48, 0.84) if hinge_count == 3 else (0.10, 0.355, 0.61, 0.84)
    for i, f in enumerate(spots):
        hy = r.y0 + h * f
        pg.rect((hx, hy, hx + 3, hy + h * 0.06), color=(0.2, 0.2, 0.2), fill=(0.55, 0.55, 0.55), width=0.4)
        hinges.append(hy)
    kx = r.x1 - r.width * 0.12 if hinge_left else r.x0 + r.width * 0.12
    ky = r.y0 + h * 0.53
    if glass:
        top = r.y0 + h * 0.08
        gh = {"half": 36, "3/4": 48, "full": 64}[glass] / 80 * h
        g = fitz.Rect(r.x0 + r.width * 0.2, top, r.x1 - r.width * 0.2, top + gh)
        pg.rect((g.x0 - 2, g.y0 - 2, g.x1 + 2, g.y1 + 2), color=DARK, fill=(0.93, 0.93, 0.93), width=0.6)
        pg.rect(g, color=DARK, fill=GLASS, width=0.5)
        if blinds:
            n = int(g.height / 3)
            for i in range(1, n):
                yy = g.y0 + i * g.height / n
                pg.line((g.x0 + 1, yy), (g.x1 - 1, yy), color=(0.45, 0.45, 0.45), width=0.4)
    if knob:
        pg.p.draw_circle((kx, ky), max(2.2, r.width * 0.035), color=(0.2, 0.2, 0.2), fill=(0.75, 0.65, 0.3), width=0.5)
    db = None
    if deadbolt:
        db = r.y0 + h * 0.46
        pg.p.draw_circle((kx, db), max(1.8, r.width * 0.028), color=(0.2, 0.2, 0.2), fill=(0.75, 0.65, 0.3), width=0.5)
    pp = None
    if peep:
        pp = r.y0 + h * 0.20
        pg.p.draw_circle((r.x0 + r.width / 2, pp), 1.6, color=(0.2, 0.2, 0.2), fill=(0.3, 0.3, 0.3), width=0.4)
    return dict(hinges=hinges, knob=(kx, ky), deadbolt=db, peep=pp)


def swing_icon(pg, x, y, hinge_left):
    """Small door seen from the side it opens toward you, with the swing arc."""
    w, h = 26, 40
    door_slab(pg, (x, y, x + w, y + h), hinge_left=hinge_left, casing=False)
    tx = x + w if hinge_left else x
    pg.arrow((tx + (6 if hinge_left else -6), y + h * 0.5), (tx + (16 if hinge_left else -16), y + h * 0.65),
             color=BLUE, width=0.8, both=False)


def visual_options(pg, k, name, x, y, w, items, icon_h=62, cols=None, aspect=0.5):
    """Row of pictures with a check box + label under each. items: (label, draw(pg, rect))"""
    n = len(items)
    cols = cols or n
    cw = w / cols
    for i, (label, draw) in enumerate(items):
        cx = x + (i % cols) * cw
        cy = y + (i // cols) * (icon_h + 24)
        iw = min(cw - 8, icon_h * aspect)
        rr = fitz.Rect(cx + (cw - iw) / 2, cy, cx + (cw + iw) / 2, cy + icon_h)
        draw(pg, rr)
        lines = label.split("\n")
        pg.check(f"{k} {name} {' '.join(lines)}", cx + 2, cy + icon_h + 11, lines[0], 6.8, maxw=cw - 2)
        if len(lines) > 1:
            pg.text(cx + 15, cy + icon_h + 19, lines[1], 6, color=DARK)
    return y + math.ceil(n / cols) * (icon_h + 24)


# --------------------------------------------------------------------------- form engine
class Form:
    subtitle = ""
    item = "ITEM"
    prefix = "I"
    max_pages = 2
    steps = []

    def card(self, pg, n, y):
        raise NotImplementedError

    def intro(self, pg, y):
        """Optional band under the how-to (e.g. a strip of product photos shared by every item)."""
        return y

    def build(self, out_path):
        src = fitz.open(COMPANY)
        doc = fitz.open()
        scratch = fitz.open()

        def height(n):
            sp = Page(scratch.new_page(width=612, height=2000), dry=True)
            return self.card(sp, n, 0)

        pages = []
        pg = Page(doc.new_page(width=612, height=792))
        header(pg, self.subtitle)
        y = how_to(pg, 132, self.steps)
        y = self.intro(pg, y)
        pages.append(pg)
        n = 0
        while True:
            n += 1
            if y + 4 + height(n) > BOTTOM:
                if len(pages) == self.max_pages:
                    break
                pg = Page(doc.new_page(width=612, height=792))
                continued(pg, self.subtitle)
                pages.append(pg)
                y = 32
            y = self.card(pg, n, y + 4)
        total = len(pages)
        for i, pg in enumerate(pages):
            pg.p = doc[i]
            footer(pg, i + 1, total)
            if i == 0:
                logo(pg, src)
            pg.finish()
        doc.set_metadata({"title": f"Customer Order Form - {self.subtitle}", "creator": "Apartment Interior Supply"})
        doc.save(out_path, garbage=3, deflate=True)
        doc.close()
        src.close()
        return out_path


# --------------------------------------------------------------------------- windows: blinds + shades
def _tile_inside(pg, x, y, w):
    o = window(pg, (x + w / 2 - 42, y, x + w / 2 + 42, y + 44), inset=9)
    pg.arrow((o.x0 + 1, o.y0 + o.height * 0.35), (o.x1 - 1, o.y0 + o.height * 0.35))


def _tile_wh(pg, x, y, w):
    o = window(pg, (x + w / 2 - 46, y, x + w / 2 + 46, y + 44), inset=9)
    pg.arrow((o.x0 + 1, o.y0 + o.height * 0.3), (o.x1 - 1, o.y0 + o.height * 0.3), width=0.9)
    pg.arrow((o.x0 + o.width * 0.7, o.y0 + 1), (o.x0 + o.width * 0.7, o.y1 - 1), color=BLUE, width=0.9)


def _tile_depth(pg, x, y, w):
    side_view(pg, x + w / 2 - 34, y + 14, 70, 24)


def window_measure(pg, k, x, top, w, h, wtag="WIDTH (inside, wall to wall)", htag="HEIGHT (top to sill)",
                   draw=None):
    """Window picture with one width arrow and one height arrow, boxes on the arrows."""
    o = window(pg, (x, top, x + w, top + h), inset=12)
    if draw:
        draw(pg, o)
    yy = o.y0 + o.height * 0.22
    pg.arrow((o.x0 + 1, yy), (o.x1 - 1, yy))
    pg.inches(f"{k} Width", o.x0 + o.width * 0.40, yy, wtag, RED)
    xx = o.x0 + o.width * 0.80
    pg.arrow((xx, o.y0 + 1), (xx, o.y1 - 1), color=BLUE)
    pg.inches(f"{k} Height", min(xx, o.x1 - 36), o.y0 + o.height * 0.66, htag, BLUE)
    return o


def depth_row(pg, k, y, label="DEPTH OF OPENING:", hint="(wall surface to window frame)"):
    side_view(pg, L + 14, y - 7, 46, 14, labels=False)
    lw = pg.text(L + 78, y + 3, label, 8.5, bold=True)
    pg.inches(f"{k} Depth", L + 78 + lw + 40, y)
    pg.text(L + 78 + lw + 78, y + 3, hint, 7, color=(0.35, 0.35, 0.35))


def _slat_bar(pg, x, y, h):
    pg.rect((x, y - h / 2, x + 34, y + h / 2), color=DARK, fill=(0.97, 0.95, 0.9), width=0.6)


def _tile_slat(pg, x, y, w):
    cx = x + w / 2 - 40
    for i, (lab, h) in enumerate((('1" slat', 4), ('2" slat', 9))):
        yy = y + 12 + i * 20
        _slat_bar(pg, cx, yy, h)
        pg.text(cx + 42, yy + 3, lab, 7.5, bold=True, color=DARK)


def slat_row(pg, k, y):
    """Slat size (we sell 1" and 2") with a little picture of each."""
    x = L + 14
    x += pg.text(x, y + 3, "SLAT SIZE:", 8.5, bold=True) + 12
    for lab, h in (('1"', 4), ('2"', 9)):
        _slat_bar(pg, x, y, h)
        x += 40
        x += pg.check(f"{k} Slat Size {lab}", x, y + 3, lab, 8) + 30
    pg.text(x, y + 3, "(check one)", 7, color=(0.35, 0.35, 0.35))


class HorizontalBlinds(Form):
    subtitle, item, prefix = "Horizontal Blinds", "WINDOW", "W"
    steps = [
        ("1  INSIDE THE OPENING", "Use a metal tape measure. Measure inside the window opening, wall to "
         "wall - not the old blind.", _tile_inside),
        ("2  1 WIDTH + 1 HEIGHT", "Measure the width (wall to wall) and the height (top of the opening to "
         "the sill). One of each.", _tile_wh),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  SLAT SIZE", 'Pick 1" or 2" slats. Not sure? Measure one old slat from top edge to bottom edge.',
         _tile_slat),
    ]
    STYLES = ["All Vinyl", "Vinyl Plus", "All Metal", "Faux Wood", 'Basswood 2"', 'Basswood 2-1/2"',
              '1" Metal Plus Mini', '2" Metal Plus', NOT_SURE]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        window_measure(pg, k, L + 20, top, 320, 150, draw=_blind_slats)
        yc = choices(pg, k, CX, top - 4, [
            dict(title="ROOM:", opts=ROOMS, other=("Other",)),
            ("BLIND STYLE (check one):", self.STYLES, 2),
            dict(title="COLOR:", opts=["White", "Alabaster", "Other"], cols=3, other=("Other",)),
            dict(title="BLIND GOES:", opts=["Inside the opening (most common)", "Outside / on the wall"], cols=1,
                 name="Mount"),
        ])
        pg.line((CX - 8, y), (CX - 8, max(yc, top + 196)), color=GREY, width=1)
        y2 = top + 150 + 14
        slat_row(pg, k, y2)
        y3 = notes_row(pg, k, y2 + 20)
        return max(y3, yc) + 2


def _blind_slats(pg, o):
    g = fitz.Rect(o.x0 + 5, o.y0 + 5, o.x1 - 5, o.y0 + o.height * 0.45)
    pg.rect((o.x0 + 3, o.y0 + 2, o.x1 - 3, o.y0 + 7), color=DARK, fill=(0.95, 0.95, 0.95), width=0.4)
    for i in range(1, 9):
        yy = o.y0 + 7 + i * (g.height - 4) / 9
        pg.line((o.x0 + 6, yy), (o.x1 - 6, yy), color=(0.6, 0.6, 0.6), width=0.5)


def _vert_slats(pg, o, outside=False):
    for i in range(1, 14):
        xx = o.x0 + 5 + i * (o.width - 10) / 14
        pg.line((xx, o.y0 + 8), (xx, o.y1 - 6), color=(0.7, 0.7, 0.7), width=0.5)


def patio_opening(pg, r, inset=12):
    """Wall with a sliding patio door opening (floor at the bottom)."""
    r = fitz.Rect(r)
    pg.rect(r, color=(0.5, 0.5, 0.5), fill=WALL)
    o = fitz.Rect(r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1)
    pg.rect(o, color=DARK, fill=(1, 1, 1), width=1)
    mid = o.x0 + o.width / 2
    for x0, x1 in ((o.x0 + 4, mid + 2), (mid - 2, o.x1 - 4)):
        pg.rect((x0, o.y0 + 4, x1, o.y1 - 2), color=(0.45, 0.45, 0.45), fill=(0.93, 0.93, 0.93), width=0.6)
        pg.rect((x0 + 4, o.y0 + 8, x1 - 4, o.y1 - 6), color=(0.55, 0.55, 0.55), fill=GLASS, width=0.4)
    pg.line((r.x0 - 4, r.y1), (r.x1 + 4, r.y1), color=DARK, width=1.4)
    return o


def _tile_vert_inside(pg, x, y, w):
    o = patio_opening(pg, (x + w / 2 - 44, y, x + w / 2 + 44, y + 44), inset=8)
    pg.arrow((o.x0 + 1, o.y0 + 10), (o.x1 - 1, o.y0 + 10), width=0.9)
    pg.arrow((o.x0 + o.width * 0.75, o.y0 + 1), (o.x0 + o.width * 0.75, o.y1 - 1), color=BLUE, width=0.9)


def _tile_vert_outside(pg, x, y, w):
    cx = x + w / 2
    pg.rect((cx - 40, y + 2, cx + 40, y + 7), fill=(0.95, 0.95, 0.95))
    for i in range(9):
        xx = cx - 36 + i * 9
        pg.rect((xx, y + 8, xx + 6, y + 42), color=(0.6, 0.6, 0.6), fill=(0.97, 0.97, 0.97), width=0.4)
    pg.arrow((cx - 40, y - 2), (cx + 40, y - 2), width=0.9)
    pg.arrow((cx + 46, y + 8), (cx + 46, y + 42), color=BLUE, width=0.9)


class VerticalBlinds(Form):
    subtitle, item, prefix = "Vertical Blinds", "OPENING", "V"
    steps = [
        ("1  WHERE DOES IT HANG?", "Most hang inside the opening (box A). On the wall above it? "
         "Use box B.", _tile_vert_inside),
        ("2  INSIDE: 1 WIDTH + 1 HEIGHT", "Width wall to wall and height top of opening to the floor or "
         "sill. One of each.", None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  ON THE WALL? (B)", "Measure the old headrail end to end and one slat top to bottom.",
         _tile_vert_outside),
    ]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        pg.text(L + 22, top + 4, "A  INSIDE THE OPENING  (most common)", 8, bold=True, color=NAVY)
        o = patio_opening(pg, (L + 20, top + 10, L + 210, top + 160), inset=12)
        _vert_slats(pg, o)
        yy = o.y0 + 22
        pg.arrow((o.x0 + 1, yy), (o.x1 - 1, yy))
        pg.inches(f"{k} Width", o.x0 + o.width * 0.42, yy, "WIDTH (wall to wall)")
        xx = o.x0 + o.width * 0.82
        pg.arrow((xx, o.y0 + 1), (xx, o.y1 - 1), color=BLUE)
        pg.inches(f"{k} Height", o.x0 + o.width * 0.5, o.y0 + o.height * 0.62, "HEIGHT (top to floor/sill)", BLUE)
        # B: outside mount - measure the old blind
        bx = L + 228
        pg.text(bx, top + 4, "B  ON THE WALL  (old blind)", 8, bold=True, color=NAVY)
        hr = fitz.Rect(bx + 4, top + 34, bx + 134, top + 40)
        pg.rect(hr, fill=(0.95, 0.95, 0.95))
        for i in range(12):
            sx = hr.x0 + 3 + i * 10.6
            pg.rect((sx, hr.y1 + 1, sx + 7.5, top + 150), color=(0.6, 0.6, 0.6), fill=(0.97, 0.97, 0.97), width=0.4)
        pg.arrow((hr.x0, hr.y0 - 6), (hr.x1, hr.y0 - 6))
        pg.inches(f"{k} Headrail", hr.x0 + hr.width / 2, hr.y0 - 6, None)
        pg.text(hr.x0 + hr.width / 2, hr.y0 - 15.5, "HEADRAIL (end to end)", 6.5, bold=True, color=RED, center=True)
        sx = hr.x0 + 3 + 6 * 10.6 + 3.7
        pg.arrow((sx, hr.y1 + 1), (sx, top + 150), color=BLUE)
        pg.inches(f"{k} Slat Length", sx, top + 100, "SLAT (top to bottom)", BLUE)
        yc = choices(pg, k, CX, top - 4, [
            dict(title="ROOM:", opts=["Living Room", "Bedroom", "Patio Door", "Other"], other=("Other",)),
            ("COLOR:", ["White", "Alabaster"], 2),
            ("SLATS:", ["Ribbed", "Smooth", NOT_SURE], 2),
            ("VALANCE (top cover):", ["Standard", "Upgrade", "Valance only - no blind"], 2),
            dict(title="BLIND OPENS TO:", opts=["Left", "Right", "Split (center)"], cols=3, name="Stack"),
        ])
        pg.line((CX - 8, y), (CX - 8, max(yc, top + 172)), color=GREY, width=1)
        y3 = notes_row(pg, k, top + 172)
        return max(y3, yc) + 2


def _shade_draw(pg, o, chain_left=False):
    pg.rect((o.x0 + 2, o.y0 + 2, o.x1 - 2, o.y0 + 8), color=DARK, fill=(0.85, 0.85, 0.85), width=0.4)
    pg.rect((o.x0 + 4, o.y0 + 8, o.x1 - 4, o.y0 + o.height * 0.55), color=(0.6, 0.55, 0.45),
            fill=(0.92, 0.88, 0.80), width=0.4)
    pg.line((o.x0 + 4, o.y0 + o.height * 0.55), (o.x1 - 4, o.y0 + o.height * 0.55), color=DARK, width=1.2)


def _chain_icon(side):
    def draw(pg, r):
        o = window(pg, (r.x0 - 8, r.y0, r.x1 + 8, r.y1), inset=5, mullion=False, sill=False)
        _shade_draw(pg, o)
        cx = o.x0 + 4 if side == "Left" else o.x1 - 4
        for i in range(10):
            pg.p.draw_circle((cx, o.y0 + 9 + i * 3.4), 0.9, color=(0.2, 0.2, 0.2), width=0.5)
    return draw


def _tile_shade(pg, x, y, w):
    o = window(pg, (x + w / 2 - 42, y, x + w / 2 + 42, y + 44), inset=9)
    _shade_draw(pg, o)
    for i in range(7):
        pg.p.draw_circle((o.x1 - 3, o.y0 + 8 + i * 3.4), 0.8, color=(0.2, 0.2, 0.2), width=0.4)


class RollerShades(Form):
    subtitle, item, prefix = "Roller Shades", "WINDOW", "R"
    steps = HorizontalBlinds.steps[:3] + [
        ("4  WHICH SIDE IS THE CHAIN?", "Stand inside facing the window. Pick the side you want the pull "
         "chain on.", _tile_shade)]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        window_measure(pg, k, L + 20, top, 320, 150, draw=_shade_draw)
        yc = choices(pg, k, CX, top - 4, [
            dict(title="ROOM:", opts=ROOMS, other=("Other",)),
            dict(title="COLOR:", opts=["White", "Champagne", "Tan", "Other"], other=("Other",)),
            dict(title="FABRIC (SUN SCREEN OPENNESS):", opts=["Sun screen 1%", "Sun screen 3%", "Sun screen 5%",
                                                            "Sun screen 10%", "Blackout", NOT_SURE], name="Fabric"),
            dict(title="SHADE GOES:", opts=["Inside the opening (most common)", "Outside / on the wall"], cols=1,
                 name="Mount"),
        ])
        pg.text(CX, yc + RS, "CHAIN SIDE (standing inside):", 8.5, bold=True)
        yc = visual_options(pg, k, "Chain Side", CX + 10, yc + RS + 6, 170,
                            [("Chain LEFT", _chain_icon("Left")), ("Chain RIGHT", _chain_icon("Right"))],
                            icon_h=36, aspect=1.1)
        pg.line((CX - 8, y), (CX - 8, max(yc, top + 196)), color=GREY, width=1)
        y2 = top + 150 + 14
        depth_row(pg, k, y2)
        y3 = notes_row(pg, k, y2 + 20)
        return max(y3, yc) + 2


# --------------------------------------------------------------------------- screens
def screen_frame(pg, r, mesh=(0.35, 0.35, 0.38), frame=(0.85, 0.85, 0.85)):
    r = fitz.Rect(r)
    pg.rect(r, color=DARK, fill=frame, width=0.8)
    inner = fitz.Rect(r.x0 + 6, r.y0 + 6, r.x1 - 6, r.y1 - 6)
    pg.rect(inner, color=(0.4, 0.4, 0.4), fill=(0.80, 0.80, 0.82), width=0.4)
    step = 4
    x = inner.x0 + step
    while x < inner.x1:
        pg.line((x, inner.y0), (x, inner.y1), color=(0.68, 0.68, 0.70), width=0.25)
        x += step
    y = inner.y0 + step
    while y < inner.y1:
        pg.line((inner.x0, y), (inner.x1, y), color=(0.68, 0.68, 0.70), width=0.25)
        y += step
    return inner


def _tile_screen(pg, x, y, w):
    r = fitz.Rect(x + w / 2 - 36, y + 2, x + w / 2 + 36, y + 44)
    screen_frame(pg, r)
    pg.arrow((r.x0, r.y0 - 3), (r.x1, r.y0 - 3), width=0.9)
    pg.arrow((r.x1 + 4, r.y0), (r.x1 + 4, r.y1), color=BLUE, width=0.9)


def _tile_frame(pg, x, y, w):
    cx = x + w / 2
    pg.rect((cx - 40, y + 14, cx + 40, y + 22), color=DARK, fill=(0.75, 0.75, 0.75), width=0.6)
    pg.arrow((cx + 46, y + 14), (cx + 46, y + 22), width=0.7)
    pg.text(cx, y + 36, "look at the edge of the frame", 6.5, color=DARK, center=True)


def _swatch_items(group, labels):
    out = []
    for lab in labels:
        p = os.path.join(PHOTOS, group, lab.lower().replace(" ", "-") + ".jpg")
        out.append((lab, (lambda path: lambda pg, r: pg.photo(r, path))(p)))
    return out


class WindowScreens(Form):
    subtitle, item, prefix = "Window Screens", "SCREEN", "S"
    steps = [
        ("1  MEASURE THE OLD SCREEN", "Take the old screen out. Measure the frame outside edge to outside "
         "edge.", _tile_screen),
        ("2  NO OLD SCREEN?", "Measure the opening in the window frame the screen sits in, and check "
         "\"No old screen\".", None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  FRAME THICKNESS", "Look at the edge of the frame and pick the size that matches.", _tile_frame),
    ]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        r = fitz.Rect(L + 24, top + 24, L + 226, top + 150)
        screen_frame(pg, r)
        pg.arrow((r.x0, r.y0 - 10), (r.x1, r.y0 - 10))
        pg.inches(f"{k} Width", r.x0 + r.width / 2, r.y0 - 10, "WIDTH (outside edge to edge)")
        pg.arrow((r.x1 + 14, r.y0), (r.x1 + 14, r.y1), color=BLUE)
        pg.inches(f"{k} Height", r.x1 - 30, r.y0 + r.height / 2, "HEIGHT (edge to edge)", BLUE)
        pg.check(f"{k} No old screen", r.x0, r.y1 + 14, "No old screen - I measured the window opening", 7.8)
        # frame thickness pictures
        fx = L + 270
        pg.text(fx, top + 4, "FRAME THICKNESS:", 8.5, bold=True)
        sizes = ('1/4"', '5/16"', '3/8"', '7/16"', '1"')
        for i, s in enumerate(sizes):
            yy = top + 22 + i * 24
            t = {'1/4"': 4, '5/16"': 5, '3/8"': 6, '7/16"': 7, '1"': 16}[s]
            pg.rect((fx + 40, yy - t / 2 - 3, fx + 58, yy + t / 2 - 3), color=DARK, fill=(0.75, 0.75, 0.75))
            pg.check(f"{k} Frame {s}", fx, yy, s, 8)
        yc = choices(pg, k, CX, top - 4, [
            ("SCREEN TYPE:", ["Bug screen", "Sun screen"], 2),
            ("BUG SCREEN COLOR:", ["Charcoal", "Grey"], 2),
            ("SUN SCREEN COLOR:", ["Black", "Brown", "Beige", "Stucco", "Gray", "Dark Bronze"], 3),
        ])
        pg.text(CX, yc + RS, "FRAME COLOR:", 8.5, bold=True)
        yc = visual_options(pg, k, "Frame Color", CX, yc + RS + 6, R - CX - 4,
                            _swatch_items("sunscreen_trim", ["White", "Tan", "Champagne", "Bronze", "Mill"]),
                            icon_h=16, cols=3, aspect=2.2)
        pg.line((CX - 8, y), (CX - 8, max(yc, top + 172)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(top + 182, yc + 6))
        return y3 + 2


def _tile_patio_screen(pg, x, y, w):
    r = fitz.Rect(x + w / 2 - 20, y, x + w / 2 + 20, y + 44)
    screen_frame(pg, r)
    pg.arrow((r.x0, r.y0 - 3), (r.x1, r.y0 - 3), width=0.9)
    pg.arrow((r.x1 + 5, r.y0), (r.x1 + 5, r.y1), color=BLUE, width=0.9)


def _series_icon(heavy):
    def draw(pg, r):
        t = 5 if heavy else 2.5
        pg.rect(r, color=DARK, fill=(0.85, 0.85, 0.85), width=t)
        screen_frame(pg, (r.x0 + t, r.y0 + t, r.x1 - t, r.y1 - t), frame=(0.85, 0.85, 0.85))
    return draw


class PatioScreens(Form):
    subtitle, item, prefix = "Patio Door Screens", "SCREEN DOOR", "P"
    steps = [
        ("1  MEASURE THE OLD SCREEN DOOR", "Lift it out if you can. Measure the frame outside edge to outside "
         "edge.", _tile_patio_screen),
        ("2  NO OLD SCREEN DOOR?", "Measure the track opening: width side to side and height from the "
         "bottom track to the top track.", None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  PICK THE FRAME", "Standard frame (most common) or heavy-duty extruded frame.", None),
    ]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        r = fitz.Rect(L + 90, top + 24, L + 200, top + 220)
        screen_frame(pg, r)
        pg.rect((r.x1 - 10, r.y0 + r.height * 0.5 - 8, r.x1 - 7, r.y0 + r.height * 0.5 + 8), color=DARK,
                fill=(0.3, 0.3, 0.3))
        pg.arrow((r.x0, r.y0 - 10), (r.x1, r.y0 - 10))
        pg.inches(f"{k} Width", r.x0 + r.width / 2, r.y0 - 10, "WIDTH (edge to edge)")
        pg.arrow((r.x1 + 14, r.y0), (r.x1 + 14, r.y1), color=BLUE)
        pg.inches(f"{k} Height", r.x1 + 14 + 44, r.y0 + r.height / 2, "HEIGHT", BLUE)
        pg.line((r.x1 + 14, r.y0 + r.height / 2), (r.x1 + 14 + 9, r.y0 + r.height / 2), color=BLUE, width=0.8)
        pg.check(f"{k} No old screen", L + 30, r.y1 + 14, "No old screen door - I measured the track opening", 7.8)
        yc = top - 4 + RS
        pg.text(CX, yc, "FRAME:", 8.5, bold=True)
        yc = visual_options(pg, k, "Frame", CX + 6, yc + 6, 190,
                            [("Standard\n600 Series", _series_icon(False)),
                             ("Heavy duty\n1250 Series", _series_icon(True)),
                             ("Not sure", lambda pg, r: pg.text(r.x0 + r.width / 2, r.y0 + r.height / 2, "?", 20,
                                                                bold=True, color=DARK, center=True))],
                            icon_h=44)
        yc = choices(pg, k, CX, yc - 6, [
            ("SCREEN TYPE:", ["Bug screen", "Sun screen"], 2),
            ("SCREEN COLOR:", ["Charcoal", "Grey"], 2),
        ])
        pg.text(CX, yc + RS, "FRAME COLOR:", 8.5, bold=True)
        yc = visual_options(pg, k, "Frame Color", CX, yc + RS + 6, R - CX - 4,
                            _swatch_items("patio_trim", ["White", "Tan", "Champagne", "Bronze", "Gray"]),
                            icon_h=16, cols=3, aspect=2.2)
        pg.line((CX - 8, y), (CX - 8, max(yc, r.y1 + 18)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(r.y1 + 34, yc + 6))
        return y3 + 2


# --------------------------------------------------------------------------- sliding patio doors
def _xo_icon(slides_left):
    def draw(pg, r):
        r = fitz.Rect(r.x0 - 14, r.y0, r.x1 + 14, r.y1)
        pg.rect(r, color=DARK, fill=(0.93, 0.93, 0.93), width=0.8)
        mid = r.x0 + r.width / 2
        for x0, x1 in ((r.x0 + 2, mid), (mid, r.x1 - 2)):
            pg.rect((x0 + 2, r.y0 + 3, x1 - 2, r.y1 - 3), color=(0.55, 0.55, 0.55), fill=GLASS, width=0.4)
        lab_l, lab_r = ("X", "O") if slides_left else ("O", "X")
        pg.text(r.x0 + r.width * 0.25, r.y0 + r.height * 0.42, lab_l, 10, bold=True, color=NAVY, center=True)
        pg.text(r.x0 + r.width * 0.75, r.y0 + r.height * 0.42, lab_r, 10, bold=True, color=NAVY, center=True)
        ay = r.y0 + r.height * 0.7
        if slides_left:
            pg.arrow((r.x0 + 6, ay), (mid - 2, ay), color=GREEN, width=0.9, both=False)
        else:
            pg.arrow((r.x1 - 6, ay), (mid + 2, ay), color=GREEN, width=0.9, both=False)
    return draw


def _tile_slider(pg, x, y, w):
    o = patio_opening(pg, (x + w / 2 - 44, y, x + w / 2 + 44, y + 44), inset=8)
    pg.arrow((o.x0 + 1, o.y0 + 10), (o.x1 - 1, o.y0 + 10), width=0.9)
    pg.arrow((o.x0 + o.width * 0.75, o.y0 + 1), (o.x0 + o.width * 0.75, o.y1 - 1), color=BLUE, width=0.9)


def _tile_outside(pg, x, y, w):
    _xo_icon(True)(pg, fitz.Rect(x + w / 2 - 22, y + 4, x + w / 2 + 22, y + 40))


class SlidingPatioDoors(Form):
    subtitle, item, prefix = "Sliding Patio Doors", "PATIO DOOR", "SD"
    steps = [
        ("1  MEASURE THE OPENING", "Measure the opening the door frame sits in, wall to wall and top to "
         "the floor.", _tile_slider),
        ("2  CHECK A COMMON SIZE", "Most are 5 ft, 6 ft or 8 ft wide by 6 ft 8 in tall. Check one if it "
         "matches.", None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  STAND OUTSIDE", "Looking from OUTSIDE, which panel slides? X = slides, O = stays put.", _tile_outside),
    ]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        o = patio_opening(pg, (L + 24, top + 4, L + 340, top + 170), inset=14)
        yy = o.y0 + o.height * 0.25
        pg.arrow((o.x0 + 1, yy), (o.x1 - 1, yy))
        pg.inches(f"{k} Width", o.x0 + o.width * 0.30, yy, "WIDTH (wall to wall)")
        xx = o.x0 + o.width * 0.78
        pg.arrow((xx, o.y0 + 1), (xx, o.y1 - 1), color=BLUE)
        pg.inches(f"{k} Height", xx, o.y0 + o.height * 0.62, "HEIGHT (top to floor)", BLUE)
        yv = top + 186
        pg.text(L + 22, yv, "WHICH PANEL SLIDES?  (looking from OUTSIDE)", 8.5, bold=True)
        visual_options(pg, k, "Config", L + 22, yv + 8, 300,
                       [("XO - left slides", _xo_icon(True)), ("OX - right slides", _xo_icon(False)),
                        ("Not sure", lambda pg, r: pg.text(r.x0 + r.width / 2, r.y0 + 26, "?", 20, bold=True,
                                                           color=DARK, center=True))], icon_h=36)
        yc = choices(pg, k, CX, top - 4, [
            ("COMMON SIZE (if it matches):", ["5 ft x 6'8\"", "6 ft x 6'8\"", "8 ft x 6'8\"", "Other"], 2),
            ("FRAME:", ["Vinyl", "Aluminum", NOT_SURE], 2),
            ("FRAME COLOR:", ["White", "Tan / Almond", "Bronze", "Other"], 2),
            ("GLASS:", ["Clear", "Low-E (energy saving)", "Not sure"], 2),
            ("GRIDS IN THE GLASS:", ["No", "Yes"], 2),
            ("ALSO NEED A SCREEN DOOR?", ["Yes", "No"], 2),
        ])
        pg.line((CX - 8, y), (CX - 8, max(yc, yv + 66)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(yv + 76, yc + 8))
        return y3 + 2


# --------------------------------------------------------------------------- doors
def _tile_door_wh(pg, x, y, w):
    r = fitz.Rect(x + w / 2 - 14, y + 2, x + w / 2 + 14, y + 44)
    door_slab(pg, r)
    pg.arrow((r.x0, r.y0 + 6), (r.x1, r.y0 + 6), width=0.8)
    pg.arrow((r.x1 + 12, r.y0), (r.x1 + 12, r.y1), color=BLUE, width=0.8)


def _tile_hinges(pg, x, y, w):
    r = fitz.Rect(x + w / 2 - 6, y + 2, x + w / 2 + 22, y + 44)
    d = door_slab(pg, r)
    for i, hy in enumerate(d["hinges"]):
        xx = r.x0 - 8 - i * 6
        pg.arrow((xx, r.y0), (xx, hy), color=GREEN, width=0.6)


def _tile_swing(pg, x, y, w):
    swing_icon(pg, x + w / 2 - 36, y, True)
    swing_icon(pg, x + w / 2 + 10, y, False)


def _style_strip_items(names):
    items = []
    for label, slug_ in names:
        p = os.path.join(PHOTOS, "door_style", slug_ + ".jpg") if slug_ else None
        if slug_ and slug_.startswith("glass:"):
            kind = slug_.split(":")[1]
            items.append((label, (lambda kd: lambda pg, r: door_slab(
                pg, r, glass=kd.replace("+b", ""), blinds=kd.endswith("+b"), casing=False))(kind)))
        elif slug_ == "steel6":
            items.append((label, lambda pg, r: _six_panel(pg, r)))
        elif p:
            items.append((label, (lambda path: lambda pg, r: pg.photo(r, path))(p)))
        else:
            items.append((label, lambda pg, r: pg.text(r.x0 + r.width / 2, r.y0 + r.height / 2 + 6, "?", 20,
                                                        bold=True, color=DARK, center=True)))
    return items


def _six_panel(pg, r):
    door_slab(pg, r, casing=False, knob=False)
    w, h = r.width, r.height
    for (fx0, fy0, fx1, fy1) in ((.15, .05, .45, .18), (.55, .05, .85, .18), (.15, .23, .45, .55),
                                 (.55, .23, .85, .55), (.15, .62, .45, .94), (.55, .62, .85, .94)):
        pg.rect((r.x0 + w * fx0, r.y0 + h * fx1 * 0 + h * fy0, r.x0 + w * fx1, r.y0 + h * fy1),
                color=(0.55, 0.55, 0.55), width=0.4)


INTERIOR_STYLES = [("6 Panel", "6-panel"), ("2 Panel", "2-panel"), ("2 Panel\nArch", "2-panel-arch"),
                   ("2 Panel\nArch Plank", "2-panel-arch-plank"), ("5 Panel", "5-panel"),
                   ("5 Panel\nShaker", "5-panel-shaker"), ("3 Panel\nShaker", "3-panel-shaker-equal"),
                   ("3 Panel\nCraftsman", "3-panel-shaker-craftsman"), ("2 Panel\nShaker", "2-panel-shaker"),
                   ("1 Panel\nShaker", "1-panel-shaker"), ("Flat\n(smooth)", "hc-primecoat"),
                   ("Not sure\n/ match", None)]

EXTERIOR_STYLES = [("6 Panel", "steel6"), ("Flat (smooth)", "hc-primecoat"),
                   ('Half glass\n22" x 36"', "glass:half"), ('3/4 glass\n22" x 48"', "glass:3/4"),
                   ('Full glass\n22" x 64"', "glass:full"), ('Half + blinds\n22" x 36"', "glass:half+b"),
                   ('3/4 + blinds\n22" x 48"', "glass:3/4+b"), ('Full + blinds\n22" x 64"', "glass:full+b"),
                   ("Not sure / match", None)]


def door_measure(pg, k, x, top, exterior=False):
    """Big door with measuring arrows. Hinge side on the left. Returns bottom y."""
    r = fitz.Rect(x + 128, top + 14, x + 226, top + 232)
    d = door_slab(pg, r, deadbolt=exterior, peep=exterior, hinge_count=4)
    # width under the door
    pg.arrow((r.x0, r.y1 + 8), (r.x1, r.y1 + 8))
    pg.inches(f"{k} Width", r.x0 + r.width / 2 + 20, r.y1 + 26)
    pg.text(r.x0 + r.width / 2 - 56, r.y1 + 29, "DOOR WIDTH", 6.5, bold=True, color=RED)
    # height on the far right
    hx = r.x1 + 92
    pg.line((r.x1, r.y0), (hx + 4, r.y0), color=GREY, width=0.5, dashes="[2] 0")
    pg.arrow((hx, r.y0), (hx, r.y1), color=BLUE)
    pg.inches(f"{k} Height", hx, r.y0 + r.height * 0.86, "DOOR HEIGHT", BLUE)
    # knob (and deadbolt / peephole) from the top of the door
    kx, ky = d["knob"]
    ax = r.x1 + 16
    pg.line((r.x1, r.y0), (ax + 4, r.y0), color=GREY, width=0.5, dashes="[2] 0")
    pg.line((kx, ky), (ax, ky), color=(0.5, 0.3, 0.6), width=0.5, dashes="[2] 0")
    pg.arrow((ax, r.y0), (ax, ky), color=(0.5, 0.3, 0.6), width=0.9)
    pg.inches(f"{k} Knob", ax + 38, ky, "TOP TO KNOB CENTER", (0.5, 0.3, 0.6))
    if exterior:
        dy = d["deadbolt"]
        ax2 = r.x1 + 8
        pg.line((kx, dy), (ax2, dy), color=(0.6, 0.4, 0.1), width=0.5, dashes="[2] 0")
        pg.arrow((ax2, r.y0), (ax2, dy), color=(0.6, 0.4, 0.1), width=0.9)
        pg.inches(f"{k} Deadbolt", ax + 38, dy - 24, "TOP TO DEADBOLT CENTER", (0.6, 0.4, 0.1))
    # hinges: top of door to top of each hinge, on the hinge side
    for i, hy in enumerate(d["hinges"]):
        ax = r.x0 - 8 - i * 7
        pg.line((ax - 2, r.y0), (r.x0, r.y0), color=GREY, width=0.5, dashes="[2] 0")
        pg.arrow((ax, r.y0), (ax, hy), color=GREEN, width=0.8)
        pg.line((ax, hy), (r.x0, hy), color=GREEN, width=0.4, dashes="[1.5] 0")
        bx = x + 38
        by = hy + (10 if i == 0 else 0)
        pg.line((bx + 36, by), (ax, hy), color=GREEN, width=0.4)
        tag = f"TOP TO HINGE {i + 1}" + ("  (if any)" if i == 3 else "")
        pg.inches(f"{k} Hinge {i + 1}", bx, by, tag, GREEN)
    ty = r.y0 + r.height * 0.84 + 20
    pg.text(x + 4, ty, "Measure from the TOP of the door to", 6.5, color=DARK)
    pg.text(x + 4, ty + 8, "the TOP of each hinge, top one first.", 6.5, color=DARK)
    pg.text(x + 4, ty + 17, "Only 3 hinges? Leave #4 blank.", 6.5, bold=True, color=DARK)
    return r.y1 + 38


def door_card(pg, k, n, y, item, exterior):
    y = card_bar(pg, y, f"{item} {n}", k)
    top = y + 6
    yb = door_measure(pg, k, L + 6, top, exterior)
    # right column
    yc = top - 4 + RS
    pg.text(CX, yc, "SWING  (stand where you see the hinges):", pg.fit("SWING  (stand where you see the hinges):",
                                                                     R - CX - 4, 8.5, True), bold=True)
    yc = visual_options(pg, k, "Swing", CX + 8, yc + 6, 180,
                        [("Hinges LEFT", lambda pg, r: swing_icon(pg, r.x0 - 2, r.y0, True)),
                         ("Hinges RIGHT", lambda pg, r: swing_icon(pg, r.x0 - 2, r.y0, False))], icon_h=42)
    groups = [("DOOR THICKNESS:", ['1-3/8" (inside)', '1-3/4" (outside)'], 2),
              dict(title="DOOR EDGE (long side):", cols=3, name="Edge",
                   opts=["Beveled", "Square", "Not sure"])]
    if exterior:
        groups += [("DOOR OPENS:", ["In (into the unit)", "Out"], 2)]
    else:
        groups += [("FINISH:", ["Primecoat (paint)", "Embossed Primecoat", "Oak", "Walnut"], 2)]
    groups += [("NEED THE FRAME TOO (pre-hung)?", ["No - door only", "Yes - door + frame"], 2),
               ("HARDWARE:", (["None", "Entry knob", "Deadbolt", "Peephole"] if exterior
                              else ["None", "Passage (no lock)", "Privacy (lock)"]), 2),
               dict(title='HINGE SIZE (how tall):', opts=['3-1/2"', '4"', "Not sure", "Other"], cols=4,
                    other=("Other",)),
               dict(title="FIRE-RATED? (metal label on the hinge edge):", name="Fire Rated", cols=3,
                    opts=["No", "20 min", "45 min", "60 min", "90 min", "Not sure"])]
    yc = choices(pg, k, CX, yc - 4, groups)
    if exterior:     # peephole location, from the top of the door like the other measurements
        yc += RS
        lw = pg.text(CX, yc, "PEEPHOLE - top of door to center:", 8, bold=True)
        pg.field(f"{k} Peephole", (CX + lw + 4, yc - 9, CX + lw + 54, yc + 3), size=8.5, align=1)
        pg.text(CX + lw + 58, yc, "in", 7.5)
        yc += 3
    # hinge corners, with a little picture of each
    yc += RS
    pg.text(CX, yc, "HINGE CORNERS:", 8.5, bold=True)
    for i, (lab, rad) in enumerate((("Round", 0.35), ("Square", None), ("Not sure", "?"))):
        hx = CX + i * 66
        hy = yc + 6
        if rad == "?":
            pg.text(hx + 6, hy + 15, "?", 13, bold=True, color=DARK)
        else:
            pg.p.draw_rect(fitz.Rect(hx, hy, hx + 13, hy + 20), color=DARK, fill=(0.75, 0.75, 0.75), width=0.6,
                           radius=rad)
            for sy in (hy + 4, hy + 10, hy + 16):
                pg.p.draw_circle((hx + 6.5, sy), 1.1, color=DARK, fill=(1, 1, 1), width=0.4)
        pg.check(f"{k} Hinge Corners {lab}", hx + 17, hy + 14, lab, 7.8)
    yc += 30
    ys = max(yb, yc) + 6
    pg.line((CX - 8, y), (CX - 8, ys - 4), color=GREY, width=1)
    # what the door is made of, then core - right above the style pictures
    pg.rect((L, ys - 2, R, ys + 1.5), color=None, fill=GREY)
    ys += 14
    pg.text(L + 6, ys, "DOOR MATERIAL:", 8.5, bold=True)
    for i, m in enumerate(("Wood", "Metal (steel)", "Fiberglass")):
        pg.check(f"{k} Material {m.split()[0]}", L + 92 + i * 70, ys, m, 8)
    pg.text(CX - 70, ys, "CORE (knock on it):", 8.5, bold=True)
    for i, c_ in enumerate(("Hollow", "Solid", "Not sure")):
        pg.check(f"{k} Core {c_}", CX + 30 + i * 58, ys, c_, 8)
    ys += 10
    # style strip
    pg.rect((L, ys - 2, R, ys + 1.5), color=None, fill=GREY)
    pg.text(L + 6, ys + 12, "DOOR LOOK (check the closest):" if not exterior else "DOOR STYLE (check one):", 8.5,
            bold=True)
    names = EXTERIOR_STYLES if exterior else INTERIOR_STYLES
    if exterior:
        pg.text(L + 130, ys + 12, "Standard door glass sizes shown. Glass with blinds = blinds built in "
                "between the glass.", 6.8, color=DARK)
    ye = visual_options(pg, k, "Style", L + 4, ys + 18, R - L - 8, _style_strip_items(names), icon_h=60,
                        cols=len(names))
    if not exterior:
        # panel count and style name are separate, so any mix can be ordered (e.g. a 2-panel Camden)
        yp = ye + 8
        x = L + 8
        x += pg.text(x, yp + 3, "NUMBER OF PANELS:", 8.5, bold=True) + 8
        for lab in ("1", "2", "3", "4", "5", "6", "Flat"):
            x += pg.check(f"{k} Panels {lab}", x, yp + 3, lab, 8) + 22
        x += 6
        pg.text(x, yp + 3, "STYLE NAME (if known):", 8.5, bold=True)
        pg.field(f"{k} Style Name", (x + 118, yp - 7, R - 6, yp + 7), size=8.5)
        ye = yp + 8
    return notes_row(pg, k, ye + 8, x1=R - 6) + 2


class InteriorDoors(Form):
    subtitle, item, prefix = "Interior Doors", "DOOR", "D"
    max_pages = 3
    steps = [
        ("1  MEASURE THE DOOR", "Measure the door itself (not the frame): width side to side, height top "
         "to bottom.", _tile_door_wh),
        ("2  HINGES + KNOB", "Measure from the TOP of the door down to the top of each hinge and to the "
         "center of the knob.", _tile_hinges),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  WHICH WAY IT SWINGS", "Stand on the side you can see the hinges. Are they on the left or the "
         "right?", _tile_swing),
    ]

    def card(self, pg, n, y):
        return door_card(pg, f"{self.prefix}{n}", n, y, self.item, exterior=False)


class ExteriorDoors(Form):
    subtitle, item, prefix = "Exterior Doors", "DOOR", "X"
    max_pages = 3
    steps = [
        ("1  MEASURE THE DOOR", "Measure the door itself (not the frame): width side to side, height top "
         "to bottom.", _tile_door_wh),
        ("2  HINGES, KNOB, DEADBOLT", "Measure from the TOP of the door down to the top of each hinge and to "
         "the center of the knob and deadbolt.", _tile_hinges),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  WHICH WAY IT SWINGS", "Stand on the side you can see the hinges. Are they on the left or the "
         "right?", _tile_swing),
    ]

    def card(self, pg, n, y):
        return door_card(pg, f"{self.prefix}{n}", n, y, self.item, exterior=True)


# --------------------------------------------------------------------------- shared bits for the newer forms
def photo_strip(pg, y, title, group, labels, note=None, h=58, keys=None):
    """A band of product photos with the letter / name under each - picked by name in each item below."""
    pg.bar(y, title)
    y += 18
    n = len(labels)
    cw = (R - L - 8) / n
    for i, lab in enumerate(labels):
        x = L + 4 + i * cw
        slug_ = (keys[i] if keys else lab).lower().replace(" ", "-").replace(":", "")
        path = os.path.join(PHOTOS, group, slug_ + ".jpg")
        iw = min(cw - 6, h * 1.3)
        pg.photo((x + (cw - iw) / 2, y, x + (cw + iw) / 2, y + h), path)
        pg.text(x + cw / 2, y + h + 9, lab, pg.fit(lab, cw - 2, 7.2, True), bold=True, center=True)
    y += h + 12
    if note:
        pg.text(L + 6, y + 2, note, 6.8, color=DARK)
        y += 6
    return y + 4


def wall_opening(pg, r, inset=10):
    """Wall with a plain opening down to the floor (closet / door opening). Returns the opening rect."""
    r = fitz.Rect(r)
    pg.rect(r, color=(0.5, 0.5, 0.5), fill=WALL)
    o = fitz.Rect(r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1)
    pg.rect((o.x0 - 4, o.y0 - 4, o.x1 + 4, o.y1), color=DARK, fill=(0.97, 0.97, 0.95), width=0.8)
    pg.rect(o, color=DARK, fill=(1, 1, 1), width=0.8)
    pg.line((r.x0 - 4, r.y1), (r.x1 + 4, r.y1), color=DARK, width=1.4)
    return o


def bypass_icon(pg, r):
    r = fitz.Rect(r)
    w = r.width
    pg.rect(r, color=DARK, fill=(0.97, 0.97, 0.95), width=0.6)
    pg.rect((r.x0 + 2, r.y0 + 3, r.x0 + w * 0.58, r.y1), color=DARK, fill=WOOD, width=0.6)
    pg.rect((r.x0 + w * 0.42, r.y0 + 3, r.x1 - 2, r.y1), color=DARK, fill=(0.93, 0.93, 0.9), width=0.6)
    pg.arrow((r.x0 + w * 0.2, r.y0 + r.height * 0.55), (r.x0 + w * 0.45, r.y0 + r.height * 0.55),
             color=GREEN, width=0.7)


def bifold_icon(pg, r):
    r = fitz.Rect(r)
    pg.rect(r, color=DARK, fill=(0.97, 0.97, 0.95), width=0.6)
    n = 4
    pw = (r.width - 4) / n
    for i in range(n):
        x = r.x0 + 2 + i * pw
        pg.rect((x, r.y0 + 3, x + pw, r.y1), color=DARK, fill=WOOD if i % 2 == 0 else (0.93, 0.93, 0.9), width=0.5)
    pg.line((r.x0 + r.width / 2 - 6, r.y0 + r.height * 0.5), (r.x0 + r.width / 2, r.y0 + r.height * 0.4),
            color=GREEN, width=0.7)
    pg.line((r.x0 + r.width / 2 + 6, r.y0 + r.height * 0.5), (r.x0 + r.width / 2, r.y0 + r.height * 0.4),
            color=GREEN, width=0.7)


# --------------------------------------------------------------------------- closet + bi-fold doors
def _tile_closet(pg, x, y, w):
    o = wall_opening(pg, (x + w / 2 - 40, y, x + w / 2 + 40, y + 44), inset=8)
    pg.arrow((o.x0 + 1, o.y0 + 8), (o.x1 - 1, o.y0 + 8), width=0.9)
    pg.arrow((o.x0 + o.width * 0.75, o.y0 + 1), (o.x0 + o.width * 0.75, o.y1 - 1), color=BLUE, width=0.9)


def _tile_closet_types(pg, x, y, w):
    bypass_icon(pg, (x + w / 2 - 50, y + 4, x + w / 2 - 6, y + 42))
    bifold_icon(pg, (x + w / 2 + 6, y + 4, x + w / 2 + 50, y + 42))


class ClosetDoors(Form):
    subtitle, item, prefix = "Closet & Bi-Fold Doors", "CLOSET", "C"
    steps = [
        ("1  MEASURE THE OPENING", "Measure the closet opening inside the trim: wall to wall, and the top "
         "of the opening to the floor.", _tile_closet),
        ("2  NO TRIM / DOORS OFF?", "Same thing - measure the bare opening. Don't measure the old doors.", None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  SLIDING OR FOLDING?", "Sliding doors (bypass), folding doors (bi-fold) or mirrored doors. "
         "We'll tell you if your size is standard or custom.", _tile_closet_types),
    ]

    def intro(self, pg, y):
        return photo_strip(pg, y, "MIRRORED & WARDROBE DOORS", "closet_type",
                           ["Mirrored Bypass", "Mirrored Swing", "White Vinyl Board"], h=50,
                           keys=["mirrored-bypass-door", "mirrored-swing-door", "white-vinyl-board-door"])

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        o = wall_opening(pg, (L + 30, top + 6, L + 330, top + 170), inset=14)
        mid = o.x0 + o.width / 2
        pg.rect((o.x0 + 3, o.y0 + 5, mid + 8, o.y1), color=(0.6, 0.6, 0.6), fill=WOOD, width=0.5)
        pg.rect((mid - 8, o.y0 + 5, o.x1 - 3, o.y1), color=(0.6, 0.6, 0.6), fill=(0.94, 0.94, 0.91), width=0.5)
        yy = o.y0 + 22
        pg.arrow((o.x0 + 1, yy), (o.x1 - 1, yy))
        pg.inches(f"{k} Width", o.x0 + o.width * 0.38, yy, "WIDTH (wall to wall)")
        xx = o.x0 + o.width * 0.84
        pg.arrow((xx, o.y0 + 1), (xx, o.y1 - 1), color=BLUE)
        pg.inches(f"{k} Height", xx - 10, o.y0 + o.height * 0.62, "HEIGHT (top to floor)", BLUE)
        yv = top + 180
        pg.text(L + 22, yv + 8, "ROOM:", 8.5, bold=True)
        pg.field(f"{k} Room", (L + 58, yv - 2, L + 200, yv + 11), size=8.5)
        yc = top - 4 + RS
        pg.text(CX, yc, "DOOR TYPE:", 8.5, bold=True)
        yc = visual_options(pg, k, "Type", CX + 6, yc + 6, 180,
                            [("Sliding (bypass)", bypass_icon), ("Folding (bi-fold)", bifold_icon)],
                            icon_h=40, aspect=1.2)
        yc = choices(pg, k, CX, yc - 6, [
            ("OR MIRRORED / WARDROBE:", ["Mirrored bypass", "Mirrored swing", "White vinyl board"], 2),
            ("MIRROR FRAME COLOR:", ["White", "Satin nickel", "Aztec gold", NOT_SURE], 2),
            ("DOOR STYLE:", ["Flat (smooth)", "Shaker", "Colonist (6 panel)", "Mirrored", NOT_SURE], 2),
            ("PANELS ON EACH DOOR:", ["Flat", "2", "6", "Other"], 4),
            ("HOW MANY DOORS?", ["2", "4", "Not sure"], 3),
            ("FINISH:", ["Primecoat (paint)", "White", "Not sure"], 2),
            ("NEED THE TRACK / HARDWARE?", ["Yes", "No"], 2),
            ("BALL CATCH (keeps doors closed)?", ["Yes", "No"], 2),
        ])
        pg.line((CX - 8, y), (CX - 8, max(yc, yv + 14)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(yv + 30, yc + 8))
        return y3 + 2


# --------------------------------------------------------------------------- baseboard + casing
TRIM_LETTERS = list("ABCDEFGHIJ")


def _tile_base(pg, x, y, w):
    cx = x + w / 2
    pg.rect((cx - 46, y, cx + 46, y + 40), color=(0.5, 0.5, 0.5), fill=WALL)
    pg.rect((cx - 46, y + 33, cx + 46, y + 40), color=DARK, fill=(1, 1, 1), width=0.6)
    pg.arrow((cx - 46, y + 28), (cx + 46, y + 28), width=0.9)
    pg.line((cx - 52, y + 40), (cx + 52, y + 40), color=DARK, width=1.2)


def _tile_casing(pg, x, y, w):
    cx = x + w / 2
    pg.rect((cx - 40, y, cx + 40, y + 44), color=(0.5, 0.5, 0.5), fill=WALL)
    pg.rect((cx - 15, y + 6, cx + 15, y + 44), color=DARK, fill=(1, 1, 1), width=2.5)
    pg.rect((cx - 12, y + 9, cx + 12, y + 44), color=DARK, fill=WOOD, width=0.6)


class Trim(Form):
    subtitle, item, prefix = "Baseboard & Casing", "UNIT / ROOM", "T"
    steps = [
        ("1  BASEBOARD = FEET OF WALL", "Measure along the floor, wall by wall, and add it up. Skip door "
         "openings. Write total feet.", _tile_base),
        ("2  CASING = DOOR OPENINGS", "Count the door openings that need trim around them, and if it's "
         "one side or both sides.", _tile_casing),
        ("3  ROUND UP", "Round feet up to the next whole foot - we add a little extra for cuts.", None),
        ("4  PICK THE PROFILE", "Find the shape that matches below (A-K), or check \"Match what's there\". "
         "Plain flat / square boards = K: write the thickness and height.", None),
    ]

    def intro(self, pg, y):
        from .catalog import TRIM_PROFILES
        names = {k: d.split(" ")[0] for k, d in TRIM_PROFILES}
        return photo_strip(pg, y, "TRIM PROFILES  (pick the letter in each item)", "trim_profile",
                           TRIM_LETTERS + ["K"], keys=[x.lower() for x in TRIM_LETTERS + ["K"]], h=46)

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        y += 16
        pg.text(L + 8, y, "ROOM(S):", 8.5, bold=True)
        pg.field(f"{k} Room", (L + 56, y - 10, L + 250, y + 3), size=8.5)
        pg.text(L + 262, y, "BASEBOARD:", 8.5, bold=True)
        pg.field(f"{k} Baseboard Feet", (L + 326, y - 10, L + 370, y + 3), align=1)
        pg.text(L + 374, y, "feet", 8)
        pg.text(L + 404, y, "CASING:", 8.5, bold=True)
        pg.field(f"{k} Casing Openings", (L + 446, y - 10, L + 476, y + 3), align=1)
        pg.text(L + 480, y, "door openings", 8)
        y += 16
        pg.text(L + 8, y, "CASING ON:", 8.5, bold=True)
        pg.check(f"{k} Casing One Side", L + 70, y, "One side", 8)
        pg.check(f"{k} Casing Both Sides", L + 140, y, "Both sides", 8)
        pg.text(L + 262, y, "PROFILE:", 8.5, bold=True)
        for i, ltr in enumerate(TRIM_LETTERS):
            pg.check(f"{k} Profile {ltr}", L + 310 + i * 24, y, ltr, 8)
        y += 14
        pg.check(f"{k} Profile K", L + 8, y, "K FLAT / SQUARE:", 8)
        pg.text(L + 100, y, "thick", 7.5)
        pg.field(f"{k} Flat Thickness", (L + 120, y - 9, L + 160, y + 3), size=8, align=1)
        pg.text(L + 164, y, "x tall", 7.5)
        pg.field(f"{k} Flat Height", (L + 186, y - 9, L + 226, y + 3), size=8, align=1)
        pg.text(L + 229, y, "in", 7.5)
        pg.check(f"{k} Profile Match", L + 310, y, "Match what's there / not sure", 8)
        y3 = notes_row(pg, k, y + 16, x1=R - 6)
        return y3 + 2


# --------------------------------------------------------------------------- door hardware
def _knob_icon(kind):
    def draw(pg, r):
        r = fitz.Rect(r)
        cx, cy = r.x0 + r.width / 2, r.y0 + r.height / 2
        pg.rect((cx - 7, r.y0, cx + 7, r.y1), color=DARK, fill=WOOD, width=0.5)
        pg.p.draw_circle((cx, cy), 6, color=DARK, fill=(0.75, 0.75, 0.75), width=0.6)
        if kind == "privacy":
            pg.p.draw_circle((cx, cy), 1.6, color=DARK, fill=(0.3, 0.3, 0.3), width=0.4)
        if kind == "entry":
            pg.p.draw_circle((cx, cy - 1), 1.3, color=DARK, fill=DARK, width=0.3)
            pg.line((cx, cy - 1), (cx, cy + 2.5), color=DARK, width=1)
    return draw


def _tile_backset(pg, x, y, w):
    cx = x + w / 2
    pg.rect((cx - 40, y + 6, cx + 30, y + 40), color=DARK, fill=WOOD, width=0.6)
    pg.p.draw_circle((cx + 10, y + 23), 7, color=DARK, fill=(1, 1, 1), width=0.6)
    pg.arrow((cx + 10, y + 2), (cx + 30, y + 2), width=0.8)
    pg.line((cx + 10, y + 2), (cx + 10, y + 23), color=GREY, width=0.4, dashes="[1.5] 0")


class DoorHardware(Form):
    subtitle, item, prefix = "Door Hardware", "UNIT", "H"
    steps = [
        ("1  COUNT BY TYPE", "Passage = no lock (closets, halls). Privacy = push-button lock (bed / "
         "bath). Entry = key lock.", None),
        ("2  PICK A STYLE + FINISH", "Choose a style from the pictures below and a finish.", None),
        ("3  BACKSET", "Measure from the edge of the door to the CENTER of the knob hole.", _tile_backset),
        ("4  NOT SURE?", "Check \"Match what's there\" and we'll match your current hardware.", None),
    ]

    def intro(self, pg, y):
        return photo_strip(pg, y, "HARDWARE STYLES", "hardware_style",
                           ["Marina", "Soma", "Lombard", "Sea Cliff", "Tiburon"], h=52)

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 6
        pg.text(L + 8, top + 10, "HOW MANY OF EACH:", 8.5, bold=True)
        for i, (lab, kind) in enumerate((("Passage (no lock)", "passage"), ("Privacy (lock)", "privacy"),
                                         ("Entry (key)", "entry"))):
            x = L + 14 + i * 118
            _knob_icon(kind)(pg, fitz.Rect(x, top + 18, x + 26, top + 52))
            pg.text(x + 32, top + 30, lab, 8, bold=True)
            pg.field(f"{k} Qty {kind.title()}", (x + 32, top + 34, x + 72, top + 48), align=1)
        yc = choices(pg, k, CX, top - 4, [
            ("STYLE:", ["Marina", "Soma", "Lombard", "Sea Cliff", "Tiburon", NOT_SURE], 2),
            ("FINISH:", ["Satin Nickel", "Matte Black", "Not sure"], 2),
            ('BACKSET:', ['2-3/8"', '2-3/4"', "Not sure"], 3),
        ])
        pg.line((CX - 8, y), (CX - 8, yc), color=GREY, width=1)
        y3 = notes_row(pg, k, max(top + 66, yc + 8))
        return y3 + 2


# --------------------------------------------------------------------------- bath hardware
class BathHardware(Form):
    subtitle, item, prefix = "Bath Hardware", "UNIT", "B"
    steps = [
        ("1  COUNT THE PIECES", "How many towel bars, towel rings and paper holders for each unit.", None),
        ("2  TOWEL BAR SIZE", "Measure the old bar end to end (most are 18\" or 24\").", None),
        ("3  PICK A COLLECTION", "Choose the collection from the pictures below.", None),
        ("4  NOT SURE?", "Check \"Match what's there\" and we'll match it.", None),
    ]

    def intro(self, pg, y):
        from .catalog import BATH_COLLECTIONS
        return photo_strip(pg, y, "BATH HARDWARE COLLECTIONS", "bath_collection",
                           [n for n, _ in BATH_COLLECTIONS], h=52)

    def card(self, pg, n, y):
        from .catalog import BATH_COLLECTIONS
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 6
        pg.text(L + 8, top + 10, "HOW MANY OF EACH:", 8.5, bold=True)
        for i, lab in enumerate(("Towel Bar", "Towel Ring", "Paper Holder")):
            x = L + 14 + i * 118
            pg.text(x, top + 28, lab, 8, bold=True)
            pg.field(f"{k} Qty {lab}", (x + 62, top + 18, x + 98, top + 32), align=1)
        pg.text(L + 14, top + 52, "TOWEL BAR SIZE:", 8, bold=True)
        for i, sz in enumerate(('18"', '24"', "Not sure")):
            pg.check(f"{k} Bar Size {sz}", L + 100 + i * 60, top + 52, sz, 8)
        yc = choices(pg, k, CX, top - 4, [
            ("COLLECTION:", [n for n, _ in BATH_COLLECTIONS] + [NOT_SURE], 2),
        ])
        pg.line((CX - 8, y), (CX - 8, yc), color=GREY, width=1)
        y3 = notes_row(pg, k, max(top + 72, yc + 8))
        return y3 + 2


# --------------------------------------------------------------------------- countertops
def counter_plan(pg, r, layout, labels=True):
    """Top view of a countertop: straight / L / U / island, back edge along the wall."""
    r = fitz.Rect(r)
    d = min(r.width, r.height) * 0.28
    top = (0.86, 0.84, 0.80)
    if labels:
        pg.rect((r.x0, r.y0, r.x1, r.y0 + 4), color=None, fill=(0.6, 0.6, 0.6))   # wall
    shapes = {"Straight": [(r.x0, r.y0 + 4, r.x1, r.y0 + 4 + d)],
              "L-Shape": [(r.x0, r.y0 + 4, r.x1, r.y0 + 4 + d), (r.x0, r.y0 + 4, r.x0 + d, r.y1)],
              "U-Shape": [(r.x0, r.y0 + 4, r.x1, r.y0 + 4 + d), (r.x0, r.y0 + 4, r.x0 + d, r.y1),
                          (r.x1 - d, r.y0 + 4, r.x1, r.y1)],
              "Island": [(r.x0 + r.width * 0.15, r.y0 + r.height * 0.35, r.x1 - r.width * 0.15,
                          r.y0 + r.height * 0.35 + d)]}[layout]
    for s_ in shapes:
        pg.rect(s_, color=DARK, fill=top, width=0.6)


class Countertops(Form):
    subtitle, item, prefix = "Countertops", "COUNTERTOP", "K"
    max_pages = 2
    steps = [
        ("1  PICK THE SHAPE", "Straight, L or U - the side against the wall is A, then B, then C.",
         lambda pg, x, y, w: counter_plan(pg, fitz.Rect(x + w / 2 - 30, y, x + w / 2 + 30, y + 42), "U-Shape")),
        ("2  MEASURE ALONG THE WALL", "Measure each run along the wall, end to end - include the corner.",
         None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  DEPTH + SINK", "Depth = wall to front edge. Sink = left end to the CENTER of the sink.", None),
    ]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        # big U drawing with a box on each run (fill the ones you have)
        r = fitz.Rect(L + 40, top + 18, L + 330, top + 180)
        counter_plan(pg, r, "U-Shape")
        d = min(r.width, r.height) * 0.28
        pg.text(r.x0 + r.width / 2, r.y0 - 4, "WALL", 6.5, bold=True, color=DARK, center=True)
        ay = r.y0 + 4 + d / 2
        pg.arrow((r.x0 + 1, ay), (r.x1 - 1, ay))
        pg.inches(f"{k} Run A", r.x0 + r.width / 2, ay, "A  (back wall)")
        bx = r.x0 + d / 2
        pg.arrow((bx, r.y0 + 5), (bx, r.y1 - 1), color=BLUE)
        pg.inches(f"{k} Run B", bx + 46, r.y0 + r.height * 0.70, "B  (left side)", BLUE)
        cx_ = r.x1 - d / 2
        pg.arrow((cx_, r.y0 + 5), (cx_, r.y1 - 1), color=GREEN)
        pg.inches(f"{k} Run C", cx_ - 46, r.y0 + r.height * 0.70, "C  (right side)", GREEN)
        pg.text(L + 40, r.y1 + 12, "Straight top? Fill A only.  L-shape? Fill A + B.", 7, color=DARK)
        y2 = r.y1 + 30
        lw = pg.text(L + 22, y2 + 3, "DEPTH:", 8.5, bold=True)
        pg.inches(f"{k} Depth", L + 22 + lw + 40, y2)
        pg.text(L + 150, y2 + 3, "SINK CENTER FROM LEFT END:", 8.5, bold=True)
        pg.inches(f"{k} Sink Center", L + 318, y2)
        yc = choices(pg, k, CX, top - 4, [
            ("ROOM:", ["Kitchen", "Bathroom vanity"], 2),
            ("SHAPE:", ["Straight", "L-Shape", "U-Shape", "Island"], 2),
            ("MATERIAL:", ["Laminate", "Synthetic", "Thinscape", "Custom", NOT_SURE], 2),
            ("SINK:", ["Single bowl", "Double bowl", "Vanity bowl", "No sink"], 2),
            ("BACKSPLASH (on the wall):", ["Yes", "No"], 2),
            ("END SPLASH:", ["None", "Left", "Right", "Both"], 2),
        ])
        pg.text(CX, yc + 12, "Color: we'll bring samples.", 7.5, color=DARK)
        yc += 16
        pg.line((CX - 8, y), (CX - 8, max(yc, y2 + 12)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(y2 + 24, yc + 8))
        return y3 + 2


# --------------------------------------------------------------------------- cabinets
def cabinet_icon(kind):
    def draw(pg, r):
        r = fitz.Rect(r)
        body = (0.97, 0.96, 0.93)
        if kind in ("Wall Cabinet",):
            r = fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * 0.7)
        pg.rect(r, color=DARK, fill=body, width=0.6)
        w, h = r.width, r.height
        if kind == "Drawer Base":
            for i in range(3):
                pg.rect((r.x0 + 2, r.y0 + 2 + i * (h - 4) / 3, r.x1 - 2, r.y0 + 2 + (i + 1) * (h - 4) / 3 - 1),
                        color=DARK, width=0.4)
            return
        dtop = r.y0 + 2
        if kind in ("Base Cabinet", "Single Box", "Double Box"):
            pg.rect((r.x0 + 2, r.y0 + 2, r.x1 - 2, r.y0 + h * 0.22), color=DARK, width=0.4)
            dtop = r.y0 + h * 0.22 + 2
        doors = 1 if kind in ("Single Box",) else 2
        if kind == "Extended Double Box with Sink":
            doors = 3
        dw = (w - 4) / doors
        for i in range(doors):
            pg.rect((r.x0 + 2 + i * dw, dtop, r.x0 + 2 + (i + 1) * dw - 1, r.y1 - 2), color=DARK, width=0.4)
        if "Sink" in kind:
            pg.rect((r.x0 + w * 0.3, r.y0 - 3, r.x1 - w * 0.3, r.y0 + 1), color=DARK, fill=(0.8, 0.8, 0.85),
                    width=0.4)
    return draw


def cabinet_measure(pg, k, x, top):
    """Front of a cabinet + side view, with width / height / depth boxes on the arrows."""
    f = fitz.Rect(x + 30, top + 26, x + 170, top + 150)
    pg.rect(f, color=DARK, fill=(0.97, 0.96, 0.93), width=1)
    pg.rect((f.x0 + 4, f.y0 + 4, f.x1 - 4, f.y0 + 26), color=DARK, width=0.5)
    pg.rect((f.x0 + 4, f.y0 + 30, f.x0 + f.width / 2 - 1, f.y1 - 4), color=DARK, width=0.5)
    pg.rect((f.x0 + f.width / 2 + 1, f.y0 + 30, f.x1 - 4, f.y1 - 4), color=DARK, width=0.5)
    pg.text(f.x0 + f.width / 2, f.y1 + 10, "FRONT", 6.5, bold=True, color=DARK, center=True)
    pg.arrow((f.x0, f.y0 - 10), (f.x1, f.y0 - 10))
    pg.inches(f"{k} Width", f.x0 + f.width / 2, f.y0 - 10, "WIDTH")
    pg.arrow((f.x1 + 12, f.y0), (f.x1 + 12, f.y1), color=BLUE)
    pg.inches(f"{k} Height", f.x1 + 12, f.y0 + f.height * 0.55, "HEIGHT", BLUE)
    sv = fitz.Rect(x + 232, top + 26, x + 300, top + 150)
    pg.rect(sv, color=DARK, fill=(0.93, 0.92, 0.88), width=1)
    pg.rect((sv.x0 - 6, sv.y0, sv.x0, sv.y1), color=None, fill=(0.6, 0.6, 0.6))
    pg.text(sv.x0 + sv.width / 2, sv.y1 + 10, "SIDE", 6.5, bold=True, color=DARK, center=True)
    pg.arrow((sv.x0, sv.y1 - 18), (sv.x1, sv.y1 - 18), color=GREEN)
    pg.inches(f"{k} Depth", sv.x0 + sv.width / 2, sv.y1 - 38, "DEPTH (wall to front)", GREEN)
    return top + 166


class PrefabCabinets(Form):
    subtitle, item, prefix = "Pre-Fab Cabinets", "CABINET", "F"
    steps = [
        ("1  WHAT KIND?", "Kitchen cabinet or bathroom vanity - then pick the box that matches.", None),
        ("2  MEASURE THE OLD BOX", "Width across the front, height floor (or bottom) to top, depth "
         "wall to front.", None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  PICK THE STYLE", "Choose a door style from the pictures below.", None),
    ]

    def intro(self, pg, y):
        from .catalog import CABINET_STYLES
        return photo_strip(pg, y, "CABINET DOOR STYLES", "cabinet_style", CABINET_STYLES, h=50)

    def card(self, pg, n, y):
        from .catalog import CABINET_STYLES
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 6
        yb = cabinet_measure(pg, k, L + 6, top) + 10
        pg.text(L + 8, yb + 6, "KITCHEN:", 8.5, bold=True)
        kit = ["Base Cabinet", "Sink Base", "Drawer Base", "Wall Cabinet"]
        visual_options(pg, k, "Item", L + 64, yb - 6, 300, [(t, cabinet_icon(t)) for t in kit], icon_h=26,
                       aspect=1.3)
        yb += 46
        pg.text(L + 8, yb + 6, "VANITY:", 8.5, bold=True)
        van = ["Single Box", "Double Box", "Double Box with Sink", "Extended Double Box with Sink"]
        labels = ["Single Box", "Double Box", "Double + Sink", "Ext. Double + Sink"]
        visual_options(pg, k, "Item", L + 64, yb - 6, 300,
                       [(lab, cabinet_icon(t)) for lab, t in zip(labels, van)], icon_h=26, aspect=1.3)
        yb += 46
        yc = choices(pg, k, CX, top - 4, [
            ("DOOR STYLE:", CABINET_STYLES + [NOT_SURE], 2),
            ("DOOR HINGES ON:", ["Left", "Right", "Pair (2 doors)"], 3),
            dict(title="CONCEALED HINGE BORES?", opts=["Yes", "No"], cols=2, name="Hinge Cuts"),
        ])
        yc = hinge_cut_rows(pg, k, CX, yc)
        yc = choices(pg, k, CX, yc - 4, [
            ("DRAWERS INCLUDED:", ["Yes", "No"], 2),
            ("DOORS ONLY (keep the box)?", ["Yes", "No"], 2),
        ])
        pg.line((CX - 8, y), (CX - 8, max(yc, yb)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(yb + 10, yc + 8))
        return y3 + 2


def _tile_wall_space(pg, x, y, w):
    cx = x + w / 2
    pg.rect((cx - 46, y, cx + 46, y + 42), color=(0.5, 0.5, 0.5), fill=WALL)
    pg.rect((cx - 40, y + 22, cx + 40, y + 42), color=DARK, fill=(0.97, 0.96, 0.93), width=0.5)
    pg.arrow((cx - 46, y + 15), (cx + 46, y + 15), width=0.8)


class CustomCabinets(Form):
    subtitle, item, prefix = "Custom Cabinets", "CABINET", "M"
    steps = [
        ("1  MEASURE THE SPACE", "Measure the wall space the cabinet fills, wall to wall (or to the next "
         "cabinet).", _tile_wall_space),
        ("2  HEIGHT + DEPTH", "Height floor to top (wall cabinets: top to bottom). Depth wall to front.",
         None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  TELL US THE LAYOUT", "How many doors and drawers - or snap a photo and email it with this form.",
         None),
    ]

    def intro(self, pg, y):
        from .catalog import CABINET_STYLES
        return photo_strip(pg, y, "CABINET DOOR STYLES", "cabinet_style", CABINET_STYLES, h=50)

    def card(self, pg, n, y):
        from .catalog import CABINET_STYLES
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 6
        yb = cabinet_measure(pg, k, L + 6, top)
        pg.text(L + 8, yb + 6, "DOORS:", 8.5, bold=True)
        pg.field(f"{k} Doors", (L + 52, yb - 4, L + 82, yb + 9), align=1)
        pg.text(L + 98, yb + 6, "DRAWERS:", 8.5, bold=True)
        pg.field(f"{k} Drawers", (L + 152, yb - 4, L + 182, yb + 9), align=1)
        pg.text(L + 198, yb + 6, "FINISH / COLOR:", 8.5, bold=True)
        pg.field(f"{k} Finish", (L + 276, yb - 4, CX - 14, yb + 9), size=8.5)
        yb += 18
        yc = choices(pg, k, CX, top - 4, [
            ("ROOM:", ["Kitchen", "Bathroom vanity"], 2),
            ("CABINET:", ["Base", "Sink base", "Wall", "Tall / pantry", "Vanity"], 2),
            ("DOOR STYLE:", CABINET_STYLES + [NOT_SURE], 2),
            ("DOOR HINGES ON:", ["Left", "Right", "Pair (2 doors)"], 3),
            dict(title="CONCEALED HINGE BORES?", opts=["Yes", "No"], cols=2, name="Hinge Cuts"),
        ])
        yc = hinge_cut_rows(pg, k, CX, yc)
        yc = choices(pg, k, CX, yc - 4, [
            dict(title="REFACE EXISTING (THERMOFOIL)?", opts=["Yes", "No"], cols=2, name="Refacing"),
        ])
        pg.text(CX, yc + 10, "Refacing: we'll bring thermofoil color samples.", 7, color=DARK)
        yc += 14
        pg.line((CX - 8, y), (CX - 8, max(yc, yb)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(yb + 10, yc + 8))
        return y3 + 2



# --------------------------------------------------------------------------- shower doors
def tub_shower(pg, r, glass=True):
    """Front view: tiled wall, tub along the bottom, glass door above it. Returns the opening rect."""
    r = fitz.Rect(r)
    pg.rect(r, color=(0.5, 0.5, 0.5), fill=(0.93, 0.94, 0.95))
    tub = fitz.Rect(r.x0 + 6, r.y1 - r.height * 0.24, r.x1 - 6, r.y1)
    pg.rect(tub, color=DARK, fill=(1, 1, 1), width=0.8)
    o = fitz.Rect(tub.x0, r.y0 + 10, tub.x1, tub.y0)
    if glass:
        pg.rect((o.x0 + 2, o.y0, o.x0 + o.width / 2 + 4, o.y1), color=(0.45, 0.5, 0.55), fill=GLASS, width=0.6)
        pg.rect((o.x0 + o.width / 2 - 4, o.y0 + 2, o.x1 - 2, o.y1), color=(0.45, 0.5, 0.55), fill=GLASS, width=0.6)
    return o


def _tile_shower(pg, x, y, w):
    o = tub_shower(pg, (x + w / 2 - 44, y, x + w / 2 + 44, y + 44))
    pg.arrow((o.x0 + 1, o.y0 + 8), (o.x1 - 1, o.y0 + 8), width=0.9)


def _tile_shower_h(pg, x, y, w):
    o = tub_shower(pg, (x + w / 2 - 44, y, x + w / 2 + 44, y + 44))
    pg.arrow((o.x0 + o.width * 0.75, o.y0 + 1), (o.x0 + o.width * 0.75, o.y1 - 1), color=BLUE, width=0.9)


class ShowerDoors(Form):
    subtitle, item, prefix = "Shower Doors & Enclosures", "SHOWER", "S"
    steps = [
        ("1  WIDTH", "Measure wall to wall where the door sits - on top of the tub or the shower curb.",
         _tile_shower),
        ("2  HEIGHT", "Top of the tub (or curb) up to where you want the glass to stop.", _tile_shower_h),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  PICK THE TYPE", "Sliding, swing door or a full enclosure. Many styles and custom options - "
         "add a photo if you can.", None),
    ]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        o = tub_shower(pg, (L + 30, top + 4, L + 330, top + 150))
        yy = o.y0 + 16
        pg.arrow((o.x0 + 1, yy), (o.x1 - 1, yy))
        pg.inches(f"{k} Width", o.x0 + o.width * 0.36, yy, "WIDTH (wall to wall)")
        xx = o.x0 + o.width * 0.86
        pg.arrow((xx, o.y0 + 1), (xx, o.y1 - 1), color=BLUE)
        pg.inches(f"{k} Height", xx - 12, o.y0 + o.height * 0.62, "HEIGHT (tub up)", BLUE)
        yv = top + 164
        pg.text(L + 22, yv + 8, "ROOM:", 8.5, bold=True)
        pg.field(f"{k} Room", (L + 58, yv - 2, L + 200, yv + 11), size=8.5)
        yc = choices(pg, k, CX, top - 4, [
            ("GOES ON:", ["Tub", "Shower stall"], 2),
            ("TYPE:", ["Sliding (bypass)", "Swing / pivot door", "Enclosure (door + panels)", "Fixed panel",
                       NOT_SURE], 2),
            ("FRAME:", ["Framed", "Semi-frameless", "Frameless", "Not sure"], 2),
            ("FINISH:", ["Chrome", "Satin nickel", "Matte black", "Not sure"], 2),
            ("GLASS:", ["Clear", "Obscure / rain", "Not sure"], 2),
        ])
        pg.line((CX - 8, y), (CX - 8, max(yc, yv + 14)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(yv + 24, yc + 6))
        return y3 + 2


# --------------------------------------------------------------------------- mirror frames (Mirror Mate)
def mirror_pic(pg, r, frame=True):
    r = fitz.Rect(r)
    pg.rect(r, color=(0.5, 0.5, 0.5), fill=WALL)
    m = fitz.Rect(r.x0 + r.width * 0.12, r.y0 + 8, r.x1 - r.width * 0.12, r.y1 - r.height * 0.22)
    if frame:
        pg.rect((m.x0 - 5, m.y0 - 5, m.x1 + 5, m.y1 + 5), color=DARK, fill=(0.45, 0.33, 0.22), width=0.6)
    pg.rect(m, color=DARK, fill=GLASS, width=0.6)
    pg.rect((r.x0 + 4, r.y1 - r.height * 0.14, r.x1 - 4, r.y1), color=DARK, fill=(0.97, 0.96, 0.93), width=0.6)
    return m


def _tile_mirror(pg, x, y, w):
    m = mirror_pic(pg, (x + w / 2 - 40, y, x + w / 2 + 40, y + 44), frame=False)
    pg.arrow((m.x0 + 1, m.y0 + m.height / 2), (m.x1 - 1, m.y0 + m.height / 2), width=0.9)


def _tile_mirror_frame(pg, x, y, w):
    mirror_pic(pg, (x + w / 2 - 40, y, x + w / 2 + 40, y + 44), frame=True)


class MirrorFrames(Form):
    subtitle, item, prefix = "Mirror Frames", "MIRROR", "Y"
    steps = [
        ("1  MEASURE THE GLASS", "Measure the mirror itself, edge to edge - not the wall.", _tile_mirror),
        ("2  WIDTH + HEIGHT", "One width (side to side) and one height (top to bottom).", None),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  ADD A FRAME", "Mirror Mate frame kits go right over your mirror - no tools, 70+ finishes. "
         "We'll bring samples.", _tile_mirror_frame),
    ]

    def card(self, pg, n, y):
        k = f"{self.prefix}{n}"
        y = card_bar(pg, y, f"{self.item} {n}", k)
        top = y + 8
        m = mirror_pic(pg, (L + 40, top + 4, L + 320, top + 150), frame=False)
        yy = m.y0 + m.height * 0.3
        pg.arrow((m.x0 + 1, yy), (m.x1 - 1, yy))
        pg.inches(f"{k} Width", m.x0 + m.width * 0.42, yy, "WIDTH (glass edge to edge)")
        xx = m.x0 + m.width * 0.85
        pg.arrow((xx, m.y0 + 1), (xx, m.y1 - 1), color=BLUE)
        pg.inches(f"{k} Height", xx - 12, m.y0 + m.height * 0.7, "HEIGHT", BLUE)
        yc = choices(pg, k, CX, top - 4, [
            ("ROOM:", ["Bathroom", "Other"], 2),
            ("MIRROR IS:", ["Clipped to wall", "Glued to wall", "Medicine cabinet", "Not sure"], 2),
            ("FRAME FINISH:", ["Show me samples", "Match another unit"], 1),
        ])
        pg.line((CX - 8, y), (CX - 8, max(yc, top + 152)), color=GREY, width=1)
        y3 = notes_row(pg, k, max(top + 166, yc + 8))
        return y3 + 2


FORMS = [HorizontalBlinds, VerticalBlinds, RollerShades, InteriorDoors, ExteriorDoors, ClosetDoors,
         WindowScreens, PatioScreens, SlidingPatioDoors, Trim, DoorHardware, BathHardware, Countertops,
         PrefabCabinets, CustomCabinets, ShowerDoors, MirrorFrames]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for cls in FORMS:
        f = cls()
        print(f.build(os.path.join(OUT_DIR, f"Customer Order Form - {f.subtitle}.pdf")))
    from .cabinet_layout import CUSTOMER_FORM, customer_form_pdf     # box-by-box kitchen layout (landscape)
    print(customer_form_pdf(os.path.join(OUT_DIR, CUSTOMER_FORM)))


if __name__ == "__main__":
    main()
