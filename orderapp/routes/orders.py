import io
import os
import re
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)

from ..catalog import FORMS, form_list, public_spec
from ..changes import add_change, new_batch, propose_contact, propose_customer_fields
from ..db import (OPEN_STATUSES, STATUSES, add_status, dumps, find_customer, loads, now, q, setting, x)
from ..emailer import MailError, provider
from ..paths import default_output_dir, ensure_dir
from ..pdf_fill import fill_form
from ..security import flash_link, login_required
from ..suggest import measurements_for_unit, missing_fields, suggestions, to_form_block

bp = Blueprint("orders", __name__)


def today():
    return datetime.now().strftime(setting("date_format") or "%m/%d/%Y")


def customer_header(c):
    if not c:
        return {}
    return {k: (c[k] or "") for k in ("name", "acct", "address", "city", "state", "zip", "mgmt", "phone")}


def output_dir():
    return ensure_dir(setting("output_dir") or default_output_dir())


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', "-", s).strip()[:150]


# ---------------- editor ----------------
@bp.route("/orders/new")
@login_required
def new():
    form_key = request.args.get("form")
    cid = request.args.get("customer", type=int)
    if not form_key:
        customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
        return render_template("order_pick.html", forms=form_list(), customers=customers, cid=cid)
    return redirect(url_for("orders.editor", form=form_key, customer=cid or ""))


@bp.route("/orders/editor")
@login_required
def editor():
    oid = request.args.get("order", type=int)
    order = None
    if oid:
        order = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
        form_key = order["form_key"]
        cid = order["customer_id"]
        data = loads(order["data"])
    else:
        form_key = request.args.get("form")
        cid = request.args.get("customer", type=int)
        data = {}
        copy_id = request.args.get("copy", type=int)
        if copy_id:
            src = q("SELECT * FROM orders WHERE id=?", (copy_id,), one=True)
            if src:
                data = loads(src["data"])
                form_key = form_key or src["form_key"]
                cid = cid or src["customer_id"]
                data.setdefault("header", {})["po"] = ""
    if form_key not in FORMS:
        flash("This order has no order form (it is a tracking-only entry).", "info")
        return redirect(url_for("orders.view", oid=oid)) if oid else redirect(url_for("orders.new"))
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True) if cid else None
    if not oid:
        h = data.setdefault("header", {})
        for k, v in customer_header(c).items():
            h.setdefault(k, v)
        h.setdefault("date", today())
        data.setdefault("sales_rep", setting("sales_rep"))
        if request.args.get("unit") and c:
            _, _, rows = measurements_for_unit(c["id"], request.args["unit"], form_key=form_key)
            data["blocks"] = [to_form_block(form_key, loads(r["data"]), r["room"], request.args["unit"])
                              for r in rows]
    units = q("""SELECT u.unit_number, f.name AS fp FROM units u LEFT JOIN floorplans f ON f.id=u.floorplan_id
                 WHERE u.customer_id=? ORDER BY length(u.unit_number), u.unit_number""", (cid,)) if cid else []
    fps = q("SELECT id, name FROM floorplans WHERE customer_id=? ORDER BY name", (cid,)) if cid else []
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    last = q("""SELECT id, po, order_date FROM orders WHERE customer_id=? AND form_key=? AND id<>?
                ORDER BY id DESC LIMIT 1""", (cid, form_key, oid or 0), one=True) if cid else None
    return render_template("order_editor.html", spec=public_spec(form_key), form_key=form_key, order=order,
                           data=data, c=c, customers=customers, units=[dict(u) for u in units],
                           fps=[dict(f) for f in fps], sugg=suggestions(form_key, cid), forms=form_list(),
                           last=last)


