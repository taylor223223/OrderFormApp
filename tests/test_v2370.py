"""v2.37.0: invoice # from Jimmy's reply, Sent Items, open requests + follow-ups, scheduled emails / reminders,
new assistant tools, formatting + charts. Claude and the mailbox are faked; all data is made up."""
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


class FakeMail:
    name = "graph"

    def __init__(self):
        self.inbox, self.sent_items, self.sent, self.replies = [], [], [], []

    def available(self):
        return True, ""

    def list_messages(self, days=3, limit=100, **kw):
        return [{k: v for k, v in m.items() if k != "body"} for m in self.inbox]

    def list_sent(self, days=7, limit=300):
        return [{k: v for k, v in m.items() if k != "body"} for m in self.sent_items]

    def get_message(self, mid):
        return dict(next(m for m in self.inbox + self.sent_items if m["msg_id"] == mid))

    def send(self, to, cc, subject, body, attachments, review=True):
        self.sent.append({"to": to, "subject": subject, "body": body})
        return "sent"


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting, x
        a.cid = x("INSERT INTO customers(name, city, status, email) VALUES ('Sample Palms', 'Phoenix', 'Active', "
                  "'office@samplepalms.test')")
        x("INSERT INTO contacts(customer_id, name, role, email) VALUES (?,?,?,?)",
          (a.cid, "Pat Sample", "Maint. Super", "pat@samplepalms.test"))
        for k, v in {"ai_enabled": "1", "ai_api_key": "sk-ant-test", "ai_mode": "live", "order_to": "desk@company.test",
                     "ai_coworker_domain": "company.test", "ai_my_email": "me@company.test",
                     "ai_started": "2020-01-01 00:00", "ai_signature": "Taylor"}.items():
            set_setting(k, v)
    return a


@pytest.fixture()
def A(app, monkeypatch):
    from orderapp.ai import assistant as A
    monkeypatch.setattr(A, "ask_json", lambda *a, **k: {"category": "fyi", "summary": "fyi"})
    return A


def _m(mid, sender, subject, body, when=None, **kw):
    return dict({"msg_id": mid, "sender": sender, "sender_name": sender.split("@")[0], "subject": subject,
                 "body": body, "received": when or datetime.now().strftime("%Y-%m-%d %H:%M"), "attachments": [],
                 "attachment_names": []}, **kw)


