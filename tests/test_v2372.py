"""v2.37.2: the assistant's window on the dashboard. All data is made up."""
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting
        set_setting("ai_enabled", "1")
    return a


def _login(app):
    c = app.test_client()
    tok = lambda p: re.search(r'name="csrf-token" content="([^"]+)"', c.get(p, follow_redirects=True).get_data(as_text=True)).group(1)  # noqa
    c.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456",
                           "csrf_token": tok("/setup")})
    if c.get("/").status_code != 200:
        c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok("/login")})
    return c


def test_dashboard_has_assistant_window(app):
    c = _login(app)
    html = c.get("/").get_data(as_text=True)
    assert 'id="ai"' in html and "Nothing waiting on you" in html and 'name="next" value="dash"' in html
    with app.app_context():
        from orderapp.db import x
        from orderapp.ai.followup import schedule
        x("INSERT INTO ai_chat(ts, role, text) VALUES (?,?,?)", ("2026-11-16 09:00", "me", "how many orders this week"))
        x("INSERT INTO ai_chat(ts, role, text) VALUES (?,?,?)", ("2026-11-16 09:00", "assistant", "You sent **4** orders."))
        x("""INSERT INTO ai_log(ts, msg_id, sender, sender_name, subject, received, category, summary, action, status)
             VALUES ('2026-11-16 08:00','m1','pat@samplepalms.test','Pat Sample','Screens','2026-11-16 08:00','order',
             'Pat wants 2 screens - needs sizes','','held')""")
        schedule("reminder", "2026-12-01 08:00", {"text": "Call the sample office"}, "Call the sample office")
    html = c.get("/").get_data(as_text=True)
    assert "<b>4</b>" in html and "how many orders this week" in html
    assert "Pat wants 2 screens" in html and "1 on you" in html and "Call the sample office" in html


def test_send_from_dashboard_comes_back(app, monkeypatch):
    c = _login(app)
    import orderapp.ai.commands as C
    monkeypatch.setattr(C, "ask", lambda text, email_customer=False: ("ok", []))
    tok = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True)).group(1)
    r = c.post("/assistant/note", data={"text": "hi", "next": "dash", "csrf_token": tok})
    assert r.status_code == 302 and r.headers["Location"].endswith("/#ai")


def test_chat_always_knows_todays_date(app, monkeypatch):
    """Old chat lines are stamped with their day; today's date comes from the clock, never from the chat."""
    from datetime import date, timedelta
    import orderapp.ai.commands as C
    seen = {}

    def fake_call(model, system, msgs, **kw):
        seen["system"], seen["msgs"] = system, [dict(m) for m in msgs]
        return {"content": [{"type": "text", "text": "ok"}]}
    monkeypatch.setattr(C, "call", fake_call)
    with app.app_context():
        from orderapp.db import x
        x("DELETE FROM ai_chat")
        x("INSERT INTO ai_chat(ts, role, text) VALUES (?,?,?)", ("2026-11-13 09:05", "me", "put Sample Palms on today"))
        x("INSERT INTO ai_chat(ts, role, text) VALUES (?,?,?)", ("2026-11-13 09:06", "assistant", "Added to today (Fri)."))
        C.ask("what's on my route today?")
    t = date.today()
    assert f"TODAY IS {t:%A %B %d, %Y} ({t:%Y-%m-%d})" in seen["system"]
    assert f"{t + timedelta(days=1):%a %m/%d}={t + timedelta(days=1):%Y-%m-%d}" in seen["system"]
    assert seen["msgs"][0]["content"].startswith("[sent Fri 11/13 9:05 AM] put Sample Palms")
    assert seen["msgs"][1]["content"].startswith("[sent Fri 11/13 9:06 AM]")
    last = seen["msgs"][-1]["content"]
    assert last.startswith(f"[now: {t:%A %Y-%m-%d}") and "what's on my route today?" in last
    assert "do NOT move, reschedule or change" in seen["system"]
