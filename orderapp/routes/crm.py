"""CRM: interactions log, follow-up tasks, deals pipeline, weekly report."""
import csv
import io
import os
import re
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
    return q("SELECT id, name, acct_type FROM customers ORDER BY name COLLATE NOCASE")


def _contact_options():
    rows = q("""SELECT k.id, k.name, k.role, k.customer_id FROM contacts k WHERE coalesce(k.name,'')<>''
                ORDER BY k.name""")
    return [dict(r) for r in rows]


def log_activity(customer_id, kind, when, contact_id=None, contact_name="", location="", subject="",
                 topics="", notes="", outcome="", temp=""):
    return x("""INSERT INTO activities(customer_id, contact_id, contact_name, kind, occurred_at, location, subject,
                topics, notes, outcome, owner, created, temp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (customer_id, contact_id, contact_name, kind, when, location, subject, topics, notes, outcome,
              session.get("username"), now(), temp or None))


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
    from ..accounts import ICON, PLURAL, counts
    return render_template("crm_home.html", acct_counts=counts(), acct_icons=ICON, acct_plural=PLURAL,
                           partners=q("""SELECT c.id, c.name, c.acct_type, c.services, c.city, c.phone,
                                         (SELECT MAX(a.occurred_at) FROM activities a WHERE a.customer_id=c.id) last
                                         FROM customers c WHERE coalesce(c.acct_type,'Property')<>'Property'
                                         ORDER BY last IS NULL, last DESC, c.name COLLATE NOCASE LIMIT 12"""),
                           tasks=tasks, recent=recent, pipe=pipe, stages=DEAL_STAGES,
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
        from ..people import find_person, save_person
        if f.get("save_contact") or f.get("contact_role"):
            contact_id = save_person(cid, contact_name, f.get("contact_role", ""), f.get("contact_phone", ""),
                                     f.get("contact_email", ""))
        else:
            k = find_person(cid, contact_name)
            contact_id = k["id"] if k else None
    location = f.get("location", "").strip()
    if not location and cid and kind in ("In person", "Site visit", "Drop-in", "Meeting"):
        c = q("SELECT address, city FROM customers WHERE id=?", (cid,), one=True)
        location = ", ".join(v for v in [c["address"], c["city"]] if v) if c else ""
    topics = ", ".join(f.getlist("topics"))
    temp = f.get("temp") if f.get("temp") in ("Warm", "Cold") else ""
    aid = log_activity(cid, kind, when, contact_id, contact_name, location, f.get("subject", "").strip(), topics,
                       f.get("notes", "").strip(), f.get("outcome", "").strip(), temp)
    # logging a visit at a property that's on today's route checks that stop off
    if cid and kind in ("In person", "Site visit", "Drop-in", "Meeting"):
        st = q("""SELECT * FROM route_stops WHERE customer_id=? AND day=? AND status='Planned'
                  ORDER BY position LIMIT 1""", (cid, date.today().isoformat()), one=True)
        if st:
            from .routes_plan import mark_done, plan_day
            mark_done(st, activity_id=aid)
            try:
                plan_day(st["day"])
            except Exception:  # noqa: BLE001 - map lookups are best-effort here
                pass
    if cid and f.get("status") in CUSTOMER_STATUSES:
        x("UPDATE customers SET status=?, updated=? WHERE id=?", (f["status"], now(), cid))
    msgs = ["Logged."]
    if f.get("follow_up"):
        x("INSERT INTO tasks(customer_id, activity_id, title, due_date, owner, created) VALUES (?,?,?,?,?,?)",
          (cid, aid, f.get("follow_up_title", "").strip() or f.get("subject", "").strip()
           or (f.get("notes", "").strip()[:60] and "Follow up: " + f.get("notes", "").strip()[:60]) or f"Follow up ({kind.lower()})", f["follow_up"],
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


# ---------------------------------------------------------------- contact log
@bp.route("/crm/contacts")
@login_required
def contacts_log():
    """Rolling contact log - every property / person you were in touch with, newest first."""
    from .. import contact_log as CL
    added = CL.sync_orders()
    from ..people import POSITIONS, sync_people
    sync_people()
    a = request.args
    frm = a.get("from") or (date.today() - timedelta(days=30)).isoformat()
    to = a.get("to") or date.today().isoformat()
    rows = CL.entries(frm, to, a.get("q", "").strip(), a.get("how", ""), a.get("temp", ""))
    if a.get("csv"):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "Property", "Contact person", "Position", "How", "Warm/Cold", "What", "Notes", "Order #"])
        for r in rows:
            w.writerow([r["occurred_at"][:10], r["cname"] or "", r["contact_name"] or "", r["role"] or "", r["kind"], r["temp"] or "",
                        r["subject"] or "", (r["notes"] or "").replace("\n", " "), r["order_id"] or ""])
        from flask import Response
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=Contact log {frm} to {to}.csv"})
    return render_template("crm_contacts.html", rows=rows, frm=frm, to=to, how=CL.HOW, temps=CL.TEMPS,
                           kinds=ACTIVITY_KINDS, args=a, added=added, week=CL.week_counts(), positions=POSITIONS,
                           customers=_customers(), now_local=datetime.now().strftime("%Y-%m-%dT%H:%M"))


@bp.route("/crm/contacts/<int:aid>", methods=["POST"])
@login_required
def contact_edit(aid):
    """Inline edit from the contact log (how, warm/cold, who, date). Answers JSON for the auto-save."""
    from flask import jsonify
    r = q("SELECT * FROM activities WHERE id=?", (aid,), one=True) or abort(404)
    f = request.form
    kind = f.get("kind", r["kind"])
    if kind not in ACTIVITY_KINDS:
        kind = r["kind"]
    temp = f.get("temp", r["temp"] or "")
    temp = temp if temp in ("Warm", "Cold") else None
    when = (f.get("date") or r["occurred_at"][:10]) + r["occurred_at"][10:]
    name = f.get("contact_name", r["contact_name"] or "").strip()
    kid = r["contact_id"]
    if name and r["customer_id"]:      # the person (and their position) goes on the property's profile
        from ..people import save_person
        kid = save_person(r["customer_id"], name, f.get("role", ""))
    elif not name:
        kid = None
    x("UPDATE activities SET kind=?, temp=?, contact_name=?, contact_id=?, occurred_at=? WHERE id=?",
      (kind, temp, name, kid, when, aid))
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    flash("Saved.", "ok")
    return redirect(request.referrer or url_for("crm.contacts_log"))


@bp.route("/crm/people/<int:cid>")
@login_required
def people_json(cid):
    """People saved at a property (for name suggestions + their position)."""
    from flask import jsonify
    from ..people import people_at
    return jsonify({"people": [{"name": r["name"], "role": r["role"] or "", "phone": r["phone"] or ""}
                               for r in people_at(cid) if r["name"]]})


@bp.route("/crm/contacts/add", methods=["POST"])
@login_required
def contact_add():
    """Add one or more people at a property in one go (one log entry per person)."""
    f = request.form
    cid = f.get("customer_id", type=int)
    if not cid:
        flash("Pick a property.", "error")
        return redirect(url_for("crm.contacts_log"))
    kind = f.get("kind") if f.get("kind") in ACTIVITY_KINDS else "In person"
    temp = f.get("temp") if f.get("temp") in ("Warm", "Cold") else ""
    when = (f.get("occurred_at") or datetime.now().strftime("%Y-%m-%dT%H:%M")).replace("T", " ")
    from ..people import save_person
    people = [(n.strip(), r, p) for n, r, p in zip(f.getlist("person_name"), f.getlist("person_role"),
                                                   f.getlist("person_phone") + [""] * 50) if n.strip()]
    people += [(n.strip(), "", "") for n in (f.get("people") or "").replace(";", ",").split(",") if n.strip()]
    for nm, role, phone in people or [("", "", "")]:
        kid = save_person(cid, nm, role, phone) if nm else None
        log_activity(cid, kind, when, kid, nm, subject=f.get("subject", "").strip(),
                     notes=f.get("notes", "").strip(), temp=temp)
    named = [p for p in people if p[0]]
    flash(f"Logged {max(1, len(people))} contact(s)" + (f" - {len(named)} saved to the property's contacts." if named
                                                       else "."), "ok")
    return redirect(url_for("crm.contacts_log"))


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
_NOISE = ("Added to route for", "Route stop", "On the way", "Estimating:")
# reasons the route planner / Along the way attach to a suggested stop - not for the manager's report
_AUTO_WHY = re.compile(r"(\$[\d,]+ last 12 mo|no order in \d+ days|last contact \d+ days ago|no contact logged|"
                       r"never contacted|\bprospect\b|^Near .+\([\d.]+ mi\))", re.I)


def _clean_note(v):
    """Drop auto-generated route reasons ("Near X (1.2 mi): $5,000 last 12 mo, last contact 40 days ago")."""
    v = (v or "").strip()
    if not v or v.startswith(_NOISE) or v == "Order" or v.startswith("Order #"):
        return ""
    if _AUTO_WHY.search(v):        # a whole auto-written reason line
        return ""
    return v


def _visit_rows(rows, orders):
    """One line per property per day: the visit, plus any calls / texts / notes / orders that day, folded
    together. Entries with no property keep their own line."""
    from ..contact_log import IN_PERSON
    from ..people import order_contact
    by_day_cust = {}
    for o in orders:
        if o["customer_id"] and o["status"] != "Cancelled":
            d = (o["created"] or "")[:10]
            by_day_cust.setdefault((o["customer_id"], d), []).append(o)
    groups, order = {}, []
    for r in rows:
        key = (r["customer_id"], r["occurred_at"][:10]) if r["customer_id"] else ("-", r["id"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    out, used_orders = [], set()
    for key in order:
        acts = groups[key]
        first = acts[0]
        visited = any(a["kind"] in IN_PERSON for a in acts)
        kinds = []
        for a in acts:
            k = "Visit" if a["kind"] in IN_PERSON else a["kind"]
            if k not in kinds:
                kinds.append(k)
        contacts = []
        for a in acts:
            n = (a["contact_name"] or "").strip()
            if n and n not in contacts:
                contacts.append(n)
        if not contacts and first["customer_id"] and visited:
            k = order_contact(first["customer_id"])
            if k:
                contacts.append(k["name"])
        notes = []
        for a in sorted(acts, key=lambda a: a["kind"] not in IN_PERSON):
            bits = []
            for v in (a["subject"], a["topics"], a["notes"], a["outcome"]):
                v = "; ".join(c for c in (_clean_note(ln) for ln in (v or "").split("\n")) if c)
                if v and v not in bits:
                    bits.append(v)
            if bits:
                txt = " - ".join(bits)
                if visited and a["kind"] not in IN_PERSON:
                    txt = f"{a['kind']}: {txt}"
                if txt not in notes:
                    notes.append(txt)
        ordered = ""
        what = []
        for a in acts:
            if (a["ordered"] or "") == "Yes":
                ordered = "Yes"
                if a["order_what"]:
                    what.append(a["order_what"])
            elif (a["ordered"] or "") == "No" and not ordered:
                ordered = "No"
        day_orders = list(by_day_cust.get(key, [])) if key[0] != "-" else []
        for a in acts:   # orders linked to an entry this day
            if a["order_id"]:
                o = next((o for o in orders if o["id"] == a["order_id"]), None)
                if o and o not in day_orders and o["status"] != "Cancelled":
                    day_orders.append(o)
        for o in day_orders:
            used_orders.add(o["id"])
            ordered = "Yes"
            t = (o["title"] or "Order").strip() + (f" (PO {o['po']})" if o["po"] else "")
            if t not in what:
                what.append(t)
        if visited:
            kinds.sort(key=lambda k: k != "Visit")
        lead = next((a for a in acts if a["kind"] in IN_PERSON), first)
        out.append({"key": f"{key[0]}|{key[1]}" if key[0] != "-" else f"a{key[1]}",
                    "day": first["occurred_at"][:10], "time": lead["occurred_at"][11:16], "cid": first["customer_id"],
                    "cname": first["cname"] or "", "city": first["city"] or "", "mgmt": first["mgmt"] or "",
                    "where": next((a["location"] for a in acts if a["location"]), "") or "",
                    "kinds": kinds, "visited": visited, "contacts": contacts, "ordered": ordered,
                    "what": "; ".join(what), "notes": notes, "n": len(acts)})
    return out, used_orders


def report_data(mon):
    sun = mon + timedelta(days=6)
    rows = q("""SELECT a.*, c.name AS cname, c.city, c.mgmt FROM activities a
                LEFT JOIN customers c ON c.id=a.customer_id
                WHERE substr(a.occurred_at,1,10) BETWEEN ? AND ? ORDER BY a.occurred_at, a.id""",
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
    visits, _ = _visit_rows(rows, orders)
    for v in visits:
        for n in v["contacts"]:
            people.add((n, v["cname"]))
    deals_moved = q("""SELECT d.*, c.name AS cname FROM deals d LEFT JOIN customers c ON c.id=d.customer_id
                       WHERE substr(d.updated,1,10) BETWEEN ? AND ? ORDER BY d.updated""",
                    (mon.isoformat(), sun.isoformat()))
    stops = q("""SELECT s.*, c.name AS cname FROM route_stops s LEFT JOIN customers c ON c.id=s.customer_id
                 WHERE s.day BETWEEN ? AND ? AND s.status='Done' ORDER BY s.day, s.position""",
              (mon.isoformat(), sun.isoformat()))
    open_tasks = q("""SELECT t.*, c.name AS cname FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
                      WHERE t.done=0 AND coalesce(t.due_date,'') <> '' AND t.due_date <= ? ORDER BY t.due_date""",
                   ((sun + timedelta(days=7)).isoformat(),))
    visits, edited = apply_edits(mon, visits)
    return {"mon": mon, "sun": sun, "rows": rows, "visits": visits, "by_kind": by_kind, "edited": edited,
            "people": sorted(people, key=lambda p: p[0]), "props": sorted(props), "orders": orders,
            "deals": deals_moved, "stops": stops, "tasks": open_tasks, "rep": setting("sales_rep"),
            "n_visits": sum(1 for v in visits if v["visited"]),
            "n_props": len({v["cname"] for v in visits if v["cname"]}),
            "n_ordered": sum(1 for v in visits if v["ordered"] == "Yes"),
            "n_remote": sum(n for k, n in by_kind.items() if k in ("Call", "Text", "Email", "Voicemail"))}


# ---- your edits to a week's report (names, notes, orders, extra lines) - kept per week, applied on top of
# what the CRM has, so new visits logged later still show up
EDIT_FIELDS = ("day", "cname", "contacts", "ordered", "what", "notes")


def _edit_key(mon):
    return f"report_edit:{mon.isoformat()}"


def load_edits(mon):
    from ..db import loads
    e = loads(setting(_edit_key(mon)) or "", {}) or {}
    return {"rows": e.get("rows", {}), "deleted": e.get("deleted", []), "added": e.get("added", [])}


def _blank(key):
    return {"key": key, "day": "", "time": "", "cid": None, "cname": "", "city": "", "mgmt": "", "where": "",
            "kinds": [], "visited": False, "contacts": [], "ordered": "", "what": "", "notes": [], "n": 0}


def apply_edits(mon, visits):
    e = load_edits(mon)
    if not (e["rows"] or e["deleted"] or e["added"]):
        return visits, False
    out = []
    for v in visits:
        if v["key"] in e["deleted"]:
            continue
        v = dict(v, contacts=list(v["contacts"]), notes=list(v["notes"]))
        v.update({k: val for k, val in e["rows"].get(v["key"], {}).items() if k in EDIT_FIELDS})
        v["edited"] = v["key"] in e["rows"]
        out.append(v)
    for a in e["added"]:
        v = _blank(a.get("key", "n"))
        v.update({k: val for k, val in a.items() if k in EDIT_FIELDS})
        v["visited"] = True
        v["kinds"] = ["Visit"]
        v["edited"] = True
        out.append(v)
    out.sort(key=lambda v: (v["day"] or "9999", v["time"] or ""))
    return out, True


def _row_from_form(f, key):
    g = lambda n: (f.get(f"{key}__{n}") or "").strip()
    return {"day": g("day"), "cname": g("cname"),
            "contacts": [c.strip() for c in g("contacts").split(",") if c.strip()],
            "ordered": g("ordered") if g("ordered") in ("Yes", "No") else "", "what": g("what"),
            "notes": [ln.strip() for ln in g("notes").splitlines() if ln.strip()]}


NAVY, BLUE, GREEN, LINE, SOFT = "#14213d", "#1f4fa3", "#2e7d32", "#d5dbe6", "#f3f6fb"


def report_html(dd):
    from markupsafe import escape as e
    out = [f"""<table class="band"><tr><td class="bandl"><div class="t">Weekly Activity Report</div>
           <div class="s">{e(dd['rep'] or '')}</div></td>
           <td class="bandr">{dd['mon']:%b %d} &ndash; {dd['sun']:%b %d, %Y}</td></tr></table>"""]
    tiles = [(dd["n_visits"], "Site visits"), (dd["n_props"], "Properties"), (dd["n_remote"], "Calls / texts / emails"),
             (len(dd["orders"]), "Orders")]
    out.append('<table class="tiles"><tr>' + "".join(
        f'<td class="tile"><div class="num">{n}</div><div class="lbl">{lbl}</div></td>' for n, lbl in tiles)
        + "</tr></table>")
    out.append('<h2>Visits &amp; contacts <span class="hint">one line per property per day</span></h2>')
    out.append('<table class="grid"><tr><th class="c-day">Day</th><th>Property</th><th>Contact</th>'
               '<th class="c-ord">Ordered</th><th>Notes</th></tr>')
    last_day = None
    for i, v in enumerate(dd["visits"]):
        try:
            dname = datetime.strptime(v["day"], "%Y-%m-%d").strftime("%a %m/%d")
        except ValueError:
            dname = v["day"]
        show_day = dname if v["day"] != last_day else ""
        last_day = v["day"]
        prop = f"<b>{e(v['cname'])}</b>" if v["cname"] else "<i>No property</i>"
        if v["ordered"] == "Yes":
            ordc = f'<span class="yes">YES</span>' + (f'<div class="sub">{e(v["what"])}</div>' if v["what"] else "")
        elif v["ordered"] == "No":
            ordc = '<span class="no">No</span>'
        else:
            ordc = '<span class="dash">&ndash;</span>'
        notes = "<br/>".join(e(n) for n in v["notes"])
        cls = "alt" if i % 2 else ""
        out.append(f'<tr class="{cls}"><td class="c-day">{show_day}</td>'
                   f'<td>{prop}</td><td>{"<br/>".join(e(c) for c in v["contacts"])}</td><td class="c-ord">{ordc}</td>'
                   f'<td class="notes">{notes}</td></tr>')
    if not dd["visits"]:
        out.append('<tr><td colspan="5" class="dash">Nothing logged this week.</td></tr>')
    out.append("</table>")
    if dd["orders"]:
        out.append('<h2>Orders</h2><table class="grid"><tr><th>Created</th><th>Property</th><th>Order</th>'
                   '<th>PO</th><th>Status</th></tr>' + "".join(
                       f'<tr class="{"alt" if i % 2 else ""}"><td>{e((o["created"] or "")[5:10].replace("-", "/"))}</td>'
                       f'<td>{e(o["cname"] or "")}</td><td>{e(o["title"] or "")}</td><td>{e(o["po"] or "")}</td>'
                       f'<td>{e(o["status"] or "")}</td></tr>' for i, o in enumerate(dd["orders"])) + "</table>")
    return "\n".join(out)


REPORT_CSS = f"""body{{font-family:sans-serif;font-size:8.5pt;color:#222}}
.band{{width:100%;border-collapse:collapse;margin-bottom:8pt}}
.band td{{background-color:{NAVY};color:#fff;padding:9pt 10pt;border:none}}
.band .t{{font-size:16pt;font-weight:bold}} .band .s{{font-size:10pt;color:#c9d6f0}}
.bandr{{text-align:right;font-size:11pt;font-weight:bold;vertical-align:middle}}
.tiles{{width:100%;border-collapse:separate;border-spacing:4pt 0;margin-bottom:6pt}}
.tile{{width:25%;background-color:{SOFT};border:0.6pt solid {LINE};text-align:center;padding:5pt 2pt}}
.num{{font-size:17pt;font-weight:bold;color:{BLUE}}} .lbl{{font-size:7pt;color:#556;text-transform:uppercase}}
h2{{font-size:11pt;color:{NAVY};margin:10pt 0 4pt;border-bottom:1.2pt solid {BLUE};padding-bottom:2pt}}
.hint{{font-size:7.5pt;color:#889;font-weight:normal}}
.grid{{border-collapse:collapse;width:100%}}
.grid th{{background-color:{BLUE};color:#fff;text-align:left;padding:3pt 4pt;font-size:8pt}}
.grid td{{border-bottom:0.5pt solid {LINE};padding:3pt 4pt;text-align:left;vertical-align:top}}
.grid tr.alt td{{background-color:{SOFT}}}
.c-day{{width:52pt;font-weight:bold;color:{NAVY}}} .c-ord{{width:90pt}}
.sub{{font-size:7.5pt;color:#667;font-weight:normal}} .kind{{font-size:7pt;color:{BLUE}}}
.yes{{color:{GREEN};font-weight:bold}} .no{{color:#888}} .dash{{color:#aab}}
.notes{{font-size:8pt}}"""


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _wrap(text, width, font="helv", size=8.5):
    """Lines of `text` that fit `width` points (built-in PDF fonts, so it measures the same on every PC)."""
    import pymupdf as fitz
    out = []
    for para in (text or "").split("\n"):
        line = ""
        for w in para.split(" "):
            t = (line + " " + w) if line else w
            if fitz.get_text_length(t, fontname=font, fontsize=size) <= width:
                line = t
                continue
            if line:
                out.append(line)
            while fitz.get_text_length(w, fontname=font, fontsize=size) > width:   # one very long word
                k = len(w)
                while k > 1 and fitz.get_text_length(w[:k], fontname=font, fontsize=size) > width:
                    k -= 1
                out.append(w[:k])
                w = w[k:]
            line = w
        out.append(line)
    return out


def _pdf_safe(t):
    return (t or "").replace("–", "-").replace("—", "-").replace("’", "'").replace("‘", "'") \
        .replace("“", '"').replace("”", '"').replace("•", "-")


def report_pdf(dd, path):
    """The weekly report, drawn line by line (every row's text and lines are placed together, so they always
    line up, on any page)."""
    import pymupdf as fitz
    doc = fitz.open()
    W, H = fitz.paper_rect("letter").br
    L, R, TOP, BOT = 32, W - 32, 34, H - 40
    navy, blue, green, line, soft, grey = (_rgb(NAVY), _rgb(BLUE), _rgb(GREEN), _rgb(LINE), _rgb(SOFT),
                                           (0.4, 0.42, 0.47))
    FS, LH, PAD = 8.5, 10.6, 4
    st = {"page": None, "y": 0}

    def new_page():
        st["page"] = doc.new_page(width=W, height=H)
        st["y"] = TOP

    def text(x, y, t, size=FS, font="helv", color=(0.13, 0.13, 0.13)):
        st["page"].insert_text((x, y), _pdf_safe(t), fontsize=size, fontname=font, color=color)

    def table(title, cols, rows, hint=""):
        """cols: [(header, width fraction)]; rows: [[(text, font, color), ...]]"""
        widths = [(R - L) * f for _, f in cols]
        xs = [L + sum(widths[:i]) for i in range(len(cols))]

        def head():
            pg = st["page"]
            pg.draw_rect(fitz.Rect(L, st["y"], R, st["y"] + 15), color=None, fill=blue)
            for (h, _), x in zip(cols, xs):
                text(x + PAD, st["y"] + 10.5, h, 8, "hebo", (1, 1, 1))
            st["y"] += 15

        if st["y"] + 60 > BOT:
            new_page()
        text(L, st["y"] + 12, title, 11.5, "hebo", navy)
        if hint:
            text(L + fitz.get_text_length(title, fontname="hebo", fontsize=11.5) + 6, st["y"] + 12, hint, 7.5,
                 "helv", grey)
        st["y"] += 16
        st["page"].draw_line((L, st["y"]), (R, st["y"]), color=blue, width=1.2)
        st["y"] += 3
        head()
        for i, row in enumerate(rows):
            full = row[0][3] if isinstance(row[0], tuple) and len(row[0]) == 4 else None   # day, for a new page
            if full is not None:
                row = [row[0][:3]] + list(row[1:])
            cells = []      # each cell: [(line, font, size, color)]
            for cell, w in zip(row, widths):
                segs = cell if isinstance(cell, list) else [cell]
                lines = []
                for val, font, color in segs:
                    size = 7.5 if font == "sub" else FS
                    fnt = "helv" if font == "sub" else font
                    for part in val if isinstance(val, list) else [val]:
                        lines += [(ln, fnt, size, color) for ln in _wrap(part, w - 2 * PAD, fnt, size)]
                cells.append(lines)
            hgt = max(len(c) for c in cells) * LH + 2 * PAD
            if st["y"] + hgt > BOT:
                new_page()
                head()
                if full:
                    cells[0] = [(ln, "hebo", FS, row[0][2]) for ln in _wrap(full, widths[0] - 2 * PAD, "hebo")]
            pg = st["page"]
            if i % 2:
                pg.draw_rect(fitz.Rect(L, st["y"], R, st["y"] + hgt), color=None, fill=soft)
            for lines, x in zip(cells, xs):
                for j, (ln, fnt, size, color) in enumerate(lines):
                    text(x + PAD, st["y"] + PAD + 8 + j * LH, ln, size, fnt, color)
            st["y"] += hgt
            pg.draw_line((L, st["y"]), (R, st["y"]), color=line, width=0.5)
        if not rows:
            text(L + PAD, st["y"] + 12, "Nothing logged this week.", FS, "helv", grey)
            st["y"] += 16
        st["y"] += 12

    new_page()
    pg = st["page"]
    # header band
    pg.draw_rect(fitz.Rect(L, TOP, R, TOP + 46), color=None, fill=navy)
    text(L + 12, TOP + 22, "Weekly Activity Report", 17, "hebo", (1, 1, 1))
    text(L + 12, TOP + 37, dd["rep"] or "", 10, "helv", (0.79, 0.84, 0.94))
    rng = f"{dd['mon']:%b %d} - {dd['sun']:%b %d, %Y}"
    text(R - 12 - fitz.get_text_length(rng, fontname="hebo", fontsize=11), TOP + 28, rng, 11, "hebo", (1, 1, 1))
    st["y"] = TOP + 56
    # summary tiles
    tiles = [(dd["n_visits"], "SITE VISITS"), (dd["n_props"], "PROPERTIES"), (dd["n_remote"], "CALLS / TEXTS / EMAILS"),
             (len(dd["orders"]), "ORDERS")]
    gap = 6
    tw = (R - L - gap * (len(tiles) - 1)) / len(tiles)
    for i, (n, lbl) in enumerate(tiles):
        x = L + i * (tw + gap)
        pg.draw_rect(fitz.Rect(x, st["y"], x + tw, st["y"] + 40), color=line, fill=soft, width=0.6)
        ns = str(n)
        text(x + (tw - fitz.get_text_length(ns, fontname="hebo", fontsize=17)) / 2, st["y"] + 21, ns, 17, "hebo", blue)
        text(x + (tw - fitz.get_text_length(lbl, fontname="helv", fontsize=6.8)) / 2, st["y"] + 33, lbl, 6.8, "helv", grey)
    st["y"] += 52
    # visits
    rows, last = [], None
    for v in dd["visits"]:
        try:
            dname = datetime.strptime(v["day"], "%Y-%m-%d").strftime("%a %m/%d")
        except ValueError:
            dname = v["day"] or ""
        show = dname if v["day"] != last else ""
        last = v["day"]
        dark = (0.13, 0.13, 0.13)
        if v["ordered"] == "Yes":
            ordc = [("YES", "hebo", green)] + ([(v["what"], "sub", grey)] if v["what"] else [])
        else:
            ordc = ("No" if v["ordered"] == "No" else "-", "helv", grey)
        rows.append([(show, "hebo", navy, dname), (v["cname"] or "(no property)", "hebo", dark),
                     (", ".join(v["contacts"]), "helv", dark), ordc, (v["notes"] or [""], "helv", dark)])
    table("Visits & contacts", [("Day", .10), ("Property", .22), ("Contact", .16), ("Ordered", .17), ("Notes", .35)],
          rows, "one line per property per day")
    if dd["orders"]:
        table("Orders", [("Created", .10), ("Property", .30), ("Order", .32), ("PO", .14), ("Status", .14)],
              [[((o["created"] or "")[5:10].replace("-", "/"), "helv", (0.13, 0.13, 0.13)),
                (o["cname"] or "", "helv", (0.13, 0.13, 0.13)), (o["title"] or "", "helv", (0.13, 0.13, 0.13)),
                (o["po"] or "", "helv", (0.13, 0.13, 0.13)), (o["status"] or "", "helv", (0.13, 0.13, 0.13))]
               for o in dd["orders"]])
    n = len(doc)
    for i, p in enumerate(doc):
        p.insert_text((L, H - 18), _pdf_safe(f"{dd['rep'] or ''} - week of {dd['mon']:%m/%d/%Y}"), fontsize=7,
                      color=(0.45, 0.48, 0.55))
        p.insert_text((R - 46, H - 18), f"Page {i + 1} of {n}", fontsize=7, color=(0.45, 0.48, 0.55))
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def report_csv(dd):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Property", "Contact", "Visited", "Ordered", "What was ordered", "Notes"])
    for v in dd["visits"]:
        w.writerow([v["day"], v["cname"], "; ".join(v["contacts"]), "Yes" if v["visited"] else "No",
                    v["ordered"], v["what"], " | ".join(v["notes"])])
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
                           report_css=REPORT_CSS.replace("body{", ".report{"),
                           prev=(mon - timedelta(days=7)).isoformat(), nxt=(mon + timedelta(days=7)).isoformat(),
                           report_to=setting("report_to"), report_cc=setting("report_cc"),
                           provider=setting("email_provider"))


@bp.route("/crm/report/edit", methods=["POST"])
@login_required
def report_edit():
    import json
    from ..db import set_setting
    mon = _report_week()
    f = request.form
    if f.get("reset"):
        set_setting(_edit_key(mon), "")
        flash("Report reset to what's in the CRM.", "ok")
        return redirect(url_for("crm.report", week=mon.isoformat()))
    e = load_edits(mon)
    # what the CRM alone would give, to keep only the lines you actually changed
    raw = {v["key"]: v for v in _visit_rows(
        q("""SELECT a.*, c.name AS cname, c.city, c.mgmt FROM activities a LEFT JOIN customers c ON c.id=a.customer_id
             WHERE substr(a.occurred_at,1,10) BETWEEN ? AND ? ORDER BY a.occurred_at, a.id""",
          (mon.isoformat(), (mon + timedelta(days=6)).isoformat())),
        q("SELECT o.* FROM orders o WHERE substr(o.created,1,10) BETWEEN ? AND ?",
          (mon.isoformat(), (mon + timedelta(days=6)).isoformat())))[0]}
    rows, deleted, added = dict(e["rows"]), list(e["deleted"]), []
    for key in f.getlist("keys"):
        if f.get(f"{key}__del"):
            if key in raw and key not in deleted:
                deleted.append(key)
            rows.pop(key, None)
            continue
        new = _row_from_form(f, key)
        if key in raw:
            base = raw[key]
            diff = {k: v for k, v in new.items() if v != base.get(k)}
            if diff:
                rows[key] = diff
            else:
                rows.pop(key, None)
        elif any(new[k] for k in ("cname", "contacts", "notes", "what")):
            added.append(dict(new, key=key if key.startswith("n") else f"n{len(added)}"))
    for i, a in enumerate(added):
        a["key"] = f"n{i}"
    set_setting(_edit_key(mon), json.dumps({"rows": rows, "deleted": deleted, "added": added}))
    flash("Report changes saved. View PDF / email use them. (The CRM itself isn't changed.)", "ok")
    return redirect(url_for("crm.report", week=mon.isoformat()))


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
    with open(path, "rb") as fh:       # serve a copy so the saved file isn't held open (Windows locks it)
        data = fh.read()
    return send_file(io.BytesIO(data), mimetype="application/pdf", download_name=name + ".pdf")


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
    body = (f"Hi,\n\nAttached is my activity report for {mon:%m/%d} - {dd['sun']:%m/%d}: {dd['n_visits']} site visits, "
            f"{len(dd['rows'])} interactions in all ({summ}) with {len(dd['people'])} people at {len(dd['props'])} properties, "
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
