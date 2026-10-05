"""Manager Order Forms: simple fillable PDFs a property's maintenance manager fills out.

Not the internal rep forms. Each item is a picture of what to measure with the fill-in
boxes sitting right on the arrows, plus big check-one choices. The manager emails it to
the rep and the order can go in the same day.

    python -m orderapp.manager_forms            -> forms/manager/*.pdf (blank, fillable)
"""
import os

import pymupdf as fitz

HERE = os.path.dirname(__file__)
COMPANY = os.path.join(HERE, "forms", "company", "OF-Horizontal Blind Order Form.pdf")
OUT_DIR = os.path.join(HERE, "forms", "manager")

CYAN = (0.655, 1.0, 1.0)
NAVY = (0.0, 0.0, 0.4)
GREY = (0.85, 0.85, 0.85)
WALL = (0.90, 0.88, 0.84)
GLASS = (0.80, 0.91, 0.98)
RED = (0.80, 0.10, 0.10)
L, R = 18, 594
RS = 12.5     # row spacing of the check-one lists
PH = 150      # height of the measuring pictures
FRACTIONS = [" ", "1/8", "1/4", "3/8", "1/2", "5/8", "3/4", "7/8"]
REP_EMAIL = "Taylor@ApartmentInterior.net"
EMAIL_JS = ('this.mailDoc({bUI: true, cTo: "%s", cSubject: "Manager Order - " + this.documentFileName, '
            'cMsg: "Measurements attached."});' % REP_EMAIL)

BLIND_STYLES = ["All Vinyl", "Vinyl Plus", "All Metal", "Faux Wood", 'Basswood 2"', 'Basswood 2-1/2"',
                '1" Metal Plus Mini', '2" Metal Plus', "Not sure - match what's there"]
BLIND_COLORS = ["White", "Alabaster", "Other"]
ROOMS = ["Bedroom", "Kitchen", "Living Room", "Other"]
MOUNTS = ["Inside the opening (most common)", "Outside / on the wall"]


class Page:
    def __init__(self, page):
        self.p = page
        self.widgets = []          # added last so page fonts stay intact

    # ---- printed things
    def text(self, x, y, s, size=8, bold=False, color=(0, 0, 0), center=False):
        font = "hebo" if bold else "helv"
        if center:
            x -= fitz.get_text_length(s, fontname=font, fontsize=size) / 2
        self.p.insert_text((x, y), s, fontsize=size, fontname=font, color=color)
        return fitz.get_text_length(s, fontname=font, fontsize=size)

    def wrap(self, x, y, s, width, size=7.5, lead=9.2, bold_first=None):
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

    def bar(self, y, title, h=14):
        self.p.draw_rect(fitz.Rect(L, y, R, y + h), color=None, fill=NAVY)
        self.text(L + 6, y + h - 4, title, 9, bold=True, color=(1, 1, 1))

    def arrow(self, a, b, color=RED, width=1.1):
        a, b = fitz.Point(a), fitz.Point(b)
        self.p.draw_line(a, b, color=color, width=width)
        for tip, tail in ((a, b), (b, a)):
            d = tail - tip
            n = abs(d) or 1
            u = fitz.Point(d.x / n, d.y / n)
            v = fitz.Point(-u.y, u.x)
            pts = [tip, tip + u * 6 + v * 3, tip + u * 6 - v * 3]
            self.p.draw_polyline(pts + [tip], color=color, fill=color, width=0.5)

    def window(self, r, inset=14):
        """Wall with a window opening in it; returns the opening rect."""
        r = fitz.Rect(r)
        self.p.draw_rect(r, color=(0.5, 0.5, 0.5), fill=WALL, width=0.6)
        o = fitz.Rect(r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset - 6)
        self.p.draw_rect(o, color=(0.3, 0.3, 0.3), fill=(1, 1, 1), width=1)
        g = fitz.Rect(o.x0 + 5, o.y0 + 5, o.x1 - 5, o.y1 - 5)
        self.p.draw_rect(g, color=(0.55, 0.55, 0.55), fill=GLASS, width=0.6)
        self.p.draw_line((g.x0 + g.width / 2, g.y0), (g.x0 + g.width / 2, g.y1), color=(0.55, 0.55, 0.55), width=0.6)
        sill = fitz.Rect(o.x0 - 6, o.y1, o.x1 + 6, o.y1 + 5)
        self.p.draw_rect(sill, color=(0.4, 0.4, 0.4), fill=(0.75, 0.72, 0.68), width=0.5)
        return o

    # ---- fill-in things
    def field(self, name, rect, size=9, fill=CYAN, align=0):
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_name = name
        w.rect = fitz.Rect(rect)
        w.text_font, w.text_fontsize = "Helv", size
        w.fill_color, w.border_color, w.border_width = fill, (0, 0, 0), 0.6
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

    def inches(self, name, cx, cy, tag=None):
        """[ whole ] [ frac v ] "   centered on (cx, cy)"""
        x = cx - 33
        if tag:
            self.text(cx, cy - 10, tag, 6.5, bold=True, color=RED, center=True)
        self.p.draw_rect(fitz.Rect(x - 2, cy - 8, x + 68, cy + 8), color=None, fill=(1, 1, 1))
        self.field(name, (x, cy - 7, x + 26, cy + 7), size=9, align=1)
        self.fraction(name + " frac", (x + 28, cy - 7, x + 61, cy + 7))
        self.text(x + 62.5, cy + 3, '"', 9, bold=True)

    def check(self, name, x, y, label, size=8):
        w = fitz.Widget()
        w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
        w.field_name = name
        w.rect = fitz.Rect(x, y - 8.5, x + 9.5, y + 1)
        w.fill_color, w.border_color, w.border_width = (1, 1, 1), (0, 0, 0), 0.8
        w.field_value = False
        self.widgets.append(w)
        return self.text(x + 13, y, label, size)

    def labeled(self, x, y, label, name, width, size=8.5):
        lw = self.text(x, y, label, size, bold=True)
        self.field(name, (x + lw + 4, y - 10, x + width, y + 3))

    def finish(self):
        for w in self.widgets:
            self.p.add_widget(w)


