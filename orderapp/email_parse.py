"""Read an order request email and work out customer, PO, units and line items."""
import re
from collections import Counter

from .catalog import FORMS, forms_for_product
from .db import find_customer, norm_name, q
from .importer import guess_product, read_table
from .pdf_fill import read_filled_form

FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com", "live.com",
             "msn.com", "comcast.net", "cox.net", "me.com"}
COLORS = ["alabaster", "white", "almond", "bronze", "champagne", "mill", "charcoal", "grey", "gray", "black",
          "oak legacy", "walnut legacy", "primecoat", "embossed primecoat"]

NUM = r"\d{1,3}(?:\s*-?\s*\d{1,2}/\d{1,2}|\.\d+)?"
RE_DIM = re.compile(rf"({NUM})\s*(?:\"|''|in\.?|inches)?\s*(?:x|X|×|by)\s*({NUM})\s*(?:\"|''|in\.?|inches)?")
RE_WH = re.compile(rf"\b(?:w|width)\s*[:=]?\s*({NUM}).{{0,20}}?\b(?:h|height)\s*[:=]?\s*({NUM})", re.I)
RE_PO = re.compile(r"\bP\.?\s?O\.?\s*(?:#|number|num|no\.?)?\s*[:\-]?\s*#?\s*([A-Z0-9][A-Z0-9\-]{2,})", re.I)
RE_UNIT = re.compile(r"\b(?:unit|apt\.?|apartment|ste\.?|suite)\s*#?\s*:?\s*([A-Z]?\d{1,5}[A-Z]?(?:-\d{1,4})?)\b",
                     re.I)
RE_HASH_UNIT = re.compile(r"(?:^|\s)#\s?(\d{2,5}[A-Z]?)\b")
RE_QTY = re.compile(r"(?:\bqty\.?\s*[:=]?\s*(\d{1,3})\b|^\s*\(?(\d{1,3})\)?\s*(?:x\s+|pcs?\b|ea\b|-\s)|\b(\d{1,3})\s*(?:pcs?|ea|each)\b)",
                    re.I)
RE_QTY_WORD = re.compile(r"(?<![\d/.\-#])\b(\d{1,2})\s+(?:new\s+|replacement\s+|more\s+)?(?:[a-z\-]+\s+)?"
                         r"(?:blinds?|doors?|screens?|verticals?|horizontals?|panels?|pieces|bypass|bi-?pass|"
                         r"pre-?hung|baseboards?|cabinets?|vertical|horizontal|mini)\b", re.I)
RE_PHONE = re.compile(r"\(?\b(\d{3})\)?[\s.\-]?(\d{3})[\s.\-](\d{4})\b")


def _clean_num(s):
    return re.sub(r"\s+", " ", s.strip()).replace(" -", "-").replace("- ", "-")


def _domain(email):
    return (email or "").split("@")[-1].lower().strip()


def match_customer(sender, text):
    c = find_customer(email=sender)
    if c:
        return c, "sender email"
    dom = _domain(sender)
    if dom and dom not in FREE_MAIL:
        r = q("""SELECT c.* FROM customers c LEFT JOIN contacts k ON k.customer_id=c.id
                 WHERE lower(c.email) LIKE ? OR lower(k.email) LIKE ? LIMIT 1""", (f"%@{dom}", f"%@{dom}"), one=True)
        if r:
            return r, f"email domain @{dom}"
    t = norm_name(text)
    best = None
    for r in q("SELECT * FROM customers"):
        n = norm_name(r["name"])
        if len(n) >= 4 and n in t:
            if best is None or len(n) > len(norm_name(best["name"])):
                best = r
    if best:
        return best, "name in email"
    return None, ""


