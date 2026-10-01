"""Review queue: proposed changes from CSV imports, emails and order history.
Nothing touches customer records until you approve it on the Review page."""
from .db import CUSTOMER_FIELDS, add_status, dumps, find_customer, loads, now, q, x

KIND_ORDER = ["new_customer", "set_field", "new_contact", "new_floorplan", "new_unit",
              "new_measurement", "history_order"]
KIND_LABEL = {
    "new_customer": "New customer", "set_field": "Customer field", "new_contact": "New contact",
    "new_floorplan": "New floorplan", "new_unit": "New unit", "new_measurement": "Measurement",
    "history_order": "Previous order",
}


def new_batch(source, label):
    return x("INSERT INTO change_batches(source, label, created) VALUES (?,?,?)", (source, label, now()))


def add_change(batch_id, kind, customer_id=None, customer_ref=None, field=None, old=None, new=None,
               payload=None, default_on=True):
    return x("""INSERT INTO changes(batch_id, kind, customer_id, customer_ref, field, old_value, new_value,
                payload, default_on) VALUES (?,?,?,?,?,?,?,?,?)""",
             (batch_id, kind, customer_id, customer_ref, field, old, new, dumps(payload or {}),
              1 if default_on else 0))


def _same(a, b):
    n = lambda s: "".join(ch for ch in str(s or "").lower() if ch.isalnum())  # noqa: E731
    return n(a) == n(b)


def propose_customer_fields(batch_id, cust, fields, ref=None):
    """Compare incoming values with a customer record: fill blanks (pre-checked),
    flag conflicts (unchecked). Returns number of proposals."""
    n = 0
    for f in CUSTOMER_FIELDS:
        if f in ("notes", "name") and cust is not None:
            continue
        v = (fields.get(f) or "").strip()
        if not v:
            continue
        old = (cust[f] if cust is not None else "") or ""
        if cust is not None and _same(old, v):
            continue
        add_change(batch_id, "set_field", customer_id=cust["id"] if cust is not None else None,
                   customer_ref=ref, field=f, old=old, new=v, default_on=not old)
        n += 1
    return n


def propose_contact(batch_id, cust, contact, ref=None):
    email = (contact.get("email") or "").strip().lower()
    name = (contact.get("name") or "").strip()
    phone = (contact.get("phone") or "").strip()
    if not (email or name or phone):
        return 0
    if cust is not None:
        for c in q("SELECT * FROM contacts WHERE customer_id=?", (cust["id"],)):
            if (email and (c["email"] or "").lower() == email) or (name and _same(c["name"], name) and not email):
                # existing contact: fill gaps only
                upd = {k: v for k, v in (("phone", phone), ("name", name), ("email", email))
                       if v and not c[k]}
                if upd:
                    add_change(batch_id, "new_contact", customer_id=cust["id"], customer_ref=ref,
                               payload={"contact_id": c["id"], **upd},
                               new=", ".join(f"{k}: {v}" for k, v in upd.items()), old=c["name"] or c["email"])
                    return 1
                return 0
    add_change(batch_id, "new_contact", customer_id=cust["id"] if cust is not None else None,
               customer_ref=ref, payload=contact,
               new=" / ".join(x for x in [name, email, phone] if x))
    return 1


def pending_batches():
    return q("""SELECT b.*, (SELECT COUNT(*) FROM changes c WHERE c.batch_id=b.id AND c.status='pending') AS n
                FROM change_batches b WHERE b.status='pending' ORDER BY b.id DESC""")


def pending_count():
    r = q("SELECT COUNT(*) AS n FROM changes WHERE status='pending'", one=True)
    return r["n"] if r else 0


def describe(ch):
    p = loads(ch["payload"])
    k = ch["kind"]
    if k == "new_customer":
        return p.get("name", "")
    if k == "set_field":
        return f"{ch['field']}"
    if k == "new_floorplan":
        return p.get("name", "")
    if k == "new_unit":
        return f"Unit {p.get('unit_number')}" + (f" -> {p['floorplan_name']}" if p.get("floorplan_name") else "")
    if k == "new_measurement":
        from .catalog import PRODUCTS
        where = p.get("unit_number") and f"Unit {p['unit_number']}" or p.get("floorplan_name") or "customer"
        dims = " x ".join(str(p.get("data", {}).get(d)) for d in ("width", "height") if p.get("data", {}).get(d))
        return f"{PRODUCTS.get(p.get('product'), {}).get('label', p.get('product'))} - {where}" + \
            (f" - {p.get('room')}" if p.get("room") else "") + (f" ({dims})" if dims else "")
    if k == "history_order":
        return f"PO {p.get('po') or '-'} {p.get('order_date') or ''} {p.get('title') or ''}"
    return ""


def _floorplan_id(cid, name):
    if not name:
        return None
    r = q("SELECT id FROM floorplans WHERE customer_id=? AND lower(name)=lower(?)", (cid, name), one=True)
    if r:
        return r["id"]
    return x("INSERT INTO floorplans(customer_id, name) VALUES (?,?)", (cid, name))


