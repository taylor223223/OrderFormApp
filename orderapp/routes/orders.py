import io
import os
import re
from datetime import date, datetime

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)

from .. import reorder as RO
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
    h = {k: (c[k] or "") for k in ("name", "acct", "address", "city", "state", "zip", "mgmt", "phone")}
    # who to talk to: the Maint. Super, else the Property Manager (people.order_contact); the property's own
    # phone / email fill in what that person doesn't have
    from ..people import order_contact
    p = order_contact(c["id"]) or {}
    h.update(contact=(p.get("name") or "").strip(), contact_phone=(p.get("phone") or c["phone"] or "").strip(),
             contact_email=(p.get("email") or c["email"] or "").strip())
    return h


def _layouts_for(cid):
    """This property's box-by-box cabinet layouts (to attach to a cabinet / countertop order)."""
    if not cid:
        return []
    from ..cabinet_layout import is_v2
    from ..db import loads
    return [r for r in q("SELECT id, name, unit, data FROM kitchen_layouts WHERE customer_id=? ORDER BY updated DESC",
                         (cid,)) if is_v2(loads(r["data"], {}))]


def contacts_json(cid):
    """Saved people at a property, for the editor's contact picker."""
    if not cid:
        return []
    from ..people import people_at
    return [{"name": p["name"] or "", "role": p["role"] or "", "phone": p["phone"] or "", "email": p["email"] or ""}
            for p in people_at(cid) if p["name"]]


def output_dir():
    return ensure_dir(setting("output_dir") or default_output_dir())


def _iso(d):
    """Order date as YYYY-MM-DD (it's stored the way it's typed: 10/13/2026, 2026-10-13...)."""
    s = (d or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def order_folder(customer, day=None):
    """Where an order PDF goes: Orders folder / Customer (default), / 2026-10, or / Customer / 2026-10."""
    layout = setting("pdf_layout") or "customer"
    month = (day or date.today().isoformat())[:7]
    if layout == "month":
        return os.path.join(output_dir(), month)
    if layout == "customer_month":
        return os.path.join(output_dir(), safe_name(customer), month)
    return os.path.join(output_dir(), safe_name(customer))


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]+', "-", s).strip()[:150]


def _photos_for(oid):
    from ..photos import photos_for
    return photos_for(oid)


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
    from ..catalog import upgrade_blocks
    upgrade_blocks(data)
    h = data.setdefault("header", {})
    if not oid:
        ch = customer_header(c)
        for k, v in ch.items():
            # the contact is always the property's current Maint. Super / PM (even on a copied order);
            # other boxes only fill in when blank
            if not str(h.get(k) or "").strip() or (k.startswith("contact") and ch.get("contact")):
                h[k] = v
        if copy_id:
            h["date"] = today()
        h.setdefault("date", today())
    elif c and not str(h.get("contact") or "").strip():
        for k in ("contact", "contact_phone", "contact_email"):
            h[k] = h.get(k) or customer_header(c)[k]
    if not oid:
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
    # Cabinet Order Form: the order IS the kitchen layout (new orders, and orders saved that way); cabinet
    # orders from before v2.29 (typed line by line) still open in the line editor
    from .. import cabinet_layout as CL
    layout_mode = bool(FORMS[form_key].get("layout_editor")) and (not oid or bool(data.get("layout")))
    ldata = CL.normalize(data["layout"]) if data.get("layout") else CL.order_starter()
    return render_template("order_editor.html", spec=public_spec(form_key), form_key=form_key, order=order,
                           layout_mode=layout_mode, ldata=ldata, C=CL,
                           data=data, c=c, customers=customers, units=[dict(u) for u in units],
                           fps=[dict(f) for f in fps], sugg=suggestions(form_key, cid), forms=form_list(),
                           last=last, photos=_photos_for(oid) if oid else [], people=contacts_json(cid),
                           layouts=_layouts_for(cid),
                           kind=((order["kind"] if order else None) or request.args.get("kind") or "order"))


