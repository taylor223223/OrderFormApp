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
                messages = prov.list_messages(days=days, unread_only=unread, search=search)
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
    return render_template("email_inbox.html", provider=prov.name, ok=ok, why=why, messages=messages, error=error,
                           days=days, search=search, unread=unread, recent=recent,
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


@bp.route("/email/msg/<int:rid>/order", methods=["POST"])
@login_required
def make_order(rid):
    r = q("SELECT * FROM email_messages WHERE id=?", (rid,), one=True) or abort(404)
    p = loads(r["parsed"])
    form_key = request.form.get("form_key")
    if form_key not in FORMS:
        flash("Pick which order form to use.", "error")
        return redirect(url_for("emails.message", rid=rid))
    cid = request.form.get("customer_id", type=int)
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True) if cid else None
    po = request.form.get("po", "").strip() or p.get("po", "")
    units = [u.strip() for u in re.split(r"[,\s]+", request.form.get("units", "")) if u.strip()]
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
    data["header"].setdefault("date", today())
    if not data["header"].get("date"):
        data["header"]["date"] = today()
    data.setdefault("sales_rep", setting("sales_rep"))
    note = f"From email: {r['sender_name'] or r['sender']} - {r['subject']}"[:200]
    data["comments"] = data.get("comments") or ""
    oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, notes, created,
               updated) VALUES (?,?,?,?,?,'Draft',?,'email',?,?,?)""",
            (cid, form_key, FORMS[form_key]["title"], data["header"]["po"], data["header"]["date"], dumps(data),
             note, now(), now()))
    add_status(oid, "Draft", note)
    x("UPDATE email_messages SET status='ordered', order_id=? WHERE id=?", (oid, rid))
    if c is not None and r["sender"] and request.form.get("add_contact"):
        b = new_batch("email", f"Contact from email: {r['sender']}")
        if propose_contact(b, c, {"name": r["sender_name"], "email": r["sender"],
                                  "phone": p.get("contact", {}).get("phone", "")}):
            flash_link("The sender isn't a saved contact for this customer.",
                       url_for("imports.review_batch", bid=b), "Review contact", "info")
        else:
            x("DELETE FROM change_batches WHERE id=?", (b,))
    n = len(data.get("blocks", []))
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