def _unit_id(cid, number, building=None, fp_name=None):
    if not number:
        return None
    r = q("SELECT * FROM units WHERE customer_id=? AND lower(unit_number)=lower(?)", (cid, number), one=True)
    fp = _floorplan_id(cid, fp_name) if fp_name else None
    if r:
        if fp and not r["floorplan_id"]:
            x("UPDATE units SET floorplan_id=? WHERE id=?", (fp, r["id"]))
        return r["id"]
    return x("INSERT INTO units(customer_id, unit_number, building, floorplan_id) VALUES (?,?,?,?)",
             (cid, number, building, fp))


def apply_changes(batch_id, accepted_ids):
    """Apply the accepted changes of a batch; reject the rest."""
    accepted_ids = {int(i) for i in accepted_ids}
    rows = list(q("SELECT * FROM changes WHERE batch_id=? AND status='pending'", (batch_id,)))
    rows.sort(key=lambda r: (KIND_ORDER.index(r["kind"]) if r["kind"] in KIND_ORDER else 99, r["id"]))
    refs = {}
    applied = skipped = 0
    for ch in rows:
        if ch["id"] not in accepted_ids:
            x("UPDATE changes SET status='rejected' WHERE id=?", (ch["id"],))
            continue
        p = loads(ch["payload"])
        cid = ch["customer_id"] or refs.get(ch["customer_ref"])
        k = ch["kind"]
        if k == "new_customer":
            existing = find_customer(p.get("name"), p.get("acct"))
            if existing:
                cid = existing["id"]
            else:
                cols = [f for f in CUSTOMER_FIELDS if p.get(f)]
                cid = x(f"INSERT INTO customers({','.join(cols + ['created', 'updated'])}) "
                        f"VALUES ({','.join('?' * (len(cols) + 2))})",
                        [p[f] for f in cols] + [now(), now()])
            refs[ch["customer_ref"]] = cid
        elif not cid:
            x("UPDATE changes SET status='skipped' WHERE id=?", (ch["id"],))
            skipped += 1
            continue
        elif k == "set_field":
            if ch["field"] in CUSTOMER_FIELDS:
                x(f"UPDATE customers SET {ch['field']}=?, updated=? WHERE id=?", (ch["new_value"], now(), cid))
        elif k == "new_contact":
            if p.get("contact_id"):
                for f in ("name", "email", "phone"):
                    if p.get(f):
                        x(f"UPDATE contacts SET {f}=? WHERE id=?", (p[f], p["contact_id"]))
            else:
                x("INSERT INTO contacts(customer_id, name, role, email, phone) VALUES (?,?,?,?,?)",
                  (cid, p.get("name"), p.get("role"), p.get("email"), p.get("phone")))
        elif k == "new_floorplan":
            _floorplan_id(cid, p.get("name"))
        elif k == "new_unit":
            _unit_id(cid, p.get("unit_number"), p.get("building"), p.get("floorplan_name"))
        elif k == "new_measurement":
            fp = _floorplan_id(cid, p.get("floorplan_name")) if p.get("floorplan_name") else None
            uid = None
            if p.get("unit_number"):
                uid = _unit_id(cid, p["unit_number"], p.get("building"), p.get("floorplan_name"))
                if fp:  # unit has a floorplan -> store on the floorplan so every unit shares it
                    uid = None if not p.get("unit_specific") else uid
            x("""INSERT INTO measurements(customer_id, floorplan_id, unit_id, product, room, data, verified,
                 notes, updated) VALUES (?,?,?,?,?,?,?,?,?)""",
              (cid, fp, uid, p.get("product") or "other", p.get("room"), dumps(p.get("data") or {}),
               1 if p.get("verified") else 0, p.get("notes"), now()))
        elif k == "history_order":
            oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source,
                       notes, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (cid, p.get("form_key"), p.get("title") or "Imported order", p.get("po"), p.get("order_date"),
                     p.get("status") or "Completed", dumps(p.get("data") or {}), p.get("source") or "import",
                     p.get("notes"), now(), now()))
            add_status(oid, p.get("status") or "Completed", "Imported")
        x("UPDATE changes SET status='applied' WHERE id=?", (ch["id"],))
        applied += 1
    x("UPDATE change_batches SET status='done' WHERE id=?", (batch_id,))
    return applied, skipped


def gap_fill_from_history(customer_id):
    """Propose filling blank customer fields from header data of past orders."""
    cust = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not cust:
        return None
    found = {}
    for o in q("SELECT data FROM orders WHERE customer_id=? ORDER BY id DESC", (customer_id,)):
        h = loads(o["data"]).get("header", {})
        for f in ("acct", "address", "city", "state", "zip", "mgmt", "phone"):
            if h.get(f) and not cust[f] and f not in found:
                found[f] = h[f]
    if not found:
        return None
    b = new_batch("history", f"Missing info for {cust['name']} from previous orders")
    propose_customer_fields(b, cust, found)
    return b