@bp.route("/orders/save", methods=["POST"])
@login_required
def save():
    form_key = request.form.get("form_key")
    if form_key not in FORMS:
        abort(400)
    data = loads(request.form.get("payload"), {})
    if FORMS[form_key].get("layout_editor") and isinstance(data.get("layout"), dict):
        # the cabinet order's boxes (kitchen layout) -> its lines, so tracking / history see each box
        from .. import cabinet_layout as CL
        data["layout"] = CL.normalize(dict(data["layout"], v=CL.V))
        data["blocks"] = CL.order_lines(data["layout"], form_key)
        data.pop("layout_id", None)
    elif "layout" in data:
        data.pop("layout")
    if any(f["key"] == "door_edge" for f in FORMS[form_key]["blocks"][0]):
        from ..door_edge import fill_blocks
        fill_blocks(data.get("blocks"))      # 30 x 80 -> Full Book, 29-3/4 x 79-1/2 -> Beveled (if not picked)
    for b in data.get("blocks") or []:      # cabinet hinge bores: bottom = same distance as the top unless typed
        if isinstance(b, dict) and b.get("hinge_top") and not b.get("hinge_bottom"):
            b["hinge_bottom"] = b["hinge_top"]
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
    from ..order_kind import kind_title
    kind = "quote" if request.form.get("kind") == "quote" else "order"
    title = kind_title(FORMS[form_key]["title"], kind)
    if oid:
        x("""UPDATE orders SET customer_id=?, form_key=?, title=?, po=?, order_date=?, data=?, updated=?,
             est_value=NULL, kind=? WHERE id=?""", (cid, form_key, title, po, odate, dumps(data), now(), kind, oid))
        if cid:   # keep the auto contact-log entry on the right property
            x("UPDATE activities SET customer_id=? WHERE order_id=? AND owner='auto'", (cid, oid))
    else:
        oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, created,
                   updated, kind) VALUES (?,?,?,?,?,'Draft',?,?,?,?,?)""",
                (cid, form_key, title, po, odate, dumps(data), request.form.get("source", "manual"), now(), now(),
                 kind))
        add_status(oid, "Draft", "Created")
        try:   # every order puts its property on the CRM contact log
            from ..contact_log import sync_orders
            sync_orders()
        except Exception:  # noqa: BLE001 - never block saving an order
            pass
        email_id = request.form.get("email_id", type=int)
        if email_id:
            x("UPDATE email_messages SET status='ordered', order_id=? WHERE id=?", (oid, email_id))
    try:   # sizes on the order go onto the property's units by themselves (no duplicates)
        from ..unit_sizes import save_order_sizes
        save_order_sizes(oid)
    except Exception:  # noqa: BLE001 - never block saving an order
        pass
    from .photos import _save_uploads
    new_photos = _save_uploads(oid, "photo_caption")
    if new_photos:
        add_status(oid, q("SELECT status FROM orders WHERE id=?", (oid,), one=True)["status"],
                   f"{new_photos} photo(s) added")
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
    if action not in ("pdf", "send") and new_photos:
        o2 = q("SELECT pdf_path FROM orders WHERE id=?", (oid,), one=True)
        if o2["pdf_path"] and os.path.exists(o2["pdf_path"]):
            _make_pdf(oid)      # keep the saved PDF's photo pages current
    if action in ("pdf", "send"):
        path, warns = _make_pdf(oid)
        from ..holds import hold_for, hold_message
        h = hold_for(cid)
        if h:
            flash(hold_message(h), "error")
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


def _append_layout(pdf, lid, cid, form_key=None):
    import pymupdf as fitz
    from .. import cabinet_layout as C
    from ..db import loads
    r = q("SELECT * FROM kitchen_layouts WHERE id=?", (lid,), one=True)
    if not r or not C.is_v2(loads(r["data"], {})):
        return pdf
    c = q("SELECT * FROM customers WHERE id=?", (r["customer_id"] or cid,), one=True)
    lay = dict(r, data=loads(r["data"], {}))
    # a cabinet order gets the cabinets-only layout; the countertop is its own order form
    extra = C.make_pdf(lay, dict(c) if c else None, with_top=not str(form_key or "").startswith("cabinet"))
    doc = fitz.open(stream=pdf, filetype="pdf")
    doc.insert_pdf(fitz.open(stream=extra, filetype="pdf"))
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def _make_pdf(oid):
    o = q("SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE o.id=?",
          (oid,), one=True)
    data = loads(o["data"])
    cust = o["cname"] or data.get("header", {}).get("name") or "Customer"
    if o["form_key"] == RO.FORM_KEY:
        pdf, warns = RO.render(o, data, cust), []
    elif data.get("layout") and FORMS.get(o["form_key"], {}).get("layout_editor"):
        from .. import cabinet_layout as CL     # the Cabinet Order Form: the kitchen layout drawing + box list
        from ..pdf_fill import check_order
        h = data.get("header") or {}
        pdf = CL.make_pdf({"data": data["layout"], "name": data["layout"].get("room") or "Kitchen",
                           "unit": data["layout"].get("unit") or ""},
                          None, order={"header": dict(h, name=h.get("name") or cust), "comments": data.get("comments"),
                                       "sales_rep": data.get("sales_rep")})
        warns = check_order(o["form_key"], data)
    else:
        pdf, warns = fill_form(o["form_key"], data)
    from ..order_kind import is_quote, kind_title, stamp_quote
    if is_quote(o):
        pdf = stamp_quote(pdf, RO.TITLE if o["form_key"] == RO.FORM_KEY else FORMS[o["form_key"]]["title"])
    if data.get("layout_id"):   # the attached cabinet layout's drawing + countertop pages
        try:
            pdf = _append_layout(pdf, int(data["layout_id"]), o["customer_id"], o["form_key"])
        except Exception as e:  # noqa: BLE001 - never lose the order form over the drawing
            warns = list(warns) + [f"The cabinet layout couldn't be added to the PDF: {e}"]
    from ..photos import append_photo_pages
    try:      # photo references go on their own pages at the end of the order
        pdf = append_photo_pages(pdf, oid, f"{cust} - PO {o['po'] or '-'} - Order #{oid}")
    except Exception as e:  # noqa: BLE001 - never lose the order form over a photo
        warns = list(warns) + [f"Photos couldn't be added to the PDF: {e}"]
    day = _iso(o["order_date"])
    ftitle = RO.TITLE if o["form_key"] == RO.FORM_KEY else FORMS[o["form_key"]]["title"]
    fname = safe_name(f"{day} - {cust} - {kind_title(ftitle, o['kind'])} - "
                      f"PO {o['po'] or 'none'} - #{oid}.pdf")
    folder = ensure_dir(order_folder(cust, day))
    path = os.path.join(folder, fname)
    if o["pdf_path"] and os.path.exists(o["pdf_path"]) and os.path.abspath(o["pdf_path"]) != os.path.abspath(path):
        try:                      # re-made after an edit or a rename: don't leave the old copy behind
            os.remove(o["pdf_path"])
        except OSError:
            pass
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
                           is_reorder=o["form_key"] == RO.FORM_KEY,
                           photos=photos_for(oid), products_lbl=products_lbl,
                           statuses=STATUSES, pdf_exists=bool(o["pdf_path"] and os.path.exists(o["pdf_path"])))


@bp.route("/orders/<int:oid>/pdf")
@login_required
def pdf(oid):
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    if o["form_key"] not in FORMS and o["form_key"] != RO.FORM_KEY:
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
    has_form = o["form_key"] in FORMS or o["form_key"] == RO.FORM_KEY
    photos = photos_for(oid)
    if not has_form and not photos:
        flash("Add a photo or use an order form before emailing this order.", "error")
        return redirect(url_for("orders.view", oid=oid))
    data = loads(o["data"])
    stale = bool(has_form and photos and o["pdf_path"] and os.path.exists(o["pdf_path"]) and
                 max(os.path.getmtime(p["path"]) for p in photos) > os.path.getmtime(o["pdf_path"]))
    if has_form and (stale or not (o["pdf_path"] and os.path.exists(o["pdf_path"]))):
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
        pmode = request.form.get("photos_mode", "inpdf" if has_form else "pdf")
        if photos and pmode == "pdf" and not has_form:
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
        x("UPDATE orders SET sent_subject=?, sent_to=? WHERE id=?",
          (request.form.get("subject", ""), ", ".join(a for a in (to, request.form.get("cc", "").strip()) if a), oid))
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
    from ..order_kind import is_quote
    if is_quote(o):
        body = body.replace("Please see the attached", "Please price the attached", 1)
        if "price" not in body.lower():
            body = "Quote request - please price this and send the quote back (not an order yet).\n\n" + body
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


@bp.route("/orders/<int:oid>/reorder", methods=["POST"])
@login_required
def reorder(oid):
    """One click: a new draft with the same items, today's date and a blank PO."""
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    if o["form_key"] not in FORMS:
        flash("That order has no order form to copy.", "error")
        return redirect(url_for("orders.view", oid=oid))
    from ..order_kind import kind_title
    data = loads(o["data"]) or {}
    h = data.setdefault("header", {})
    h["po"] = ""
    h["date"] = today()
    if o["customer_id"]:      # who to talk to: the same person a new order starts with (Maint. Super, else PM)
        c = q("SELECT * FROM customers WHERE id=?", (o["customer_id"],), one=True)
        if c:
            ch = customer_header(c)
            for k in ("contact", "contact_phone", "contact_email"):
                if ch.get(k):
                    h[k] = ch[k]
    nid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, notes, created,
               updated) VALUES (?,?,?,?,?,'Draft',?,?,?,?,?)""",
            (o["customer_id"], o["form_key"], kind_title(o["title"], "order"), "", date.today().isoformat(), dumps(data),
             "reorder", f"Reorder of #{oid}", now(), now()))
    add_status(nid, "Draft", f"Reorder of #{oid}")
    from ..holds import hold_for, hold_message
    hd = hold_for(o["customer_id"])
    if hd:
        flash(hold_message(hd), "error")
    flash(f"Reorder ready as draft #{nid} - same items as #{oid}. Add the PO, check quantities, then send.", "ok")
    return redirect(url_for("orders.editor", order=nid))


@bp.route("/orders/<int:oid>/status", methods=["POST"])
@login_required
def set_status(oid):
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    st = request.form.get("status")
    from ..order_kind import TAPS, kind_title
    tap = next((t for t in TAPS if t[0] == request.form.get("tap")), None)
    if tap:                       # one tap: "Quote sent" = status Sent + it's an estimate
        st = tap[2]
        if tap[1] and tap[1] != (o["kind"] or "order"):
            x("UPDATE orders SET kind=?, title=? WHERE id=?", (tap[1], kind_title(o["title"], tap[1]), oid))
            if o["pdf_path"] and os.path.exists(o["pdf_path"]) and o["form_key"] in FORMS:
                _make_pdf(oid)    # banner on / off
            o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
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
    """Delete = move to Recently deleted (30 days). The next page shows an Undo button."""
    from .. import order_trash
    tid = order_trash.delete(oid)
    if tid is None:
        abort(404)
    nxt = request.form.get("next") or ""
    if not nxt.startswith("/tracking"):
        nxt = url_for("orders.tracking")
    import re
    nxt = re.sub(r"[?&]undo=\d+", "", nxt).rstrip("?&")
    return redirect(nxt + ("&" if "?" in nxt else "?") + f"undo={tid}")


@bp.route("/orders/restore/<int:tid>", methods=["POST"])
@login_required
def restore(tid):
    from .. import order_trash
    oid = order_trash.restore(tid)
    if oid is None:
        flash("That order is no longer in Recently deleted.", "error")
        return redirect(url_for("orders.tracking"))
    flash(f"Order #{oid} is back.", "ok")
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") else url_for("orders.view", oid=oid))


# ---------------- tracking ----------------
@bp.route("/tracking")
@login_required
def tracking():
    from ..order_kind import TAP_LABELS, TAPS, status_label
    month = request.args.get("month", "").strip()[:7]
    st = request.args.get("status", "all" if month else "open")
    s = request.args.get("q", "").strip()
    # sent_on: when it was sent off (older orders: the first time it was marked Sent)
    sql = """SELECT o.*, c.name AS cname, coalesce(nullif(o.sent_at, ''), (SELECT MIN(h.ts) FROM order_status_history h
                    WHERE h.order_id=o.id AND h.status='Sent')) AS sent_on
             FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE 1=1"""
    args = []
    if month:   # dashboard "Orders this month"
        sql += """ AND o.status<>'Draft'
                   AND substr(coalesce(nullif(o.sent_at,''), nullif(o.order_date,''), o.created), 1, 7)=?"""
        args.append(month)
    if st == "open":
        sql += f" AND o.status IN ({','.join('?' * len(OPEN_STATUSES))})"
        args += OPEN_STATUSES
    elif st in TAP_LABELS:
        _t = next(t for t in TAPS if t[0] == st)
        sql += " AND o.status=?"
        args.append(_t[2])
        if _t[1]:
            sql += " AND coalesce(o.kind,'order')=?"
            args.append(_t[1])
    elif st and st != "all":
        sql += " AND o.status=?"
        args.append(st)
    if s:
        sql += " AND (c.name LIKE ? OR o.po LIKE ? OR o.title LIKE ? OR o.vendor_ref LIKE ? OR o.notes LIKE ?)"
        args += [f"%{s}%"] * 5
    sql += " ORDER BY CASE WHEN coalesce(o.due_date,'')='' THEN 1 ELSE 0 END, o.due_date, o.updated DESC"
    rows = q(sql, args)
    from .. import order_trash
    order_trash.purge()
    trash = order_trash.recent()
    undo = None
    if request.args.get("undo", "").isdigit():
        undo = q("SELECT * FROM order_trash WHERE id=?", (int(request.args["undo"]),), one=True)
    counts = {}
    for r in q("SELECT status, coalesce(kind,'order') kind, COUNT(*) n FROM orders GROUP BY status, kind"):
        lab = status_label(r["status"], r["kind"])
        counts[lab] = counts.get(lab, 0) + r["n"]
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    from ..order_insights import tracking_totals
    try:
        totals = tracking_totals()
    except Exception:   # noqa: BLE001 - the board must always load
        totals = None
    return render_template("tracking.html", rows=rows, statuses=STATUSES, st=st, s=s, counts=counts,
                           customers=customers, month=month, totals=totals, out_dir=output_dir(),
                           trash=trash, undo=undo)


@bp.route("/orders/folder", methods=["POST"])
@login_required
def open_orders_folder():
    """Open the folder all order PDFs are saved in, in File Explorer on this computer."""
    from ..emailer import open_file
    try:
        open_file(output_dir())
    except Exception:  # noqa: BLE001
        flash(f"Your order PDFs are in: {output_dir()}", "info")
    return redirect(request.referrer or url_for("orders.tracking"))


@bp.route("/tracking/add", methods=["POST"])
@login_required
def tracking_add():
    """Manual tracking entry for an order placed outside the app."""
    title = request.form.get("title", "").strip() or "Manual order"
    cid = request.form.get("customer_id", type=int)
    from ..order_kind import TAPS
    tap = next((t for t in TAPS if t[0] == request.form.get("tap")), None)
    st = tap[2] if tap else (request.form.get("status") if request.form.get("status") in STATUSES else "Sent")
    kind = tap[1] if tap and tap[1] else "order"
    oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, vendor_ref,
               due_date, notes, created, updated, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, None, title, request.form.get("po", "").strip(), request.form.get("order_date", "").strip() or today(),
             st, "{}", "manual-tracking", request.form.get("vendor_ref", "").strip(),
             request.form.get("due_date", "").strip(), request.form.get("notes", "").strip(), now(), now(), kind))
    if st == "Sent":       # placed outside the app: it went out on its order date
        x("UPDATE orders SET sent_at=? WHERE id=?", (request.form.get("order_date", "").strip() or now(), oid))
    add_status(oid, st, "Added manually")
    flash("Tracking entry added.", "ok")
    return redirect(url_for("orders.tracking"))


# ---------------- JSON helpers for the editor ----------------
@bp.route("/api/countertop-shape", methods=["POST"])
@login_required
def api_countertop_shape():
    """The countertop line's top-view drawing for its kitchen shape (shown under the line while it's filled in)."""
    from .. import kitchen_shape as K
    v = request.get_json(silent=True) or {}
    v = {k: str(val) for k, val in v.items() if isinstance(val, (str, int, float))}
    return jsonify({"svg": K.countertop_svg(v.get("ct_shape") or "Straight", v)})


@bp.route("/api/order-help", methods=["POST"])
@login_required
def api_order_help():
    """Previous orders of this product for the customer + a rough price range for the lines being filled in."""
    from .. import order_insights as OI
    j = request.get_json(silent=True) or {}
    cid = j.get("customer")
    try:
        cid = int(cid) if cid else None
    except (TypeError, ValueError):
        cid = None
    fk = j.get("form") if j.get("form") in FORMS else None
    blocks = [b for b in (j.get("blocks") or []) if isinstance(b, dict)][:30]
    prev = OI.previous_for(cid, fk, blocks) if cid and fk else []
    for r in prev:
        r.pop("size", None)
    est = OI.estimate_order(cid, fk, blocks) if fk and blocks else None
    return jsonify({"previous": prev, "estimate": est})

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
    from ..holds import hold_for
    h = hold_for(cid)
    return jsonify({"ok": True, "header": customer_header(c), "people": contacts_json(cid),
                    "hold": {"name": h["name"], "balance": f"{h['balance'] or 0:,.2f}"} if h else None})


@bp.route("/orders/<int:oid>/learn", methods=["POST"])
@login_required
def learn(oid):
    """Save this order's line items as measurements for their units / floorplans."""
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    if not o["customer_id"]:
        flash("Link the order to a customer first.", "error")
        return redirect(url_for("orders.view", oid=oid))
    from ..unit_sizes import save_order_sizes
    a, u = save_order_sizes(oid)
    flash(f"Sizes saved to units: {a} new, {u} updated." if (a or u) else
          "Every size on this order is already saved on its units.", "ok")
    return redirect(url_for("orders.view", oid=oid))


