"""v2.37.3: cheaper chat - prompt caching, shorter tool answers, quick asks on the fast model. Made-up data only."""
import os
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
        set_setting("ai_api_key", "sk-ant-test")
    return a


class Resp:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_chat_call_is_cached_and_priced_right(app, monkeypatch):
    import orderapp.ai.client as CL
    sent = []

    def fake_post(body, key, version):
        sent.append(body)
        return Resp({"content": [{"type": "text", "text": "ok"}], "usage": {
            "input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 9000,
            "output_tokens": 200}})
    monkeypatch.setattr(CL, "_post", fake_post)
    msgs = [{"role": "user", "content": "hi"}]
    tools = [{"name": "a", "input_schema": {}}, {"name": "b", "input_schema": {}}]
    with app.app_context():
        CL.call("claude-sonnet-5-5", "SYSTEM", msgs, tools=tools, purpose="chat")
        from orderapp.db import q
        row = q("SELECT * FROM ai_usage ORDER BY id DESC LIMIT 1", one=True)
    b = sent[0]
    assert b["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert b["tools"][-1]["cache_control"] and "cache_control" not in b["tools"][0]
    assert b["messages"][-1]["content"][-1]["cache_control"]
    assert msgs == [{"role": "user", "content": "hi"}] and "cache_control" not in tools[-1]   # caller's lists untouched
    # 1000 full + 9000 cached at a tenth + 200 out, at Sonnet (2, 10) per million
    assert row["in_tok"] == 10000 and abs(row["cost"] - (1000 * 2 + 9000 * 0.2 + 200 * 10) / 1e6) < 1e-9


def test_quick_asks_use_the_fast_model(app):
    from orderapp.ai.commands import _brief, pick_model
    with app.app_context():
        from orderapp.db import set_setting
        assert "haiku" in pick_model("remind me Friday to call Pat at Sample Palms")
        assert "haiku" in pick_model("log a visit at Sample Palms")
        assert "sonnet" in pick_model("Pat called, reorder the 2 screen doors for 204")
        assert "sonnet" in pick_model("add Sample Palms to Wednesday and also email Crystal the quote")
        assert "sonnet" in pick_model("chart my sales by month")
        set_setting("ai_chat_cheap", "0")
        assert "sonnet" in pick_model("log a visit at Sample Palms")
        set_setting("ai_chat_cheap", "1")
    assert len(_brief({"rows": ["x" * 100] * 200})) < 5100 and _brief({"a": 1}) == '{"a":1}'


def test_waiting_on_jimmy_can_be_cleared(app):
    """Old sent orders without an invoice # don't pile up; Done / Clear all / the chat take them off the list."""
    from datetime import datetime, timedelta
    from orderapp.ai.commands import t_close_requests
    from orderapp.ai.followup import close_request, open_requests
    old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M")
    new = datetime.now().strftime("%Y-%m-%d %H:%M")
    with app.app_context():
        from orderapp.db import x
        ids = {}
        for k, when in (("old", old), ("a", new), ("b", new), ("c", new)):
            ids[k] = x("INSERT INTO orders(form_key, title, status, kind, sent_at, created, updated) VALUES "
                       "('door', ?, 'Sent', 'order', ?, ?, ?)", (f"Sample {k}", when, when, when))
        office = lambda: [r["id"] for r in open_requests() if r["waiting_on"] == "office"]  # noqa: E731
        assert ids["old"] not in office() and {ids["a"], ids["b"], ids["c"]} <= set(office())
        close_request("order", ids["a"])
        assert ids["a"] not in office()
        assert t_close_requests(ids=[ids["b"]]) == {"cleared": 1} and ids["b"] not in office()
        assert t_close_requests(waiting_on="office")["cleared"] >= 1 and office() == []
        from orderapp.db import q
        assert q("SELECT status FROM orders WHERE id=?", (ids["c"],), one=True)["status"] == "Sent"   # order untouched


def test_bulk_mark_delivered_in_one_step(app):
    """'mark them all delivered, if an invoice is needed use 1s' - one tool call does every order."""
    from orderapp.ai.commands import t_bulk_update_orders
    with app.app_context():
        from orderapp.db import q, x
        made = [x("INSERT INTO orders(form_key, title, status, kind, sent_at, created) VALUES "
                  "('door', 'Sample bulk', ?, 'order', '2026-10-01 09:00', '2026-10-01 09:00')", (st,))
                for st in ("Sent", "Confirmed", "Sent", "Draft")]
        quote = x("INSERT INTO orders(form_key, title, status, kind, sent_at) VALUES ('door','Sample q','Sent','quote',"
                  "'2026-10-01 09:00')")
        r = t_bulk_update_orders("Delivered", sent_before="2026-11-01", invoice_if_missing="111111")
        got = {o["id"]: o for o in q("SELECT * FROM orders")}
        for oid in made[:3]:
            assert got[oid]["status"] == "Delivered"
        assert got[made[0]]["invoice_no"] == "111111" and got[made[3]]["status"] == "Draft"
        assert got[quote]["status"] == "Sent" and r["updated"] >= 3


def test_tracking_cleanup_button(app):
    import re as _re
    c = app.test_client()
    tok = lambda p: _re.search(r'name="csrf-token" content="([^"]+)"', c.get(p, follow_redirects=True).get_data(as_text=True)).group(1)  # noqa
    c.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456", "csrf_token": tok("/setup")})
    if c.get("/tracking").status_code != 200:
        c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok("/login")})
    assert "Clean up past orders" in c.get("/tracking").get_data(as_text=True)
    with app.app_context():
        from orderapp.db import x
        oid = x("INSERT INTO orders(form_key, title, status, kind, sent_at) VALUES ('door','Sample t','Sent','order',"
                "'2026-09-01 09:00')")
    r = c.post("/tracking/bulk_delivered", data={"before": "2026-10-01", "invoice": "", "csrf_token": tok("/tracking")})
    assert r.status_code == 302
    with app.app_context():
        from orderapp.db import q
        assert q("SELECT status FROM orders WHERE id=?", (oid,), one=True)["status"] == "Delivered"


