"""Sales history + reports (month / year, customer, product), goals and commission."""
import csv
import io
import os
import uuid
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from .. import sales as S
from ..db import q, set_setting, setting, x
from ..paths import data_dir
from ..security import login_required

bp = Blueprint("sales", __name__)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _upload_dir():
    d = os.path.join(data_dir(), "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@bp.route("/sales")
@login_required
def home():
    ys = S.years()
    year = request.args.get("year") or ys[0]
    cat = request.args.get("category") or ""
    cid = _int(request.args.get("customer"))
    today = date.today()
    y = int(year)
    # "to date" compares the same stretch of each year (Jan 1 - today's month/day)
    md = today.strftime("%m-%d") if y == today.year else "12-31"
    upto, ly_upto = f"{y}-{md}", f"{y - 1}-{md if md != '02-29' else '02-28'}"
    this_m = monthly = S.monthly(y, cid, cat or None)
    last_m = S.monthly(y - 1, cid, cat or None)
    ytd = S.total(y, cid, cat or None, upto=upto)
    lytd = S.total(y - 1, cid, cat or None, upto=ly_upto)
    gl = S.goals(y)
    month_idx = today.month - 1 if y == today.year else 11
    cur = gl[month_idx]
    customers = S.by_customer(y, cat or None)
    last_cust = {(r["customer_id"] or r["name"]): r["total"] for r in S.by_customer(y - 1, cat or None, upto=ly_upto)}
    for r in customers:
        ly = last_cust.get(r["customer_id"] or r["name"])
        r["ly"] = ly or 0
        r["change"] = round((r["total"] - ly) / ly * 100) if ly else None
    cats = S.by_category(y, cid)
    cat_ly = {r["category"]: r["total"] for r in S.by_category(y - 1, cid, upto=ly_upto)}
    for r in cats:
        r["ly"] = cat_ly.get(r["category"], 0)
    comm_ytd = sum(g["commission"] for g in gl[:month_idx + 1])
    return render_template(
        "sales.html", year=y, years=ys, cat=cat, cid=cid, categories=S.CATEGORIES, months=MONTHS,
        this_m=this_m, last_m=last_m, ytd=ytd, lytd=lytd, goals=gl, cur=cur, comm_ytd=comm_ytd,
        customers=customers, cats=cats, gaps=S.gaps(y) if not cid else [], has=S.has_data(),
        change=round((ytd - lytd) / lytd * 100) if lytd else None, month_name=MONTHS[month_idx],
        cname=(q("SELECT name FROM customers WHERE id=?", (cid,), one=True) or {"name": ""})["name"] if cid else "",
        all_customers=q("""SELECT DISTINCT c.id, c.name FROM sales s JOIN customers c ON c.id=s.customer_id
                            ORDER BY c.name COLLATE NOCASE"""),
        unmatched=q("SELECT COUNT(*) n FROM sales WHERE customer_id IS NULL", one=True)["n"], monthly=monthly)


@bp.route("/sales/invoices")
@login_required
def invoices():
    year = request.args.get("year") or ""
    cid = _int(request.args.get("customer"))
    cat = request.args.get("category") or ""
    search = request.args.get("q", "").strip()
    rows = S.invoices(search, year or None, cid, cat or None)
    return render_template("sales_invoices.html", rows=rows, search=search, year=year, years=S.years(),
                           cid=cid, cat=cat, categories=S.CATEGORIES,
                           total=sum(r["total"] or 0 for r in rows))


@bp.route("/sales/invoice/<int:sid>")
@login_required
def invoice(sid):
    s = q("""SELECT s.*, coalesce(c.name, s.customer_name) cname FROM sales s
             LEFT JOIN customers c ON c.id=s.customer_id WHERE s.id=?""", (sid,), one=True)
    if not s:
        abort(404)
    return render_template("sales_invoice.html", s=s,
                           lines=q("SELECT * FROM sales_lines WHERE sale_id=? ORDER BY id", (sid,)))


@bp.route("/sales/invoice/<int:sid>/delete", methods=["POST"])
@login_required
def invoice_delete(sid):
    x("DELETE FROM sales WHERE id=?", (sid,))
    flash("Invoice removed from sales history.", "ok")
    return redirect(url_for("sales.invoices"))


@bp.route("/sales/import", methods=["GET", "POST"])
@login_required
def import_page():
    if request.method == "POST" and "file" in request.files:
        f = request.files["file"]
        if not f or not f.filename:
            flash("Pick a CSV file first.", "error")
            return redirect(url_for("sales.import_page"))
        raw = f.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        headers, rows = S.read_csv(text)
        if not rows:
            flash("That file has no rows (or isn't a CSV). In Excel use File > Save As > CSV.", "error")
            return redirect(url_for("sales.import_page"))
        token = uuid.uuid4().hex
        with open(os.path.join(_upload_dir(), f"sales_{token}.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return render_template("sales_import.html", step="map", headers=headers, rows=rows[:8], n=len(rows),
                               mapping=S.guess_mapping(headers), labels=S.FIELD_LABELS, token=token,
                               filename=f.filename)
    if request.method == "POST":
        token = request.form.get("token", "")
        if not token.isalnum():
            abort(400)
        path = os.path.join(_upload_dir(), f"sales_{token}.csv")
        if not os.path.exists(path):
            flash("That upload expired - pick the file again.", "error")
            return redirect(url_for("sales.import_page"))
        with open(path, encoding="utf-8") as fh:
            headers, rows = S.read_csv(fh.read())
        mapping = {k: request.form.get(f"map_{k}") for k in S.FIELD_LABELS if request.form.get(f"map_{k}")}
        if not mapping.get("invoice_date") or not (mapping.get("amount") or
                                                   (mapping.get("qty") and mapping.get("unit_price"))):
            flash("Pick at least the Date column and the Line total column (or Qty + Unit price).", "error")
            return render_template("sales_import.html", step="map", headers=headers, rows=rows[:8], n=len(rows),
                                   mapping=mapping, labels=S.FIELD_LABELS, token=token, filename="")
        res = S.import_rows(rows, mapping)
        os.remove(path)
        msg = (f"Imported {res['invoices']} invoice(s): {res['added']} new, {res['replaced']} updated. "
               f"{res['matched']} matched to customers in the app.")
        if res["skipped_rows"]:
            msg += f" Skipped {res['skipped_rows']} row(s) with no date or amount."
        flash(msg, "ok")
        if res["unmatched"]:
            flash(f"{len(res['unmatched'])} customer name(s) didn't match a customer here, e.g. "
                  f"{', '.join(res['unmatched'][:5])}. They still count in every report; add the customer "
                  f"(same name or account #) and click 'Re-link customers'.", "info")
        return redirect(url_for("sales.home"))
    return render_template("sales_import.html", step="upload", labels=S.FIELD_LABELS)


@bp.route("/sales/template.csv")
@login_required
def template():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(S.TEMPLATE_HEADERS)
    w.writerow(["INV-10001", "2026-03-14", "Sample Apartments", "1045", "PO-123", "TA", "VB-9684",
                "Vertical blind 96 x 84 alabaster", "Blinds & Shades", "2", "118.00", "236.00", "150.00"])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=Sales import template.csv"})


@bp.route("/sales/relink", methods=["POST"])
@login_required
def relink():
    n = S.relink_customers()
    flash(f"Linked {n} invoice(s) to customers.", "ok")
    return redirect(url_for("sales.home"))


@bp.route("/sales/goals", methods=["GET", "POST"])
@login_required
def goals():
    year = int(request.values.get("year") or date.today().year)
    if request.method == "POST":
        set_setting("commission_rate", request.form.get("commission_rate", "").strip())
        set_setting("monthly_goal", request.form.get("monthly_goal", "").strip())
        set_setting("commission_basis", "margin" if request.form.get("commission_basis") == "margin" else "sales")
        for m in range(1, 13):
            p = f"{year}-{m:02d}"
            g, r = request.form.get(f"goal_{m}", "").strip(), request.form.get(f"rate_{m}", "").strip()
            gv, rv = S._num(g), S._num(r)
            if gv is None and rv is None:
                x("DELETE FROM sales_goals WHERE period=?", (p,))
            else:
                x("""INSERT INTO sales_goals(period, goal, rate) VALUES (?,?,?)
                     ON CONFLICT(period) DO UPDATE SET goal=excluded.goal, rate=excluded.rate""", (p, gv, rv))
        flash("Goals and commission saved.", "ok")
        return redirect(url_for("sales.goals", year=year))
    return render_template("sales_goals.html", year=year, goals=S.goals(year), months=MONTHS,
                           rate=setting("commission_rate"), goal=setting("monthly_goal"),
                           basis=setting("commission_basis") or "sales", years=sorted(
                               set(S.years()) | {str(date.today().year), str(date.today().year + 1)}, reverse=True))


@bp.route("/sales/export.csv")
@login_required
def export():
    year = request.args.get("year") or ""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Invoice #", "Date", "Customer", "Acct #", "PO #", "Rep", "Category", "Item #", "Description",
                "Qty", "Unit Price", "Amount", "Cost"])
    sql = """SELECT s.*, coalesce(c.name, s.customer_name) cname, l.category, l.item_no, l.description, l.qty,
             l.unit_price, l.amount, l.cost lcost FROM sales s JOIN sales_lines l ON l.sale_id=s.id
             LEFT JOIN customers c ON c.id=s.customer_id"""
    args = []
    if year:
        sql += " WHERE substr(s.invoice_date,1,4)=?"
        args.append(year)
    for r in q(sql + " ORDER BY s.invoice_date, s.id", args):
        w.writerow([r["invoice_no"], r["invoice_date"], r["cname"], r["acct"], r["po"], r["rep"], r["category"],
                    r["item_no"], r["description"], r["qty"], r["unit_price"], r["amount"], r["lcost"]])
    name = f"Sales history{' ' + year if year else ''}.csv"
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})
