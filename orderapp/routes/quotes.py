"""Quotes / estimates, account price books, and the morning delivery list."""
import os
from datetime import date

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from .. import deliveries as DL
from .. import pricing as P
from .. import quotes as QT
from ..db import loads, now, q, setting, x
from ..paths import ensure_dir
from ..sales import CATEGORIES, _num
from ..security import login_required

bp = Blueprint("quotes", __name__)


def _hold(cid):
    from ..holds import hold_for
    return hold_for(cid) if cid else None


# ------------------------------------------------------------------ quotes
@bp.route("/quotes")
@login_required
def index():
    st = request.args.get("status", "")
    sql = """SELECT qu.*, c.name cname, (SELECT SUM(qty*coalesce(unit_price,0)) FROM quote_lines WHERE quote_id=qu.id) total
             FROM quotes qu LEFT JOIN customers c ON c.id=qu.customer_id"""
    args = []
    if st:
        sql += " WHERE qu.status=?"
        args.append(st)
    rows = q(sql + " ORDER BY qu.id DESC LIMIT 300", args)
    counts = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM quotes GROUP BY status")}
    return render_template("quotes.html", rows=rows, status=st, statuses=QT.QUOTE_STATUSES, counts=counts,
                           now_date=date.today().isoformat(),
                           customers=q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE"))


@bp.route("/quotes/new", methods=["GET", "POST"])
@login_required
def new():
    v = request.values
    cid = v.get("customer", type=int) or v.get("customer_id", type=int)
    fid = v.get("floorplan", type=int)
    sid = v.get("invoice", type=int)
    units = v.get("units", type=int) or 1
    if not cid and fid:
        f = q("SELECT customer_id FROM floorplans WHERE id=?", (fid,), one=True)
        cid = f["customer_id"] if f else None
    if not cid and sid:
        s = q("SELECT customer_id FROM sales WHERE id=?", (sid,), one=True)
        cid = s["customer_id"] if s else None
    if not cid:
        flash("Pick a customer for the quote.", "error")
        return redirect(url_for("quotes.index"))
    title = v.get("title", "").strip()
    if fid:
        fp = q("SELECT name FROM floorplans WHERE id=?", (fid,), one=True)
        title = title or f"Turn kit - floorplan {fp['name'] if fp else ''} x {units} unit{'s' if units != 1 else ''}"
        qid = QT.create(cid, title, "turn kit", fid, units)
        n = QT.add_floorplan(qid, cid, fid, units)
        if not n:
            flash("That floorplan has no measurements yet - draw its floor plan or add measurements, "
                  "then try again. Started an empty quote.", "info")
    elif sid:
        s = q("SELECT invoice_no FROM sales WHERE id=?", (sid,), one=True)
        qid = QT.create(cid, title or f"Re-quote of invoice {s['invoice_no'] if s else ''}", "invoice")
        QT.add_invoice(qid, cid, sid)
    else:
        qid = QT.create(cid, title, "manual")
    return redirect(url_for("quotes.edit", qid=qid))


@bp.route("/quotes/<int:qid>", methods=["GET", "POST"])
@login_required
def edit(qid):
    qu = QT.get(qid) or abort(404)
    if request.method == "POST":
        f = request.form
        x("""UPDATE quotes SET title=?, valid_until=?, notes=?, terms=?, updated=? WHERE id=?""",
          (f.get("title", "").strip(), f.get("valid_until", ""), f.get("notes", "").strip(), f.get("terms", "").strip(),
           now(), qid))
        keep = []
        for i, lid in enumerate(f.getlist("line_id")):
            desc = f.getlist("description")[i].strip()
            if not desc and not f.getlist("item_no")[i].strip():
                continue
            qty = _num(f.getlist("qty")[i]) or 0
            price = _num(f.getlist("unit_price")[i])
            vals = (i, f.getlist("item_no")[i].strip(), desc, f.getlist("category")[i], qty,
                    f.getlist("unit")[i] or "ea", price, f.getlist("price_source")[i])
            if lid.isdigit() and q("SELECT 1 FROM quote_lines WHERE id=? AND quote_id=?", (int(lid), qid), one=True):
                x("""UPDATE quote_lines SET position=?, item_no=?, description=?, category=?, qty=?, unit=?, unit_price=?,
                     price_source=? WHERE id=?""", vals + (int(lid),))
                keep.append(int(lid))
            else:
                keep.append(x("""INSERT INTO quote_lines(position, item_no, description, category, qty, unit, unit_price,
                                 price_source, quote_id) VALUES (?,?,?,?,?,?,?,?,?)""", vals + (qid,)))
        if keep:
            x(f"DELETE FROM quote_lines WHERE quote_id=? AND id NOT IN ({','.join('?' * len(keep))})", [qid] + keep)
        else:
            x("DELETE FROM quote_lines WHERE quote_id=?", (qid,))
        act = f.get("action", "save")
        if act in ("pdf", "send"):
            from .orders import output_dir
            path = QT.make_pdf(qid, ensure_dir(os.path.join(output_dir(), "Quotes")))
            if act == "pdf":
                from ..emailer import open_file
                try:
                    open_file(path)
                except Exception:  # noqa: BLE001
                    pass
                flash(f"Quote PDF saved: {path}", "ok")
            else:
                return redirect(url_for("quotes.send", qid=qid))
        else:
            flash("Quote saved.", "ok")
        return redirect(url_for("quotes.edit", qid=qid))
    ls = QT.lines(qid)
    for ln in ls:
        ln["meta"] = loads(ln["meta"]) if ln["meta"] else None
    fps = q("""SELECT f.id, f.name, (SELECT COUNT(*) FROM measurements m WHERE m.floorplan_id=f.id AND m.unit_id IS NULL) n,
               (SELECT COUNT(*) FROM units u WHERE u.floorplan_id=f.id) units FROM floorplans f WHERE f.customer_id=?
               ORDER BY f.name""", (qu["customer_id"],)) if qu["customer_id"] else []
    invs = q("""SELECT id, invoice_no, invoice_date, total FROM sales WHERE customer_id=? ORDER BY invoice_date DESC
                LIMIT 12""", (qu["customer_id"],)) if qu["customer_id"] else []
    return render_template("quote.html", qu=qu, lines=ls, totals=QT.totals(qid), fps=fps, invoices=invs,
                           categories=CATEGORIES, statuses=QT.QUOTE_STATUSES, hold=_hold(qu["customer_id"]),
                           order_ids=[int(i) for i in (qu["order_ids"] or "").split(",") if i])


@bp.route("/quotes/<int:qid>/add", methods=["POST"])
@login_required
def add_from(qid):
    qu = QT.get(qid) or abort(404)
    fid = request.form.get("floorplan", type=int)
    sid = request.form.get("invoice", type=int)
    if fid:
        units = request.form.get("units", type=int) or 1
        n = QT.add_floorplan(qid, qu["customer_id"], fid, units)
        if not qu["floorplan_id"]:
            x("UPDATE quotes SET floorplan_id=?, units=? WHERE id=?", (fid, units, qid))
        flash(f"Added {n} line(s) from the floorplan x {units} unit(s).", "ok")
    elif sid:
        n = QT.add_invoice(qid, qu["customer_id"], sid)
        flash(f"Added {n} line(s) from the invoice at today's prices.", "ok")
    return redirect(url_for("quotes.edit", qid=qid))


@bp.route("/quotes/<int:qid>/status", methods=["POST"])
@login_required
def status(qid):
    QT.get(qid) or abort(404)
    QT.set_status(qid, request.form.get("status", ""))
    flash(f"Quote marked {request.form.get('status')}.", "ok")
    return redirect(url_for("quotes.edit", qid=qid))


@bp.route("/quotes/<int:qid>/order", methods=["POST"])
@login_required
def to_order(qid):
    qu = QT.get(qid) or abort(404)
    ids, other = QT.to_orders(qid)
    if not ids:
        flash("None of these lines came from measurements, so there's no form to fill automatically. "
              "Start the order from New Order (the quote is marked Ordered).", "info")
        return redirect(url_for("quotes.edit", qid=qid))
    msg = f"Made {len(ids)} draft order(s) from {qu['number']} - check each one, add the PO, then send."
    if other:
        msg += f" {len(other)} line(s) without measurements need to go on an order by hand."
    flash(msg, "ok")
    return redirect(url_for("orders.editor", order=ids[0]))


@bp.route("/quotes/<int:qid>/send", methods=["GET", "POST"])
@login_required
def send(qid):
    qu = QT.get(qid) or abort(404)
    contacts = q("SELECT * FROM contacts WHERE customer_id=? AND coalesce(email,'')<>''", (qu["customer_id"],))
    to_default = (contacts[0]["email"] if contacts else "") or (qu["email"] or "")
    t = QT.totals(qid)
    subject = f"Quote {qu['number']} - {qu['cname'] or ''}" + (f" - {qu['title']}" if qu["title"] else "")
    body = (f"Hi,\n\nAttached is quote {qu['number']} for {qu['cname'] or 'you'}"
            f"{' (' + qu['title'] + ')' if qu['title'] else ''}: ${t['subtotal']:,.2f}, good until {qu['valid_until']}."
            f"\n\nReply \"approved\" (with a PO number if you use one) and I'll get it ordered.\n\n"
            f"Thank you,\n{setting('sales_rep') or ''}")
    if request.method == "POST":
        from ..emailer import MailError, provider
        from .orders import output_dir
        path = qu["pdf_path"] if qu["pdf_path"] and os.path.exists(qu["pdf_path"]) else \
            QT.make_pdf(qid, ensure_dir(os.path.join(output_dir(), "Quotes")))
        prov = provider(setting)
        try:
            kw = {"out_dir": ensure_dir(os.path.join(output_dir(), "Quotes"))} if prov.name == "eml" else {}
            msg = prov.send(request.form.get("to", "").strip(), request.form.get("cc", "").strip(),
                            request.form.get("subject") or subject, request.form.get("body") or body, [path],
                            review=True, **kw)
        except MailError as e:
            flash(str(e), "error")
            return redirect(url_for("quotes.send", qid=qid))
        except Exception as e:  # noqa: BLE001
            flash(f"Couldn't make the email: {e}", "error")
            return redirect(url_for("quotes.send", qid=qid))
        if qu["status"] == "Draft":
            x("UPDATE quotes SET status='Sent', sent_at=?, updated=? WHERE id=?", (now(), now(), qid))
        flash(msg + " Nothing is sent until you press Send. Quote marked Sent.", "ok")
        return redirect(url_for("quotes.edit", qid=qid))
    return render_template("quote_send.html", qu=qu, to=to_default, contacts=contacts, subject=subject, body=body,
                           totals=t)


@bp.route("/quotes/<int:qid>/delete", methods=["POST"])
@login_required
def delete(qid):
    x("DELETE FROM quotes WHERE id=?", (qid,))
    flash("Quote deleted.", "ok")
    return redirect(url_for("quotes.index"))


@bp.route("/api/items")
@login_required
def api_items():
    term = request.args.get("q", "").strip()
    if len(term) < 2:
        return jsonify([])
    return jsonify(P.search_items(term, request.args.get("customer", type=int)))


@bp.route("/api/price")
@login_required
def api_price():
    p = P.price_for(request.args.get("customer", type=int), request.args.get("item_no") or None,
                    request.args.get("description") or None, request.args.get("category") or None)
    return jsonify(p or {})


# ------------------------------------------------------------------ account pricing
@bp.route("/customers/<int:cid>/prices", methods=["GET", "POST"])
@login_required
def prices(cid):
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True) or abort(404)
    if request.method == "POST":
        f = request.form
        n = 0
        for i, item in enumerate(f.getlist("item_no")):
            raw = f.getlist("agreed")[i].strip()
            P.set_agreed(cid, item, f.getlist("description")[i], f.getlist("category")[i], _num(raw) if raw else None)
            n += 1
        if f.get("new_description", "").strip() and _num(f.get("new_price")) is not None:
            P.set_agreed(cid, f.get("new_item_no", "").strip(), f.get("new_description").strip(),
                         f.get("new_category", ""), _num(f.get("new_price")), f.get("new_notes", ""))
        flash("Account prices saved.", "ok")
        return redirect(url_for("quotes.prices", cid=cid))
    book, extra = P.price_book(cid)
    return render_template("prices.html", c=c, book=book, extra=extra, categories=CATEGORIES)