def _order(app, status="Sent", kind="order", subject="Sample Palms Window Screen", **kw):
    with app.app_context():
        from orderapp.db import x
        return x("""INSERT INTO orders(customer_id, form_key, title, po, status, data, kind, sent_subject, sent_at,
                    created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (app.cid, "window_screen", "Window Screen Order Form", "", status,
                  json.dumps({"header": dict({"name": "Sample Palms"}, **kw)}), kind, subject,
                  datetime.now().strftime("%Y-%m-%d %H:%M"), "2026-11-01", "2026-11-01"))


def _row(app, sql, args=()):
    with app.app_context():
        from orderapp.db import q
        r = q(sql, args, one=True)
        return dict(r) if r else None


# ---------------------------------------------------------------- invoice #
def test_jimmy_reply_puts_invoice_on_the_order(app, A):
    oid = _order(app)
    mail = FakeMail()
    mail.inbox = [_m("j1", "jimmy@company.test", "RE: Sample Palms Window Screen",
                     "285901 - entered\n\nFrom: Taylor\nSent: ...\nPlease see the attached order")]
    with app.app_context():
        r = A.run(prov=mail)
    o = _row(app, "SELECT * FROM orders WHERE id=?", (oid,))
    assert o["invoice_no"] == "285901" and o["invoice_date"] == date.today().isoformat() and o["status"] == "Confirmed"
    item = r["items"][0]
    assert "invoice 285901" in item["action"] and item["status"] == "done"
    h = _row(app, "SELECT note FROM order_status_history WHERE order_id=? ORDER BY id DESC", (oid,))
    assert "Invoice 285901 entered" in h["note"]


def test_invoice_typed_in_and_tracking_slot(app):
    oid = _order(app, subject="Sample Palms Door")
    draft = _order(app, status="Ready", subject="")
    c = app.test_client()
    tok = lambda p: re.search(r'name="csrf-token" content="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa
    c.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456",
                           "csrf_token": tok("/setup")})
    if c.get("/tracking").status_code != 200:
        c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok("/login")})
    t = tok("/tracking")
    c.post(f"/orders/{oid}/invoice", data={"invoice_no": "123456", "csrf_token": t})
    assert _row(app, "SELECT invoice_no FROM orders WHERE id=?", (oid,))["invoice_no"] == "123456"
    html = c.get("/tracking?status=all").get_data(as_text=True)
    assert 'value="123456"' in html and "Invoice #" in html
    assert f"/orders/{draft}/invoice" not in html              # not sent yet: no slot


# ---------------------------------------------------------------- Sent Items
def test_sent_items_close_what_you_handled_and_log_crm(app, A):
    from orderapp.db import x
    with app.app_context():
        lid = x("""INSERT INTO ai_log(ts, msg_id, sender, sender_name, subject, received, category, summary, action,
                   status, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                ("2026-11-10", "old1", "pat@samplepalms.test", "Pat", "Blinds for 204", "2026-11-10 08:00", "question",
                 "asks about blinds", "Held for you", "held", "[]"))
    mail = FakeMail()
    mail.sent_items = [_m("s1", "me@company.test", "RE: Blinds for 204", "Yes we have them.\nTaylor",
                          to=["pat@samplepalms.test"], conv_id="c-1"),
                       _m("s2", "me@company.test", "Sample Palms Vertical Blind Quote", "Please price the attached",
                          to=["desk@company.test"], attachment_names=["vb.pdf"])]
    with app.app_context():
        r = A.run(prov=mail)
    assert r["sent_read"] == 2 and r["closed_by_you"] >= 1 and r["crm_logged"] == 1 and r["tracked_from_outlook"] == 1
    assert _row(app, "SELECT reviewed FROM ai_log WHERE id=?", (lid,))["reviewed"] == 1
    a = _row(app, "SELECT * FROM activities WHERE customer_id=? AND kind='Email'", (app.cid,))
    assert a and a["email_id"] and (a["notes"] or "") == ""            # just Email + date, linked to the thread
    o = _row(app, "SELECT * FROM orders WHERE source='outlook'")
    assert o["kind"] == "quote" and o["status"] == "Sent" and o["customer_id"] == app.cid
    with app.app_context():                                             # read once only
        assert A.run(prov=mail)["sent_read"] == 0


def test_thread_page_shows_the_conversation(app):
    c = app.test_client()
    with c.session_transaction():
        pass
    tok = lambda p: re.search(r'name="csrf-token" content="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa
    c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok("/login")})
    a = _row(app, "SELECT * FROM activities WHERE kind='Email' AND email_id IS NOT NULL")
    html = c.get(f"/email/thread?rid={a['email_id']}").get_data(as_text=True)
    assert "Yes we have them." in html and "You sent" in html


