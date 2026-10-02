"""CRM: interactions log, follow-up tasks, deals pipeline, weekly report."""
import csv
import io
import os
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for

from ..db import (ACTIVITY_KINDS, CUSTOMER_STATUSES, DEAL_STAGES, TOPICS, now, q, setting, x)
from ..emailer import MailError, provider
from ..paths import ensure_dir
from ..security import flash_link, login_required

bp = Blueprint("crm", __name__)


def _d(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def week_bounds(any_day=None):
    d = any_day or date.today()
    mon = d - timedelta(days=d.weekday())
    return mon, mon + timedelta(days=6)


def _customers():
    return q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")


def _contact_options():
    rows = q("""SELECT k.id, k.name, k.role, k.customer_id FROM contacts k WHERE coalesce(k.name,'')<>''
                ORDER BY k.name""")
    return [dict(r) for r in rows]


def log_activity(customer_id, kind, when, contact_id=None, contact_name="", location="", subject="",
                 topics="", notes="", outcome=""):
    return x("""INSERT INTO activities(customer_id, contact_id, contact_name, kind, occurred_at, location, subject,
                topics, notes, outcome, owner, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
             (customer_id, contact_id, contact_name, kind, when, location, subject, topics, notes, outcome,
              session.get("username"), now()))


# ---------------------------------------------------------------- dashboard
@bp.route("/crm")
@login_required
def home():
    today = date.today().isoformat()
    mon, sun = week_bounds()
    tasks = q("""SELECT t.*, c.name AS cname FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
                 WHERE t.done=0 AND coalesce(t.due_date,'') <= ? ORDER BY t.due_date""",
              ((date.today() + timedelta(days=7)).isoformat(),))
    recent = q("""SELECT a.*, c.name AS cname FROM activities a LEFT JOIN customers c ON c.id=a.customer_id
                  ORDER BY a.occurred_at DESC LIMIT 15""")
    pipe = {r["stage"]: r for r in q("""SELECT stage, COUNT(*) n, coalesce(SUM(value),0) v FROM deals
                                         GROUP BY stage""")}
    week = q("""SELECT kind, COUNT(*) n FROM activities WHERE substr(occurred_at,1,10) BETWEEN ? AND ?
                GROUP BY kind ORDER BY n DESC""", (mon.isoformat(), sun.isoformat()))
    stale = q("""SELECT c.id, c.name, c.status, MAX(a.occurred_at) AS last FROM customers c
                 LEFT JOIN activities a ON a.customer_id=c.id
                 WHERE coalesce(c.status,'') IN ('Active','Prospect')
                 GROUP BY c.id HAVING last IS NULL OR last < ? ORDER BY last LIMIT 12""",
              ((date.today() - timedelta(days=30)).isoformat(),))
    return render_template("crm_home.html", tasks=tasks, recent=recent, pipe=pipe, stages=DEAL_STAGES,
                           week=week, stale=stale, today=today, customers=_customers(),
                           contacts=_contact_options(), kinds=ACTIVITY_KINDS, topics=TOPICS,
                           statuses=CUSTOMER_STATUSES, pre_customer=request.args.get("customer", type=int),
                           now_local=datetime.now().strftime("%Y-%m-%dT%H:%M"))


@bp.route("/crm/log", methods=["POST"])
@login_required
def log():
    f = request.form
    cid = f.get("customer_id", type=int)
    kind = f.get("kind") or "Call"
    when = (f.get("occurred_at") or datetime.now().strftime("%Y-%m-%dT%H:%M")).replace("T", " ")
    contact_name = f.get("contact_name", "").strip()
    contact_id = None
    if cid and contact_name:
        k = q("SELECT id FROM contacts WHERE customer_id=? AND lower(name)=lower(?)", (cid, contact_name), one=True)
        if k:
            contact_id = k["id"]
        elif f.get("save_contact"):
            contact_id = x("INSERT INTO contacts(customer_id, name, role, phone, email) VALUES (?,?,?,?,?)",
                           (cid, contact_name, f.get("contact_role", ""), f.get("contact_phone", ""),
                            f.get("contact_email", "")))
    location = f.get("location", "").strip()
    if not location and cid and kind in ("Site visit", "Drop-in", "Meeting"):
        c = q("SELECT address, city FROM customers WHERE id=?", (cid,), one=True)
        location = ", ".join(v for v in [c["address"], c["city"]] if v) if c else ""
    topics = ", ".join(f.getlist("topics"))
    aid = log_activity(cid, kind, when, contact_id, contact_name, location, f.get("subject", "").strip(), topics,
                       f.get("notes", "").strip(), f.get("outcome", "").strip())
    if cid and f.get("status") in CUSTOMER_STATUSES:
        x("UPDATE customers SET status=?, updated=? WHERE id=?", (f["status"], now(), cid))
    msgs = ["Logged."]
    if f.get("follow_up"):
        x("INSERT INTO tasks(customer_id, activity_id, title, due_date, owner, created) VALUES (?,?,?,?,?,?)",
          (cid, aid, f.get("follow_up_title", "").strip() or f"Follow up ({kind.lower()})", f["follow_up"],
           session.get("username"), now()))
        msgs.append(f"Follow-up set for {f['follow_up']}.")
    if f.get("route_day") and cid:
        from .routes_plan import add_stop
        add_stop(f["route_day"], customer_id=cid, purpose=f.get("subject", "") or f"{kind} follow-up",
                 source=kind)
        flash_link(" ".join(msgs) + f" Added to your route for {f['route_day']}.",
                   url_for("plan.day", day=f["route_day"]), "Open route", "ok")
    else:
        flash(" ".join(msgs), "ok")
    nxt = f.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("crm.home"))


# ---------------------------------------------------------------- activities
@bp.route("/crm/activities")
@login_required
def activities():
    a = request.args
    frm = a.get("from") or (date.today() - timedelta(days=30)).isoformat()
    to = a.get("to") or date.today().isoformat()
    sql = """SELECT a.*, c.name AS cname FROM activities a LEFT JOIN customers c ON c.id=a.customer_id
             WHERE substr(a.occurred_at,1,10) BETWEEN ? AND ?"""
    args = [frm, to]
    if a.get("kind"):
        sql += " AND a.kind=?"
        args.append(a["kind"])
    if a.get("q"):
        like = f"%{a['q']}%"
        sql += " AND (c.name LIKE ? OR a.contact_name LIKE ? OR a.notes LIKE ? OR a.subject LIKE ? OR a.topics LIKE ?)"
        args += [like] * 5
    sql += " ORDER BY a.occurred_at DESC LIMIT 500"
    return render_template("crm_activities.html", rows=q(sql, args), frm=frm, to=to, kinds=ACTIVITY_KINDS,
                           kind=a.get("kind", ""), s=a.get("q", ""))


@bp.route("/crm/activity/<int:aid>", methods=["GET", "POST"])
@login_required
def activity(aid):
    r = q("SELECT a.*, c.name AS cname FROM activities a LEFT JOIN customers c ON c.id=a.customer_id WHERE a.id=?",
          (aid,), one=True) or abort(404)
    if request.method == "POST":
        if request.form.get("delete"):
            x("DELETE FROM activities WHERE id=?", (aid,))
            flash("Deleted.", "ok")
            return redirect(url_for("crm.activities"))
        f = request.form
        x("""UPDATE activities SET kind=?, occurred_at=?, contact_name=?, location=?, subject=?, topics=?, notes=?,
             outcome=? WHERE id=?""",
          (f.get("kind"), f.get("occurred_at", "").replace("T", " "), f.get("contact_name"), f.get("location"),
           f.get("subject"), ", ".join(f.getlist("topics")), f.get("notes"), f.get("outcome"), aid))
        flash("Saved.", "ok")
        return redirect(url_for("crm.activity", aid=aid))
    return render_template("crm_activity.html", r=r, kinds=ACTIVITY_KINDS, topics=TOPICS,
                           sel_topics=[t.strip() for t in (r["topics"] or "").split(",") if t.strip()])


# ---------------------------------------------------------------- tasks
@bp.route("/crm/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    if request.method == "POST":
        t = request.form.get("title", "").strip()
        if t:
            x("INSERT INTO tasks(customer_id, title, due_date, owner, created) VALUES (?,?,?,?,?)",
              (request.form.get("customer_id", type=int), t, request.form.get("due_date") or None,
               session.get("username"), now()))
            flash("Task added.", "ok")
        nxt = request.form.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("crm.tasks"))
    show = request.args.get("show", "open")
    rows = q(f"""SELECT t.*, c.name AS cname FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
                 WHERE {'t.done=0' if show == 'open' else '1=1'}
                 ORDER BY t.done, CASE WHEN t.due_date IS NULL THEN 1 ELSE 0 END, t.due_date LIMIT 500""")
    return render_template("crm_tasks.html", rows=rows, show=show, customers=_customers(),
                           today=date.today().isoformat())


@bp.route("/crm/task/<int:tid>/toggle", methods=["POST"])
@login_required
def task_toggle(tid):
    t = q("SELECT * FROM tasks WHERE id=?", (tid,), one=True) or abort(404)
    x("UPDATE tasks SET done=?, done_at=? WHERE id=?", (0 if t["done"] else 1, None if t["done"] else now(), tid))
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("crm.tasks"))


@bp.route("/crm/task/<int:tid>/delete", methods=["POST"])
@login_required
def task_delete(tid):
    x("DELETE FROM tasks WHERE id=?", (tid,))
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("crm.tasks"))


# ---------------------------------------------------------------- pipeline
@bp.route("/crm/pipeline")
@login_required
def pipeline():
    deals = q("""SELECT d.*, c.name AS cname FROM deals d LEFT JOIN customers c ON c.id=d.customer_id
                 ORDER BY d.updated DESC""")
    cols = {s: [] for s in DEAL_STAGES}
    for d in deals:
        cols.setdefault(d["stage"] or "Lead", []).append(d)
    totals = {s: sum((d["value"] or 0) for d in v) for s, v in cols.items()}
    return render_template("crm_pipeline.html", cols=cols, totals=totals, stages=DEAL_STAGES,
                           customers=_customers(), topics=TOPICS)


@bp.route("/crm/deal", methods=["POST"])
@bp.route("/crm/deal/<int:did>", methods=["GET", "POST"])
@login_required
def deal(did=None):
    d = q("SELECT d.*, c.name AS cname FROM deals d LEFT JOIN customers c ON c.id=d.customer_id WHERE d.id=?",
          (did,), one=True) if did else None
    if did and not d:
        abort(404)
    if request.method == "POST":
        f = request.form
        if did and f.get("delete"):
            x("DELETE FROM deals WHERE id=?", (did,))
            flash("Deal deleted.", "ok")
            return redirect(url_for("crm.pipeline"))
        if did and f.get("stage_only"):
            old = d["stage"]
            x("UPDATE deals SET stage=?, updated=? WHERE id=?", (f["stage"], now(), did))
            if old != f["stage"] and d["customer_id"]:
                log_activity(d["customer_id"], "Other", datetime.now().strftime("%Y-%m-%d %H:%M"),
                             subject=f"Deal '{d['name']}': {old} -> {f['stage']}")
            nxt = f.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("crm.pipeline"))
        try:
            val = float(str(f.get("value", "")).replace("$", "").replace(",", "") or 0)
        except ValueError:
            val = 0
        vals = (f.get("customer_id", type=int), f.get("name", "").strip() or "New deal", f.get("product", ""),
                val, f.get("stage") or "Lead", f.get("close_date") or None, f.get("notes", ""), now())
        if did:
            x("""UPDATE deals SET customer_id=?, name=?, product=?, value=?, stage=?, close_date=?, notes=?, updated=?
                 WHERE id=?""", vals + (did,))
        else:
            x("""INSERT INTO deals(customer_id, name, product, value, stage, close_date, notes, updated, owner,
                 created) VALUES (?,?,?,?,?,?,?,?,?,?)""", vals + (session.get("username"), now()))
        flash("Deal saved.", "ok")
        nxt = f.get("next") or ""
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("crm.pipeline"))
    return render_template("crm_deal.html", d=d, stages=DEAL_STAGES, customers=_customers(), topics=TOPICS)


@bp.route("/customers/<int:cid>/status", methods=["POST"])
@login_required
def set_customer_status(cid):
    st = request.form.get("status")
    if st in CUSTOMER_STATUSES or st == "":
        x("UPDATE customers SET status=?, updated=? WHERE id=?", (st or None, now(), cid))
    return redirect(url_for("customers.view", cid=cid, tab="crm"))


# ---------------------------------------------------------------- weekly report
def report_data(mon):
    sun = mon + timedelta(days=6)
    rows = q("""SELECT a.*, c.name AS cname, c.city, c.mgmt FROM activities a
                LEFT JOIN customers c ON c.id=a.customer_id
                WHERE substr(a.occurred_at,1,10) BETWEEN ? AND ? ORDER BY a.occurred_at""",
             (mon.isoformat(), sun.isoformat()))
    by_kind, people, props = {}, set(), set()
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        if r["contact_name"]:
            people.add((r["contact_name"], r["cname"]))
        if r["cname"]:
            props.add(r["cname"])
    orders = q("""SELECT o.*, c.name AS cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                  WHERE substr(o.created,1,10) BETWEEN ? AND ? ORDER BY o.created""", (mon.isoformat(), sun.isoformat()))
    deals_moved = q("""SELECT d.*, c.name AS cname FROM deals d LEFT JOIN customers c ON c.id=d.customer_id
                       WHERE substr(d.updated,1,10) BETWEEN ? AND ? ORDER BY d.updated""",
                    (mon.isoformat(), sun.isoformat()))
    stops = q("""SELECT s.*, c.name AS cname FROM route_stops s LEFT JOIN customers c ON c.id=s.customer_id
                 WHERE s.day BETWEEN ? AND ? AND s.status='Done' ORDER BY s.day, s.position""",
              (mon.isoformat(), sun.isoformat()))
    open_tasks = q("""SELECT t.*, c.name AS cname FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
                      WHERE t.done=0 AND coalesce(t.due_date,'') <> '' AND t.due_date <= ? ORDER BY t.due_date""",
                   ((sun + timedelta(days=7)).isoformat(),))
    return {"mon": mon, "sun": sun, "rows": rows, "by_kind": by_kind, "people": sorted(people, key=lambda p: p[0]),
            "props": sorted(props), "orders": orders, "deals": deals_moved, "stops": stops, "tasks": open_tasks,
            "rep": setting("sales_rep")}


def report_html(dd):
    from markupsafe import escape as e
    out = [f"<h1>Weekly Activity Report</h1><p><b>{e(dd['rep'])}</b> &middot; "
           f"{dd['mon']:%a %m/%d/%Y} - {dd['sun']:%a %m/%d/%Y}</p>"]
    summ = ", ".join(f"{k}: {v}" for k, v in sorted(dd["by_kind"].items(), key=lambda kv: -kv[1])) or "none"
    out.append(f"<h2>Summary</h2><p>{len(dd['rows'])} interactions ({e(summ)}) with {len(dd['people'])} people "
               f"at {len(dd['props'])} properties. {len(dd['stops'])} route stops completed. "
               f"{len(dd['orders'])} orders created. {len(dd['deals'])} deals updated.</p>")
    out.append("<h2>Interactions</h2><table><tr><th>When</th><th>Type</th><th>Property</th><th>Contact</th>"
               "<th>Topic / notes</th></tr>")
    for r in dd["rows"]:
        note = " - ".join(x for x in [r["subject"] or "", r["topics"] or "", r["notes"] or "", r["outcome"] or ""] if x)
        out.append(f"<tr><td>{e(r['occurred_at'][5:16])}</td><td>{e(r['kind'])}</td><td>{e(r['cname'] or '')}</td>"
                   f"<td>{e(r['contact_name'] or '')}</td><td>{e(note)}</td></tr>")
    out.append("</table>")
    if dd["orders"]:
        out.append("<h2>Orders</h2><ul>" + "".join(
            f"<li>{e(o['cname'] or '')} - {e(o['title'] or '')} {('PO ' + e(o['po'])) if o['po'] else ''} ({e(o['status'])})</li>"
            for o in dd["orders"]) + "</ul>")
    if dd["deals"]:
        out.append("<h2>Pipeline</h2><ul>" + "".join(
            f"<li>{e(d['cname'] or '')} - {e(d['name'])}: {e(d['stage'])}"
            f"{(' - $' + format(d['value'], ',.0f')) if d['value'] else ''}</li>" for d in dd["deals"]) + "</ul>")
    if dd["tasks"]:
        out.append("<h2>Upcoming follow-ups</h2><ul>" + "".join(
            f"<li>{e(t['due_date'])} - {e(t['cname'] or '')}: {e(t['title'])}</li>" for t in dd["tasks"]) + "</ul>")
    return "\n".join(out)


def report_pdf(dd, path):
    import pymupdf as fitz
    css = """body{font-family:sans-serif;font-size:9pt;color:#222} h1{font-size:16pt;color:#14213d;margin:0}
             h2{font-size:11pt;color:#1f4fa3;margin:10pt 0 4pt} table{border-collapse:collapse;width:100%}
             th,td{border:0.5pt solid #ccd;padding:2pt 3pt;text-align:left;vertical-align:top} th{background:#eef2f8}"""
    story = fitz.Story(html=report_html(dd), user_css=css)
    writer = fitz.DocumentWriter(path)
    mediabox = fitz.paper_rect("letter")
    where = mediabox + (36, 36, -36, -36)
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return path


def report_csv(dd):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date/time", "Type", "Property", "City", "Mgmt Co", "Contact", "Where", "Subject", "Topics", "Notes",
                "Outcome"])
    for r in dd["rows"]:
        w.writerow([r["occurred_at"], r["kind"], r["cname"] or "", r["city"] or "", r["mgmt"] or "",
                    r["contact_name"] or "", r["location"] or "", r["subject"] or "", r["topics"] or "",
                    r["notes"] or "", r["outcome"] or ""])
    return out.getvalue().encode("utf-8-sig")


def _report_week():
    d = _d(request.values.get("week")) or (date.today() - timedelta(days=7 if date.today().weekday() < 2 else 0))
    return week_bounds(d)[0]


@bp.route("/crm/report")
@login_required
def report():
    mon = _report_week()
    dd = report_data(mon)
    return render_template("crm_report.html", dd=dd, html=report_html(dd), mon=mon,
                           prev=(mon - timedelta(days=7)).isoformat(), nxt=(mon + timedelta(days=7)).isoformat(),
                           report_to=setting("report_to"), report_cc=setting("report_cc"),
                           provider=setting("email_provider"))


@bp.route("/crm/report/file/<kind>")
@login_required
def report_file(kind):
    mon = _report_week()
    dd = report_data(mon)
    name = f"Weekly report {mon:%Y-%m-%d} - {setting('sales_rep')}"
    if kind == "csv":
        return send_file(io.BytesIO(report_csv(dd)), mimetype="text/csv", as_attachment=True, download_name=name + ".csv")
    from ..routes.orders import output_dir
    path = report_pdf(dd, os.path.join(ensure_dir(os.path.join(output_dir(), "Weekly reports")), name + ".pdf"))
    return send_file(path, mimetype="application/pdf", download_name=name + ".pdf")


@bp.route("/crm/report/send", methods=["POST"])
@login_required
def report_send():
    from ..routes.orders import output_dir
    mon = _report_week()
    dd = report_data(mon)
    to = request.form.get("to", "").strip()
    if not to:
        flash("Enter your manager's email (it's remembered for next week).", "error")
        return redirect(url_for("crm.report", week=mon.isoformat()))
    from ..db import set_setting
    set_setting("report_to", to)
    set_setting("report_cc", request.form.get("cc", "").strip())
    folder = ensure_dir(os.path.join(output_dir(), "Weekly reports"))
    name = f"Weekly report {mon:%Y-%m-%d} - {setting('sales_rep')}"
    pdf = report_pdf(dd, os.path.join(folder, name + ".pdf"))
    csvp = os.path.join(folder, name + ".csv")
    with open(csvp, "wb") as fh:
        fh.write(report_csv(dd))
    subject = f"Weekly activity report - {dd['rep']} - week of {mon:%m/%d/%Y}"
    summ = ", ".join(f"{v} {k.lower()}{'s' if v != 1 else ''}" for k, v in dd["by_kind"].items()) or "no logged activity"
    body = (f"Hi,\n\nAttached is my activity report for {mon:%m/%d} - {dd['sun']:%m/%d}: {len(dd['rows'])} interactions "
            f"({summ}) with {len(dd['people'])} people at {len(dd['props'])} properties, "
            f"{len(dd['orders'])} orders.\n\nThanks,\n{dd['rep']}")
    review = request.form.get("mode", setting("send_mode")) != "send"
    prov = provider(setting)
    try:
        kw = {"out_dir": folder} if prov.name == "eml" else {}
        msg = prov.send(to, request.form.get("cc", "").strip(), subject, body, [pdf, csvp], review=review, **kw)
    except (MailError, Exception) as e:  # noqa: BLE001
        flash(f"Couldn't email the report: {e}. The files are saved in {folder}.", "error")
        return redirect(url_for("crm.report", week=mon.isoformat()))
    flash(msg, "ok")
    return redirect(url_for("crm.report", week=mon.isoformat()))
