"""Company Data: every account, invoice and line from the company system, plus analysis tools."""
import csv
import io

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for

from .. import company_db as CD
from .. import sales as S
from ..security import login_required

bp = Blueprint("company", __name__)


def _f():
    a = request.args
    return {k: a.get(k, "").strip() for k in ("year", "rep", "category", "cust_key", "city", "from", "to", "item",
                                              "account", "type") if a.get(k, "").strip()}


@bp.route("/company")
@login_required
def home():
    if not CD.has_data():
        return render_template("company_home.html", empty=True)
    ys = CD.years()
    year = request.args.get("year") or (ys[0] if ys else "")
    rep = request.args.get("rep", "")
    flt = {"year": year} if year else {}
    if rep:
        flt["rep"] = rep
    o = CD.overview()
    show_years = sorted(ys)[-4:]
    yoy = CD.monthly_series(show_years, {"rep": rep} if rep else {})
    by_rep = CD.pivot("rep", "sales", filters=flt, limit=12)
    by_cat = CD.pivot("category", "sales", filters=flt, limit=14)
    accts = CD.pivot("account", "sales", filters=flt, limit=15)
    items = CD.top_items(year or None, rep or None, 15)
    kpi = CD.pivot("year", "sales", filters={k: v for k, v in flt.items()})
    docs = CD.pivot("year", "docs", filters=flt)
    buyers = CD.pivot("year", "accounts", filters=flt)
    return render_template("company_home.html", empty=False, o=o, years=ys, year=year, rep=rep, reps=CD.rep_names(),
                           yoy=yoy, months=CD.MONTHS, by_rep=by_rep, by_cat=by_cat, accts=accts, items=items,
                           kpi_total=kpi["total"], kpi_docs=docs["total"], kpi_buyers=buyers["total"],
                           acct_links=_links(accts["keys"]))


def _links(names):
    if not names:
        return []
    keys = {r["customer"]: r["cust_key"] for r in CD.q(
        "SELECT DISTINCT customer, cust_key FROM co_docs WHERE customer IN (%s)" % ",".join("?" * len(names)), names)}
    return [url_for("company.account", key=keys[n]) if keys.get(n) else "" for n in names]


@bp.route("/company/accounts")
@login_required
def accounts():
    term = request.args.get("q", "").strip()
    rep = request.args.get("rep", "")
    city = request.args.get("city", "")
    sort = request.args.get("sort", "total")
    rows = CD.accounts(term, rep, city, sort)
    return render_template("company_accounts.html", rows=rows, q=term, rep=rep, city=city, sort=sort,
                           reps=CD.rep_names(), cities=CD.cities())


@bp.route("/company/account/<path:key>")
@login_required
def account(key):
    ac = CD.profile(key)
    if not ac:
        abort(404)
    return render_template("company_account.html", ac=ac, key=key)


@bp.route("/company/account/<path:key>/add", methods=["POST"])
@login_required
def add_account(key):
    cid = CD.add_to_customers(key)
    if not cid:
        abort(404)
    flash("Linked to your Customers.", "ok")
    return redirect(url_for("customers.view", cid=cid))


@bp.route("/company/sales")
@login_required
def sales():
    a = request.args
    args = dict(term=a.get("q", "").strip(), rep=a.get("rep", ""), cust=a.get("account", "").strip(),
                item=a.get("item", "").strip(), frm=a.get("from", ""), to=a.get("to", ""), doc_type=a.get("type", ""))
    if a.get("format") == "csv":
        rows, _ = CD.lines(limit=500000, **args)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Date", "Type", "Num", "Account", "Rep", "Item #", "Item", "Memo", "Category", "Qty", "Price", "Amount"])
        for r in rows:
            w.writerow([r["doc_date"], r["doc_type"], r["num"], r["customer"], r["rep"], r["item_no"], r["item_desc"],
                        r["memo"], r["category"], r["qty"], r["price"], r["amount"]])
        return Response("﻿" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=Company sales lines.csv"})
    rows, tot = CD.lines(limit=1000, **args)
    return render_template("company_sales.html", rows=rows, tot=tot, a=a, reps=CD.rep_names())


@bp.route("/company/doc/<int:did>")
@login_required
def doc(did):
    d, lines = CD.doc(did)
    if not d:
        abort(404)
    return render_template("company_doc.html", d=d, lines=lines)


@bp.route("/company/explore")
@login_required
def explore():
    return render_template("company_explore.html", dims=CD.DIMS, measures=CD.MEASURES, years=CD.years(),
                           reps=CD.rep_names(), categories=S.CATEGORIES, cities=CD.cities(), a=request.args)


@bp.route("/company/api/pivot")
@login_required
def api_pivot():
    a = request.args
    p = CD.pivot(a.get("dim", "month"), a.get("measure", "sales"), a.get("split", ""), _f(),
                 int(a.get("limit") or 25))
    if p["dim"] == "account":
        p["links"] = _links(p["keys"])
    if a.get("format") == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([p["dim_label"]] + [s["name"] for s in p["series"]])
        for i, lab in enumerate(p["labels"]):
            w.writerow([lab] + [s["values"][i] for s in p["series"]])
        return Response("﻿" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=Company data.csv"})
    return jsonify(p)


@bp.route("/company/insights")
@login_required
def insights():
    rep = request.args.get("rep", "")
    ins = CD.insights(rep)
    return render_template("company_insights.html", ins=ins, rep=rep, reps=CD.rep_names())