def parse_lines(text):
    items = []
    current_unit = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(">"):
            continue
        low = line.lower()
        if low.startswith(("from:", "sent:", "to:", "cc:", "subject:")):
            break  # quoted reply below
        units = RE_UNIT.findall(line) or RE_HASH_UNIT.findall(line)
        if units:
            current_unit = units[0]
        product = guess_product(line)
        dims = RE_DIM.findall(line) or RE_WH.findall(line)
        if not product and not dims:
            continue
        it = {"product": product, "unit": units[0] if units else current_unit}
        if dims:
            it["width"], it["height"] = _clean_num(dims[0][0]), _clean_num(dims[0][1])
        m = RE_QTY.search(line)
        if m:
            it["qty"] = next(g for g in m.groups() if g)
        else:
            m = RE_QTY_WORD.search(line)
            if m:
                it["qty"] = m.group(1)
        for col in COLORS:
            if re.search(rf"\b{col}\b", low):
                it["color"] = col.title().replace("Gray", "Grey")
                break
        if re.search(r"\b(left hand|lh)\b", low):
            it["swing"] = "Left Hand"
        elif re.search(r"\b(right hand|rh)\b", low):
            it["swing"] = "Right Hand"
        if "solid" in low:
            it["core"] = "Solid Core"
        elif "hollow" in low:
            it["core"] = "Hollow Core"
        for room in ("master", "bedroom", "kitchen", "living", "bath", "patio", "closet", "laundry"):
            if room in low:
                it["room"] = {"living": "Living Room", "master": "Master Bedroom"}.get(room, room.title())
                break
        it["source_line"] = line[:160]
        items.append(it)
    return items


def parse_email(subject, body, sender="", sender_name="", attachments=None):
    attachments = attachments or []
    text = f"{subject}\n{body or ''}"
    res = {"subject": subject, "sender": sender, "sender_name": sender_name, "customer_id": None,
           "customer_name": "", "match_reason": "", "po": "", "units": [], "items": [], "form_key": None,
           "filled_forms": [], "tables": [], "contact": {"name": sender_name, "email": sender, "phone": ""}}
    cust, why = match_customer(sender, text)
    if cust:
        res.update(customer_id=cust["id"], customer_name=cust["name"], match_reason=why)
    m = RE_PO.search(text)
    if m:
        res["po"] = m.group(1).strip("-")
    units = []
    for u in RE_UNIT.findall(text) + RE_HASH_UNIT.findall(text):
        if u not in units:
            units.append(u)
    res["units"] = units
    res["items"] = parse_lines(body or "")
    ph = RE_PHONE.findall(body or "")
    if ph:
        res["contact"]["phone"] = "{}-{}-{}".format(*ph[-1])  # signature is usually last
    for fname, data in attachments:
        lf = fname.lower()
        if lf.endswith(".pdf"):
            fk, fdata = read_filled_form(data)
            if fk:
                res["filled_forms"].append({"file": fname, "form_key": fk, "data": fdata})
                if not res["customer_id"]:
                    hc = find_customer(fdata["header"].get("name"), fdata["header"].get("acct"))
                    if hc:
                        res.update(customer_id=hc["id"], customer_name=hc["name"], match_reason="attached form")
                if not res["po"] and fdata["header"].get("po"):
                    res["po"] = fdata["header"]["po"]
        elif lf.endswith((".csv", ".xlsx")):
            try:
                headers, rows = read_table(data, fname)
                res["tables"].append({"file": fname, "headers": headers, "rows": len(rows)})
            except Exception:  # noqa: BLE001
                pass
    prods = Counter(i["product"] for i in res["items"] if i.get("product"))
    if res["filled_forms"]:
        res["form_key"] = res["filled_forms"][0]["form_key"]
    elif prods:
        top = prods.most_common(1)[0][0]
        res["form_key"] = (forms_for_product(top) or [None])[0]
    if not res["units"] and res["items"]:
        res["units"] = sorted({i["unit"] for i in res["items"] if i.get("unit")})
    return res


def form_title(fk):
    return FORMS[fk]["title"] if fk in FORMS else ""