# ------------------------------------------------------------------ morning delivery list
@bp.route("/deliveries", methods=["GET", "POST"])
@login_required
def deliveries():
    if request.method == "POST":
        if request.form.get("check_email"):
            r = DL.check_email()
            if r["error"]:
                flash(r["error"], "error")
            elif not r["new"]:
                flash(f"No new delivery-list email today (looking for \"{setting('delivery_subject_word')}\" in the "
                      f"subject).", "info")
            else:
                flash(f"Read {r['new']} delivery list(s): {r['matched']} order(s) matched, {r['applied']} marked "
                      f"Delivered.", "ok")
            return redirect(url_for("quotes.deliveries"))
        if request.form.get("apply"):
            ids = [int(i) for i in request.form.getlist("order_id") if i.isdigit()]
            n = DL.apply(ids)
            cid = request.form.get("check_id", type=int)
            if cid:
                row = q("SELECT applied FROM delivery_checks WHERE id=?", (cid,), one=True)
                done = set(loads(row["applied"], [])) | set(ids) if row else set(ids)
                from ..db import dumps
                x("UPDATE delivery_checks SET applied=? WHERE id=?", (dumps(sorted(done)), cid))
            flash(f"Marked {n} order(s) Delivered.", "ok")
            return redirect(url_for("quotes.deliveries"))
        text = request.form.get("text", "")
        f = request.files.get("file")
        if f and f.filename:
            text += "\n" + DL.attachment_text([(f.filename, f.read())])
        if not text.strip():
            flash("Paste the delivery list (or attach it) first.", "error")
            return redirect(url_for("quotes.deliveries"))
        _, res, _ = DL.record(text, "pasted", "Pasted delivery list", now()[:16], auto_apply=False)
        flash(f"Found {len(res)} matching order(s). Check them below and click Mark delivered.", "ok")
        return redirect(url_for("quotes.deliveries"))
    checks = DL.recent(8)
    for c in checks:
        for r in c["results"]:
            o = q("SELECT status FROM orders WHERE id=?", (r["order_id"],), one=True)
            r["now"] = o["status"] if o else "(deleted)"
        c["unmatched"] = DL.unmatched_lines(c["body"], c["results"])
    return render_template("deliveries.html", checks=checks, word=setting("delivery_subject_word") or "",
                           auto=(setting("delivery_auto") or "1") == "1", open_n=len(DL.candidates()),
                           today=date.today().isoformat())