def test_a_broken_page_says_why_and_logs_it():
    from flask import Blueprint
    app = create_app(db_path=os.path.join(tempfile.mkdtemp(), "b.db"), testing=True)
    bp = Blueprint("boom", __name__)

    @bp.route("/boom-test")
    def boom():
        raise ValueError("sample failure")
    app.register_blueprint(bp)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    c = app.test_client()
    r = c.get("/boom-test")
    assert r.status_code == 500 and "ValueError: sample failure" in r.get_data(as_text=True)
    log = os.path.join(os.path.dirname(app.config["DB_PATH"]), "crash.log")
    assert "sample failure" in open(log, encoding="utf-8").read()


def test_cut_off_chat_record_no_longer_breaks_the_assistant_page(app):
    """A huge request used to save a cut-off record that crashed every Assistant page."""
    import json as _j
    import re as _re
    from orderapp.ai.commands import actions_json, load_actions
    big = [{"tool": "set_order_step", "input": {"order_id": i, "step": "Delivered"}, "result": {"x": "y" * 900}}
           for i in range(200)]
    saved = actions_json(big)
    assert len(saved) <= 20000 and isinstance(_j.loads(saved), list)
    assert load_actions('[{"tool": "x", "inp') == [] and load_actions(None) == []
    with app.app_context():
        from orderapp.db import x
        x("INSERT INTO ai_chat(ts, role, text, actions) VALUES (?,?,?,?)",
          ("2026-11-15 20:00", "assistant", "Marked them.", '[{"tool": "set_order_step", "input": {"order_id": 1'))
    c = app.test_client()
    tok = lambda p: _re.search(r'name="csrf-token" content="([^"]+)"', c.get(p, follow_redirects=True).get_data(as_text=True)).group(1)  # noqa
    c.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456", "csrf_token": tok("/setup")})
    if c.get("/assistant").status_code != 200:
        c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok("/login")})
    for page in ("/assistant", "/assistant?tab=requests", "/assistant?tab=activity", "/"):
        assert c.get(page).status_code == 200, page
