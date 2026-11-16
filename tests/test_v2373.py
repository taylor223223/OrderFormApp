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
