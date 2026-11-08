"""v2.33.0: the AI email assistant - Claude is faked here (no API calls). All data is made up."""
import json
import os
import re
import tempfile
from datetime import date, timedelta

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


class FakeMail:
    name = "graph"

    def __init__(self):
        self.inbox, self.sent = [], []

    def available(self):
        return True, ""

    def list_messages(self, days=3, limit=100, **kw):
        return [{k: v for k, v in m.items() if k != "body"} for m in self.inbox]

    def get_message(self, mid):
        return dict(next(m for m in self.inbox if m["msg_id"] == mid))

    def send(self, to, cc, subject, body, attachments, review=True):
        self.sent.append({"to": to, "subject": subject, "body": body, "attachments": list(attachments)})
        return "sent"


class FakeClaude:
    """classify -> self.cls; extract -> self.read (Sonnet) / self.read2 (Haiku, defaults to the same)."""

    def __init__(self):
        self.cls, self.read, self.read2, self.calls = {}, {}, None, []

    def ask_json(self, model, system, prompt, max_tokens=0, purpose=""):
        self.calls.append(purpose)
        if purpose == "sort":
            return dict(self.cls)
        if "Haiku" in purpose and self.read2 is not None:
            return json.loads(json.dumps(self.read2))
        return json.loads(json.dumps(self.read))

    def ask(self, *a, **k):
        return "Thanks - I'll check and get right back to you."


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    cat = os.path.join(tempfile.mkdtemp(), "catalog.pdf")
    import pymupdf
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Sample catalog (made up)")
    d.save(cat)
    with a.app_context():
        from orderapp.db import set_setting, x
        a.cid = x("INSERT INTO customers(name, address, city, state, zip, status, email) VALUES "
                  "('Sample Palms (made up)', '1 Test Rd', 'Phoenix', 'AZ', '85020', 'Active', 'office@samplepalms.test')")
        x("INSERT INTO contacts(customer_id, name, role, email, phone) VALUES (?,?,?,?,?)",
          (a.cid, "Pat Sample", "Maint. Super", "pat@samplepalms.test", "602-555-0100"))
        for k, v in {"ai_enabled": "1", "ai_api_key": "sk-test", "ai_mode": "practice", "order_to": "desk@company.test",
                     "ai_catalog_path": cat, "ai_test_senders": "tester@example.test",
                     "ai_practice_to": "me@company.test", "ai_coworker_domain": "company.test",
                     "ai_my_email": "me@company.test"}.items():
            set_setting(k, v)
    return a


@pytest.fixture()
def env(app, monkeypatch):
    from orderapp.ai import assistant as A
    mail, claude = FakeMail(), FakeClaude()
    monkeypatch.setattr(A, "ask_json", claude.ask_json)
    monkeypatch.setattr(A, "ask", claude.ask)
    return app, A, mail, claude


def _msg(mid, sender, subject, body, names=None):
    return {"msg_id": mid, "sender": sender, "sender_name": sender.split("@")[0], "subject": subject, "body": body,
            "received": date.today().isoformat() + " 08:00", "attachments": [], "attachment_names": names or []}


ORDER_READ = {"kind": "order", "customer_name": "Sample Palms (made up)", "contact_name": "Pat Sample",
              "contact_phone": "602-555-0100", "contact_email": "pat@samplepalms.test", "po": "SP-1", "unit": "204",
              "is_reorder": False, "reorder_confirmed": False, "reorder_match": "",
              "forms": [{"form_key": "window_screen", "lines": [{"qty": "2", "unit": "204", "width": "35",
                                                                 "height": "48", "color": "Bronze"}]}],
              "missing": [], "reply_to_customer": "Got it - 2 bronze screens for unit 204. Catalog attached.\n\nThank you,\nTaylor",
              "notes_for_rep": "", "certain": True}


def _q(app, sql, args=()):
    with app.app_context():
        from orderapp.db import q
        return [dict(r) for r in q(sql, args)]