@bp.route("/orders/<int:oid>/contact_from_email", methods=["POST"])
@login_required
def contact_from_email(oid):
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    c = q("SELECT * FROM customers WHERE id=?", (o["customer_id"],), one=True)
    b = new_batch("email", "Contact from email")
    propose_contact(b, c, {"name": request.form.get("name"), "email": request.form.get("email"),
                           "phone": request.form.get("phone")})
    return redirect(url_for("imports.review_batch", bid=b))


@bp.route("/orders/<int:oid>/reorder_lines", methods=["GET", "POST"])
@login_required
def reorder_lines(oid):
    """Edit a reorder request's lines (qty, item, description, size, for, PO, comments) and re-make the PDF."""
    o = q("SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE o.id=?",
          (oid,), one=True) or abort(404)
    if o["form_key"] != RO.FORM_KEY:
        abort(404)
    data = loads(o["data"]) or {}
    if request.method == "POST":
        f = request.form
        n = f.get("n", type=int) or 0
        lines = [{k: f.get(f"{k}_{i}", "") for k in ("qty", "item_no", "desc", "size", "for", "invoice",
                                                      "last_date", "last_price", "notes")} for i in range(n + 5)]
        data["lines"] = RO.clean_lines(lines)
        data["comments"] = f.get("comments", "").strip()
        data["contact"] = f.get("contact", "").strip()
        po = f.get("po", "").strip()
        data.setdefault("header", {})["po"] = po
        data["header"].update(contact=data["contact"], contact_phone=f.get("contact_phone", "").strip(),
                              contact_email=f.get("contact_email", "").strip())
        x("UPDATE orders SET data=?, po=?, updated=? WHERE id=?", (dumps(data), po, now(), oid))
        _make_pdf(oid)
        flash(f"Saved - {len(data['lines'])} line(s). PDF updated.", "ok")
        return redirect(url_for("orders.view", oid=oid))
    return render_template("reorder_lines.html", o=o, data=data, lines=data.get("lines") or [])