@bp.route("/orders/save", methods=["POST"])
@login_required
def save():
    form_key = request.form.get("form_key")
    if form_key not in FORMS:
        abort(400)
    data = loads(request.form.get("payload"), {})
    oid = request.form.get("order_id", type=int)
    cid = request.form.get("customer_id", type=int)
    action = request.form.get("action", "save")
    h = data.get("header", {})
    # link to an existing customer by name/acct if none chosen
    cust = q("SELECT * FROM customers WHERE id=?", (cid,), one=True) if cid else None
    if cust is None and (h.get("name") or h.get("acct")):
        cust = find_customer(h.get("name"), h.get("acct"))
        cid = cust["id"] if cust else None
    po, odate = h.get("po", ""), h.get("date", "")
    title = FORMS[form_key]["title"]
    if oid:
        x("""UPDATE orders SET customer_id=?, form_key=?, title=?, po=?, order_date=?, data=?, updated=?
             WHERE id=?""", (cid, form_key, title, po, odate, dumps(data), now(), oid))
    else:
        oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, created,
                   updated) VALUES (?,?,?,?,?,'Draft',?,?,?,?)""",
                (cid, form_key, title, po, odate, dumps(data), request.form.get("source", "manual"), now(), now()))
        add_status(oid, "Draft", "Created")
        email_id = request.form.get("email_id", type=int)
        if email_id:
            x("UPDATE email_messages SET status='ordered', order_id=? WHERE id=?", (oid, email_id))
    # customer info the order has that the customer record is missing -> review queue
    if cust is not None:
        pend = q("SELECT 1 FROM changes WHERE customer_id=? AND status='pending' AND kind='set_field'",
                 (cust["id"],), one=True)
        if not pend:
            b = new_batch("order", f"Order #{oid}: info not on {cust['name']}'s record")
            n = propose_customer_fields(b, cust, {k: v for k, v in h.items() if k != "name"})
            if n:
                flash_link(f"This order has {n} customer detail(s) that differ from the saved record.",
                           url_for("imports.review_batch", bid=b), "Review", "info")
            else:
                x("DELETE FROM change_batches WHERE id=?", (b,))
    elif h.get("name"):
        b = new_batch("order", f"Order #{oid}: new customer {h['name']}")
        ref = "new:order" + str(oid)
        add_change(b, "new_customer", customer_ref=ref, payload={k: v for k, v in h.items() if k not in ("po", "date")},
                   new=h["name"])
        flash_link(f"'{h['name']}' isn't a saved customer yet.", url_for("imports.review_batch", bid=b),
                   "Add them", "info")
    if action in ("pdf", "send"):
        path, warns = _make_pdf(oid)
        for w in warns:
            flash(w, "info")
        if action == "send":
            return redirect(url_for("orders.send_page", oid=oid))
        flash(f"PDF saved: {path}", "ok")
    else:
        flash("Order saved.", "ok")
    miss = missing_fields(form_key, data)
    if miss:
        flash("Still empty: " + ", ".join(miss[:8]) + ("..." if len(miss) > 8 else ""), "warn")
    return redirect(url_for("orders.view", oid=oid))


def _make_pdf(oid):
    o = q("SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE o.id=?",
          (oid,), one=True)
    data = loads(o["data"])
    pdf, warns = fill_form(o["form_key"], data)
    cust = o["cname"] or data.get("header", {}).get("name") or "Customer"
    fname = safe_name(f"{cust} - {FORMS[o['form_key']]['title']} - PO {o['po'] or 'none'} - #{oid}.pdf")
    folder = ensure_dir(os.path.join(output_dir(), safe_name(cust)))
    path = os.path.join(folder, fname)
    with open(path, "wb") as fh:
        fh.write(pdf)
    x("UPDATE orders SET pdf_path=?, updated=? WHERE id=?", (path, now(), oid))
    if o["status"] == "Draft":
        x("UPDATE orders SET status='Ready' WHERE id=?", (oid,))
        add_status(oid, "Ready", "PDF created")
    return path, warns


# ---------------- view / actions ----------------
@bp.route("/orders/<int:oid>")
@login_required
def view(oid):
    o = q("SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE o.id=?",
          (oid,), one=True) or abort(404)
    data = loads(o["data"])
    hist = q("SELECT * FROM order_status_history WHERE order_id=? ORDER BY id DESC", (oid,))
    spec = public_spec(o["form_key"]) if o["form_key"] in FORMS else None
    miss = missing_fields(o["form_key"], data) if spec else []
    labels = {f["key"]: f["label"] for f in spec["fields"]} if spec else {}
    from ..catalog import PRODUCTS as _P
    products_lbl = {k: v["label"] for k, v in _P.items()}
    from ..photos import photos_for
    return render_template("order_view.html", o=o, data=data, hist=hist, spec=spec, labels=labels, miss=miss,
                           photos=photos_for(oid), products_lbl=products_lbl,
                           statuses=STATUSES, pdf_exists=bool(o["pdf_path"] and os.path.exists(o["pdf_path"])))


@bp.route("/orders/<int:oid>/pdf")
@login_required
def pdf(oid):
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    if o["form_key"] not in FORMS:
        abort(404)
    if request.args.get("regen") or not (o["pdf_path"] and os.path.exists(o["pdf_path"])):
        _make_pdf(oid)
        o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    return send_file(o["pdf_path"], mimetype="application/pdf", as_attachment=bool(request.args.get("dl")),
                     download_name=os.path.basename(o["pdf_path"]))


@bp.route("/orders/<int:oid>/open_folder", methods=["POST"])
@login_required
def open_folder(oid):
    from ..emailer import open_file
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    if o["pdf_path"] and os.path.exists(o["pdf_path"]):
        try:
            open_file(os.path.dirname(o["pdf_path"]))
        except Exception as e:  # noqa: BLE001
            flash(f"Couldn't open the folder: {e}", "error")
    return redirect(url_for("orders.view", oid=oid))


def _fmt(tmpl, o, data):
    h = data.get("header", {})
    vals = {"title": o["title"] or "", "customer": h.get("name") or "", "po": o["po"] or "",
            "date": o["order_date"] or "", "sales_rep": setting("sales_rep") or "", "order_id": o["id"]}
    try:
        return tmpl.format(**vals)
    except (KeyError, ValueError, IndexError):
        return tmpl


@bp.route("/orders/<int:oid>/send", methods=["GET", "POST"])
@login_required
def send_page(oid):
    from ..photos import photos_for, photos_pdf
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    has_form = o["form_key"] in FORMS
    photos = photos_for(oid)
    if not has_form and not photos:
        flash("Add a photo or use an order form before emailing this order.", "error")
        return redirect(url_for("orders.view", oid=oid))
    data = loads(o["data"])
    if has_form and not (o["pdf_path"] and os.path.exists(o["pdf_path"])):
        _make_pdf(oid)
        o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    cust = q("SELECT name FROM customers WHERE id=?", (o["customer_id"],), one=True) if o["customer_id"] else None
    cname = (cust["name"] if cust else "") or data.get("header", {}).get("name") or "Customer"
    folder = ensure_dir(os.path.join(output_dir(), safe_name(cname)))
    if request.method == "POST":
        to = request.form.get("to", "").strip()
        if not to:
            flash("Enter a To address.", "error")
            return redirect(url_for("orders.send_page", oid=oid))
        attachments = [o["pdf_path"]] if has_form else []
        pmode = request.form.get("photos_mode", "pdf")
        if photos and pmode == "pdf":
            pp = photos_pdf(oid, os.path.join(folder, safe_name(f"{cname} - {o['title']} - photos - #{oid}.pdf")),
                            title=f"{cname} - PO {o['po'] or '-'}")
            if pp:
                attachments.append(pp)
        elif photos and pmode == "images":
            attachments += [p["path"] for p in photos]
        total = sum(os.path.getsize(a) for a in attachments if os.path.exists(a))
        if total > 20 * 1024 * 1024:
            flash(f"Attachments total {total / 1048576:.1f} MB - most mail servers stop at 20-25 MB. "
                  "Remove some photos or send them as one PDF.", "error")
            return redirect(url_for("orders.send_page", oid=oid))
        review = request.form.get("mode", setting("send_mode")) != "send"
        prov = provider(setting)
        if prov.name == "eml" and current_app.config.get("CLOUD"):
            # on the hosted app: hand the draft to the device instead of opening it on the server
            from email.message import EmailMessage
            import mimetypes
            m = EmailMessage()
            m["To"], m["Subject"], m["X-Unsent"] = to, request.form.get("subject", ""), "1"
            if request.form.get("cc", "").strip():
                m["Cc"] = request.form["cc"].strip()
            m.set_content(request.form.get("body", ""))
            for a in attachments:
                mt = (mimetypes.guess_type(a)[0] or "application/octet-stream").split("/")
                with open(a, "rb") as fh:
                    m.add_attachment(fh.read(), maintype=mt[0], subtype=mt[1], filename=os.path.basename(a))
            add_status(oid, o["status"], f"Email draft downloaded for {to}")
            return send_file(io.BytesIO(bytes(m)), mimetype="message/rfc822", as_attachment=True,
                             download_name=safe_name(f"{cname} - order {oid}.eml"))
        try:
            kw = {}
            if prov.name == "eml":
                kw["out_dir"] = folder
            msg = prov.send(to, request.form.get("cc", "").strip(), request.form.get("subject", ""),
                            request.form.get("body", ""), attachments, review=review, **kw)
        except MailError as e:
            flash(str(e), "error")
            return redirect(url_for("orders.send_page", oid=oid))
        except Exception as e:  # noqa: BLE001
            flash(f"Email failed: {e}", "error")
            return redirect(url_for("orders.send_page", oid=oid))
        if not review:
            x("UPDATE orders SET status='Sent', sent_at=?, updated=? WHERE id=?", (now(), now(), oid))
            add_status(oid, "Sent", f"Emailed to {to}")
        elif prov.name != "eml":
            add_status(oid, o["status"], f"Email draft prepared for {to}")
        flash(msg + (" Mark the order as Sent once it's gone." if review else ""), "ok")
        return redirect(url_for("orders.view", oid=oid))
    cust_emails = []
    if o["customer_id"]:
        c = q("SELECT email FROM customers WHERE id=?", (o["customer_id"],), one=True)
        if c and c["email"]:
            cust_emails.append(c["email"])
        cust_emails += [k["email"] for k in q("SELECT email FROM contacts WHERE customer_id=? AND email<>''",
                                              (o["customer_id"],))]
    body = _fmt(setting("body_template"), o, data)
    if not has_form:
        extra = [f"Units: {data['units']}" if data.get("units") else "", data.get("comments", "")]
        extra = "\n".join(e for e in extra if e)
        if extra:
            body = body.replace("\n\nThank you", f"\n\n{extra}\n\nThank you", 1) if "\n\nThank you" in body \
                else body + "\n\n" + extra
    return render_template("order_send.html", o=o, to=setting("order_to"), cc=setting("order_cc"),
                           subject=_fmt(setting("subject_template"), o, data), body=body,
                           cust_emails=cust_emails, provider=setting("email_provider"), mode=setting("send_mode"),
                           has_form=has_form, photos=photos,
                           share_files=([{"url": url_for("orders.pdf", oid=oid), "name": os.path.basename(o["pdf_path"])}]
                                        if has_form else []) +
                                       [{"url": url_for("photos.show", pid=p["id"]), "name": os.path.basename(p["path"])}
                                        for p in photos])


@bp.route("/orders/<int:oid>/status", methods=["POST"])
@login_required
def set_status(oid):
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    st = request.form.get("status")
    if st in STATUSES:
        fields = {"status": st, "updated": now()}
        for k in ("vendor_ref", "due_date", "notes"):
            if k in request.form:
                fields[k] = request.form.get(k, "").strip()
        if st == "Sent" and not o["sent_at"]:
            fields["sent_at"] = now()
        x(f"UPDATE orders SET {', '.join(k + '=?' for k in fields)} WHERE id=?", list(fields.values()) + [oid])
        if st != o["status"] or request.form.get("note"):
            add_status(oid, st, request.form.get("note", "").strip())
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") else url_for("orders.view", oid=oid))


@bp.route("/orders/<int:oid>/delete", methods=["POST"])
@login_required
def delete(oid):
    import shutil
    from ..photos import photo_dir
    x("DELETE FROM orders WHERE id=?", (oid,))
    shutil.rmtree(photo_dir(oid), ignore_errors=True)
    flash("Order deleted (the PDF file, if any, was left in your output folder).", "ok")
    return redirect(url_for("orders.tracking"))


# ---------------- tracking ----------------
@bp.route("/tracking")
@login_required
def tracking():
    st = request.args.get("status", "open")
    s = request.args.get("q", "").strip()
    sql = """SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE 1=1"""
    args = []
    if st == "open":
        sql += f" AND o.status IN ({','.join('?' * len(OPEN_STATUSES))})"
        args += OPEN_STATUSES
    elif st and st != "all":
        sql += " AND o.status=?"
        args.append(st)
    if s:
        sql += " AND (c.name LIKE ? OR o.po LIKE ? OR o.title LIKE ? OR o.vendor_ref LIKE ? OR o.notes LIKE ?)"
        args += [f"%{s}%"] * 5
    sql += " ORDER BY CASE WHEN coalesce(o.due_date,'')='' THEN 1 ELSE 0 END, o.due_date, o.updated DESC"
    rows = q(sql, args)
    counts = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM orders GROUP BY status")}
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    return render_template("tracking.html", rows=rows, statuses=STATUSES, st=st, s=s, counts=counts,
                           customers=customers)


@bp.route("/tracking/add", methods=["POST"])
@login_required
def tracking_add():
    """Manual tracking entry for an order placed outside the app."""
    title = request.form.get("title", "").strip() or "Manual order"
    cid = request.form.get("customer_id", type=int)
    oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, vendor_ref,
               due_date, notes, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, None, title, request.form.get("po", "").strip(), request.form.get("order_date", "").strip() or today(),
             request.form.get("status", "Sent"), "{}", "manual-tracking", request.form.get("vendor_ref", "").strip(),
             request.form.get("due_date", "").strip(), request.form.get("notes", "").strip(), now(), now()))
    add_status(oid, request.form.get("status", "Sent"), "Added manually")
    flash("Tracking entry added.", "ok")
    return redirect(url_for("orders.tracking"))


# ---------------- JSON helpers for the editor ----------------
@bp.route("/api/unit")
@login_required
def api_unit():
    cid = request.args.get("customer", type=int)
    form_key = request.args.get("form")
    unit_no = request.args.get("unit", "").strip()
    fp_id = request.args.get("floorplan", type=int)
    if not cid or form_key not in FORMS:
        return jsonify({"ok": False, "error": "Pick a customer first."})
    unit, fid, rows = measurements_for_unit(cid, unit_no or None, fp_id, form_key)
    fp = q("SELECT name FROM floorplans WHERE id=?", (fid,), one=True) if fid else None
    blocks = [to_form_block(form_key, loads(r["data"]), r["room"], unit_no or None) for r in rows]
    msg = ""
    if unit_no and not unit:
        msg = f"Unit {unit_no} isn't saved for this customer yet."
    elif not rows:
        msg = "No saved measurements for this product" + (f" in floorplan {fp['name']}" if fp else "") + "."
    return jsonify({"ok": True, "blocks": blocks, "floorplan": fp["name"] if fp else None,
                    "unit_found": bool(unit), "message": msg,
                    "verified": [bool(r["verified"]) for r in rows]})


@bp.route("/api/customer/<int:cid>")
@login_required
def api_customer(cid):
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True)
    if not c:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "header": customer_header(c)})


@bp.route("/orders/<int:oid>/learn", methods=["POST"])
@login_required
def learn(oid):
    """Save this order's line items as measurements for their units / floorplans."""
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    if not o["customer_id"]:
        flash("Link the order to a customer first.", "error")
        return redirect(url_for("orders.view", oid=oid))
    from ..catalog import products_for_form
    from ..importer import guess_product
    data = loads(o["data"])
    prods = products_for_form(o["form_key"])
    b = new_batch("order", f"Measurements from order #{oid}")
    n = 0
    for blk in data.get("blocks", []):
        if not blk.get("unit") or not (blk.get("width") or blk.get("height")):
            continue
        u = q("""SELECT u.*, f.name AS fp FROM units u LEFT JOIN floorplans f ON f.id=u.floorplan_id
                 WHERE u.customer_id=? AND lower(u.unit_number)=lower(?)""", (o["customer_id"], blk["unit"]), one=True)
        product = blk.get("product") or guess_product(blk.get("_product", "")) or (prods[0] if prods else "other")
        d = {k: v for k, v in blk.items() if k not in ("unit", "line_comments", "_room", "_product", "price", "product",
                                                       "description", "amount", "notes", "qty", "item_no") and v}
        room = d.pop("room", "") or blk.get("_room", "")
        add_change(b, "new_measurement", customer_id=o["customer_id"],
                   payload={"product": product, "room": room, "data": d, "unit_number": blk["unit"],
                            "floorplan_name": u["fp"] if u and u["fp"] else "", "verified": False})
        n += 1
    if not n:
        x("DELETE FROM change_batches WHERE id=?", (b,))
        flash("No lines with a unit number and measurements to save.", "info")
        return redirect(url_for("orders.view", oid=oid))
    return redirect(url_for("imports.review_batch", bid=b))


@bp.route("/orders/<int:oid>/contact_from_email", methods=["POST"])
@login_required
def contact_from_email(oid):
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    c = q("SELECT * FROM customers WHERE id=?", (o["customer_id"],), one=True)
    b = new_batch("email", "Contact from email")
    propose_contact(b, c, {"name": request.form.get("name"), "email": request.form.get("email"),
                           "phone": request.form.get("phone")})
    return redirect(url_for("imports.review_batch", bid=b))
