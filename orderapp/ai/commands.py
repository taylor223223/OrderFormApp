"""Talk to the assistant like a person: "add Sample Palms to my Wednesday", "what did Riverwalk order last month",
"move tomorrow's 2nd stop to Friday", "Pat called - 2 screens 35x48 for 204".

Claude gets a set of tools - the same things you can do in the app - and decides which to use. Customer orders /
quotes you relay go through the normal order process (process_customer_message), so nothing about orders changes.
Every exchange is kept in ai_chat and logged for the daily summary.
"""
import json
from datetime import date, datetime, timedelta

from ..db import now, q, set_setting, setting, x
from .client import AIError, call

SYSTEM = """You are {me}'s assistant inside his sales app (he's a sales rep for an apartment-interior products supplier:
doors, blinds, screens, cabinets, countertops...). He types to you like he'd talk to a coworker - often several
things at once ("word vomit"). Do ALL of it.

TODAY IS {today}. That comes from the computer's clock and is always right - it is the only source of today's date.
The next 7 days: {week}.
- Older chat messages are stamped [sent ...] with when they were written. "Today" / "tomorrow" / "this Friday" inside an
  old message meant relative to THAT day, not to today. Never work out today's date from the chat.
- If he says you have the date wrong, just use the date above from then on - do NOT move, reschedule or change
  anything because of it unless he names exactly what to move and where.

You can do anything he can do in the app - use the tools: look things up, add / move / mark visited route stops, write
day notes, add tasks and reminders, log CRM touches, save customers and contacts, find / reorder / build orders and
quote requests, send unsent orders to Jimmy, move orders along, put Jimmy's invoice # on orders, forward returned
quotes, send or schedule emails, see what's open and who it's waiting on, give overviews, numbers, charts and
recommendations.
- Customer orders / quote requests / reorders / measure requests (from a call, text, or him describing one) go to
  process_customer_message - that runs the normal order process. Do the rest yourself. Several in one message is fine.
- Look up ids with the find_ tools - never guess an id. Days are YYYY-MM-DD; work out "Wednesday", "tomorrow",
  "next Tuesday at 8" from today's date (the coming one, never a past day).
- He lets you act without asking - just do it and tell him exactly what you did. Only ask (one short question)
  when something is truly ambiguous (two customers match, which order he means). Never ask for things you can look up.
- A PO number is optional - never ask for one or hold anything because it's missing.
- Don't delete anything unless he clearly asks.
- Remembering: when he corrects you, tells you how he likes something done, or says "from now on / always / never",
  save it with add_rule (short and specific) and say you'll remember it. Follow every rule below.
- How to answer: short and easy to scan. You may use light markdown - **bold** for key numbers or names, short
  bullet lists, a small table when comparing things, a short heading only for longer overviews. For a graph or chart
  (or a trend that's clearer as one) call make_chart. Follow his formatting preferences in the rules.
- Finish with what you did (or found) - every action, plainly.{rules}"""


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10]).isoformat()
    except ValueError:
        return None


# ------------------------------------------------------------------ the tools
def t_find_customers(query, limit=8):
    s = f"%{(query or '').strip()}%"
    rows = q("""SELECT id, name, city, phone, email FROM customers WHERE name LIKE ? OR acct LIKE ? OR address LIKE ?
                OR mgmt LIKE ? ORDER BY name LIMIT ?""", (s, s, s, s, int(limit)))
    if not rows:
        from ..db import find_customer
        c = find_customer(name=query)
        rows = [c] if c else []
    return [{k: r[k] for k in ("id", "name", "city", "phone", "email")} for r in rows]


def t_customer_details(customer_id):
    from .assistant import customer_facts
    from ..holds import hold_for
    c = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not c:
        return {"error": "No customer with that id."}
    h = hold_for(customer_id)
    return {"facts": customer_facts(c)[:6000], "address": f"{c['address'] or ''}, {c['city'] or ''} {c['zip'] or ''}",
            "account_hold": (f"ON HOLD - owes ${h['balance'] or 0:,.2f}" if h else "no hold")}