def test_confident_order_practice_mode(env):
    app, A, mail, claude = env
    claude.cls = {"category": "order", "summary": "Pat wants 2 screens", "forms": ["window_screen"]}
    claude.read = ORDER_READ
    mail.inbox = [_msg("m1", "pat@samplepalms.test", "Screens unit 204", "2 bronze screens 35 x 48 unit 204 PO SP-1")]
    with app.app_context():
        r = A.run(prov=mail)
    assert r["new"] == 1 and "read order (Sonnet)" in claude.calls and "cross-check (Haiku)" in claude.calls
    o = _q(app, "SELECT * FROM orders WHERE source='ai'")[-1]
    assert o["form_key"] == "window_screen" and o["kind"] == "order" and o["po"] == "SP-1" and o["pdf_path"]
    # practice: the order-desk email went to my own address, marked PRACTICE; the customer reply was held
    assert [s["to"] for s in mail.sent] == ["me@company.test"] and mail.sent[0]["subject"].startswith("[PRACTICE]")
    out = _q(app, "SELECT * FROM ai_outbox ORDER BY id")
    cust = [x for x in out if x["kind"] == "customer"][-1]
    assert cust["status"] == "practice" and "catalog" in cust["attachments"].lower()
    assert _q(app, "SELECT status FROM ai_log ORDER BY id DESC LIMIT 1")[0]["status"] == "done"
    # read once only
    with app.app_context():
        assert A.run(prov=mail)["new"] == 0


def test_missing_info_asks_then_finishes(env):
    app, A, mail, claude = env
    claude.cls = {"category": "order", "summary": "screens", "forms": ["window_screen"]}
    claude.read = dict(ORDER_READ, contact_phone="", forms=[{"form_key": "window_screen", "lines": [
        {"qty": "1", "unit": "301", "width": "35", "height": ""}]}],
        missing=["What's the height of the screen for unit 301?"], certain=False,
        reply_to_customer="Thanks! What's the height of the screen, and a good phone number?")
    mail.inbox = [_msg("m2", "tester@example.test", "Screen 301", "one screen 35 wide for 301")]
    with app.app_context():
        A.run(prov=mail)
    sent = mail.sent[-1]
    assert sent["to"] == "tester@example.test" and sent["subject"] == "Re: Screen 301"       # a test address: really sent
    assert any(p.endswith(".pdf") for p in sent["attachments"])                              # the catalog
    t = _q(app, "SELECT * FROM ai_threads WHERE status='waiting'")
    assert len(t) == 1 and "Contact phone number" in t[0]["reason"]
    # the customer answers in the same thread -> confident -> sent in
    claude.cls = {"category": "other", "summary": "reply with height", "forms": []}
    claude.read = dict(ORDER_READ, forms=[{"form_key": "window_screen", "lines": [
        {"qty": "1", "unit": "301", "width": "35", "height": "60"}]}])
    mail.inbox.append(_msg("m3", "tester@example.test", "RE: Screen 301", "60 tall. Pat 602-555-0100"))
    with app.app_context():
        A.run(prov=mail)
    assert _q(app, "SELECT status FROM ai_threads WHERE id=?", (t[0]["id"],))[0]["status"] == "done"
    last = _q(app, "SELECT * FROM orders WHERE source='ai' ORDER BY id DESC LIMIT 1")[0]
    assert '"height": "60"' in last["data"]
    assert len(_q(app, "SELECT * FROM orders WHERE source='ai' AND notes LIKE 'AI: waiting%'")) == 0   # old draft gone


def test_disagreement_and_photos_are_held(env):
    app, A, mail, claude = env
    claude.cls = {"category": "order", "summary": "screens", "forms": ["window_screen"]}
    claude.read = ORDER_READ
    claude.read2 = dict(ORDER_READ, forms=[{"form_key": "window_screen", "lines": [
        {"qty": "2", "unit": "204", "width": "53", "height": "48"}]}])
    mail.inbox = [_msg("m4", "pat@samplepalms.test", "Screens", "2 screens 35x48")]
    with app.app_context():
        A.run(prov=mail)
    h = _q(app, "SELECT * FROM ai_log WHERE msg_id='m4'")[0]
    assert h["status"] == "held" and "disagree" in h["reasons"] and "width" in h["reasons"]
    claude.read2 = None
    mail.inbox = [_msg("m5", "pat@samplepalms.test", "Doors", "see photo", names=["IMG_2041.jpg", "image001.png"])]
    with app.app_context():
        A.run(prov=mail)
    h = _q(app, "SELECT * FROM ai_log WHERE msg_id='m5'")[0]
    assert h["status"] == "held" and "Photos attached" in h["reasons"]
    assert A.photo_names(["image001.png", "Outlook-abc.png"]) == []                  # signature logos aren't photos