# --------------------------------------------------------------------------- shared parts
def header(pg, title, subtitle, logo_src):
    p = pg.p
    p.draw_rect(fitz.Rect(L, 8, R, 760), color=(0, 0, 0), width=1.5)
    p.draw_rect(fitz.Rect(352, 8, R, 52), color=None, fill=(0, 0, 0))
    pg.text(473, 26, "MANAGER ORDER FORM", 14, bold=True, color=(1, 1, 1), center=True)
    pg.text(473, 44, subtitle.upper(), 12.5, bold=True, color=(1, 1, 1), center=True)
    y = 66
    for lab, name in (("PROPERTY:", "Property"), ("YOUR NAME:", "Manager Name"),
                      ("PHONE:", "Manager Phone"), ("EMAIL:", "Manager Email")):
        pg.labeled(356, y, lab, name, R - 360)
        y += 15
    pg.labeled(356, y, "DATE:", "Date", 470 - 360)
    pg.labeled(478, y, "PO #:", "PO Number", R - 482)
    return logo_src


def logo(pg, src):
    # same AIS logo as the company forms (copied last so fonts on this page aren't disturbed)
    pg.p.show_pdf_page(fitz.Rect(40, 18, 309, 117), src, 0, clip=fitz.Rect(68, 22, 337, 121))


def footer(pg, page_no, pages):
    p = pg.p
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


def ruler(pg, x, y, w):
    """1 inch of tape measure with 1/8 marks, showing 35-3/8"."""
    p = pg.p
    p.draw_rect(fitz.Rect(x, y, x + w, y + 22), color=(0.3, 0.3, 0.3), fill=(1.0, 0.86, 0.2), width=0.6)
    step = (w - 16) / 8
    for i in range(9):
        h = 12 if i in (0, 8) else 8 if i == 4 else 6 if i % 2 == 0 else 4
        xx = x + 8 + i * step
        p.draw_line((xx, y), (xx, y + h), color=(0, 0, 0), width=0.6)
    pg.text(x + 8, y + 20, "35", 6.5, bold=True, center=True)
    pg.text(x + 8 + 8 * step, y + 20, "36", 6.5, bold=True, center=True)
    xx = x + 8 + 3 * step
    p.draw_line((xx, y - 5), (xx, y + 14), color=RED, width=1.2)
    pg.text(xx, y - 7, '35-3/8"', 7, bold=True, color=RED, center=True)


def side_view(pg, x, y, w, h, labels=True):
    """Cross-section of the wall: the arrow is the depth of the window opening."""
    p = pg.p
    p.draw_rect(fitz.Rect(x, y, x + w * 0.62, y + h), color=(0.5, 0.5, 0.5), fill=WALL, width=0.6)
    gx = x + w * 0.62
    p.draw_rect(fitz.Rect(gx, y - 4, gx + 6, y + h + 4), color=(0.3, 0.3, 0.3), fill=GLASS, width=0.6)
    if labels:
        pg.text(gx + 9, y + h / 2 + 2, "glass", 6, color=(0.3, 0.3, 0.3))
        pg.text(x + 2, y - 3, "room side", 6, color=(0.3, 0.3, 0.3))
    pg.arrow((x + 1, y + h / 2), (gx - 1, y + h / 2))


