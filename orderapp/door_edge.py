"""Door slab edge: a whole-inch size like 30 x 80 or 36 x 80 normally means a FULL BOOK (square edge) door;
the standard door is a beveled-edge door 1/4" narrower and 1/2" shorter (29-3/4 x 79-1/2, 35-3/4 x 79-1/2)."""
import re

BEVELED = "Beveled Edge (standard)"
FULL_BOOK = "Full Book (square edge)"
EDGES = [BEVELED, FULL_BOOK]
FULL_HEIGHTS = (80, 84, 96)


def inches(v):
    """'29-3/4' / '29 3/4' / '29.75' / '29 3/4"' -> 29.75; None if unreadable."""
    s = str(v or "").replace('"', "").replace("in", "").replace("½", " 1/2").replace("¼", " 1/4") \
        .replace("¾", " 3/4").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)(?:\s*[- ]\s*(\d+)/(\d+))?$", s)
    if not m:
        m2 = re.match(r"^(\d+)/(\d+)$", s)
        return int(m2.group(1)) / int(m2.group(2)) if m2 else None
    v = float(m.group(1))
    if m.group(2):
        v += int(m.group(2)) / int(m.group(3))
    return v


def frac(v):
    whole = int(v)
    rest = round((v - whole) * 8) / 8
    names = {0.125: "1/8", 0.25: "1/4", 0.375: "3/8", 0.5: "1/2", 0.625: "5/8", 0.75: "3/4", 0.875: "7/8"}
    return f"{whole}" + (f"-{names[rest]}" if rest in names else "")


def guess(width, height):
    """Which edge the size points to (or '' when it can't tell)."""
    w, h = inches(width), inches(height)
    if not w or not h:
        return ""
    if w == int(w) and h in FULL_HEIGHTS:
        return FULL_BOOK
    if h in (79.5, 83.5, 95.5):
        return BEVELED
    return ""


def standard_size(width, height):
    """30 x 80 -> '29-3/4 x 79-1/2' (the beveled-edge door for a full-book size); '' if not a full-book size."""
    w, h = inches(width), inches(height)
    if not w or not h or guess(width, height) != FULL_BOOK:
        return ""
    return f"{frac(w - 0.25)} x {frac(h - 0.5)}"


def fill_blocks(blocks):
    """Set the edge on lines that don't have one yet, from their size. Returns the number set."""
    n = 0
    for b in blocks or []:
        if isinstance(b, dict) and not b.get("door_edge"):
            g = guess(b.get("width"), b.get("height"))
            if g:
                b["door_edge"] = g
                n += 1
    return n
