"""v2.36.0: every email is read on each check (all pages, inbox sub-folders, no 60-email cap) and the check
leaves a per-email summary. Claude and the mailbox are faked; all data is made up."""
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


def _when(mins_ago):
    return (datetime.now() - timedelta(minutes=mins_ago)).strftime("%Y-%m-%d %H:%M")


class FakeMail:
    name = "graph"

    def __init__(self, msgs):
        self.msgs, self.calls = msgs, []

    def available(self):
        return True, ""

    def list_messages(self, days=3, limit=100, all_folders=False, unread_only=False, **kw):
        self.calls.append({"days": days, "limit": limit, "all_folders": all_folders, "unread_only": unread_only})
        got = [m for m in self.msgs if (all_folders or m.get("folder") == "inbox")
               and not (unread_only and m.get("read"))]
        return [{k: v for k, v in m.items() if k != "body"} for m in got][:limit]

    def get_message(self, mid):
        return dict(next(m for m in self.msgs if m["msg_id"] == mid))

    def send(self, *a, **k):
        return "sent"


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting
        for k, v in {"ai_enabled": "1", "ai_api_key": "sk-ant-test", "ai_mode": "practice",
                     "ai_my_email": "me@company.test", "ai_started": "2020-01-01 00:00"}.items():
            set_setting(k, v)
    return a


@pytest.fixture()
def A(app, monkeypatch):
    from orderapp.ai import assistant as A

    def fake_process(m, email_id=None, customer=None):
        cat = "junk" if "newsletter" in m["subject"].lower() else "question"
        A.log(m, email_id, None, {"category": cat, "summary": "Summary of " + m["subject"]},
              "Ignored (junk)" if cat == "junk" else "Answered", "info" if cat == "junk" else "done")
    monkeypatch.setattr(A, "process", fake_process)
    return A


def _m(i, folder="inbox", subject=None, sender=None):
    return {"msg_id": f"id-{folder}-{i}", "sender": sender or f"person{i}@example.test", "sender_name": f"Person {i}",
            "subject": subject or f"Question {i}", "body": "hi", "received": _when(500 - i), "folder": folder,
            "attachments": [], "attachment_names": []}


def test_reads_every_email_no_cap_and_subfolders(app, A):
    msgs = [_m(i) for i in range(150)] + [_m(1, folder="Vendors"), _m(2, folder="News", subject="Weekly newsletter")]
    msgs += [dict(_m(i, folder="Read"), read=True) for i in range(5)]     # already opened in Outlook - left alone
    mail = FakeMail(msgs)
    with app.app_context():
        r = A.run(prov=mail)
    assert mail.calls[0]["all_folders"] is True and mail.calls[0]["limit"] >= 1000
    assert mail.calls[0]["unread_only"] is True
    assert not any(it["subject"].startswith("Question") and "Read" in it.get("email", "") for it in r["items"])
    assert r["new"] == 152 and r["left"] == 0 and len(r["items"]) == 152
    # newest emails are not left behind anymore
    assert any(it["subject"] == "Question 149" for it in r["items"])
    it = next(i for i in r["items"] if i["subject"] == "Question 5")
    assert it["summary"] == "Summary of Question 5" and it["action"] == "Answered" and it["from"] == "Person 5"
    with app.app_context():
        r2 = A.run(prov=mail)
    assert r2["new"] == 0 and r2["seen"] == 152 and r2["items"] == []


