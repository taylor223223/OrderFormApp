"""Account types: the CRM covers properties AND the contractors, management companies, vendors and other
companies Taylor works with. Plus "works with" links between accounts (a contractor <-> the properties
they work at) and "properties this company manages" for management companies."""
import re

from .db import now, q, x

PROPERTY = "Property"
CONTRACTOR = "Contractor"
MGMT = "Management Company"
VENDOR = "Vendor / Supplier"
OTHER = "Other"
ACCOUNT_TYPES = [PROPERTY, CONTRACTOR, MGMT, VENDOR, OTHER]
PLURAL = {PROPERTY: "Properties", CONTRACTOR: "Contractors", MGMT: "Management Cos.", VENDOR: "Vendors",
          OTHER: "Other"}
ICON = {PROPERTY: "🏢", CONTRACTOR: "🛠️", MGMT: "🏛️", VENDOR: "📦", OTHER: "•"}
TRADES = ["General contractor", "Make-ready / turns", "Remodel / renovation", "Restoration", "Paint",
          "Flooring", "Countertops", "Cabinets", "Glass / mirrors", "Doors / trim install", "Handyman",
          "Plumbing", "Electrical", "HVAC", "Cleaning", "Landscaping"]

_CONTRACTOR = re.compile(r"\b(construction|contracting|contractors?|restoration|remodel(ing)?|resurfacing|builders?|"
                         r"handyman|maintenance(?! super)|renovation resources|paint(ing)? &|painting|plumbing|"
                         r"electric(al)?|roofing|flooring|glass)\b", re.I)
_MGMT = re.compile(r"\b(property management|community management|asset management|real estate|realty|"
                   r"investments?|properties)\b", re.I)


def clean_type(v):
    """Whatever someone typed / imported -> one of ACCOUNT_TYPES ('' if blank)."""
    s = str(v or "").strip().lower()
    if not s:
        return ""
    for t in ACCOUNT_TYPES:
        if s == t.lower():
            return t
    if any(w in s for w in ("apartment", "property", "community", "condo", "complex", "hoa", "senior", "student")):
        return MGMT if "management" in s or "mgmt" in s else PROPERTY
    if any(w in s for w in ("contract", "construct", "builder", "trade", "sub", "install", "restor", "remodel")):
        return CONTRACTOR
    if any(w in s for w in ("mgmt", "management", "owner", "operator", "pm co")):
        return MGMT
    if any(w in s for w in ("vendor", "supplier", "supply", "manufactur", "distribut")):
        return VENDOR
    return OTHER


def guess_type(name):
    """Best guess from the account name (used once on old data; never overrides a type someone picked)."""
    n = str(name or "")
    if "(renovations)" in n.lower() or "(rehab)" in n.lower():
        return PROPERTY
    if _CONTRACTOR.search(n) or n.strip().lower() in ("turn", "cortland build"):
        return CONTRACTOR
    if _MGMT.search(n):
        return MGMT
    if n.strip().lower() in ("cash account", "cash sale", "cash customer"):
        return OTHER
    return PROPERTY


def type_of(c):
    try:
        return c["acct_type"] or PROPERTY
    except (KeyError, IndexError):
        return PROPERTY


def counts():
    rows = q("SELECT coalesce(nullif(acct_type,''), ?) t, COUNT(*) n FROM customers GROUP BY t", (PROPERTY,))
    d = {r["t"]: r["n"] for r in rows}
    return [(t, d.get(t, 0)) for t in ACCOUNT_TYPES]


# ------------------------------------------------------------------ links between accounts
def links_for(cid):
    return q("""SELECT l.id, l.note, c.id AS cid, c.name, c.acct_type, c.city, c.phone
                FROM account_links l JOIN customers c ON c.id = CASE WHEN l.a_id=? THEN l.b_id ELSE l.a_id END
                WHERE l.a_id=? OR l.b_id=? ORDER BY c.name COLLATE NOCASE""", (cid, cid, cid))


def link(a, b, note=""):
    if not a or not b or a == b:
        return None
    lo, hi = sorted((int(a), int(b)))
    have = q("SELECT id FROM account_links WHERE a_id=? AND b_id=?", (lo, hi), one=True)
    if have:
        if note:
            x("UPDATE account_links SET note=? WHERE id=?", (note, have["id"]))
        return have["id"]
    return x("INSERT INTO account_links(a_id, b_id, note, created) VALUES (?,?,?,?)", (lo, hi, note, now()))


def unlink(lid):
    x("DELETE FROM account_links WHERE id=?", (lid,))


def _key(s):
    s = re.sub(r"\b(llc|inc|corp|co|company|the|group|residential|properties|property management|management)\b",
               " ", str(s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def managed_by(c):
    """For a management company: the properties whose Mgmt Co. field names it."""
    k = _key(c["name"])
    if len(k) < 3:
        return []
    rows = q("""SELECT id, name, city, mgmt, status FROM customers WHERE id<>? AND coalesce(mgmt,'')<>''
                AND coalesce(nullif(acct_type,''), 'Property')='Property' ORDER BY name COLLATE NOCASE""",
             (c["id"],))
    return [r for r in rows if _key(r["mgmt"]) == k or (len(k) >= 5 and k in _key(r["mgmt"]))]


def mgmt_account_for(c):
    """For a property: the Management Company account its Mgmt Co. field points at (if one exists)."""
    k = _key(c["mgmt"]) if c["mgmt"] else ""
    if len(k) < 3:
        return None
    for r in q("SELECT id, name FROM customers WHERE acct_type=?", (MGMT,)):
        if _key(r["name"]) == k:
            return r
    return None
