"""Keeping track of every email request until it's handled (v2.37.0).

- Sent Items: what you sent yourself in Outlook closes the matching "needs you" items, restarts the follow-up clock,
  logs a CRM "Email" entry for customers, and tracks orders / quote requests you emailed the order desk directly.
- Open requests: one list of everything waiting - on you, on a customer, or on Jimmy / the office.
- Follow-ups: customers who go quiet get a short polite nudge (a set number of times), then it's handed to you.
- Scheduled: emails to send later and reminders ("remind me Friday to call Curtis").
"""
import json
import re
from datetime import date, datetime, timedelta

from ..db import add_status, find_customer, now, q, setting, x

ORDERISH_WORDS = re.compile(r"\b(order|quote|pricing|price|po|reorder|estimate)\b", re.I)


def _key(subject):
    from ..routes.emails import thread_key
    return thread_key(subject)


def _int(name, default):
    try:
        return int(setting(name) or default)
    except ValueError:
        return default


def bdays_since(when):
    """Business days (Mon-Fri) from `when` (YYYY-MM-DD...) to today."""
    try:
        d = datetime.strptime((when or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0
    n, t = 0, date.today()
    while d < t:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def days_since(when):
    try:
        return (date.today() - datetime.strptime((when or "")[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return 0


def customer_for_email(addr):
    if not addr:
        return None
    return find_customer(email=addr)


def customer_in_text(text):
    """The customer whose name appears in a subject line (longest name wins), else None."""
    low = " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "
    best = None
    for c in q("SELECT id, name FROM customers"):
        n = re.sub(r"[^a-z0-9]+", " ", (c["name"] or "").lower()).strip()
        if len(n) >= 4 and f" {n} " in low and (best is None or len(n) > len(best[1])):
            best = (c["id"], n)
    return best[0] if best else None


def log_email_activity(customer_id, when, contact_name, subject, email_id):
    """CRM entry: just 'Email' and the date - the email itself is one click away (it links to the thread)."""
    if q("SELECT 1 FROM activities WHERE customer_id=? AND email_id=?", (customer_id, email_id), one=True):
        return None
    return x("""INSERT INTO activities(customer_id, contact_name, kind, occurred_at, subject, notes, owner, created,
                email_id) VALUES (?,?,?,?,?,?,?,?,?)""",
             (customer_id, contact_name or "", "Email", (when or now())[:16], subject or "", "", "assistant", now(),
              email_id))


# ------------------------------------------------------------------ Sent Items
def sync_sent(prov, days=7):
    """Read what you sent since the last check and act on each new one. Returns counts for the check summary."""
    res = {"sent": 0, "closed": 0, "crm": 0, "tracked": 0, "error": ""}
    if setting("ai_read_sent") == "0" or not hasattr(prov, "list_sent"):
        return res
    try:
        sent = prov.list_sent(days=days, limit=500)
    except Exception as e:  # noqa: BLE001 - never stop the inbox check over this
        res["error"] = f"Couldn't read Sent Items: {e}"
        return res
    known = {r["msg_id"] for r in q("SELECT msg_id FROM email_messages WHERE direction='out'")}
    from ..routes.emails import _store
    for m in sorted([m for m in sent if m["msg_id"] not in known], key=lambda m: m.get("received") or ""):
        try:
            full = prov.get_message(m["msg_id"])
        except Exception:  # noqa: BLE001
            full = {"subject": m.get("subject") or "", "body": "", "attachments": [], "attachment_names": []}
        full.update(msg_id=m["msg_id"], direction="out", to_addr=", ".join(m.get("to") or []),
                    conv_id=m.get("conv_id") or full.get("conv_id"), sender=setting("ai_my_email") or "",
                    sender_name="You", received=m.get("received") or full.get("received") or now()[:16])
        full["attachments"] = full.get("attachments") or []
        rid = _store(full, prov.name)
        res["sent"] += 1
        for k, v in apply_sent(full, rid, m.get("to") or []).items():
            res[k] = res.get(k, 0) + v
    return res


def apply_sent(m, email_id, to_list):
    """One email you sent: close what it handled, log it, and track it if it went to the order desk."""
    from .assistant import is_coworker, is_me
    out = {"closed": 0, "crm": 0, "tracked": 0}
    subj, conv, when = m.get("subject") or "", m.get("conv_id"), m.get("received") or now()[:16]
    key = _key(subj)
    desk = (setting("order_to") or "").strip().lower()
    logged = set()
    for addr in to_list:
        a = (addr or "").strip().lower()
        if not a or is_me(a):
            continue
        for t in q("SELECT * FROM ai_threads WHERE status IN ('held', 'waiting', 'open') AND "
                   "(lower(sender)=? OR (conv_id IS NOT NULL AND conv_id=?))", (a, conv or "-")):
            if not ((conv and t["conv_id"] == conv) or _key(t["subject"]) == key):
                continue
            if t["status"] == "waiting":     # you followed up yourself - restart the follow-up clock
                x("UPDATE ai_threads SET last_activity=?, nudged_at=?, updated=? WHERE id=?", (when, when, now(), t["id"]))
            else:
                x("UPDATE ai_threads SET status='done', reason='You replied in Outlook', updated=? WHERE id=?",
                  (now(), t["id"]))
                out["closed"] += 1
        for g in q("SELECT id, subject FROM ai_log WHERE reviewed=0 AND status IN ('held', 'error') AND lower(sender)=?",
                   (a,)):
            if _key(g["subject"]) == key:
                x("UPDATE ai_log SET reviewed=1 WHERE id=?", (g["id"],))
                out["closed"] += 1
        for o in q("SELECT id, subject FROM ai_outbox WHERE status IN ('held', 'practice') AND lower(to_addr)=?", (a,)):
            if _key(o["subject"]) == key:
                x("UPDATE ai_outbox SET status='cancelled', note='You replied yourself in Outlook' WHERE id=?", (o["id"],))
        if a != desk and not is_coworker(a):
            c = customer_for_email(a)
            if c and c["id"] not in logged:
                k = q("SELECT name FROM contacts WHERE customer_id=? AND lower(email)=?", (c["id"], a), one=True)
                if log_email_activity(c["id"], when, k["name"] if k else "", subj, email_id):
                    out["crm"] += 1
                logged.add(c["id"])
    if desk and desk in [(t or "").lower() for t in to_list]:
        out["tracked"] += track_desk_email(m, key)
    return out


def track_desk_email(m, key):
    """An order / quote request you emailed Jimmy straight from Outlook: put it on Order Tracking (sent) so his
    "111111 - entered" reply and returned quotes find it. Emails the app sent itself are already there."""
    from ..order_notices import is_reply
    subj = m.get("subject") or ""
    if is_reply(subj) or subj.lower().startswith(("fw:", "fwd:")):
        return 0
    for o in q("SELECT id, sent_subject FROM orders WHERE coalesce(sent_subject,'')<>''"):
        if _key(o["sent_subject"]) == key:
            return 0
    text = f"{subj} {(m.get('body') or '')[:2000]} {' '.join(m.get('attachment_names') or [])}"
    if not ORDERISH_WORDS.search(text) and not any(n.lower().endswith(".pdf") for n in m.get("attachment_names") or []):
        return 0
    kind = "quote" if re.search(r"\b(quote|pricing|price|estimate)\b", subj, re.I) else "order"
    cid = customer_in_text(subj)
    when = m.get("received") or now()[:16]
    oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, notes, created,
               updated, kind, sent_at, sent_subject, sent_to) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, None, subj[:120] or "Emailed order", "", when[:10], "Sent", "{}", "outlook",
             "Emailed from Outlook - picked up from your Sent Items", now(), now(), kind, when, subj,
             setting("order_to") or ""))
    add_status(oid, "Sent", "Emailed to the order desk from Outlook")
    return 1


# ------------------------------------------------------------------ open requests
def open_requests():
    """Everything still open: [{waiting_on: you|customer|office, what, who, since, days, link, kind, id}]."""
    items = []
    for g in q("""SELECT * FROM ai_log WHERE status IN ('held', 'error') AND reviewed=0 ORDER BY id"""):
        items.append({"waiting_on": "you", "what": g["summary"] or g["subject"] or "", "who": g["sender_name"] or
                      g["sender"] or "", "since": (g["received"] or g["ts"] or "")[:16], "kind": g["category"] or "",
                      "id": g["id"], "ref": "log", "detail": g["action"] or "", "subject": g["subject"] or ""})
    for t in q("SELECT * FROM ai_threads WHERE status='waiting' ORDER BY id"):
        since = t["last_activity"] or t["updated"] or t["created"]
        items.append({"waiting_on": "customer", "what": t["subject"] or "", "who": t["sender"] or "", "since": since,
                      "kind": t["category"] or "", "id": t["id"], "ref": "thread", "nudges": t["nudges"] or 0,
                      "detail": t["reason"] or "Waiting for their answer", "subject": t["subject"] or ""})
    # only orders sent in the last few weeks - older ones without a # were handled before the app tracked invoices
    since = (datetime.now() - timedelta(days=_int("ai_invoice_days", 3))).strftime("%Y-%m-%d")
    quote_since = (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d")   # quotes customers sit on for weeks drop off
    for o in q("""SELECT o.*, c.name cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                  WHERE o.status='Sent' AND coalesce(o.waiting_closed,'')='' ORDER BY o.id"""):
        quote = (o["kind"] or "order") == "quote"
        if not quote and (o["invoice_no"] or "").strip():
            continue
        if (o["sent_at"] or o["updated"] or o["created"] or "")[:10] < since:
            continue
        items.append({"waiting_on": "office", "what": f"#{o['id']} {o['cname'] or ''} {o['title'] or ''}".strip(),
                      "who": "Jimmy / office", "since": o["sent_at"] or o["updated"] or "", "kind": o["kind"] or "order",
                      "id": o["id"], "ref": "order", "subject": o["sent_subject"] or "",
                      "detail": "Quote not back yet" if quote else "No invoice # yet"})
    for o in q("""SELECT o.*, c.name cname, (SELECT MAX(ts) FROM order_status_history h WHERE h.order_id=o.id AND
                  h.status='Quoted') quoted_at FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                  WHERE o.status='Quoted' AND coalesce(o.kind,'order')='quote' AND coalesce(o.waiting_closed,'')=''
                  ORDER BY o.id"""):
        if (o["quoted_at"] or o["updated"] or o["created"] or "")[:10] < quote_since:
            continue
        items.append({"waiting_on": "customer", "what": f"Quote #{o['id']} {o['cname'] or ''} {o['title'] or ''}".strip(),
                      "who": o["cname"] or "", "since": o["quoted_at"] or o["updated"] or "", "kind": "quote",
                      "id": o["id"], "ref": "order", "subject": "", "detail": "Waiting for their approval"})
    for it in items:
        it["days"] = bdays_since(it["since"])
        it["overdue"] = it["days"] >= {"you": 1, "customer": _int("ai_nudge_customer_days", 2),
                                       "office": _int("ai_nudge_office_days", 1)}[it["waiting_on"]]
    order = {"you": 0, "office": 1, "customer": 2}
    items.sort(key=lambda i: (order[i["waiting_on"]], -i["days"]))
    return items


def close_request(ref, rid):
    if ref == "thread":
        x("UPDATE ai_threads SET status='done', reason='Closed by you', updated=? WHERE id=?", (now(), rid))
    elif ref == "log":
        x("UPDATE ai_log SET reviewed=1 WHERE id=?", (rid,))
    elif ref == "order":                                   # stop waiting on it - the order itself isn't changed
        x("UPDATE orders SET waiting_closed=? WHERE id=?", (now(), rid))


def close_requests(waiting_on="", ids=None):
    """Clear several at once: everything waiting on you / office / customer, or just the listed ids. Returns how many."""
    n = 0
    for it in open_requests():
        if waiting_on and it["waiting_on"] != waiting_on:
            continue
        if ids and it["id"] not in ids:
            continue
        close_request(it["ref"], it["id"])
        n += 1
    return n


# ------------------------------------------------------------------ follow-ups
def _questions(body):
    qs = [ln.strip(" -•\t") for ln in (body or "").splitlines() if ln.strip().endswith("?")]
    return [s for s in qs if 6 < len(s) < 200][:4]


def nudge_body(name, subject, last_body, quote=False):
    from .assistant import signature
    first = (name or "").split(" ")[0] if name and "@" not in name else ""
    hi = f"Hi {first}," if first else "Hi,"
    if quote:
        mid = (f"Just following up on the quote I sent over for {subject}. Any questions, or would you like to go "
               "ahead with it? Happy to adjust anything.")
    else:
        qs = _questions(last_body)
        mid = (f"Just following up on my email about {subject}." +
               ("\n\nWhen you get a chance, I still need:\n" + "\n".join(f"- {s}" for s in qs) if qs else
                " When you get a chance, could you send over the details so I can get this going?"))
    return f"{hi}\n\n{mid}\n\nThank you,\n{signature() or ''}".strip()


def run_nudges():
    """Customers who went quiet get a short follow-up; after the last one it's handed back to you."""
    from .assistant import log, queue_email
    done = {"nudged": 0, "handed_back": 0, "quote_followups": 0}
    wait_days, most = _int("ai_nudge_customer_days", 2), _int("ai_nudge_max", 2)
    if wait_days <= 0:
        return done
    for t in q("SELECT * FROM ai_threads WHERE status='waiting'"):
        since = t["nudged_at"] or t["last_activity"] or t["updated"] or t["created"]
        if bdays_since(since) < wait_days or not (t["sender"] or "").strip():
            continue
        subj = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", t["subject"] or "", flags=re.I).strip() or "your request"
        g = {"msg_id": None, "sender": t["sender"], "sender_name": t["sender"], "subject": t["subject"],
             "received": now()[:16]}
        if (t["nudges"] or 0) >= most:
            log(g, None, t["id"], {"category": "followup", "summary": f"No answer about {subj}"},
                f"No answer after {most} follow-up(s) - over to you", "held", ["Call them, or close it."],
                customer_id=t["customer_id"])
            x("UPDATE ai_threads SET status='held', reason=?, updated=? WHERE id=?",
              (f"No answer after {most} follow-ups", now(), t["id"]))
            done["handed_back"] += 1
            continue
        last = q("""SELECT o.body FROM ai_outbox o JOIN ai_log l ON l.id=o.log_id WHERE l.thread_id=? AND o.kind='customer'
                    ORDER BY o.id DESC LIMIT 1""", (t["id"],), one=True)
        k = q("SELECT name FROM contacts WHERE lower(email)=lower(?)", (t["sender"],), one=True)
        body = nudge_body(k["name"] if k else "", subj, last["body"] if last else "")
        lid = log(g, None, t["id"], {"category": "followup", "summary": f"Follow-up about {subj}"},
                  f"Followed up with {t['sender']} (no answer in {bdays_since(since)} business days)", "done",
                  customer_id=t["customer_id"])
        st = queue_email("customer", t["sender"], "Re: " + subj, body, [], lid)
        x("UPDATE ai_log SET action=action || ? WHERE id=?", (f" - email {st}", lid))
        x("UPDATE ai_threads SET nudges=coalesce(nudges,0)+1, nudged_at=?, updated=? WHERE id=?", (now(), now(), t["id"]))
        done["nudged"] += 1
    qdays = _int("ai_nudge_quote_days", 5)
    if qdays > 0:
        for it in [i for i in open_requests() if i["ref"] == "order" and i["kind"] == "quote"
                   and i["waiting_on"] == "customer" and i["days"] >= qdays]:
            if q("SELECT 1 FROM order_status_history WHERE order_id=? AND note LIKE 'Quote follow-up%'", (it["id"],),
                 one=True):
                continue
            o = q("SELECT * FROM orders WHERE id=?", (it["id"],), one=True)
            h = (json.loads(o["data"] or "{}") or {}).get("header", {})
            to = h.get("quote_email") or h.get("contact_email") or ""
            if not to:
                continue
            title = f"{h.get('name') or ''} {o['title'] or ''}".strip()
            g = {"msg_id": None, "sender": to, "sender_name": h.get("contact_name") or to,
                 "subject": f"Quote #{o['id']}", "received": now()[:16]}
            lid = log(g, None, None, {"category": "followup", "summary": f"Quote follow-up: {title}"},
                      f"Followed up on quote #{o['id']} with {to}", "done", order_ids=[o["id"]],
                      customer_id=o["customer_id"])
            st = queue_email("customer", to, f"Following up - your quote for {title}",
                             nudge_body(h.get("contact_name") or "", title, "", quote=True), [], lid)
            add_status(o["id"], o["status"], f"Quote follow-up emailed to {to} ({st})")
            done["quote_followups"] += 1
    return done


# ------------------------------------------------------------------ scheduled emails / reminders
def schedule(kind, due, payload, note=""):
    return x("INSERT INTO ai_scheduled(kind, due, payload, status, note, created) VALUES (?,?,?,?,?,?)",
             (kind, due, json.dumps(payload), "waiting", note, now()))


def parse_when(when):
    """'2026-11-20 08:00' / '2026-11-20T08:00' / '2026-11-20' (8 AM) -> 'YYYY-MM-DD HH:MM' or None."""
    s = (when or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s[:19] if fmt.endswith("%S") else s[:16] if "%H" in fmt else s[:10], fmt)
            if fmt == "%Y-%m-%d":
                d = d.replace(hour=8)
            return d.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return None


def due_scheduled():
    """Send the emails and fire the reminders whose time has come. Returns how many ran."""
    from .assistant import catalog_file, log, queue_email
    n = 0
    for s in q("SELECT * FROM ai_scheduled WHERE status='waiting' AND due<=? ORDER BY due",
               (datetime.now().strftime("%Y-%m-%d %H:%M"),)):
        p = json.loads(s["payload"] or "{}")
        g = {"msg_id": None, "sender": p.get("to") or "", "sender_name": p.get("to") or "You",
             "subject": p.get("subject") or p.get("text") or "", "received": now()[:16]}
        if s["kind"] == "email":
            lid = log(g, None, None, {"category": "scheduled", "summary": f"Scheduled email: {p.get('subject')}"},
                      f"Sent the email you scheduled to {p.get('to')}", "done", customer_id=p.get("customer_id"))
            atts = [catalog_file()] if p.get("attach_catalog") else []
            st = queue_email("customer", p.get("to"), p.get("subject") or "", p.get("body") or "", atts, lid)
            x("UPDATE ai_scheduled SET status=?, done_at=?, note=? WHERE id=?", ("sent", now(), f"email {st}", s["id"]))
        else:
            log({**g, "sender_name": "Reminder"}, None, None,
                {"category": "reminder", "summary": p.get("text") or "Reminder"}, f"⏰ Reminder: {p.get('text')}",
                "held", [], customer_id=p.get("customer_id"))
            x("UPDATE ai_scheduled SET status='done', done_at=? WHERE id=?", (now(), s["id"]))
        n += 1
    return n


def scheduled_list(include_done=False):
    rows = q("SELECT * FROM ai_scheduled " + ("" if include_done else "WHERE status='waiting' ") + "ORDER BY due")
    return [dict(r, payload=json.loads(r["payload"] or "{}")) for r in rows]