# --------------------------------------------------------------------------- horizontal blinds
def how_to_measure_blinds(pg, y):
    pg.bar(y, "HOW TO MEASURE  (about 2 minutes per window)")
    y += 12
    tiles = 4
    tw = (R - L) / tiles
    for i in range(1, tiles):
        pg.p.draw_line((L + i * tw, y), (L + i * tw, y + 104), color=GREY, width=1)
    steps = [
        ("1  INSIDE THE OPENING", "Use a metal tape measure. Measure inside the window opening, "
         "wall to wall - not the old blind."),
        ("2  3 WIDTHS + 3 HEIGHTS", "Windows aren't square. Measure width at the top, middle and bottom "
         "and height at the left, center and right. Fill in every box - we use the smallest."),
        ("3  READ TO 1/8\"", "Write the whole inches, then pick the fraction. Don't round and "
         "don't subtract anything - we do that."),
        ("4  DEPTH", "Measure how deep the opening is, from the wall surface back to the window frame."),
    ]
    for i, (head, body) in enumerate(steps):
        x = L + i * tw + 8
        pg.text(x, y + 13, head, 8.5, bold=True, color=NAVY)
        pg.wrap(x, y + 25, body, tw - 16, 7.3, 8.8)
    # little pictures under steps 1-4
    y -= 10
    x0 = L + 30
    o = pg.window((x0, y + 62, x0 + 80, y + 114), inset=10)
    pg.arrow((o.x0 + 1, o.y0 + o.height / 2), (o.x1 - 1, o.y0 + o.height / 2))
    x1 = L + tw + 24
    o = pg.window((x1, y + 70, x1 + 96, y + 116), inset=9)
    for k in range(3):
        yy = o.y0 + 4 + k * (o.height - 8) / 2
        pg.arrow((o.x0 + 1, yy), (o.x1 - 1, yy), width=0.8)
    for k in range(3):
        xx = o.x0 + 6 + k * (o.width - 12) / 2
        pg.arrow((xx, o.y0 + 1), (xx, o.y1 - 1), color=(0.1, 0.4, 0.8), width=0.8)
    ruler(pg, L + 2 * tw + 22, y + 86, tw - 44)
    side_view(pg, L + 3 * tw + 28, y + 76, 80, 28)
    return y + 118


