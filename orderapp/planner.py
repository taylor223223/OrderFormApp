"""Plan my week / month: what's coming up, who to see and why, and where the month is heading.

- glance(start, days): every day's route stops, follow-ups, orders due, deals and quotes closing
- recommendations(days): accounts worth a visit or call, with the reason and a suggested day (grouped by city)
- projection(): this month's sales so far, the run-rate finish, and a plan scenario you can edit
  (expected $ on each planned stop + open quotes x win rate) against the monthly goal
"""
import calendar
from collections import OrderedDict
from datetime import date, timedelta

from .db import q, setting

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
OPEN_ORDER = ("Ready", "Sent", "Confirmed", "Backordered", "Shipped", "On Hold")


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _iso_order_date(s):
    from .routes.orders import _iso
    return _iso(s)


# ------------------------------------------------------------------ at a glance
def glance(start, days):
    """[{date, iso, name, stops, tasks, orders_due, deals, quotes}] for each day from start."""
    end = start + timedelta(days=days - 1)
    a, b = start.isoformat(), end.isoformat()
    out = OrderedDict()
    for i in range(days):
        dd = start + timedelta(days=i)
        out[dd.isoformat()] = {"date": dd, "iso": dd.isoformat(), "name": WEEKDAYS[dd.weekday()], "stops": [],
                               "tasks": [], "orders_due": [], "deals": [], "quotes": [], "expected": 0.0}
    for s in q("""SELECT s.*, c.name cname, c.city FROM route_stops s LEFT JOIN customers c ON c.id=s.customer_id
                  WHERE s.day BETWEEN ? AND ? ORDER BY s.day, s.position""", (a, b)):
        out[s["day"]]["stops"].append(dict(s))
        out[s["day"]]["expected"] += s["expected"] or 0
    for t in q("""SELECT t.*, c.name cname FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
                  WHERE t.done=0 AND t.due_date BETWEEN ? AND ?""", (a, b)):
        if t["due_date"][:10] in out:
            out[t["due_date"][:10]]["tasks"].append(dict(t))
    for o in q(f"""SELECT o.*, c.name cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                   WHERE o.status IN ({','.join('?' * len(OPEN_ORDER))}) AND coalesce(o.due_date,'')<>''""",
               OPEN_ORDER):
        dd = _iso_order_date(o["due_date"])
        if dd in out:
            out[dd]["orders_due"].append(dict(o))
    for dl in q("""SELECT d.*, c.name cname FROM deals d LEFT JOIN customers c ON c.id=d.customer_id
                   WHERE d.stage NOT IN ('Won','Lost') AND d.close_date BETWEEN ? AND ?""", (a, b)):
        out[dl["close_date"][:10]]["deals"].append(dict(dl))
    for qu in q("""SELECT qq.*, c.name cname FROM quotes qq LEFT JOIN customers c ON c.id=qq.customer_id
                   WHERE qq.status IN ('Draft','Sent') AND qq.valid_until BETWEEN ? AND ?""", (a, b)):
        out[qu["valid_until"][:10]]["quotes"].append(dict(qu))
    return list(out.values())


def month_grid(year, month):
    """Weeks (Mon-Sun) of the month, each day with counts, for a calendar view."""
    cal = calendar.Calendar(firstweekday=0)
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    start = first - timedelta(days=first.weekday())
    end = last + timedelta(days=6 - last.weekday())
    days = {d["iso"]: d for d in glance(start, (end - start).days + 1)}
    weeks = []
    for wk in cal.monthdatescalendar(year, month):
        weeks.append([dict(days[d.isoformat()], in_month=d.month == month) for d in wk])
    return weeks


# ------------------------------------------------------------------ recommendations
def _my_rep():
    return setting("report_rep") or setting("sales_rep") or ""


def _customer_for_company(cust_key, name):
    from .db import find_customer
    c = find_customer(name=name)
    return dict(c) if c else None