def test_reorder_needs_confirmation(env):
    app, A, mail, claude = env
    claude.cls = {"category": "reorder", "summary": "same as last time", "forms": ["window_screen"]}
    claude.read = dict(ORDER_READ, is_reorder=True, reorder_confirmed=False, reorder_match="Order #1: 2 screens 35x48",
                       reply_to_customer="Is this the order you mean: 2 bronze screens 35 x 48 for unit 204?")
    mail.inbox = [_msg("m6", "pat@samplepalms.test", "Reorder screens", "same as last time please")]
    with app.app_context():
        A.run(prov=mail)
    h = _q(app, "SELECT * FROM ai_log WHERE msg_id='m6'")[0]
    assert h["status"] == "waiting" and "Confirm the past order" in h["reasons"]


def test_coworker_visit_request_goes_on_the_route(env):
    app, A, mail, claude = env
    claude.cls = {"category": "visit_request", "summary": "Kelli: fill out a form at Sample Palms",
                  "property": "Sample Palms (made up)", "date": TOMORROW, "task": "Fill out the vendor form",
                  "reply": "Got it - I'll stop by tomorrow and fill it out.", "forms": []}
    mail.inbox = [_msg("m7", "kelli@company.test", "Sample Palms form", "Can you go fill out their vendor form tomorrow?")]
    with app.app_context():
        A.run(prov=mail)
    s = _q(app, "SELECT * FROM route_stops WHERE source='ai-email'")
    assert s and s[-1]["day"] == TOMORROW and "vendor form" in s[-1]["notes"] and s[-1]["customer_id"] == app.cid
    ack = _q(app, "SELECT * FROM ai_outbox WHERE kind='coworker' ORDER BY id DESC LIMIT 1")[0]
    assert ack["status"] == "practice" and ack["to_addr"] == "kelli@company.test"


def test_measure_request_without_a_day_is_to_schedule(env, app):
    _, A, mail, claude = env
    claude.cls = {"category": "measure_request", "summary": "Pat wants blinds measured", "property": "",
                  "date": "", "reply": "Happy to - I'll reach out to set a time.", "forms": []}
    mail.inbox = [_msg("m8", "pat@samplepalms.test", "Measure blinds", "can you come measure 10 units")]
    with app.app_context():
        A.run(prov=mail)
    t = _q(app, "SELECT * FROM tasks WHERE owner='ai-schedule'")
    assert t and "Measure" in t[-1]["title"]
    c = app.test_client()
    tok = lambda p: re.search(r'name="csrf-token" content="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa: E731
    c.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456",
                           "csrf_token": tok("/setup")})
    page = c.get("/routes/day/" + TOMORROW).get_data(as_text=True)
    assert "Visits to schedule" in page
    for tab in ("review", "schedule", "activity", "outbox", "summaries"):
        assert c.get(f"/assistant?tab={tab}").status_code == 200
    assert "AI Assistant" in c.get("/settings").get_data(as_text=True)
    assert "Assistant" in c.get("/").get_data(as_text=True).split("<nav>")[1].split("</nav>")[0]


def test_live_mode_sends_to_the_order_desk(env):
    app, A, mail, claude = env
    with app.app_context():
        from orderapp.db import set_setting
        set_setting("ai_mode", "live")
    claude.cls = {"category": "quote", "summary": "quote for screens", "forms": ["window_screen"]}
    claude.read = dict(ORDER_READ, kind="quote", po="SP-9")
    mail.inbox = [_msg("m9", "pat@samplepalms.test", "Quote screens", "please quote 2 screens 35x48")]
    try:
        with app.app_context():
            A.run(prov=mail)
    finally:
        with app.app_context():
            set_setting("ai_mode", "practice")
    to = [s["to"] for s in mail.sent]
    assert to == ["desk@company.test", "pat@samplepalms.test"]
    assert "price" in mail.sent[0]["body"].lower()
    o = _q(app, "SELECT * FROM orders WHERE po='SP-9'")[0]
    assert (o["kind"], o["status"]) == ("quote", "Sent")
    h = json.loads(o["data"])["header"]
    assert h["quote_email"] == "pat@samplepalms.test" and h["contact"] == "Pat Sample"


def test_summary(env):
    app, A, mail, claude = env
    with app.app_context():
        body = A.build_summary("evening")
    assert "End of day" in body and "Needs you" in body and "Done for you" in body


