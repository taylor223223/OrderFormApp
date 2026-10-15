"""Orders vs. estimates (quote requests) on the same forms, and the five tap-to-set statuses.

An estimate is an order row with kind='quote': same form and lines, but it's titled "... Quote Request",
its PDF carries a QUOTE REQUEST banner, the email asks for pricing, and it's left out of order totals.
"""
# (button label, kind it sets or None to keep, stored status)
TAPS = [("Order ready", "order", "Ready"), ("Quote ready", "quote", "Ready"), ("Order sent", "order", "Sent"),
        ("Quote sent", "quote", "Sent"), ("Delivered", None, "Delivered")]
TAP_LABELS = [t[0] for t in TAPS]


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


def stamp_quote(pdf_bytes):
    """Put a QUOTE REQUEST banner across the top of every page."""
    import pymupdf as fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        w = page.rect.width
        r = fitz.Rect(w / 2 - 190, 3, w / 2 + 190, 21)
        page.draw_rect(r, color=(0.75, 0.1, 0.1), fill=(1, 0.93, 0.93), width=1.2, overlay=True)
        txt = "QUOTE REQUEST - PLEASE PRICE - NOT AN ORDER"
        tw = fitz.get_text_length(txt, fontname="hebo", fontsize=10.5)
        page.insert_text((w / 2 - tw / 2, 16), txt, fontsize=10.5, fontname="hebo", color=(0.75, 0.1, 0.1),
                         overlay=True)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out
