import os
import re
import uuid

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..catalog import FORMS, form_list, products_for_form
from ..changes import new_batch, propose_contact
from ..db import add_status, dumps, loads, now, q, setting, x
from ..email_parse import parse_email
from ..emailer import MailError, parse_uploaded_message, provider
from ..importer import guess_mapping, read_table, all_targets
from ..paths import data_dir
from ..security import flash_link, login_required
from ..suggest import measurements_for_unit, to_form_block
from .orders import customer_header, today

bp = Blueprint("emails", __name__)


def _att_dir(rid):
    d = os.path.join(data_dir(), "email_attachments", str(rid))
    os.makedirs(d, exist_ok=True)
    return d


def _store(msg, prov_name):
    """Save a fetched/uploaded message + its parse; returns row id."""
    mid = msg.get("msg_id") or f"upload:{uuid.uuid4().hex}"
    parsed = parse_email(msg["subject"], msg["body"], msg["sender"], msg["sender_name"], msg["attachments"])
    ex = q("SELECT id FROM email_messages WHERE msg_id=?", (mid,), one=True)
    vals = (prov_name, msg["subject"], msg["sender"], msg["sender_name"], msg["received"], msg["body"],
            dumps(parsed))
    if ex:
        x("""UPDATE email_messages SET provider=?, subject=?, sender=?, sender_name=?, received=?, body=?, parsed=?
             WHERE id=?""", vals + (ex["id"],))
        rid = ex["id"]
    else:
        rid = x("""INSERT INTO email_messages(provider, subject, sender, sender_name, received, body, parsed, msg_id)
                   VALUES (?,?,?,?,?,?,?,?)""", vals + (mid,))
    d = _att_dir(rid)
    for fn, data in msg["attachments"]:
        with open(os.path.join(d, os.path.basename(fn)), "wb") as fh:
            fh.write(data)
    return rid


@bp.route("/email")
@login_required
def inbox():
    prov = provider(setting)
    ok, why = prov.available()
    messages, error = [], None
    days = request.args.get("days", "14")
    search = request.args.get("q", "")
    unread = bool(request.args.get("unread"))
    if request.args.get("check") and prov.name != "eml":
        if not ok:
            error = why
        else:
            try:
                messages = prov.list_messages(days=days, unread_only=unread, search=search,
                                              subject_word=(setting("email_order_word") or "Order")
                                              if request.args.get("orders_only") else "")
            except MailError as e:
                error = str(e)
            except Exception as e:  # noqa: BLE001
                error = f"Couldn't read the inbox: {e}"
    seen = {r["msg_id"]: r for r in q("SELECT id, msg_id, status, order_id FROM email_messages")}
    for m in messages:
        r = seen.get(m["msg_id"])
        m["row"] = dict(r) if r else None
    recent = q("""SELECT e.*, c.name AS cname FROM email_messages e LEFT JOIN orders o ON o.id=e.order_id
                  LEFT JOIN customers c ON c.id=o.customer_id ORDER BY e.id DESC LIMIT 25""")
    auto_rows = q("""SELECT e.*, c.name AS cname FROM email_messages e LEFT JOIN orders o ON o.id=e.order_id
                     LEFT JOIN customers c ON c.id=o.customer_id WHERE e.auto=1 ORDER BY e.id DESC LIMIT 30""")
    auto = {"word": setting("email_order_word") or "Order", "minutes": setting("email_auto_minutes") or "0",
            "last": setting("email_last_check") or "never", "rows": auto_rows}
    return render_template("email_inbox.html", provider=prov.name, ok=ok, why=why, messages=messages, error=error,
                           days=days, search=search, unread=unread, recent=recent, auto=auto,
                           checked=bool(request.args.get("check")))


@bp.route("/email/open", methods=["POST"])
@login_required
def open_message():
    prov = provider(setting)
    try:
        msg = prov.get_message(request.form["msg_id"])
    except (MailError, Exception) as e:  # noqa: BLE001
        flash(f"Couldn't open that email: {e}", "error")
        return redirect(url_for("emails.inbox"))
    rid = _store(msg, prov.name)
    return redirect(url_for("emails.message", rid=rid))


@bp.route("/email/upload", methods=["POST"])
@login_required
def upload():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith((".eml", ".msg")):
        flash("Drop an .eml or .msg file (drag the email out of Outlook onto your desktop first).", "error")
        return redirect(url_for("emails.inbox"))
    try:
        msg = parse_uploaded_message(f.filename, f.read())
    except Exception as e:  # noqa: BLE001
        flash(f"Couldn't read that email file: {e}", "error")
        return redirect(url_for("emails.inbox"))
    return redirect(url_for("emails.message", rid=_store(msg, "upload")))