def test_quote_back_from_office_goes_to_customer(env):
    """2.33.1: a priced quote from the office is sent straight to the customer (no hold) -> Quote sent."""
    app, A, mail, claude = env
    o = _q(app, "SELECT * FROM orders WHERE po='SP-9'")[0]        # the quote request from the live-mode test
    with app.app_context():
        from orderapp.db import x
        x("UPDATE orders SET data=json_set(data, '$.header.quote_email', 'tester@example.test') WHERE id=?", (o["id"],))
    claude.cls = {"category": "quote_returned", "summary": "Office: pricing for Sample Palms", "po": "SP-9",
                  "property": "Sample Palms (made up)", "forms": []}
    m = _msg("m10", "sally@company.test", "Quote SP-9", "Here's the quote.")
    m["attachments"] = [("Quote SP-9.pdf", b"%PDF-1.4 fake")]
    m["attachment_names"] = ["Quote SP-9.pdf"]
    mail.inbox = [m]
    with app.app_context():
        A.run(prov=mail)
    sent = mail.sent[-1]
    assert sent["to"] == "tester@example.test" and sent["subject"].startswith("Your quote")
    assert any("Quote received" in p for p in sent["attachments"]) and len(sent["attachments"]) == 2
    assert _q(app, "SELECT status FROM orders WHERE id=?", (o["id"],))[0]["status"] == "Quoted"
    assert _q(app, "SELECT status FROM ai_log WHERE msg_id='m10'")[0]["status"] == "done"


def test_reorder_prompt_matches_on_sizes_not_latest(app):
    from orderapp.ai import assistant as A
    assert "Never just take the most recent order" in A.EXTRACT_SYSTEM
    with app.app_context():
        from orderapp.db import setting
        assert "Nicolette - collections" in setting("ai_coworkers")


def test_api_key_box_ignores_autofill(app):
    """2.33.2: a browser-filled password never replaces the API key; the box is never pre-filled."""
    c = app.test_client()
    page = c.get("/settings")
    if page.status_code != 200:
        tok = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok})
    html = c.get("/settings").get_data(as_text=True)
    box = re.search(r'<input name="ai_api_key"[^>]*>', html).group(0)
    assert 'type="text"' in box and 'value=""' in box and "sk-test" not in html
    tok = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    c.post("/settings", data={"csrf_token": tok, "ai_coworker_domain": "company.test", "ai_api_key": "MyWindowsPassw0rd"})
    with app.app_context():
        from orderapp.db import setting
        assert setting("ai_api_key") == "sk-test"
    c.post("/settings", data={"csrf_token": tok, "ai_coworker_domain": "company.test", "ai_api_key": " sk-ant-abc123 "})
    with app.app_context():
        from orderapp.db import set_setting, setting
        assert setting("ai_api_key") == "sk-ant-abc123"
        set_setting("ai_api_key", "sk-test")
        for k in ("ai_auto_orders", "ai_auto_customer", "ai_auto_coworker"):   # the bare posts above unticked them
            set_setting(k, "1")


def test_settings_shows_the_saved_key_and_switches(app):
    """2.33.3: the AI section shows what's really saved (key hint, on/off, practice/live)."""
    c = app.test_client()
    tok = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
    c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok})
    html = c.get("/settings").get_data(as_text=True)
    assert "saved: ••••test" in html and "<b>On</b>" in html and "Practice mode" in html


def _pdf_bytes(text):
    import pymupdf
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), text)
    return d.tobytes()


