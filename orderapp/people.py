"""People at a property: who you talked to and their position, kept on the customer's profile.

Anything that logs a person (contact log, quick log, inline edits) goes through save_person(), so the
name and position always land on the property's Contacts list without a separate step.
"""
from .db import q, x

POSITIONS = ["Maint. Super", "Maintenance", "Property Manager", "Office"]


def clean_role(role):
    """Tidy common spellings to the standard positions; anything else is kept as typed."""
    r = (role or "").strip()
    low = r.lower().replace(".", "").replace("-", " ")
    aliases = {
        "maint super": "Maint. Super", "maintenance super": "Maint. Super", "maintenance supervisor": "Maint. Super",
        "maint supervisor": "Maint. Super", "super": "Maint. Super", "supervisor": "Maint. Super",
        "maint": "Maintenance", "maintenance": "Maintenance", "maintenance tech": "Maintenance", "tech": "Maintenance",
        "pm": "Property Manager", "manager": "Property Manager", "property manager": "Property Manager",
        "community manager": "Property Manager",
        "office": "Office", "leasing": "Office", "leasing office": "Office", "front office": "Office",
    }
    return aliases.get(low, r)


def find_person(cid, name):
    """The saved contact at this property with this name (exact, case-insensitive; or a unique first-name match)."""
    name = (name or "").strip()
    if not cid or not name:
        return None
    k = q("SELECT * FROM contacts WHERE customer_id=? AND lower(trim(name))=lower(?)", (cid, name), one=True)
    if k:
        return k
    # "Drago" matches the only "Drago ..." on file (and the reverse), so first names don't make duplicates
    first = name.split()[0].lower()
    hits = [r for r in q("SELECT * FROM contacts WHERE customer_id=? AND coalesce(name,'')<>''", (cid,))
            if r["name"].strip().split()[0].lower() == first and (" " not in name or " " not in r["name"].strip())]
    return hits[0] if len(hits) == 1 else None


def save_person(cid, name, role="", phone="", email=""):
    """Add or update a person on the property's profile. Returns the contact id (None without a name/property)."""
    name = (name or "").strip()
    if not cid or not name:
        return None
    role = clean_role(role)
    k = find_person(cid, name)
    if k is None:
        return x("INSERT INTO contacts(customer_id, name, role, phone, email) VALUES (?,?,?,?,?)",
                 (cid, name, role, (phone or "").strip(), (email or "").strip()))
    upd = {}
    if role and role != (k["role"] or ""):
        upd["role"] = role
    if " " in name and " " not in (k["name"] or "").strip():
        upd["name"] = name                   # a fuller name replaces a first-name-only entry
    if phone and not k["phone"]:
        upd["phone"] = phone.strip()
    if email and not k["email"]:
        upd["email"] = email.strip()
    if upd:
        x(f"UPDATE contacts SET {', '.join(f'{c}=?' for c in upd)} WHERE id=?", (*upd.values(), k["id"]))
    return k["id"]


def sync_people():
    """Names typed on past log entries that aren't on the property's profile yet -> add them and link them."""
    n = 0
    for a in q("""SELECT id, customer_id, contact_name FROM activities WHERE contact_id IS NULL
                  AND customer_id IS NOT NULL AND coalesce(trim(contact_name),'')<>''"""):
        for nm in [s.strip() for s in a["contact_name"].replace(";", ",").split(",") if s.strip()][:1]:
            had = find_person(a["customer_id"], nm)
            kid = save_person(a["customer_id"], nm)
            if kid:
                x("UPDATE activities SET contact_id=? WHERE id=?", (kid, a["id"]))
                n += 0 if had else 1
    return n


def people_at(cid):
    """Contacts at a property with when you last talked to each one."""
    return q("""SELECT k.*, (SELECT MAX(occurred_at) FROM activities a WHERE a.contact_id=k.id) AS last_talk
                FROM contacts k WHERE k.customer_id=? ORDER BY
                CASE k.role WHEN 'Maint. Super' THEN 0 WHEN 'Property Manager' THEN 1 WHEN 'Office' THEN 2
                WHEN 'Maintenance' THEN 3 ELSE 4 END, k.name""", (cid,))