# ---------------- job package: several order / quote forms in one PDF ----------------
def package_pdf(ids):
    """One PDF with every picked order form (each exactly as its own PDF prints, layout drawings and photos
    included), behind a contents page. Returns (pdf bytes, file path, warnings)."""
    import pymupdf as fitz
    from ..order_kind import is_quote
    rows = []
    for oid in ids:
        o = q("SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE o.id=?",
              (oid,), one=True)
        if o and (o["form_key"] in FORMS or o["form_key"] == RO.FORM_KEY):
            rows.append(o)
    if not rows:
        raise ValueError("Pick at least one order or quote that has an order form.")
    warns, parts = [], []
    for o in rows:
        path, w = _make_pdf(o["id"])
        warns += [f"#{o['id']}: {m}" for m in w]
        parts.append((o, fitz.open(path)))
    quotes = all(is_quote(o) for o, _ in parts)
    custs = sorted({(o["cname"] or loads(o["data"]).get("header", {}).get("name") or "") for o, _ in parts} - {""})
    cust = custs[0] if len(custs) == 1 else ("Several properties" if custs else "Customer")
    doc = fitz.open()
    cover = doc.new_page(width=612, height=792)
    _package_cover(cover, parts, cust, quotes)
    toc, page_no = [], 2
    for o, d in parts:
        toc.append([1, f"{o['title']}" + (f" - PO {o['po']}" if o["po"] else "") + f" (#{o['id']})", page_no])
        doc.insert_pdf(d)
        page_no += len(d)
        d.close()
    doc.set_toc(toc)
    doc.set_metadata({"title": f"{cust} - {'Quote' if quotes else 'Job'} package", "creator": "AIS Sales Support"})
    pdf = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    day = date.today().isoformat()
    name = safe_name(f"{day} - {cust} - {'Quote' if quotes else 'Order'} package ({len(parts)} forms) - "
                     f"#{'-'.join(str(o['id']) for o, _ in parts)}.pdf")
    path = os.path.join(ensure_dir(order_folder(cust, day)), name)
    with open(path, "wb") as fh:
        fh.write(pdf)
    return pdf, path, warns