# ---------------------------------------------------------------- open requests + follow-ups
def test_open_requests_and_follow_ups(app, A, monkeypatch):
    from orderapp.ai import followup as F
    old = (date.today() - timedelta(days=10)).isoformat() + " 09:00"
    with app.app_context():
        from orderapp.db import x
        tid = x("""INSERT INTO ai_threads(conv_key, sender, subject, customer_id, category, status, created, updated,
                   last_activity) VALUES (?,?,?,?,?,?,?,?,?)""",
                ("k", "pat@samplepalms.test", "screens for 301", app.cid, "order", "waiting", old, old, old))
        lid = x("""INSERT INTO ai_log(ts, msg_id, thread_id, sender, sender_name, subject, received, category, summary,
                   action, status, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (old, "q1", tid, "pat@samplepalms.test", "Pat", "screens for 301", old, "order", "screens", "Asked",
                 "waiting", "[]"))
        x("INSERT INTO ai_outbox(ts, log_id, kind, to_addr, subject, body, status) VALUES (?,?,?,?,?,?,?)",
          (old, lid, "customer", "pat@samplepalms.test", "Re: screens for 301",
           "Thanks!\nWhat's the height of the screen for unit 301?\nThank you", "sent"))
        reqs = F.open_requests()
        mine = next(r for r in reqs if r["ref"] == "thread" and r["id"] == tid)
        assert mine["waiting_on"] == "customer" and mine["overdue"]
        assert any(r["waiting_on"] == "office" for r in reqs)          # sent orders without an invoice #
        mail = FakeMail()
        monkeypatch.setattr("orderapp.emailer.provider", lambda _s: mail)
        F.run_nudges()
        assert mail.sent and "height of the screen for unit 301" in mail.sent[-1]["body"]
        t = _row(app, "SELECT * FROM ai_threads WHERE id=?", (tid,))
        assert t["nudges"] == 1
        x("UPDATE ai_threads SET nudges=2, nudged_at=? WHERE id=?", (old, tid))
        F.run_nudges()                                                   # out of follow-ups: back to you
        assert _row(app, "SELECT status FROM ai_threads WHERE id=?", (tid,))["status"] == "held"


def test_scheduled_email_and_reminder(app, monkeypatch):
    from orderapp.ai import commands as C
    from orderapp.ai import followup as F
    mail = FakeMail()
    monkeypatch.setattr("orderapp.emailer.provider", lambda _s: mail)
    with app.app_context():
        past = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
        r = C.t_schedule_email("crystal@centerline.test", "Door quote", "Here it is", past)
        assert r["sends_at"] == past
        C.t_remind_me("Call Curtis about the door", past, app.cid)
        assert len(F.scheduled_list()) == 2
        assert F.due_scheduled() == 2
        assert mail.sent[-1]["to"] == "crystal@centerline.test"
        rem = _row(app, "SELECT * FROM ai_log WHERE category='reminder'")
        assert rem["status"] == "held" and "Call Curtis" in rem["action"]
        assert F.parse_when("2026-12-01") == "2026-12-01 08:00" and F.parse_when("nonsense") is None


# ---------------------------------------------------------------- other new tools
def test_new_tools(app):
    from orderapp.ai import commands as C
    with app.app_context():
        from orderapp.db import x
        oid = _order(app, subject="Sample Palms Mirror")
        assert C.t_set_invoice(oid, "777777")["invoice_no"] == "777777"
        sid = x("INSERT INTO route_stops(day, customer_id, label, position, status) VALUES (?,?,?,?,?)",
                (date.today().isoformat(), app.cid, "Sample Palms", 0, "Planned"))
        from flask import session  # noqa: F401 - mark_done logs to the CRM (needs a request context)
        with app.test_request_context():
            assert C.t_mark_visit_done(sid, notes="dropped samples")["visited"]
        assert _row(app, "SELECT status FROM route_stops WHERE id=?", (sid,))["status"] == "Done"
        ch = C.t_make_chart("bar", "Test", ["Jan", "Feb"], [{"name": "", "values": [1, 2]}], True)
        assert ch["chart"]["type"] == "bar"
        assert any(r["waiting_on"] == "office" for r in C.t_open_requests())
        assert C.t_search_emails(text="have them")
        names = {t["spec"]["name"] for t in C.TOOLS}
        for n in ("open_requests", "mark_visit_done", "set_invoice", "forward_quote", "schedule_email", "remind_me",
                  "sales_overview", "recommendations", "make_chart", "read_thread"):
            assert n in names


# ---------------------------------------------------------------- formatting + charts
def test_markdown_and_charts():
    from orderapp.ai.render import chart_svg, md
    h = str(md("**Total: $5,000**\n\n- one\n- two\n\n| Acct | $ |\n|---|---|\n| A | 1,200 |\n\n<script>x</script>"))
    assert "<b>Total: $5,000</b>" in h and "<li>one</li>" in h and "<table" in h and "<script>" not in h
    for kind in ("bar", "line", "pie"):
        svg = str(chart_svg({"type": kind, "title": "Sales", "labels": ["Jan", "Feb", "Mar"],
                             "series": [{"name": "2026", "values": [100, 250, 175]}], "money": True}))
        assert svg.startswith("<svg") and "Sales" in svg
    assert "couldn't draw" in str(chart_svg({"type": "bar", "labels": ["a"], "series": [{"values": ["x"]}]}))


def test_chat_shows_formatting_and_chart(app):
    c = app.test_client()
    tok = lambda p: re.search(r'name="csrf-token" content="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa
    c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok("/login")})
    with app.app_context():
        from orderapp.db import x
        x("INSERT INTO ai_chat(ts, role, text, actions) VALUES (?,?,?,?)",
          ("2026-11-16 09:00", "assistant", "Here's your **month**:\n- 3 orders",
           json.dumps([{"tool": "make_chart", "input": {}, "result": {"chart": {
               "type": "bar", "title": "Orders by week", "labels": ["W1"], "series": [{"values": [3]}]}}}])))
    html = c.get("/assistant").get_data(as_text=True)
    assert "<b>month</b>" in html and "Orders by week" in html and "<svg" in html
    assert "Open requests" in html and "Waiting on Jimmy" in html