def test_lookback_never_shrinks_below_a_week(app, A):
    with app.app_context():
        from orderapp.db import set_setting
        set_setting("ai_lookback_days", "7")
        set_setting("ai_last_check", _when(5))          # checked 5 minutes ago
        assert A._lookback_days() == 7
        set_setting("ai_last_check", (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d %H:%M"))
        assert A._lookback_days() == 14                  # off for 12 days -> reads all 12 (+ overlap)
        set_setting("ai_last_check", (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M"))
        assert A._lookback_days() == 30


def test_old_three_day_setting_becomes_seven(tmp_path):
    a = create_app(db_path=str(tmp_path / "b.db"), testing=True)
    with a.app_context():
        from orderapp.db import setting
        assert setting("ai_lookback_days") == "7"


def test_only_one_check_at_a_time(app, A):
    with app.app_context():
        A._CHECK_LOCK.acquire()
        try:
            mail = FakeMail([_m(900)])
            A.run(prov=mail)
            assert mail.calls == []            # the second check didn't touch the mailbox
        finally:
            A._CHECK_LOCK.release()


def test_assistant_page_shows_the_check_summary(app, A):
    with app.app_context():
        from orderapp.db import set_setting
        set_setting("ai_last_result", json.dumps({
            "when": "2026-11-11 09:00", "read": 3, "new": 2, "seen": 1, "skipped_me": 0, "skipped_old": 0,
            "left": 0, "error": "", "items": [
                {"from": "Pat Sample", "email": "pat@x.test", "subject": "Screens 204", "received": "2026-11-11 08:10",
                 "category": "order", "summary": "Pat wants 2 screens", "action": "Order sent to the desk",
                 "status": "held", "order_ids": ""},
                {"from": "Promo Co", "email": "ads@x.test", "subject": "Sale!", "received": "2026-11-11 08:00",
                 "category": "junk", "summary": "", "action": "Ignored", "status": "info", "order_ids": ""}]}))
    import re
    c = app.test_client()
    tok = lambda p: re.search(r'name="csrf-token" content="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa: E731
    c.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456",
                           "csrf_token": tok("/setup")})
    html = c.get("/assistant").get_data(as_text=True)
    assert "Pat wants 2 screens" in html and "need you" in html and "Junk / newsletters ignored: Promo Co" in html


def test_graph_follows_paging_and_skips_sent_drafts(monkeypatch):
    from orderapp.emailer import GraphMail
    g = GraphMail.__new__(GraphMail)
    pages = {
        "/me/messages": {"value": [{"id": f"a{i}", "parentFolderId": "INBOX", "receivedDateTime": "2026-11-11T15:00:00Z",
                                    "from": {"emailAddress": {"address": "x@y.test"}}} for i in range(100)]
                         + [], "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skip=100"},
        "https://graph.microsoft.com/v1.0/me/messages?$skip=100": {"value": [
            {"id": "b1", "parentFolderId": "VENDORS", "receivedDateTime": "2026-11-11T14:00:00Z", "from": {}},
            {"id": "s1", "parentFolderId": "SENT", "receivedDateTime": "2026-11-11T13:00:00Z", "from": {}}]},
    }

    def req(method, path, **kw):
        if path.startswith("/me/mailFolders/"):
            name = path.rsplit("/", 1)[1]
            return {"id": {"sentitems": "SENT", "drafts": "DRAFTS"}.get(name, "OTHER-" + name)}
        return pages[path]
    monkeypatch.setattr(g, "_req", req, raising=False)
    out = g.list_messages(days=7, limit=2000, all_folders=True)
    ids = [m["msg_id"] for m in out]
    assert len(ids) == 101 and "b1" in ids and "s1" not in ids


# ---------------- 2.36.0: reply to any email + condensed, formatted summaries
class ReplyMail(FakeMail):
    def __init__(self):
        super().__init__([])
        self.replies, self.sent = [], []

    def reply(self, msg_id, body):
        if msg_id == "gone":
            raise RuntimeError("item moved")
        self.replies.append((msg_id, body))
        return "Reply sent."

    def send(self, to, cc, subject, body, attachments, review=True):
        self.sent.append((to, subject, body, review))
        return "Sent."


def test_reply_to_any_email_in_thread_and_fallback(app, A, monkeypatch):
    import orderapp.emailer as E
    mail = ReplyMail()
    monkeypatch.setattr(E, "provider", lambda _s: mail)
    with app.app_context():
        from orderapp.db import q
        lid = A.log({"msg_id": "m-r1", "sender": "pat@x.test", "sender_name": "Pat", "subject": "Blinds?"}, None, None,
                    {"category": "question", "summary": "asks about blinds"}, "Answered", "held")
        assert A.reply_to(lid, "Yes - we carry them.\nTaylor") == "Reply sent."
        assert mail.replies == [("m-r1", "Yes - we carry them.\nTaylor")]
        assert q("SELECT reviewed FROM ai_log WHERE id=?", (lid,), one=True)["reviewed"] == 1
        o = q("SELECT * FROM ai_outbox WHERE log_id=? ORDER BY id DESC", (lid,), one=True)
        assert o["kind"] == "reply" and o["status"] == "sent" and o["subject"] == "Re: Blinds?"
        lid2 = A.log({"msg_id": "gone", "sender": "sam@x.test", "sender_name": "Sam", "subject": "RE: Door"}, None,
                     None, {"category": "question"}, "Answered", "done")
        A.reply_to(lid2, "Thanks!", mark_done=False)                # original moved -> a normal email instead
        assert mail.sent[-1][:2] == ("sam@x.test", "RE: Door") and mail.sent[-1][3] is False
        with pytest.raises(Exception):
            A.reply_to(lid2, "   ")


def test_draft_reply_uses_the_email(app, A, monkeypatch):
    seen = {}

    def fake_ask(model, system, prompt, max_tokens=0, purpose=""):
        seen["prompt"] = prompt
        return "  Hi Pat - yes, we can do that.\n\nThank you,\nTaylor  "
    monkeypatch.setattr(A, "ask", fake_ask)
    with app.app_context():
        from orderapp.db import x
        eid = x("INSERT INTO email_messages(provider, msg_id, subject, sender, body) VALUES ('graph','m-d1','Need screens','pat@x.test','Can you make 4 screens?')")
        lid = A.log({"msg_id": "m-d1", "sender": "pat@x.test", "sender_name": "Pat", "subject": "Need screens"}, eid,
                    None, {"category": "question"}, "Asked for sizes", "waiting")
        assert A.draft_reply(lid).startswith("Hi Pat")
    assert "Can you make 4 screens?" in seen["prompt"] and "Asked for sizes" in seen["prompt"]


def test_summary_is_condensed_and_formatted(app, A):
    from orderapp.routes.assistant import summary_parts
    body = ("End of day - everyone who reached out.\n\nAt a glance\n  - 9 email(s) read from 4 people\n  - 1 order(s)\n\n"
            "Needs you (1)\n  - Pat: wants screens -> no height given\n\n"
            "Who reached out and what your assistant did\n" +
            "\n".join(f"  - Person {i} (09:0{i}): question {i} -> Answered [done]" for i in range(9)))
    P = summary_parts(body)
    assert P["head"].startswith("End of day") and P["glance"][0] == "9 email(s) read from 4 people"
    who = P["sections"][1]
    assert who["items"][0] == {"who": "Person 0 (09:00)", "what": "question 0", "did": "Answered", "status": "done"}
    assert A._short("x" * 200, 80).endswith("…") and len(A._short("x" * 200, 80)) == 80
    import re
    with app.app_context():
        from orderapp.db import x
        x("INSERT INTO ai_summaries(day, period, body, created) VALUES ('2026-11-11','evening',?, '2026-11-11 17:00')", (body,))
    c = app.test_client()
    tok = lambda p: re.search(r'name="csrf-token" content="([^"]+)"', c.get(p).get_data(as_text=True)).group(1)  # noqa: E731
    page = c.get("/assistant?tab=summaries")
    if page.status_code != 200:
        c.post("/login", data={"username": "tester", "password": "pw-123456", "csrf_token": tok("/login")})
    html = c.get("/assistant?tab=summaries").get_data(as_text=True)
    assert "Show 3 more" in html and "<b>Pat</b>" in html and "9 email(s) read from 4 people" in html
    html = c.get("/assistant?tab=activity").get_data(as_text=True)
    assert "Let the assistant write it" in html