def _package_cover(pg, parts, cust, quotes):
    """Contents page: company logo, the property, and every form in the package with its PO and pages."""
    import pymupdf as fitz
    from ..manager_forms import COMPANY
    try:
        src = fitz.open(COMPANY)
        pg.show_pdf_page(fitz.Rect(36, 30, 256, 112), src, 0, clip=fitz.Rect(68, 22, 337, 121))
    except Exception:  # noqa: BLE001 - the logo is decoration
        pass
    title = "QUOTE PACKAGE" if quotes else "ORDER PACKAGE"
    pg.draw_rect(fitz.Rect(300, 40, 576, 70), color=None, fill=(0, 0, 0))
    tw = fitz.get_text_length(title, fontname="hebo", fontsize=18)
    pg.insert_text((438 - tw / 2, 61.5), title, fontsize=18, fontname="hebo", color=(1, 1, 1))
    pg.insert_text((300, 88), f"{len(parts)} forms in this PDF", fontsize=10, fontname="helv")
    pg.insert_text((36, 140), cust, fontsize=16, fontname="hebo")
    h = loads(parts[0][0]["data"]).get("header") or {}
    sub = "  ·  ".join(v for v in [h.get("address"), " ".join(p for p in [h.get("city"), h.get("state"), h.get("zip")] if p)]
                       if v)
    if sub:
        pg.insert_text((36, 158), sub, fontsize=10, fontname="helv")
    who = "  ·  ".join(v for v in [h.get("contact"), h.get("contact_phone"), h.get("contact_email")] if v)
    if who:
        pg.insert_text((36, 174), "Contact: " + who, fontsize=10, fontname="helv")
    pg.insert_text((36, 190), f"Date: {date.today():%m/%d/%Y}", fontsize=10, fontname="helv")
    if quotes:
        pg.insert_text((36, 210), "Quote request - please price every form in this package (not an order yet).",
                       fontsize=10, fontname="hebo", color=(0.7, 0.1, 0.1))
    y = 236
    cols = [("#", 36), ("Form", 66), ("Order / Quote", 330), ("PO", 420), ("Pages", 520)]
    pg.draw_rect(fitz.Rect(36, y - 13, 576, y + 4), color=None, fill=(0.88, 0.9, 0.95))
    for t, x_ in cols:
        pg.insert_text((x_ + 3, y), t, fontsize=9.5, fontname="hebo")
    from ..order_kind import is_quote
    page_no = 2
    for i, (o, d) in enumerate(parts, 1):
        y += 20
        n = len(d)
        pages = f"{page_no}" + (f"-{page_no + n - 1}" if n > 1 else "")
        title = o["title"] or ""
        size = 9.5
        while size > 6.5 and fitz.get_text_length(title, fontname="helv", fontsize=size) > 255:
            size -= 0.25
        for t, (lab, x_), sz, font in ((str(i), cols[0], 9.5, "helv"), (title, cols[1], size, "helv"),
                                       ("Quote" if is_quote(o) else "Order", cols[2], 9.5, "hebo"),
                                       (o["po"] or "-", cols[3], 9.5, "helv"), (pages, cols[4], 9.5, "helv")):
            pg.insert_text((x_ + 3, y), t, fontsize=sz, fontname=font)
        pg.draw_line((36, y + 6), (576, y + 6), color=(0.8, 0.8, 0.8), width=0.4)
        page_no += n
    pg.insert_text((36, 770), "Apartment Interior Supply  ·  5325 S. Kyrene Rd., Suite 103, Tempe, AZ 85283  ·  "
                   "Phone (480) 964-7600  ·  Fax (480) 964-7610", fontsize=7.5, fontname="helv", color=(0.4, 0.4, 0.4))