@bp.route("/email/paste", methods=["POST"])
@login_required
def paste():
    body = request.form.get("body", "")
    if not body.strip():
        flash("Paste the email text first.", "error")
        return redirect(url_for("emails.inbox"))
    sender = request.form.get("sender", "").strip()
    msg = {"msg_id": None, "subject": request.form.get("subject", "(pasted)"), "sender": sender,
           "sender_name": "", "received": now()[:16], "body": body, "attachments": []}
    return redirect(url_for("emails.message", rid=_store(msg, "paste")))


@bp.route("/email/msg/<int:rid>")
@login_required
def message(rid):
    r = q("SELECT * FROM email_messages WHERE id=?", (rid,), one=True) or abort(404)
    p = loads(r["parsed"])
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    known_contact = False
    if p.get("customer_id") and r["sender"]:
        known_contact = bool(q("""SELECT 1 FROM contacts WHERE customer_id=? AND lower(email)=lower(?)
                                  UNION SELECT 1 FROM customers WHERE id=? AND lower(email)=lower(?)""",
                               (p["customer_id"], r["sender"], p["customer_id"], r["sender"]), one=True))
    atts = sorted(os.listdir(_att_dir(rid)))
    return render_template("email_message.html", r=r, p=p, customers=customers, forms=form_list(),
                           known_contact=known_contact, atts=atts, form_titles={k: v["title"] for k, v in FORMS.items()})


def _blocks_from_email(form_key, cid, p, units_override=None):
    prods = set(products_for_form(form_key))
    items = [i for i in p.get("items", []) if not i.get("product") or i["product"] in prods]
    blocks = []
    if items:
        for it in items:
            base = {}
            if cid and it.get("unit"):
                _, _, rows = measurements_for_unit(cid, it["unit"], form_key=form_key)
                pick = [rw for rw in rows if (not it.get("product") or rw["product"] == it["product"])]
                if it.get("room"):
                    pick = [rw for rw in pick if (rw["room"] or "").lower() in it["room"].lower()
                            or it["room"].lower() in (rw["room"] or "").lower()] or pick
                if pick:
                    base = to_form_block(form_key, loads(pick[0]["data"]), pick[0]["room"], it["unit"])
            mine = {k: v for k, v in it.items() if k in ("qty", "width", "height", "color", "swing", "core")}
            blk = {**base, **to_form_block(form_key, mine, it.get("room") if not base else None, it.get("unit"))}
            if not blk.get("qty"):
                blk["qty"] = "1"
            blocks.append(blk)
    else:
        for un in (units_override or p.get("units") or []):
            if cid:
                _, _, rows = measurements_for_unit(cid, un, form_key=form_key)
                for rw in rows:
                    blocks.append(to_form_block(form_key, loads(rw["data"]), rw["room"], un))
    return blocks


