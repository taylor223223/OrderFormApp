"""Orders vs. estimates (quote requests) on the same forms, and the five tap-to-set statuses.

An estimate is an order row with kind='quote': same form and lines, but it's titled "... Quote Request",
its PDF carries a QUOTE REQUEST banner, the email asks for pricing, and it's left out of order totals.
"""
# (button label, kind it sets or None to keep, stored status)
TAPS = [("Order ready", "order", "Ready"), ("Quote ready", "quote", "Ready"), ("Order sent", "order", "Sent"),
        ("Quote sent", "quote", "Sent"), ("Delivered", None, "Delivered")]
TAP_LABELS = [t[0] for t in TAPS]
# how the buttons sit: each order step over its quote step, Delivered on its own
TAP_GRID = [["Order ready", "Order sent", "Delivered"], ["Quote ready", "Quote sent", None]]


def is_quote(o):
    try:
        return (o["kind"] or "order") == "quote"
    except (KeyError, IndexError, TypeError):
        return False


def status_label(status, kind="order"):
    q = (kind or "order") == "quote"
    word = "Quote" if q else "Order"
    return {"Ready": f"{word} ready", "Sent": f"{word} sent", "Draft": f"{word} draft",
            "Confirmed": f"{word} received"}.get(status or "", status or "")


def tap_for(status, kind):
    lab = status_label(status, kind)
    return lab if lab in TAP_LABELS else None


def kind_title(title, kind):
    """'Door Order Form' -> 'Door Quote Request' for estimates (and back)."""
    t = (title or "").replace(" Quote Request", " Order Form")
    if (kind or "order") != "quote":
        return t
    return t.replace(" Order Form", " Quote Request") if " Order Form" in t else f"{t} (Quote Request)"


def quote_word(title):
    """'Door Order Form' -> 'Door Quote Form', 'BLIND ORDER FORM' -> 'BLIND QUOTE FORM',
    'PRE-HUNG DOOR FORM' -> 'PRE-HUNG DOOR QUOTE FORM'."""
    import re
    t = (title or "").strip()
    up = t.upper() == t and any(c.isalpha() for c in t)
    if re.search(r"\border\b", t, re.I):
        return re.sub(r"\border\b", lambda m: "QUOTE" if m.group(0).isupper() else "Quote", t, count=1, flags=re.I)
    if re.search(r"\bform\b", t, re.I):
        return re.sub(r"\bform\b", "QUOTE FORM" if up else "Quote Form", t, count=1, flags=re.I)
    return t + (" - QUOTE" if up else " - Quote")


def _title_spots(page):
    """Where the form's title is printed: [(rect of the title words, bar rect or None, color, capitals?)].
    The title is the words inside the black title bar at the top; a form without a bar: its largest words
    at the top that say Order / Form."""
    import pymupdf as fitz
    bars = [d["rect"] for d in page.get_drawings() if d.get("fill") and sum(d["fill"]) < 0.35 and
            d["rect"].width > 140 and 10 < d["rect"].height < 45 and d["rect"].y0 < 110]
    words = [w for w in page.get_text("words") if w[3] < 120]
    groups = {}
    for w in words:
        c = fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
        inside = [b for b in bars if c in b]
        if inside:
            b = min(inside, key=lambda r: r.width * r.height)
            groups.setdefault(tuple(b), []).append(w)
    out = []
    for b, ws in groups.items():
        r = fitz.Rect(ws[0][:4])
        for w in ws[1:]:
            r |= fitz.Rect(w[:4])
        letters = [ch for w in ws for ch in w[4] if ch.isalpha()]
        out.append((r, fitz.Rect(b), (1, 1, 1), sum(ch.isupper() for ch in letters) > len(letters) * 0.7))
    if out:
        return out
    best = None       # no bar: the biggest "... Order Form" words near the top
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            txt = "".join(sp["text"] for sp in ln["spans"])
            if ln["bbox"][1] < 100 and ("order" in txt.lower() or "form" in txt.lower()):
                size = max(sp["size"] for sp in ln["spans"])
                if size >= 12 and (best is None or size > best[0]):
                    c = ln["spans"][0]["color"]
                    best = (size, fitz.Rect(ln["bbox"]), ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
                            txt.upper() == txt)
    return [(best[1], None, best[2], best[3])] if best else []


def stamp_quote(pdf_bytes, title="Order Form"):
    """A quote prints with QUOTE in the form's title bar where the form says ORDER ("Door Quote Form",
    "HORIZONTAL BLIND QUOTE FORM"). A page without a title bar gets a QUOTE banner across the top instead.
    title: the form's name (catalog title)."""
    import pymupdf as fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        spots = _title_spots(page)
        if spots:
            for r, _, _, _ in spots:
                page.add_redact_annot(r + (-0.5, -0.5, 0.5, 0.5), fill=False)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            for r, bar, color, upper in spots:
                new = quote_word(title.upper() if upper else title)
                room = (bar.width - 10) if bar else max(r.width + 40, 200)
                size = min(18.0, r.height * 0.85)
                while size > 7 and fitz.get_text_length(new, fontname="hebo", fontsize=size) > room:
                    size -= 0.5
                tw = fitz.get_text_length(new, fontname="hebo", fontsize=size)
                cx = (bar.x0 + bar.x1) / 2 if bar else (r.x0 + r.x1) / 2
                base = (r.y0 + r.y1) / 2 + size * 0.35
                page.insert_text((cx - tw / 2, base), new, fontsize=size, fontname="hebo", color=color, overlay=True)
            continue
        w = page.rect.width
        r = fitz.Rect(w / 2 - 190, 3, w / 2 + 190, 21)
        page.draw_rect(r, color=(0.75, 0.1, 0.1), fill=(1, 0.93, 0.93), width=1.2, overlay=True)
        txt = "QUOTE - PLEASE PRICE - NOT AN ORDER"
        tw = fitz.get_text_length(txt, fontname="hebo", fontsize=10.5)
        page.insert_text((w / 2 - tw / 2, 16), txt, fontsize=10.5, fontname="hebo", color=(0.75, 0.1, 0.1),
                         overlay=True)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