def _package_ids():
    raw = request.values.getlist("ids") or (request.values.get("ids") or "").split(",")
    out = []
    for v in raw:
        for p in str(v).split(","):
            if p.strip().isdigit() and int(p) not in out:
                out.append(int(p))
    return out


@bp.route("/orders/package", methods=["GET", "POST"])
@login_required
def package():
    """Several forms for one job (cabinets, countertop, doors...) in ONE PDF to send, instead of one email
    attachment per form. Pick the forms, make the PDF, open it or email it."""
    from ..order_kind import is_quote
    ids = _package_ids()
    cid = request.values.get("customer", type=int)
    if not cid and ids:
        r = q("SELECT customer_id FROM orders WHERE id=?", (ids[0],), one=True)
        cid = r["customer_id"] if r else None
    made = None
    if request.method == "POST" and request.form.get("make"):
        try:
            _, path, warns = package_pdf(ids)
            made = {"path": path, "name": os.path.basename(path), "warns": warns}
        except ValueError as e:
            flash(str(e), "error")
    sql = """SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
             WHERE o.form_key IS NOT NULL AND o.status NOT IN ('Cancelled')"""
    args = []
    if cid:
        sql += " AND o.customer_id=?"
        args.append(cid)
    elif ids:
        sql += f" AND o.id IN ({','.join('?' * len(ids))})"
        args += ids
    else:
        sql += " AND o.status IN ('Draft','Ready')"
    sql += " ORDER BY o.updated DESC LIMIT 60"
    rows = [r for r in q(sql, args)]
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True) if cid else None
    picked = [q("SELECT * FROM orders WHERE id=?", (i,), one=True) for i in ids]
    picked = [p for p in picked if p]
    quotes = bool(picked) and all(is_quote(p) for p in picked)
    cname = c["name"] if c else ""
    pos = ", ".join(p["po"] for p in picked if p["po"])
    subject = f"{'Quote request' if quotes else 'Order'} - {cname or 'job'}" + (f" - PO {pos}" if pos else "")
    body = (f"Hello,\n\n{'Please price' if quotes else 'Please see'} the attached {len(picked)} forms for "
            f"{cname or 'this job'} (all in one PDF)" + (f", PO {pos}" if pos else "") + ".\n\n"
            + "\n".join(f"- {p['title']}" + (f" (PO {p['po']})" if p["po"] else "") for p in picked)
            + f"\n\nThank you,\n{setting('sales_rep') or ''}")
    return render_template("order_package.html", rows=rows, ids=ids, c=c, made=made, to=setting("order_to"),
                           cc=setting("order_cc"), subject=subject, body=body, mode=setting("send_mode"),
                           provider=setting("email_provider"), quotes=quotes,
                           customers=q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE"))


@bp.route("/orders/package/file")
@login_required
def package_file():
    ids = _package_ids()
    try:
        pdf, path, _ = package_pdf(ids)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("orders.package"))
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", download_name=os.path.basename(path),
                     as_attachment=bool(request.args.get("dl")))