def create_draft(rid, form_key, cid=None, po="", units=None, add_contact=False, auto=False):
    """Turn a stored email into a Draft order (never sent - you review it first). Returns order id."""
    r = q("SELECT * FROM email_messages WHERE id=?", (rid,), one=True)
    p = loads(r["parsed"])
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True) if cid else None
    po = po or p.get("po", "")
    filled = next((ff for ff in p.get("filled_forms", []) if ff["form_key"] == form_key), None)
    if filled:
        data = filled["data"]
        h = data.setdefault("header", {})
        for k, v in customer_header(c).items():
            if not h.get(k):
                h[k] = v
    else:
        data = {"header": customer_header(c), "blocks": _blocks_from_email(form_key, cid, p, units or None)}
    data["header"]["po"] = po or data["header"].get("po", "")
    if not data["header"].get("date"):
        data["header"]["date"] = today()
    data.setdefault("sales_rep", setting("sales_rep"))
    note = (("Auto-drafted from email: " if auto else "From email: ")
            + f"{r['sender_name'] or r['sender']} - {r['subject']}")[:200]
    data["comments"] = data.get("comments") or ""
    oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, notes, created,
               updated) VALUES (?,?,?,?,?,'Draft',?,'email',?,?,?)""",
            (cid, form_key, FORMS[form_key]["title"], data["header"]["po"], data["header"]["date"], dumps(data),
             note, now(), now()))
    add_status(oid, "Draft", note)
    ids = [i for i in (r["order_ids"] or "").split(",") if i] + [str(oid)]
    x("UPDATE email_messages SET status=?, order_id=coalesce(order_id, ?), order_ids=? WHERE id=?",
      ("drafted" if auto else "ordered", oid, ",".join(ids), rid))
    if c is not None and r["sender"] and add_contact:
        b = new_batch("email", f"Contact from email: {r['sender']}")
        if propose_contact(b, c, {"name": r["sender_name"], "email": r["sender"],
                                  "phone": p.get("contact", {}).get("phone", "")}):
            flash_link("The sender isn't a saved contact for this customer.",
                       url_for("imports.review_batch", bid=b), "Review contact", "info")
        else:
            x("DELETE FROM change_batches WHERE id=?", (b,))
    return oid


def forms_for_email(p):
    """Which order forms an email needs: attached filled forms first, then one per product mentioned."""
    from ..catalog import VARIANT_PARENT, forms_for_product
    out = [ff["form_key"] for ff in p.get("filled_forms", [])]
    for it in p.get("items", []):
        fk = (forms_for_product(it.get("product")) or [None])[0] if it.get("product") else None
        fk = VARIANT_PARENT.get(fk, fk)
        if fk and fk not in out and VARIANT_PARENT.get(fk, fk) not in [VARIANT_PARENT.get(o, o) for o in out]:
            out.append(fk)
    if not out and p.get("form_key"):
        out.append(VARIANT_PARENT.get(p["form_key"], p["form_key"]))
    return out


def auto_check(prov=None):
    """Read only emails whose subject contains the order word, and draft orders from the new ones.
    Returns a summary dict. Nothing is ever sent."""
    prov = prov or provider(setting)
    word = (setting("email_order_word") or "Order").strip()
    days = setting("email_lookback_days") or "3"
    res = {"found": 0, "new": 0, "drafts": 0, "no_form": 0, "error": ""}
    if prov.name == "eml":
        res["error"] = "Email isn't connected (Settings -> Email)."
        return res
    ok, why = prov.available()
    if not ok:
        res["error"] = why
        return res
    try:
        msgs = prov.list_messages(days=days, limit=100, subject_word=word)
    except Exception as e:  # noqa: BLE001
        res["error"] = f"Couldn't read the inbox: {e}"
        return res
    res["found"] = len(msgs)
    seen = {r["msg_id"] for r in q("SELECT msg_id FROM email_messages WHERE msg_id IS NOT NULL")}
    for m in msgs:
        if m["msg_id"] in seen:
            continue
        try:
            full = prov.get_message(m["msg_id"])
        except Exception:  # noqa: BLE001
            continue
        rid = _store(full, prov.name)
        x("UPDATE email_messages SET auto=1 WHERE id=?", (rid,))
        res["new"] += 1
        p = loads(q("SELECT parsed FROM email_messages WHERE id=?", (rid,), one=True)["parsed"])
        forms = forms_for_email(p)
        if not forms:
            x("UPDATE email_messages SET status='needs form' WHERE id=?", (rid,))
            res["no_form"] += 1
            continue
        for fk in forms:
            if fk in FORMS:
                create_draft(rid, fk, p.get("customer_id"), auto=True)
                res["drafts"] += 1
    from datetime import datetime as _dt
    from ..db import set_setting
    set_setting("email_last_check", _dt.now().strftime("%Y-%m-%d %H:%M"))
    return res


@bp.route("/email/check_orders", methods=["POST"])
@login_required
def check_orders():
    r = auto_check()
    if r["error"]:
        flash(r["error"], "error")
    elif not r["new"]:
        flash(f"Checked: no new emails with \"{setting('email_order_word') or 'Order'}\" in the subject.", "ok")
    else:
        flash(f"{r['new']} new order email(s): {r['drafts']} draft order(s) ready to review"
              + (f", {r['no_form']} need you to pick the form" if r["no_form"] else "") + ".", "ok")
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("emails.inbox") + "#auto")


@bp.route("/email/msg/<int:rid>/order", methods=["POST"])
@login_required
def make_order(rid):
    q("SELECT id FROM email_messages WHERE id=?", (rid,), one=True) or abort(404)
    form_key = request.form.get("form_key")
    if form_key not in FORMS:
        flash("Pick which order form to use.", "error")
        return redirect(url_for("emails.message", rid=rid))
    units = [u.strip() for u in re.split(r"[,\s]+", request.form.get("units", "")) if u.strip()]
    oid = create_draft(rid, form_key, request.form.get("customer_id", type=int), request.form.get("po", "").strip(),
                       units, add_contact=bool(request.form.get("add_contact")))
    o = q("SELECT data FROM orders WHERE id=?", (oid,), one=True)
    n = len(loads(o["data"]).get("blocks", []))
    flash(f"Draft order created with {n} line(s) - check the predicted values (highlighted) and save.", "ok")
    return redirect(url_for("orders.editor", order=oid))


@bp.route("/email/msg/<int:rid>/import", methods=["POST"])
@login_required
def import_attachment(rid):
    fn = os.path.basename(request.form.get("file", ""))
    path = os.path.join(_att_dir(rid), fn)
    if not os.path.exists(path):
        abort(404)
    with open(path, "rb") as fh:
        raw = fh.read()
    headers, rows = read_table(raw, fn)
    token = uuid.uuid4().hex + os.path.splitext(fn)[1].lower()
    up = os.path.join(data_dir(), "uploads")
    os.makedirs(up, exist_ok=True)
    with open(os.path.join(up, token), "wb") as fh:
        fh.write(raw)
    p = loads(q("SELECT parsed FROM email_messages WHERE id=?", (rid,), one=True)["parsed"])
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    return render_template("import_map.html", headers=headers, rows=rows[:8], n=len(rows),
                           mapping=guess_mapping(headers), targets=all_targets(), token=token, filename=fn,
                           customers=customers, default_customer=str(p.get("customer_id") or ""))