def test_project_quotes_come_back_as_one(env):
    """2.33.4: a project quoted on several forms waits for every returned quote, then sends ONE combined PDF."""
    app, A, mail, claude = env
    with app.app_context():
        from orderapp.db import set_setting
        set_setting("ai_mode", "live")
    try:
        claude.cls = {"category": "quote", "summary": "quote whole floor plan", "forms": ["countertop", "trim"]}
        claude.read = dict(ORDER_READ, kind="quote", po="PROJ-1", contact_email="tester@example.test", forms=[
            {"form_key": "countertop", "lines": [{"qty": "1", "length": "96", "depth": "25-1/2"}]},
            {"form_key": "trim", "lines": [{"qty": "10", "length": "96"}]},
            {"form_key": "window_screen", "lines": [{"qty": "4", "width": "35", "height": "48"}]}])
        mail.inbox = [_msg("p1", "tester@example.test", "Quote floor plan B", "please quote countertop, trim, screens")]
        with app.app_context():
            A.run(prov=mail)
        qs = _q(app, "SELECT * FROM orders WHERE po='PROJ-1' ORDER BY id")
        assert len(qs) == 3 and len({o["quote_group"] for o in qs}) == 1 and all(o["status"] == "Sent" for o in qs), [(o["status"], o["form_key"]) for o in qs]
        # the office sends the countertop quote alone -> waits
        claude.cls = {"category": "quote_returned", "po": "PROJ-1", "forms": ["countertop"], "summary": "ct quote"}
        m = _msg("p2", "sally@company.test", "PROJ-1 countertop", "ct quote attached")
        m["attachments"], m["attachment_names"] = [("ct.pdf", _pdf_bytes("Countertop $500"))], ["ct.pdf"]
        n_sent = len(mail.sent)
        mail.inbox = [m]
        with app.app_context():
            A.run(prov=mail)
        assert len(mail.sent) == n_sent                                   # nothing to the customer yet
        assert "waiting on 2 more" in _q(app, "SELECT action FROM ai_log WHERE msg_id='p2'")[0]["action"]
        # then the other two in one email -> one combined quote goes out, all three Quoted
        claude.cls = {"category": "quote_returned", "po": "PROJ-1", "forms": [], "summary": "rest of quotes"}
        m = _msg("p3", "sally@company.test", "PROJ-1 trim + screens", "attached")
        m["attachments"] = [("trim.pdf", _pdf_bytes("Trim $90")), ("scr.pdf", _pdf_bytes("Screens $120"))]
        m["attachment_names"] = ["trim.pdf", "scr.pdf"]
        mail.inbox = [m]
        with app.app_context():
            A.run(prov=mail)
        sent = mail.sent[-1]
        assert sent["to"] == "tester@example.test" and "all in one document" in sent["body"]
        combined = [p for p in sent["attachments"] if "Combined quote" in p]
        assert len(combined) == 1
        import pymupdf
        txt = "".join(pg.get_text() for pg in pymupdf.open(combined[0]))
        assert all(s in txt for s in ("This quote includes", "Countertop $500", "Trim $90", "Screens $120"))
        assert {o["status"] for o in _q(app, "SELECT status FROM orders WHERE po='PROJ-1'")} == {"Quoted"}
    finally:
        with app.app_context():
            set_setting("ai_mode", "practice")


def test_ai_saves_contacts_and_sizes_to_the_app(env):
    """2.33.5: a new person who emails an order is added to the customer's contacts; a different phone for someone
    on file goes to Review (never overwritten); the order's sizes land on the unit."""
    app, A, mail, claude = env
    claude.cls = {"category": "order", "summary": "Jo orders screens", "forms": ["window_screen"]}
    claude.read = dict(ORDER_READ, contact_name="Jo Newperson", contact_phone="480-555-0199",
                       contact_email="jo@samplepalms.test", po="SP-20", unit="512",
                       forms=[{"form_key": "window_screen", "lines": [{"qty": "1", "unit": "512", "width": "29",
                                                                        "height": "57"}]}])
    mail.inbox = [_msg("s1", "jo@samplepalms.test", "Screen 512", "1 screen 29x57 for 512, Jo 480-555-0199")]
    with app.app_context():
        A.run(prov=mail)
    k = _q(app, "SELECT * FROM contacts WHERE email='jo@samplepalms.test'")
    assert len(k) == 1 and k[0]["name"] == "Jo Newperson" and k[0]["phone"] == "480-555-0199"
    assert "added Jo Newperson" in _q(app, "SELECT action FROM ai_log WHERE msg_id='s1'")[0]["action"]
    assert _q(app, """SELECT 1 FROM measurements m JOIN units u ON u.id=m.unit_id
                      WHERE u.unit_number='512' AND m.customer_id=?""", (app.cid,))
    # Pat (on file) writes in with a new phone -> not overwritten, goes to Review
    claude.read = dict(ORDER_READ, contact_phone="623-555-0177", po="SP-21")
    mail.inbox = [_msg("s2", "pat@samplepalms.test", "Screens again", "same thing, call me at 623-555-0177")]
    with app.app_context():
        A.run(prov=mail)
    assert _q(app, "SELECT phone FROM contacts WHERE email='pat@samplepalms.test'")[0]["phone"] == "602-555-0100"
    ch = _q(app, "SELECT * FROM changes WHERE kind='new_contact' AND status='pending'")
    assert ch and "623-555-0177" in ch[-1]["new_value"]