@bp.route("/orders/package/send", methods=["POST"])
@login_required
def package_send():
    """Email the package: one PDF attachment. Sent for real = every form in it is marked sent."""
    ids = _package_ids()
    to = request.form.get("to", "").strip()
    if not to:
        flash("Who should it go to?", "error")
        return redirect(url_for("orders.package", ids=",".join(map(str, ids))))
    try:
        _, path, _ = package_pdf(ids)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("orders.package"))
    review = request.form.get("mode", setting("send_mode")) != "send"
    prov = provider(setting)
    try:
        kw = {"out_dir": os.path.dirname(path)} if prov.name == "eml" else {}
        msg = prov.send(to, request.form.get("cc", "").strip(), request.form.get("subject", ""),
                        request.form.get("body", ""), [path], review=review, **kw)
    except MailError as e:
        flash(str(e), "error")
        return redirect(url_for("orders.package", ids=",".join(map(str, ids))))
    except Exception as e:  # noqa: BLE001
        flash(f"Email failed: {e}", "error")
        return redirect(url_for("orders.package", ids=",".join(map(str, ids))))
    for oid in ids:
        x("UPDATE orders SET sent_subject=?, sent_to=? WHERE id=?",
          (request.form.get("subject", ""), ", ".join(a for a in (to, request.form.get("cc", "").strip()) if a), oid))
        if not review:
            x("UPDATE orders SET status='Sent', sent_at=?, updated=? WHERE id=?", (now(), now(), oid))
            add_status(oid, "Sent", f"Emailed to {to} (in one PDF with {len(ids) - 1} other forms)")
        else:
            o = q("SELECT status FROM orders WHERE id=?", (oid,), one=True)
            add_status(oid, o["status"], f"Email draft prepared for {to} (package of {len(ids)} forms)")
    flash(msg + (" Mark the forms as sent once it's gone." if review else ""), "ok")
    return redirect(url_for("orders.tracking"))