def recommendations(days=7, today=None):
    """Who to see or call in the next `days`, most important first. Each: customer_id (or None + address),
    name, city, why (list), score, value ($ at stake), kind, suggested day."""
    today = today or date.today()
    end = today + timedelta(days=days)
    recs = {}

    def add(key, name, why, score, value=0.0, cid=None, city="", address="", kind="visit", link=None):
        r = recs.setdefault(key, {"key": key, "name": name, "why": [], "score": 0.0, "value": 0.0,
                                  "customer_id": cid, "city": city or "", "address": address or "", "kind": kind,
                                  "links": []})
        r["why"].append(why)
        r["score"] += score
        r["value"] = max(r["value"], value or 0)
        if link:
            r["links"].append(link)
        if cid and not r["customer_id"]:
            r["customer_id"] = cid
        if city and not r["city"]:
            r["city"] = city

    # 1) quotes waiting on an answer
    for qu in q("""SELECT qq.*, c.name cname, c.city, (SELECT SUM(qty*coalesce(unit_price,0)) FROM quote_lines
                   WHERE quote_id=qq.id) total FROM quotes qq LEFT JOIN customers c ON c.id=qq.customer_id
                   WHERE qq.status='Sent'"""):
        sent = _d(qu["sent_at"]) or _d(qu["created"])
        age = (today - sent).days if sent else 0
        if age >= 3:
            add(f"c{qu['customer_id']}", qu["cname"] or "(no customer)", f"Quote {qu['number']} sent {age} days ago - "
                f"follow up (${qu['total'] or 0:,.0f})", 30 + min(age, 30), qu["total"] or 0, qu["customer_id"],
                qu["city"], kind="call", link=("quote", qu["id"]))
    # 2) CRM follow-ups due (or overdue)
    for t in q("""SELECT t.*, c.name cname, c.city FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
                  WHERE t.done=0 AND coalesce(t.due_date,'')<>'' AND t.due_date<=?""", (end.isoformat(),)):
        late = (today - (_d(t["due_date"]) or today)).days
        add(f"c{t['customer_id']}" if t["customer_id"] else f"t{t['id']}", t["cname"] or t["title"],
            f"Follow-up: {t['title']}" + (f" ({late} days late)" if late > 0 else f" (due {t['due_date'][:10]})"),
            25 + max(0, late), 0, t["customer_id"], t["city"], kind="call")
    # 3) deals closing soon
    for dl in q("""SELECT d.*, c.name cname, c.city FROM deals d LEFT JOIN customers c ON c.id=d.customer_id
                   WHERE d.stage NOT IN ('Won','Lost') AND coalesce(d.close_date,'')<>'' AND d.close_date<=?""",
                (end.isoformat(),)):
        add(f"c{dl['customer_id']}", dl["cname"] or dl["name"], f"Deal \"{dl['name']}\" closes {dl['close_date'][:10]}"
            f" (${dl['value'] or 0:,.0f})", 30, dl["value"] or 0, dl["customer_id"], dl["city"])
    # 4) orders that need a check: backordered, or due this week and not delivered
    for o in q(f"""SELECT o.*, c.name cname, c.city FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                   WHERE o.status IN ({','.join('?' * len(OPEN_ORDER))})""", OPEN_ORDER):
        due = _d(_iso_order_date(o["due_date"])) if o["due_date"] else None
        if o["status"] == "Backordered":
            add(f"c{o['customer_id']}", o["cname"] or "(no customer)", f"Order #{o['id']} is backordered - give "
                f"them an update", 20, 0, o["customer_id"], o["city"], kind="call", link=("order", o["id"]))
        elif due and today <= due <= end and o["status"] in ("Shipped", "Confirmed"):
            add(f"c{o['customer_id']}", o["cname"] or "(no customer)", f"Order #{o['id']} due {due:%a %m/%d} - "
                f"check it went in right", 10, 0, o["customer_id"], o["city"], link=("order", o["id"]))
    # 5) company data: my accounts that are overdue for an order, or buying less
    try:
        from .company_db import insights
        ins = insights(_my_rep())
    except Exception:   # noqa: BLE001 - no company data yet
        ins = None
    if ins:
        for r in ins["overdue"][:15]:
            c = _customer_for_company(r["cust_key"], r["name"])
            add(f"c{c['id']}" if c else f"k{r['cust_key']}", c["name"] if c else r["name"],
                f"Usually orders every ~{r['gap']} days - quiet for {r['days']} (${r['t12']:,.0f} last 12 mo)",
                35 + min(r["days"] / r["gap"], 6) * 5, r["t12"], c["id"] if c else None, c["city"] if c else "")
        for r in ins["slipping"][:10]:
            c = _customer_for_company(r["cust_key"], r["name"])
            add(f"c{c['id']}" if c else f"k{r['cust_key']}", c["name"] if c else r["name"],
                f"Buying {-r['change']}% less than the year before (${r['t12']:,.0f} vs ${r['p12']:,.0f})",
                25, r["p12"] - r["t12"], c["id"] if c else None, c["city"] if c else "")
        for r in ins["cross"][:8]:
            c = _customer_for_company(r["cust_key"], r["name"])
            if not c:
                continue
            add(f"c{c['id']}", c["name"], f"Big account not buying {', '.join(r['missing'][:3])} from us", 8,
                0, c["id"], c["city"])
    # 6) active customers nobody has seen in a while
    for c in q("""SELECT c.id, c.name, c.city, (SELECT MAX(occurred_at) FROM activities a WHERE a.customer_id=c.id) last
                  FROM customers c WHERE coalesce(c.status,'Active')='Active'"""):
        last = _d(c["last"])
        if last and (today - last).days >= 45:
            add(f"c{c['id']}", c["name"], f"No visit or call logged in {(today - last).days} days", 6, 0, c["id"],
                c["city"])
    # accounts on hold: don't send them to visit for sales - flag it instead
    held = {r["customer_id"] for r in q("SELECT customer_id FROM account_holds WHERE status='hold' AND "
                                        "customer_id IS NOT NULL")}
    out = []
    for r in recs.values():
        if r["customer_id"] in held:
            r["why"].insert(0, "ACCOUNT HOLD - collect before selling")
            r["score"] -= 10
        if not r["customer_id"] and not r["address"]:
            ac = q("SELECT * FROM co_accounts WHERE cust_key=?", (r["key"][1:],), one=True) \
                if r["key"].startswith("k") else None
            if ac:
                r["address"] = ", ".join(x for x in (ac["address"], ac["city"], ac["state"]) if x)
                r["city"] = r["city"] or ac["city"] or ""
        out.append(r)
    out.sort(key=lambda r: -r["score"])
    _suggest_days(out, today, days)
    return out