def t_create_customer(name, address="", city="", state="AZ", zip="", phone="", email="", mgmt=""):
    if q("SELECT 1 FROM customers WHERE lower(name)=lower(?)", (name,), one=True):
        return {"error": "A customer with that name already exists - use find_customers."}
    cid = x("""INSERT INTO customers(name, address, city, state, zip, phone, email, mgmt, status, created, updated)
               VALUES (?,?,?,?,?,?,?,?,'Prospect',?,?)""", (name, address, city, state, zip, phone, email, mgmt,
                                                            now(), now()))
    return {"customer_id": cid}


def t_update_customer(customer_id, fields):
    from ..db import CUSTOMER_FIELDS
    ok = {k: v for k, v in (fields or {}).items() if k in CUSTOMER_FIELDS + ["status"]}
    if not ok:
        return {"error": f"Nothing to change. Fields: {', '.join(CUSTOMER_FIELDS)}, status"}
    x(f"UPDATE customers SET {', '.join(k + '=?' for k in ok)}, updated=? WHERE id=?",
      list(ok.values()) + [now(), customer_id])
    return {"updated": list(ok)}


def t_add_contact(customer_id, name, role="", phone="", email=""):
    from .assistant import save_contact
    c = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
    if not c:
        return {"error": "No customer with that id."}
    note = save_contact(c, name, phone, email, "(you asked)")
    if role:
        x("UPDATE contacts SET role=? WHERE customer_id=? AND name=? AND coalesce(role,'')=''", (role, customer_id, name))
    return {"done": note or "already on file"}


def t_find_orders(customer_id=None, text="", status="", kind="", limit=10):
    sql = ("SELECT o.id, o.title, o.po, o.order_date, o.status, coalesce(o.kind,'order') kind, c.name cname "
           "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE 1=1")
    a = []
    if customer_id:
        sql += " AND o.customer_id=?"
        a.append(customer_id)
    if text:
        sql += " AND (o.title LIKE ? OR o.po LIKE ? OR o.data LIKE ? OR o.notes LIKE ?)"
        a += [f"%{text}%"] * 4
    if status:
        sql += " AND o.status=?"
        a.append(status)
    if kind:
        sql += " AND coalesce(o.kind,'order')=?"
        a.append(kind)
    from ..order_kind import status_label
    rows = q(sql + " ORDER BY o.id DESC LIMIT ?", a + [int(limit)])
    return [{"order_id": r["id"], "customer": r["cname"], "title": r["title"], "po": r["po"], "date": r["order_date"],
             "step": status_label(r["status"], r["kind"])} for r in rows]


def t_order_details(order_id):
    from ..order_kind import status_label
    o = q("SELECT o.*, c.name cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE o.id=?",
          (order_id,), one=True)
    if not o:
        return {"error": "No order with that id."}
    d = json.loads(o["data"] or "{}")
    hist = [f"{h['ts'][:16]} {h['status']} {h['note'] or ''}" for h in
            q("SELECT * FROM order_status_history WHERE order_id=? ORDER BY id", (order_id,))]
    return {"order_id": o["id"], "customer": o["cname"], "title": o["title"], "po": o["po"],
            "step": status_label(o["status"], o["kind"]), "header": d.get("header"), "lines": d.get("blocks"),
            "comments": d.get("comments"), "history": hist[-8:]}


def t_set_order_step(order_id, step):
    from ..order_kind import APPROVED, TAPS
    labels = [t[0] for t in TAPS]
    if step not in labels:
        return {"error": f"Step must be one of: {', '.join(labels)}"}
    from ..routes.orders import apply_status
    st = apply_status(int(order_id), step, None, "Set by the AI assistant (you asked)")
    return {"order_id": order_id, "now": step, "status": st, "approved": step == APPROVED}


