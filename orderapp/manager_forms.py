"""Manager Order Forms: simple fillable PDFs a property's maintenance manager fills out.

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
EMAIL_JS = ('this.mailDoc({bUI: true, cTo: "%s", cSubject: "Manager Order - " + this.documentFileName, '
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
    pg.text(473, 26, "MANAGER ORDER FORM", 14, bold=True, color=(1, 1, 1), center=True)
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
    pg.text(L + 8, 23, f"MANAGER ORDER FORM - {subtitle.upper()}  (continued)", 11, bold=True, color=(1, 1, 1))
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
    """Check-one lists. group = (title, options, cols) or dict(title, opts, cols, other, icons)."""
    for g in groups:
        if isinstance(g, tuple):
            g = dict(zip(("title", "opts", "cols"), g))
        cols = g.get("cols", 2)
        cw = width / cols
        y += RS
        pg.text(x, y, g["title"], 8.5, bold=True)
        name = g.get("name", g["title"].split("(")[0].strip(" :").title())
        for i, o in enumerate(g["opts"]):
            ox = x + (i % cols) * cw
            oy = y + RS + (i // cols) * RS
            span = cw if (i % cols) < cols - 1 else width - (i % cols) * cw
            if o == NOT_SURE or len(o) > 26:            # long ones get a whole row
                ox, span = x, width
            lw = pg.check(f"{k} {name} {o}", ox, oy, o, 7.8, maxw=span - 2)
            if o in g.get("other", ()):                  # "Other ____"
                pg.field(f"{k} {name} {o} (describe)", (ox + 16 + lw, oy - 9, ox + span - 4, oy + 2), size=8)
        y += math.ceil(len(g["opts"]) / cols) * RS + (RS if any(
            (o == NOT_SURE or len(o) > 26) and (i % cols) for i, o in enumerate(g["opts"])) else 0) + 3
    return y


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
              casing=True, knob=True):
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
    for f in (0.10, 0.48, 0.84):
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
        doc.set_metadata({"title": f"Manager Order Form - {self.subtitle}", "creator": "Apartment Interior Supply"})
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


class HorizontalBlinds(Form):
    subtitle, item, prefix = "Horizontal Blinds", "WINDOW", "W"
    steps = [
        ("1  INSIDE THE OPENING", "Use a metal tape measure. Measure inside the window opening, wall to "
         "wall - not the old blind.", _tile_inside),
        ("2  1 WIDTH + 1 HEIGHT", "Measure the width (wall to wall) and the height (top of the opening to "
         "the sill). One of each.", _tile_wh),
        ('3  READ TO 1/8"', None, tile_ruler),
        ("4  DEPTH", "Measure how deep the opening is, from the wall surface back to the window frame.",
         _tile_depth),
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
        depth_row(pg, k, y2)
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

EXTERIOR_STYLES = [("6 Panel steel", "steel6"), ("Flat steel", "hc-primecoat"),
                   ('Half glass\n22" x 36"', "glass:half"), ('3/4 glass\n22" x 48"', "glass:3/4"),
                   ('Full glass\n22" x 64"', "glass:full"), ('Half + blinds\n22" x 36"', "glass:half+b"),
                   ('3/4 + blinds\n22" x 48"', "glass:3/4+b"), ('Full + blinds\n22" x 64"', "glass:full+b"),
                   ("Not sure / match", None)]


def door_measure(pg, k, x, top, exterior=False):
    """Big door with measuring arrows. Hinge side on the left. Returns bottom y."""
    r = fitz.Rect(x + 128, top + 14, x + 226, top + 232)
    d = door_slab(pg, r, deadbolt=exterior, peep=exterior)
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
        pg.inches(f"{k} Hinge {i + 1}", bx, by, f"TOP TO HINGE {i + 1}", GREEN)
    pg.text(x + 4, r.y0 + 2, "Measure from the TOP of the", 6.5, color=DARK)
    pg.text(x + 4, r.y0 + 10, "door to the TOP of each hinge", 6.5, color=DARK)
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
    groups = [("DOOR THICKNESS:", ['1-3/8" (most inside doors)', '1-3/4" (most outside doors)'], 1)]
    if exterior:
        groups += [("DOOR OPENS:", ["In (into the unit)", "Out"], 2)]
    else:
        groups += [("CORE (knock on it):", ["Hollow", "Solid", "Not sure"], 3),
                   ("FINISH:", ["Primecoat (paint)", "Embossed Primecoat", "Oak", "Walnut"], 2)]
    groups += [("NEED THE FRAME TOO (pre-hung)?", ["No - door only", "Yes - door + frame"], 2),
               ("HARDWARE:", (["None", "Entry knob", "Deadbolt", "Peephole"] if exterior
                              else ["None", "Passage (no lock)", "Privacy (lock)"]), 2),
               ('HINGE SIZE (tall):', ['3-1/2"', '4"', "Not sure"], 3)]
    yc = choices(pg, k, CX, yc - 4, groups)
    ys = max(yb, yc) + 6
    pg.line((CX - 8, y), (CX - 8, ys - 4), color=GREY, width=1)
    # style strip
    pg.rect((L, ys - 2, R, ys + 1.5), color=None, fill=GREY)
    pg.text(L + 6, ys + 12, "DOOR STYLE (check one):", 8.5, bold=True)
    names = EXTERIOR_STYLES if exterior else INTERIOR_STYLES
    if exterior:
        pg.text(L + 130, ys + 12, "Standard door glass sizes shown. Glass with blinds = blinds built in "
                "between the glass.", 6.8, color=DARK)
    ye = visual_options(pg, k, "Style", L + 4, ys + 18, R - L - 8, _style_strip_items(names), icon_h=60,
                        cols=len(names))
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


FORMS = [HorizontalBlinds, VerticalBlinds, RollerShades, InteriorDoors, ExteriorDoors, WindowScreens,
         PatioScreens, SlidingPatioDoors]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for cls in FORMS:
        f = cls()
        print(f.build(os.path.join(OUT_DIR, f"Manager Order Form - {f.subtitle}.pdf")))


if __name__ == "__main__":
    main()
