"""v2.37.7: things you handled in Outlook come off 'Waiting on you'; Sent by you on the Email tab; Back from a
thread goes back where you were. Made-up data only."""
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


@pytest.fixture()
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting
        set_setting("ai_my_email", "me@company.test")
        set_setting("order_to", "desk@company.test")
    return a


def _held(app, sender, subject, conv, received="2026-11-20 09:00", msg="m-in"):
    from orderapp.db import x
    eid = x("INSERT INTO email_messages(msg_id, subject, sender, received, direction, conv_id) VALUES (?,?,?,?,?,?)",
            (msg, subject, sender, received, "in", conv))
    return x("""INSERT INTO ai_log(ts, msg_id, email_id, sender, sender_name, subject, received, category, summary,
                status) VALUES (?,?,?,?,?,?,?,?,?,?)""",
             (received, msg, eid, sender, "Pat Sample", subject, received, "question", "Pat asked about screens", "held"))


def _sent(subject, to, conv, when, msg="m-out"):
    from orderapp.db import x
    return x("INSERT INTO email_messages(msg_id, subject, sender, received, direction, conv_id, to_addr) VALUES "
             "(?,?,?,?,?,?,?)", (msg, subject, "me@company.test", when, "out", conv, to))


def _row(lid):
    from orderapp.db import q
    return q("SELECT * FROM ai_log WHERE id=?", (lid,), one=True)


def test_forward_or_reply_to_anyone_in_the_thread_closes_it(app):
    from orderapp.ai.followup import apply_sent, open_requests
    with app.app_context():
        lid = _held(app, "pat@samplepalms.test", "Screens for 204", "CONV-1")
        assert any(r["id"] == lid for r in open_requests() if r["waiting_on"] == "you")
        m = {"subject": "FW: Screens for 204", "conv_id": "CONV-1", "received": "2026-11-20 10:00"}
        eid = _sent(m["subject"], "desk@company.test", "CONV-1", m["received"])
        apply_sent(m, eid, ["desk@company.test"])
        r = _row(lid)
        assert r["reviewed"] == 1 and "You replied 11/20" in r["auto_closed"] and "desk@company.test" in r["auto_closed"]


def test_reply_sent_before_the_app_saw_the_email_still_closes_it(app):
    from orderapp.ai.followup import recheck_open
    with app.app_context():
        _sent("RE: Blinds unit 12", "pat@samplepalms.test", "CONV-2", "2026-11-20 09:30")
        lid = _held(app, "pat@samplepalms.test", "Blinds unit 12", "CONV-2", received="2026-11-20 09:00", msg="m2")
        res = recheck_open(None)
        assert res["closed"] == 1 and _row(lid)["reviewed"] == 1
        # a reply from BEFORE the email came in doesn't count
        lid2 = _held(app, "pat@samplepalms.test", "Blinds unit 14", "CONV-3", received="2026-11-21 09:00", msg="m3")
        _sent("RE: Blinds unit 14", "pat@samplepalms.test", "CONV-3", "2026-11-19 09:00", msg="old")
        recheck_open(None)
        assert _row(lid2)["reviewed"] == 0


class Prov:
    def __init__(self, states):
        self.states = states

    def message_state(self, msg_id):
        return self.states.get(msg_id, {})


def test_outlook_done_signals_and_read_tag(app):
    from orderapp.ai.followup import open_requests, recheck_open
    with app.app_context():
        from orderapp.db import set_setting
        ids = {k: _held(app, f"{k}@x.test", f"Subject {k}", None, received="2026-10-01 09:00", msg=k) for k in ("flag", "del", "arch", "read", "open")}
        prov = Prov({"flag": {"done": True}, "del": {"deleted": True}, "arch": {"archived": True},
                     "read": {"read": True}, "open": {}})
        res = recheck_open(prov)
        assert res["closed"] == 3
        assert _row(ids["flag"])["auto_closed"] == "Flagged Complete in Outlook"
        assert _row(ids["del"])["auto_closed"] == "Deleted or moved in Outlook"
        assert _row(ids["read"])["reviewed"] == 0 and _row(ids["read"])["mail_read"] == 1
        reqs = {r["id"]: r for r in open_requests()}
        assert reqs[ids["read"]]["read"] is True and reqs[ids["open"]]["read"] is False
        set_setting("ai_clear_read_days", "1")         # optional: read + old -> cleared
        recheck_open(prov)
        assert _row(ids["read"])["reviewed"] == 1 and "Read in Outlook" in _row(ids["read"])["auto_closed"]
        set_setting("ai_outlook_done", "0")
        lid = _held(app, "z@x.test", "Subject z", None, msg="z")
        recheck_open(Prov({"z": {"done": True}}))
        assert _row(lid)["reviewed"] == 0


def test_outlook_hiccup_never_clears_everything(app):
    from orderapp.ai.followup import recheck_open
    with app.app_context():
        ids = [_held(app, f"p{i}@x.test", f"S{i}", None, msg=f"g{i}") for i in range(6)]
        assert recheck_open(Prov({f"g{i}": {"gone": True} for i in range(6)}))["closed"] == 0
        assert all(_row(i)["reviewed"] == 0 for i in ids)


def _login(app):
    c = app.test_client()
    tok = lambda p: re.search(r'name="csrf-token" content="([^"]+)"', c.get(p, follow_redirects=True).get_data(as_text=True)).group(1)  # noqa
    c.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456", "csrf_token": tok("/setup")})
    if c.get("/email").status_code != 200:
        c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok("/login")})
    return c


def test_email_tab_shows_sent_and_thread_back_goes_back(app):
    c = _login(app)
    with app.app_context():
        rid = _sent("RE: Sample Palms doors", "pat@samplepalms.test", "CONV-9", "2026-11-20 11:00")
    html = c.get("/email").get_data(as_text=True)
    assert "Sent by you" in html and "RE: Sample Palms doors" in html
    page = c.get(f"/email/thread?rid={rid}", headers={"Referer": "http://localhost/assistant?tab=requests"})
    assert 'href="/assistant?tab=requests"' in page.get_data(as_text=True)
    page = c.get(f"/email/thread?rid={rid}")
    assert 'href="/email"' in page.get_data(as_text=True)