def t_send_order_to_desk(order_id):
    from .assistant import order_desk_email, queue_email
    from ..routes.orders import _make_pdf
    o = q("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not o:
        return {"error": "No order with that id."}
    if not o["pdf_path"]:
        _make_pdf(order_id)
    subj, body, pdf = order_desk_email(order_id)
    desk = (setting("order_to") or "").strip()
    st = queue_email("order", desk, subj, body, [pdf], None, order_id,
                     hold_reason="" if desk else "No order desk address in Settings.")
    return {"order_id": order_id, "email": st, "to": desk}


def t_send_unsent_orders(include_drafts=False, kind=""):
    """Every order / quote request made in the app but not sent yet -> its own email to Jimmy, one by one."""
    sts = ("Ready", "Draft") if include_drafts else ("Ready",)
    sql = ("SELECT o.id, o.status, coalesce(o.kind,'order') kind FROM orders o WHERE o.status IN (%s) "
           "AND coalesce(o.sent_subject,'')='' " % ",".join("?" * len(sts)))
    a = list(sts)
    if kind:
        sql += "AND coalesce(o.kind,'order')=? "
        a.append(kind)
    rows = q(sql + "ORDER BY o.id", a)
    sent = []
    for r in rows:
        res = t_send_order_to_desk(r["id"])
        o = q("SELECT o.id, o.sent_subject, c.name cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
              "WHERE o.id=?", (r["id"],), one=True)
        sent.append({"order_id": r["id"], "kind": r["kind"], "subject": (o["sent_subject"] if o else "") or "",
                     "email": res.get("email"), "to": res.get("to")})
    drafts = 0 if include_drafts else (q("SELECT COUNT(*) n FROM orders WHERE status='Draft' AND "
                                         "coalesce(sent_subject,'')=''", one=True)["n"])
    return {"sent": sent, "count": len(sent), "drafts_not_sent": drafts,
            "note": "List each one back to Taylor (order #, subject). Drafts aren't sent unless he says to include them."}


def t_reorder(order_id, po=""):
    from ..routes.orders import make_reorder
    from ..catalog import FORMS
    o = q("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not o or o["form_key"] not in FORMS:
        return {"error": "That order can't be copied (no order form)."}
    nid = make_reorder(int(order_id), po)
    return {"new_draft_order_id": nid, "note": "Draft made - check quantities, then send_order_to_desk if he wants."}


def t_purchase_history(customer_id, text="", limit=40):
    from ..order_insights import purchases
    rows = purchases(customer_id)
    if text:
        rows = [r for r in rows if text.lower() in (r.get("desc") or "").lower()]
    return [{k: r.get(k) for k in ("date", "invoice", "qty", "desc", "price")} for r in rows[:int(limit)]]


def t_unit_sizes(customer_id, unit=""):
    sql = """SELECT u.unit_number, m.product, m.room, m.data FROM measurements m LEFT JOIN units u ON u.id=m.unit_id
             WHERE m.customer_id=?"""
    a = [customer_id]
    if unit:
        sql += " AND u.unit_number=?"
        a.append(unit)
    return [{"unit": r["unit_number"], "product": r["product"], "room": r["room"], "sizes": json.loads(r["data"] or "{}")}
            for r in q(sql + " LIMIT 60", a)]


def t_process_customer_message(text, customer_id=None, email_customer=False):
    from .assistant import note
    row = note(text, customer_id, "", bool(email_customer))
    return {"result": row["action"] if row else "nothing done", "status": row["status"] if row else ""}


def t_route_day(day):
    d = _d(day)
    if not d:
        return {"error": "Day must be YYYY-MM-DD."}
    from ..routes.routes_plan import day_note, stops_for
    return {"day": d, "note": day_note(d),
            "stops": [{"stop_id": s["id"], "order": i + 1, "place": s["label"] or s["cname"], "purpose": s["purpose"],
                       "status": s["status"], "notes": s["notes"]} for i, s in enumerate(stops_for(d))]}


def t_add_route_stop(day, customer_id=None, label="", purpose="", notes=""):
    d = _d(day)
    if not d:
        return {"error": "Day must be YYYY-MM-DD."}
    if not customer_id and not label:
        return {"error": "Give a customer_id (find_customers) or a label/address."}
    from ..routes.routes_plan import add_stop
    try:
        sid = add_stop(d, customer_id=customer_id, label=label, purpose=purpose, source="ai-chat", notes=notes)
    except Exception:  # noqa: BLE001 - map service down: add it at the end
        sid = add_stop(d, customer_id=customer_id, label=label, purpose=purpose, source="ai-chat", notes=notes,
                       smart=False)
    return {"stop_id": sid, "day": d}


def t_move_route_stop(stop_id, new_day):
    d = _d(new_day)
    s = q("SELECT * FROM route_stops WHERE id=?", (stop_id,), one=True)
    if not s or not d:
        return {"error": "Unknown stop or bad day."}
    pos = q("SELECT COALESCE(MAX(position), -1) + 1 p FROM route_stops WHERE day=?", (d,), one=True)["p"]
    x("UPDATE route_stops SET day=?, position=?, status='Planned' WHERE id=?", (d, pos, stop_id))
    return {"stop_id": stop_id, "moved_to": d}


def t_remove_route_stop(stop_id):
    x("DELETE FROM route_stops WHERE id=?", (stop_id,))
    return {"removed": stop_id}


def t_set_day_note(day, note, append=True):
    d = _d(day)
    if not d:
        return {"error": "Day must be YYYY-MM-DD."}
    from ..routes.routes_plan import day_note
    old = day_note(d)
    new = (old + "\n" + note).strip() if append and old else note.strip()
    set_setting(f"day_note:{d}", new)
    return {"day": d, "note": new}


def t_add_task(title, due_date=None, customer_id=None):
    tid = x("INSERT INTO tasks(customer_id, title, due_date, owner, created) VALUES (?,?,?,?,?)",
            (customer_id, title, _d(due_date) if due_date else None, "me", now()))
    return {"task_id": tid}


def t_list_tasks(include_done=False):
    rows = q("SELECT t.*, c.name cname FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id "
             + ("" if include_done else "WHERE t.done=0 ") + "ORDER BY coalesce(t.due_date,'9999'), t.id LIMIT 40")
    return [{"task_id": r["id"], "title": r["title"], "due": r["due_date"], "customer": r["cname"],
             "done": bool(r["done"])} for r in rows]


def t_complete_task(task_id):
    x("UPDATE tasks SET done=1, done_at=? WHERE id=?", (now(), task_id))
    return {"done": task_id}


def t_log_activity(customer_id, kind, notes="", contact_name="", when=None):
    from ..db import ACTIVITY_KINDS
    from ..routes.crm import log_activity
    if kind not in ACTIVITY_KINDS:
        return {"error": f"kind must be one of {', '.join(ACTIVITY_KINDS)}"}
    aid = log_activity(customer_id, kind, when or now()[:16], contact_name=contact_name, notes=notes)
    return {"activity_id": aid}


def t_send_email(to, subject, body, attach_catalog=True):
    from .assistant import catalog_file, queue_email
    st = queue_email("customer", to, subject, body, [catalog_file()] if attach_catalog else [], None, None)
    return {"email": st, "note": "practice mode holds it unless 'to' is a test address" if st == "practice" else ""}


def t_add_rule(rule):
    old = (setting("ai_rules") or "").strip()
    set_setting("ai_rules", (old + "\n- " + rule.strip().lstrip("- ")).strip())
    return {"rules_now": setting("ai_rules")}


def t_open_requests(waiting_on=""):
    from .followup import open_requests
    rows = open_requests()
    if waiting_on:
        rows = [r for r in rows if r["waiting_on"] == waiting_on]
    return [{k: r.get(k) for k in ("waiting_on", "what", "who", "detail", "days", "overdue", "ref", "id")}
            for r in rows[:60]]


def t_mark_visit_done(stop_id, notes="", how="In person", who="", ordered="", order_what=""):
    from ..routes.routes_plan import mark_done
    s = q("SELECT * FROM route_stops WHERE id=?", (stop_id,), one=True)
    if not s:
        return {"error": "No route stop with that id (use route_day)."}
    mark_done(s, notes=notes, ordered=ordered or None, order_what=order_what or None, how=how or None,
              who=who or None)
    return {"stop_id": stop_id, "visited": True, "crm_logged": True}


def t_set_invoice(order_id, invoice_no):
    from ..order_notices import set_invoice
    ok = set_invoice(int(order_id), str(invoice_no), note="you told the assistant")
    return {"order_id": order_id, "invoice_no": invoice_no} if ok else {"error": "No order with that id."}


def t_forward_quote(order_id, to="", message=""):
    """Email a returned quote (one order, or its whole project combined) to the customer."""
    from .assistant import catalog_file, combine_quotes, quote_group, queue_email, signature
    o = q("SELECT * FROM orders WHERE id=?", (order_id,), one=True)
    if not o:
        return {"error": "No order with that id."}
    h = (json.loads(o["data"] or "{}") or {}).get("header", {})
    to = to or h.get("quote_email") or h.get("contact_email") or ""
    if not to:
        return {"error": "No email on the quote - ask him who to send it to."}
    members = quote_group(o) or [o]
    files = [f for mbr in members for f in json.loads(mbr["quote_file"] or "[]")]
    if not files:
        return {"error": "No returned quote PDF saved on this quote yet."}
    import os
    folder = os.path.dirname(files[0])
    doc = combine_quotes(members, folder) if len(files) > 1 else files[0]
    body = message or (f"Hello {h.get('quote_to') or h.get('contact') or ''},\n\nHere is the quote you asked for. "
                       f"The product catalog is attached too. Just reply to let me know if you'd like to go ahead."
                       f"\n\nThank you,\n{signature()}")
    st = queue_email("quote", to, f"Your quote - {h.get('name') or ''} {o['title'] or ''}".strip(), body,
                     [doc, catalog_file()], None, o["id"])
    return {"to": to, "email": st, "parts": len(members)}


def t_schedule_email(to, subject, body, when, attach_catalog=False, customer_id=None):
    from .followup import parse_when, schedule
    due = parse_when(when)
    if not due:
        return {"error": "when must be 'YYYY-MM-DD HH:MM' (or a date for 8 AM)."}
    sid = schedule("email", due, {"to": to, "subject": subject, "body": body, "attach_catalog": bool(attach_catalog),
                                  "customer_id": customer_id}, f"to {to}")
    return {"scheduled_id": sid, "sends_at": due}


def t_remind_me(text, when, customer_id=None):
    from .followup import parse_when, schedule
    due = parse_when(when)
    if not due:
        return {"error": "when must be 'YYYY-MM-DD HH:MM' (or a date for 8 AM)."}
    sid = schedule("reminder", due, {"text": text, "customer_id": customer_id}, text[:60])
    x("INSERT INTO tasks(customer_id, title, due_date, owner, created) VALUES (?,?,?,?,?)",
      (customer_id, text, due[:10], "me", now()))
    return {"reminder_id": sid, "at": due, "also_added_to_tasks": True}


def t_list_scheduled():
    from .followup import scheduled_list
    return [{"id": r["id"], "kind": r["kind"], "when": r["due"], "what": r["payload"]} for r in scheduled_list()]


def t_cancel_scheduled(scheduled_id):
    x("UPDATE ai_scheduled SET status='cancelled' WHERE id=? AND status='waiting'", (scheduled_id,))
    return {"cancelled": scheduled_id}


def t_sales_overview(year=None, customer_id=None, category=""):
    from .. import sales as S
    if not S.has_data():
        return {"error": "No sales imported yet (Sales tab -> Import)."}
    y = int(year or date.today().year)
    same = f"{y - 1}-{date.today().isoformat()[5:]}" if y == date.today().year else None
    out = {"year": y, "by_month": S.monthly(y, customer_id, category or None),
           "total": S.total(y, customer_id, category or None),
           ("last_year_same_period" if same else "last_year_total"): S.total(y - 1, customer_id, category or None,
                                                                              upto=same),
           "by_category": S.by_category(y, customer_id)[:15]}
    if not customer_id:
        out["top_customers"] = S.by_customer(y, category or None, limit=15)
        out["goals"] = S.goals(y)
    return out


def t_recommendations():
    """Who to contact / what to chase: win-backs, overdue requests, quotes waiting, accounts due to reorder."""
    from .. import sales as S
    from .followup import open_requests
    reqs = open_requests()
    rec = {"overdue": [{k: r[k] for k in ("waiting_on", "what", "who", "days")} for r in reqs if r["overdue"]][:15]}
    if S.has_data():
        y = date.today().year
        rec["win_back"] = [{"customer": g["name"], "last_year": g["ly"], "last_order": g["last"]}
                           for g in S.gaps(y, limit=12)]
        due = []
        for c in q("""SELECT s.customer_id, coalesce(c.name, s.customer_name) name, COUNT(DISTINCT s.invoice_date) n,
                      MIN(s.invoice_date) first, MAX(s.invoice_date) last FROM sales s
                      LEFT JOIN customers c ON c.id=s.customer_id WHERE s.invoice_date>=?
                      GROUP BY coalesce(s.customer_id, lower(s.customer_name)) HAVING n>=4""",
                   ((date.today() - timedelta(days=365)).isoformat(),)):
            try:
                span = (date.fromisoformat(c["last"][:10]) - date.fromisoformat(c["first"][:10])).days
                every = span / max(c["n"] - 1, 1)
                quiet = (date.today() - date.fromisoformat(c["last"][:10])).days
            except ValueError:
                continue
            if every and quiet > every * 1.5 and quiet > 21:
                due.append({"customer": c["name"], "usually_every_days": round(every), "days_since_last": quiet})
        rec["due_to_reorder"] = sorted(due, key=lambda d: -d["days_since_last"] / max(d["usually_every_days"], 1))[:12]
    return rec


def t_make_chart(type, title, labels, series, money=False):
    """Draw a chart in the chat. series: [{name, values}]."""
    spec = {"type": type, "title": title, "labels": labels, "series": series, "money": bool(money)}
    return {"chart": spec, "note": "The chart shows under your reply - don't repeat all the numbers."}


def t_search_emails(text="", sender="", limit=15):
    sql, a = "SELECT id, subject, sender, sender_name, received, direction FROM email_messages WHERE 1=1", []
    if text:
        sql += " AND (subject LIKE ? OR body LIKE ?)"
        a += [f"%{text}%"] * 2
    if sender:
        sql += " AND (sender LIKE ? OR sender_name LIKE ? OR to_addr LIKE ?)"
        a += [f"%{sender}%"] * 3
    return [dict(r) for r in q(sql + " ORDER BY received DESC LIMIT ?", a + [int(limit)])]


def t_read_thread(email_id):
    from ..routes.emails import thread_messages
    from ..order_notices import reply_top
    _, msgs = thread_messages(rid=int(email_id))
    return [{"when": m["when"], "from": m["who"], "to": m["to"], "direction": m["dir"],
             "text": (reply_top(m["body"]) or m["body"])[:3000]} for m in msgs[-12:]]


S, I, B, O = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}, {"type": "object"}


def _tool(fn, desc, props, req=()):
    return {"fn": fn, "spec": {"name": fn.__name__[2:], "description": desc,
                               "input_schema": {"type": "object", "properties": props, "required": list(req)}}}


TOOLS = [
    _tool(t_find_customers, "Search customers (properties) by name, account #, address or management company.",
          {"query": S, "limit": I}, ["query"]),
    _tool(t_customer_details, "Everything on file for a customer: contacts, units, recent orders, purchases, hold.",
          {"customer_id": I}, ["customer_id"]),
    _tool(t_create_customer, "Add a new customer (property).", {"name": S, "address": S, "city": S, "state": S,
                                                               "zip": S, "phone": S, "email": S, "mgmt": S}, ["name"]),
    _tool(t_update_customer, "Change customer fields (name, acct, address, city, state, zip, mgmt, phone, email, "
          "notes, status...).", {"customer_id": I, "fields": O}, ["customer_id", "fields"]),
    _tool(t_add_contact, "Add a contact to a customer (or fill in their phone/email).",
          {"customer_id": I, "name": S, "role": S, "phone": S, "email": S}, ["customer_id", "name"]),
    _tool(t_find_orders, "Find orders / quotes in the app (newest first).", {"customer_id": I, "text": S, "status": S,
                                                                             "kind": S, "limit": I}),
    _tool(t_order_details, "One order's lines, header and history.", {"order_id": I}, ["order_id"]),
    _tool(t_set_order_step, "Move an order/quote to a step, like tapping its button: Order ready, Order sent, "
          "Delivered, Quote ready, Request sent, Quote received, Quote sent, Approved / ordered, Declined.",
          {"order_id": I, "step": S}, ["order_id", "step"]),
    _tool(t_send_unsent_orders, "Send EVERY order / quote request that's ready but not sent yet to Jimmy - one email "
          "each, subject '<Apartment> <Order type>' (+ 'Quote' for quotes). Use when he says 'send my orders', "
          "'send the ones I haven't sent', etc.", {"include_drafts": {"type": "boolean"}, "kind": S}),
    _tool(t_send_order_to_desk, "Email an order / quote request PDF to the order desk (Jimmy).", {"order_id": I},
          ["order_id"]),
    _tool(t_reorder, "Make a new draft with the same items as a past app order.", {"order_id": I, "po": S},
          ["order_id"]),
    _tool(t_purchase_history, "What a customer bought (company invoices), newest first; filter by text.",
          {"customer_id": I, "text": S, "limit": I}, ["customer_id"]),
    _tool(t_unit_sizes, "Saved sizes per unit for a customer.", {"customer_id": I, "unit": S}, ["customer_id"]),
    _tool(t_process_customer_message, "Run a customer's order / quote / reorder / measure request through the normal "
          "order process (makes the order, checks it, sends it in when sure, else asks / holds). Also use it when HE "
          "wants an order or quote request built - pass what he described (customer, items, sizes, order or quote).",
          {"text": S, "customer_id": I, "email_customer": B}, ["text"]),
    _tool(t_route_day, "A day's route stops and its note.", {"day": S}, ["day"]),
    _tool(t_add_route_stop, "Add a stop to a day's route.", {"day": S, "customer_id": I, "label": S, "purpose": S,
                                                             "notes": S}, ["day"]),
    _tool(t_move_route_stop, "Move a route stop to another day.", {"stop_id": I, "new_day": S}, ["stop_id", "new_day"]),
    _tool(t_remove_route_stop, "Take a stop off the route (only when he asks).", {"stop_id": I}, ["stop_id"]),
    _tool(t_set_day_note, "Write a day's note (adds to what's there unless append is false).",
          {"day": S, "note": S, "append": B}, ["day", "note"]),
    _tool(t_add_task, "Add a to-do (optionally due on a day, for a customer).", {"title": S, "due_date": S,
                                                                                 "customer_id": I}, ["title"]),
    _tool(t_list_tasks, "His open to-dos.", {"include_done": B}),
    _tool(t_complete_task, "Mark a to-do done.", {"task_id": I}, ["task_id"]),
    _tool(t_log_activity, "Log a CRM touch: In person, Call, Text, Email, Site visit, Drop-in, Meeting, Trade show, "
          "Voicemail, Other.", {"customer_id": I, "kind": S, "notes": S, "contact_name": S, "when": S},
          ["customer_id", "kind"]),
    _tool(t_send_email, "Email someone (catalog attached by default). Follows practice / live mode.",
          {"to": S, "subject": S, "body": S, "attach_catalog": B}, ["to", "subject", "body"]),
    _tool(t_add_rule, "Save a standing rule for the assistant (\"from now on...\", or a correction he gave you).",
          {"rule": S}, ["rule"]),
    _tool(t_open_requests, "Everything still open: waiting on him (you), on a customer, or on Jimmy/office (office). "
          "Filter with waiting_on = you | customer | office.", {"waiting_on": S}),
    _tool(t_mark_visit_done, "Mark a route stop visited and log it in the CRM (how: In person / Call / Text / Email; "
          "ordered: Yes / No; order_what: what they ordered).",
          {"stop_id": I, "notes": S, "how": S, "who": S, "ordered": S, "order_what": S}, ["stop_id"]),
    _tool(t_set_invoice, "Put the 6-digit invoice # (from Jimmy) on an order.", {"order_id": I, "invoice_no": S},
          ["order_id", "invoice_no"]),
    _tool(t_forward_quote, "Email a returned quote to the customer (a project's parts go as one combined PDF).",
          {"order_id": I, "to": S, "message": S}, ["order_id"]),
    _tool(t_schedule_email, "Send an email later. when = 'YYYY-MM-DD HH:MM' (a bare date = 8 AM).",
          {"to": S, "subject": S, "body": S, "when": S, "attach_catalog": B, "customer_id": I},
          ["to", "subject", "body", "when"]),
    _tool(t_remind_me, "A reminder at a time (shows in Needs you + Open requests, also added to his to-dos).",
          {"text": S, "when": S, "customer_id": I}, ["text", "when"]),
    _tool(t_list_scheduled, "Scheduled emails and reminders still waiting.", {}),
    _tool(t_cancel_scheduled, "Cancel a scheduled email / reminder.", {"scheduled_id": I}, ["scheduled_id"]),
    _tool(t_sales_overview, "Sales numbers from the imported company sales (his territory): by month, total, same "
          "period last year, by category, top customers, goals. Optional customer_id / category.",
          {"year": I, "customer_id": I, "category": S}),
    _tool(t_recommendations, "Who to contact / what to chase: overdue requests, win-back accounts (bought last year, "
          "not this year), accounts overdue for their usual reorder.", {}),
    _tool(t_make_chart, "Draw a chart in the chat (type: bar | line | pie). series = [{name, values}]. Use when he "
          "asks for a graph/chart, or when a trend is easier to see than a list.",
          {"type": S, "title": S, "labels": {"type": "array", "items": S},
           "series": {"type": "array", "items": O}, "money": B}, ["type", "title", "labels", "series"]),
    _tool(t_search_emails, "Find emails (received and sent) by words or by person.",
          {"text": S, "sender": S, "limit": I}),
    _tool(t_read_thread, "Read a whole email conversation (give any email id in it).", {"email_id": I}, ["email_id"]),
]
BY_NAME = {t["spec"]["name"]: t["fn"] for t in TOOLS}


def _stamp(ts):
    """'2026-11-13 14:05' -> 'Fri 11/13 2:05 PM' so old chat lines say what day they were from."""
    try:
        d = datetime.strptime((ts or "")[:16], "%Y-%m-%d %H:%M")
        return f"{d:%a %m/%d} {d:%I:%M %p}".replace(" 0", " ")
    except ValueError:
        return ts or "earlier"


def history(n=12):
    return list(reversed(q("SELECT * FROM ai_chat ORDER BY id DESC LIMIT ?", (n,))))


def ask(text, email_customer=False):
    """One message from Taylor -> Claude with tools, looping until it answers. Returns (reply, actions)."""
    from .assistant import rules_text, signature, smart
    me = signature() or "the rep"
    today = date.today()
    week = ", ".join(f"{d:%a %m/%d}={d:%Y-%m-%d}" for d in (today + timedelta(days=i) for i in range(8)))
    system = SYSTEM.format(me=me, today=f"{today:%A %B %d, %Y} ({today:%Y-%m-%d})", week=week, rules=rules_text())
    msgs = []
    for h in history(8):                      # a little memory of the conversation, each stamped with when it was said
        role = "user" if h["role"] == "me" else "assistant"
        txt = f"[sent {_stamp(h['ts'])}] " + (h["text"] or "")
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + txt
        else:
            msgs.append({"role": role, "content": txt})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":   # last time got no answer - don't send two "user" turns in a row
        msgs.append({"role": "assistant", "content": "(no answer)"})
    msgs.append({"role": "user", "content": f"[now: {datetime.now():%A %Y-%m-%d %I:%M %p} - today's real date] " + text + ("\n(If there's a customer message in this, he wants the customer "
                                                    "emailed: email_customer=true.)" if email_customer else "")})
    x("INSERT INTO ai_chat(ts, role, text) VALUES (?,?,?)", (now(), "me", text))
    actions, reply = [], ""
    for _ in range(16):
        r = call(smart(), system, msgs, tools=[t["spec"] for t in TOOLS], max_tokens=6000, purpose="chat")
        content = r.get("content") or []
        msgs.append({"role": "assistant", "content": content})
        uses = [b for b in content if b.get("type") == "tool_use"]
        reply = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if not uses:
            break
        results = []
        for u in uses:
            fn = BY_NAME.get(u["name"])
            try:
                out = fn(**(u.get("input") or {})) if fn else {"error": "unknown tool"}
            except TypeError as e:
                out = {"error": f"bad inputs: {e}"}
            except Exception as e:  # noqa: BLE001 - tell Claude, let it try another way
                out = {"error": str(e)[:300]}
            actions.append({"tool": u["name"], "input": u.get("input"), "result": out})
            results.append({"type": "tool_result", "tool_use_id": u["id"],
                            "content": json.dumps(out, default=str)[:12000]})
        msgs.append({"role": "user", "content": results})
    reply = reply or "Done."
    x("INSERT INTO ai_chat(ts, role, text, actions) VALUES (?,?,?,?)",
      (now(), "assistant", reply, json.dumps(actions, default=str)[:20000]))
    did = [a["tool"] for a in actions if not a["tool"].startswith((
        "find_", "customer_details", "order_details", "route_day", "list_", "purchase_", "unit_", "open_requests",
        "sales_overview", "recommendations", "make_chart", "search_emails", "read_thread"))]
    x("""INSERT INTO ai_log(ts, msg_id, sender, sender_name, subject, received, category, summary, action, status,
         reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
      (now(), f"chat-{datetime.now():%Y%m%d%H%M%S%f}", "", f"{me} (you)", text[:80], now()[:16], "command",
       text[:200], (reply[:300] + (f" [{', '.join(did)}]" if did else "")), "done", "[]"))
    return reply, actions