def blind_card(pg, n, y):
    """One window: unit/room/qty, the measuring picture with boxes on it, and check-one choices."""
    p = pg.p
    k = f"W{n}"
    pg.bar(y, f"WINDOW {n}", 18)
    pg.text(96, y + 13, "UNIT #", 8.5, bold=True, color=(1, 1, 1))
    pg.field(f"{k} Unit", (128, y + 3, 196, y + 15))
    pg.text(206, y + 13, "HOW MANY OF THIS SAME SIZE?", 8.5, bold=True, color=(1, 1, 1))
    pg.field(f"{k} Qty", (358, y + 3, 392, y + 15), align=1)
    pg.text(402, y + 13, "(usually 1)", 7.5, color=(1, 1, 1))
    y += 18

    # ---- WIDTH picture
    top = y + 8
    pg.text(L + 94, top + 9, "WIDTH  (wall to wall)", 8.5, bold=True, color=RED, center=True)
    o = pg.window((L + 8, top + 14, L + 180, top + 14 + PH), inset=12)
    for j, (tag, frac) in enumerate((("TOP", 0.1), ("MIDDLE", 0.5), ("BOTTOM", 0.9))):
        yy = o.y0 + o.height * frac
        pg.arrow((o.x0 + 1, yy), (o.x1 - 1, yy))
        pg.inches(f"{k} Width {tag.title()}", o.x0 + o.width / 2, yy, None)
        pg.text(o.x0 + 9, yy - 4, tag, 6, bold=True, color=RED)

    # ---- HEIGHT picture
    hx = L + 190
    pg.text(hx + 86, top + 9, "HEIGHT  (top to sill)", 8.5, bold=True, color=(0.1, 0.4, 0.8), center=True)
    o = pg.window((hx, top + 14, hx + 172, top + 14 + PH), inset=12)
    blue = (0.1, 0.4, 0.8)
    for j, (tag, frac, boxy) in enumerate((("LEFT", 0.1, 0.22), ("CENTER", 0.5, 0.5), ("RIGHT", 0.9, 0.78))):
        xx = o.x0 + o.width * frac
        pg.arrow((xx, o.y0 + 1), (xx, o.y1 - 1), color=blue)
        cx = min(max(xx, o.x0 + 36), o.x1 - 36)
        cy = o.y0 + o.height * boxy
        if cx != xx:   # short leader from the arrow to its box
            p.draw_line((xx, cy), (cx + (-34 if cx > xx else 36), cy), color=blue, width=0.8, dashes="[2] 0")
        pg.inches(f"{k} Height {tag.title()}", cx, cy)
        pg.text(cx, cy - 10, tag, 6, bold=True, color=blue, center=True)

    # ---- choices
    cx0 = L + 372
    p.draw_line((cx0 - 6, y), (cx0 - 6, y + PH + 50), color=GREY, width=1)
    yy = top + 9
    pg.text(cx0, yy, "ROOM:", 8.5, bold=True)
    for i, r in enumerate(ROOMS):
        xx, ry = cx0 + (i % 2) * 96, yy + RS + (i // 2) * RS
        lw = pg.check(f"{k} Room {r}", xx, ry, r)
        if r == "Bedroom":
            pg.field(f"{k} Bedroom #", (xx + 13 + lw + 3, ry - 9, xx + 13 + lw + 28, ry + 2), size=8, align=1)
    pg.field(f"{k} Room Other", (cx0 + 96 + 45, yy + 2 * RS - 9, R - 6, yy + 2 * RS + 2), size=8)
    yy += 3 * RS + 5
    pg.text(cx0, yy, "BLIND STYLE (check one):", 8.5, bold=True)
    for i, s in enumerate(BLIND_STYLES):
        if i < 8:
            pg.check(f"{k} Style {s}", cx0 + (i % 2) * 96, yy + RS + (i // 2) * RS, s, 7.8)
        else:
            pg.check(f"{k} Style {s}", cx0, yy + RS + 4 * RS, s, 7.8)
    yy += 13 + 5 * RS + 6
    pg.text(cx0, yy, "COLOR:", 8.5, bold=True)
    for i, c in enumerate(BLIND_COLORS):
        pg.check(f"{k} Color {c}", cx0 + i * 64, yy + RS, c)
    pg.field(f"{k} Color Other", (cx0 + 2 * 64 + 40, yy + 4, R - 6, yy + 15), size=8)
    yy += 2 * RS
    pg.text(cx0, yy, "BLIND GOES:", 8.5, bold=True)
    for i, m in enumerate(MOUNTS):
        pg.check(f"{k} Mount {m.split()[0]}", cx0, yy + RS + i * RS, m)

    # ---- depth + notes row under the pictures
    y2 = top + 14 + PH + 14
    side_view(pg, L + 14, y2 - 7, 46, 14, labels=False)
    lw = pg.text(L + 78, y2 + 3, "DEPTH OF OPENING:", 8.5, bold=True)
    pg.inches(f"{k} Depth", L + 78 + lw + 40, y2)
    pg.text(L + 78 + lw + 78, y2 + 3, "(wall surface to window frame)", 7, color=(0.35, 0.35, 0.35))
    y3 = y2 + 19
    pg.text(L + 8, y3 + 3, "NOTES:", 8.5, bold=True)
    pg.field(f"{k} Notes", (L + 46, y3 - 7, cx0 - 12, y3 + 7), size=8.5)
    return y3 + 10


def build_horizontal_blinds(out_path):
    src = fitz.open(COMPANY)
    doc = fitz.open()
    # page 1: header, how-to, window 1
    pg = Page(doc.new_page(width=612, height=792))
    header(pg, "MANAGER ORDER FORM", "Horizontal Blinds", src)
    y = how_to_measure_blinds(pg, 132)
    y = blind_card(pg, 1, y + 4)
    y = blind_card(pg, 2, y + 6)
    assert y < 758, y
    footer(pg, 1, 2)
    logo(pg, src)
    pg.finish()
    # page 2: windows 2 and 3
    pg2 = Page(doc.new_page(width=612, height=792))
    pg2.p.draw_rect(fitz.Rect(L, 8, R, 760), color=(0, 0, 0), width=1.5)
    pg2.p.draw_rect(fitz.Rect(L, 8, R, 30), color=None, fill=(0, 0, 0))
    pg2.text(L + 8, 23, "MANAGER ORDER FORM - HORIZONTAL BLINDS  (continued)", 11, bold=True, color=(1, 1, 1))
    pg2.text(R - 120, 23, "More windows? Use another form.", 7, color=(1, 1, 1))
    y = 36
    for n in (3, 4, 5):
        y = blind_card(pg2, n, y + 4)
    assert y < 758, y
    footer(pg2, 2, 2)
    pg2.finish()
    doc.set_metadata({"title": "Manager Order Form - Horizontal Blinds", "creator": "Apartment Interior Supply"})
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    src.close()
    return out_path


FORMS = {"Manager Order Form - Horizontal Blinds.pdf": build_horizontal_blinds}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, fn in FORMS.items():
        print(fn(os.path.join(OUT_DIR, name)))


if __name__ == "__main__":
    main()