def _suggest_days(recs, today, days):
    """Group stops by city and give each city a day (busiest city first), Mon-Fri, skipping today if late."""
    work = [today + timedelta(days=i) for i in range(1 if days > 1 else 0, days + 1)
            if (today + timedelta(days=i)).weekday() < 5]
    if not work:
        work = [today]
    by_city = OrderedDict()
    for r in recs:
        if r["kind"] == "visit":
            by_city.setdefault((r["city"] or "?").strip().lower(), []).append(r)
    cities = sorted(by_city.items(), key=lambda kv: -sum(r["score"] for r in kv[1]))
    load = {d: 0 for d in work}
    per_day = int(setting("plan_stops_per_day") or 6)
    for _, rs in cities:
        d = min(work, key=lambda dd: load[dd])
        for r in rs:
            if load[d] >= per_day:
                d = min(work, key=lambda dd: load[dd])
            r["day"] = d.isoformat()
            load[d] += 1
    for r in recs:
        if r["kind"] != "visit":
            r["day"] = work[0].isoformat()


# ------------------------------------------------------------------ projection
def projection(today=None, win_rate=None):
    """This month: sales so far, run-rate finish, and the plan scenario (planned stops' expected $ + open quotes x
    win rate), against the goal."""
    from . import sales as S
    today = today or date.today()
    y, m = today.year, today.month
    days_in = calendar.monthrange(y, m)[1]
    first = date(y, m, 1)
    biz = [first + timedelta(days=i) for i in range(days_in) if (first + timedelta(days=i)).weekday() < 5]
    done_biz = sum(1 for d in biz if d <= today)
    mtd = S.total(y, upto=today.isoformat(), frm=first.isoformat())
    ly = S.total(y - 1, upto=date(y - 1, m, min(today.day, calendar.monthrange(y - 1, m)[1])).isoformat(),
                 frm=date(y - 1, m, 1).isoformat())
    ly_full = S.total(y - 1, upto=date(y - 1, m, calendar.monthrange(y - 1, m)[1]).isoformat(),
                      frm=date(y - 1, m, 1).isoformat())
    run_rate = mtd / done_biz * len(biz) if done_biz else 0.0
    goals = {g["period"]: g for g in S.goals(y)}
    goal = goals.get(f"{y}-{m:02d}", {}).get("goal") or 0
    decided = q("SELECT status, COUNT(*) n FROM quotes WHERE status IN ('Approved','Ordered','Lost') GROUP BY status")
    won = sum(r["n"] for r in decided if r["status"] in ("Approved", "Ordered"))
    lost = sum(r["n"] for r in decided if r["status"] == "Lost")
    hist_rate = round(won / (won + lost) * 100) if (won + lost) >= 5 else None
    if win_rate is None:
        win_rate = float(setting("plan_win_rate") or (hist_rate or 40))
    open_q = q("""SELECT qq.id, qq.number, c.name cname, (SELECT SUM(qty*coalesce(unit_price,0)) FROM quote_lines
                  WHERE quote_id=qq.id) total FROM quotes qq LEFT JOIN customers c ON c.id=qq.customer_id
                  WHERE qq.status IN ('Sent','Approved')""")
    quotes_total = sum(r["total"] or 0 for r in open_q)
    month_end = date(y, m, days_in)
    stops = q("""SELECT s.*, c.name cname FROM route_stops s LEFT JOIN customers c ON c.id=s.customer_id
                 WHERE s.day BETWEEN ? AND ? AND s.status<>'Skipped' ORDER BY s.day, s.position""",
              (today.isoformat(), month_end.isoformat()))
    planned = sum(s["expected"] or 0 for s in stops)
    plan_total = mtd + planned + quotes_total * win_rate / 100
    return {"month": first.strftime("%B %Y"), "mtd": mtd, "ly_same": ly, "ly_full": ly_full, "goal": goal,
            "run_rate": round(run_rate, 2), "biz_days": len(biz), "biz_done": done_biz,
            "win_rate": win_rate, "hist_rate": hist_rate, "quotes": [dict(r) for r in open_q],
            "quotes_total": quotes_total, "stops": [dict(s) for s in stops], "planned": planned,
            "plan_total": round(plan_total, 2),
            "gap": round((goal - max(plan_total, run_rate)), 2) if goal else None}
