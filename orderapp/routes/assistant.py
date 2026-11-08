"""Assistant tab: what the AI email assistant read, did, sent and held for review; the daily summaries."""
import json
import threading
from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from ..db import now, q, set_setting, setting, x
from ..security import login_required

bp = Blueprint("assistant", __name__)
_running = {"on": False}


@bp.route("/assistant")
@login_required
def home():
    from ..ai.client import month_spend
    tab = request.args.get("tab", "review")
    held = q("SELECT * FROM ai_log WHERE status IN ('held','error') AND reviewed=0 ORDER BY id DESC LIMIT 200")
    outbox_for = {}
    for o in q("SELECT * FROM ai_outbox WHERE log_id IN (SELECT id FROM ai_log WHERE status IN ('held','error') "
               "AND reviewed=0) AND status IN ('held','practice','failed') ORDER BY id"):
        outbox_for.setdefault(o["log_id"], []).append(dict(o, files=json.loads(o["attachments"] or "[]")))
    log = q("SELECT * FROM ai_log ORDER BY id DESC LIMIT 300") if tab == "activity" else []
    outbox = [dict(o, files=json.loads(o["attachments"] or "[]"))
              for o in q("SELECT * FROM ai_outbox ORDER BY id DESC LIMIT 300")] if tab == "outbox" else []
    sched = q("SELECT t.*, c.name cname FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id "
              "WHERE t.done=0 AND t.owner IN ('ai','ai-schedule') ORDER BY t.id DESC")
    summ = q("SELECT * FROM ai_summaries ORDER BY id DESC LIMIT 20") if tab == "summaries" else []
    counts = {"review": len(held), "schedule": len(sched)}
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    return render_template("assistant.html", tab=tab, held=held, outbox_for=outbox_for, log=log, outbox=outbox,
                           sched=sched, summ=summ, counts=counts, spend=month_spend(),
                           enabled=setting("ai_enabled") == "1", mode=setting("ai_mode") or "practice",
                           last=setting("ai_last_check") or "", running=_running["on"], loads=json.loads,
                           today=date.today().isoformat(), customers=customers, rules=setting("ai_rules") or "",
                           last_note=request.args.get("note", type=int),
                           last_result=json.loads(setting("ai_last_result") or "{}"))


def _bg(fn):
    app = current_app._get_current_object()

    def work():
        _running["on"] = True
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:  # noqa: BLE001
                pass
            with app.app_context():
                fn()
        finally:
            _running["on"] = False
    threading.Thread(target=work, daemon=True).start()


@bp.route("/assistant/run", methods=["POST"])
@login_required
def run_now():
    if _running["on"]:
        flash("Already checking - give it a minute, then refresh.", "info")
    else:
        from ..ai.assistant import run
        _bg(run)
        flash("Checking email now - refresh in a minute to see what it did.", "ok")
    return redirect(url_for("assistant.home", tab=request.form.get("tab", "review")))


@bp.route("/assistant/summary", methods=["POST"])
@login_required
def summary_now():
    from ..ai.assistant import build_summary
    build_summary("morning" if int(now()[11:13]) < 12 else "evening")
    return redirect(url_for("assistant.home", tab="summaries"))


@bp.route("/assistant/test", methods=["POST"])
@login_required
def test():
    """Step by step: the API key, then reading the inbox, then (optionally) a test email to yourself."""
    from ..ai.client import test_key
    from ..emailer import provider
    ok, msg = test_key()
    flash(("✔ Claude: " if ok else "✘ Claude: ") + msg, "ok" if ok else "error")
    prov = provider(setting)
    if prov.name == "eml":
        flash("✘ Email: not connected - pick Outlook or Microsoft 365 in Settings -> Email.", "error")
    else:
        if hasattr(prov, "diagnose"):
            for line in prov.diagnose():
                flash("Outlook: " + line, "info")
        try:
            msgs = prov.list_messages(days=2, limit=5)
            flash(f"✔ Email: read your inbox - {len(msgs)} email(s) in the last 2 days"
                  + (f", newest: \"{msgs[0]['subject'][:60]}\" from {msgs[0]['sender']}" if msgs else "") + ".", "ok")
        except Exception as e:  # noqa: BLE001
            flash(f"✘ Email: couldn't read the inbox - {e}", "error")
        me = (request.form.get("send_to") or "").strip()
        if me:
            try:
                prov.send(me, "", "AIS Sales Support - test email", "This is a test from the app's AI assistant. "
                          "If you got this, sending works.", [], review=False)
                flash(f"✔ Email: test email sent to {me}.", "ok")
            except Exception as e:  # noqa: BLE001
                flash(f"✘ Email: couldn't send - {e}", "error")
    return redirect(request.referrer or url_for("assistant.home"))


@bp.route("/assistant/outbox/<int:oid>/send", methods=["POST"])
@login_required
def outbox_send(oid):
    from ..ai.assistant import send_outbox
    f = request.form
    if any(k in f for k in ("to_addr", "subject", "body")):
        x("UPDATE ai_outbox SET to_addr=coalesce(?, to_addr), subject=coalesce(?, subject), body=coalesce(?, body) "
          "WHERE id=?", (f.get("to_addr"), f.get("subject"), f.get("body"), oid))
    st = send_outbox(oid)
    flash("Sent." if st == "sent" else "Couldn't send - see the note on it.", "ok" if st == "sent" else "error")
    return redirect(request.referrer or url_for("assistant.home"))


@bp.route("/assistant/outbox/<int:oid>/drop", methods=["POST"])
@login_required
def outbox_drop(oid):
    x("UPDATE ai_outbox SET status='cancelled' WHERE id=?", (oid,))
    return redirect(request.referrer or url_for("assistant.home"))


@bp.route("/assistant/log/<int:lid>/done", methods=["POST"])
@login_required
def log_done(lid):
    x("UPDATE ai_log SET reviewed=1 WHERE id=?", (lid,))
    x("UPDATE ai_outbox SET status='cancelled' WHERE log_id=? AND status IN ('held','practice')", (lid,))
    return redirect(request.referrer or url_for("assistant.home"))


@bp.route("/assistant/task/<int:tid>/schedule", methods=["POST"])
@login_required
def schedule(tid):
    t = q("SELECT * FROM tasks WHERE id=?", (tid,), one=True)
    day = (request.form.get("day") or "").strip()
    if t and day:
        from .routes_plan import add_stop
        title = t["title"].replace("To schedule: ", "")
        try:
            add_stop(day, customer_id=t["customer_id"], label="" if t["customer_id"] else title[:60],
                     purpose=title[:80], source="ai-email", notes=title)
        except Exception:  # noqa: BLE001
            add_stop(day, customer_id=t["customer_id"], label="" if t["customer_id"] else title[:60],
                     purpose=title[:80], source="ai-email", notes=title, smart=False)
        x("UPDATE tasks SET done=1, done_at=? WHERE id=?", (now(), tid))
        flash(f"Added to your route on {day}.", "ok")
    elif t and request.form.get("done"):
        x("UPDATE tasks SET done=1, done_at=? WHERE id=?", (now(), tid))
    return redirect(request.referrer or url_for("assistant.home", tab="schedule"))


@bp.route("/assistant/mode", methods=["POST"])
@login_required
def mode():
    m = request.form.get("mode")
    if m in ("practice", "live"):
        set_setting("ai_mode", m)
        flash("Live: confident emails are sent for you." if m == "live" else
              "Practice mode: everything is done in the app, emails are held.", "ok")
    if request.form.get("enabled") in ("0", "1"):
        set_setting("ai_enabled", request.form["enabled"])
        if request.form["enabled"] == "1" and not setting("ai_started"):
            set_setting("ai_started", now()[:16])   # only emails from now on - not the whole old inbox
    return redirect(request.referrer or url_for("assistant.home"))


@bp.route("/assistant/note", methods=["POST"])
@login_required
def note():
    """A phone call or text, typed in: the assistant handles it like an email from that customer."""
    from ..ai.assistant import note as ai_note
    from ..ai.client import AIError
    text = (request.form.get("text") or "").strip()
    if not text:
        flash("Type what the customer said first.", "error")
        return redirect(url_for("assistant.home"))
    if setting("ai_enabled") != "1":
        flash("Turn the assistant on first (Settings -> AI Assistant).", "error")
        return redirect(url_for("assistant.home"))
    try:
        row = ai_note(text, request.form.get("customer_id", type=int), request.form.get("email", ""),
                      bool(request.form.get("email_customer")))
    except AIError as e:
        flash(f"The assistant couldn't do it: {e}", "error")
        return redirect(url_for("assistant.home"))
    if row:
        flash(f"🤖 {row['action']}", "ok" if row["status"] in ("done", "waiting", "info") else "info")
    return redirect(url_for("assistant.home", tab="review" if row and row["status"] == "held" else "activity",
                            note=row["id"] if row else None))


@bp.route("/assistant/rules", methods=["POST"])
@login_required
def rules():
    set_setting("ai_rules", (request.form.get("rules") or "").strip())
    flash("Rules saved - the assistant follows them from the next email on.", "ok")
    return redirect(url_for("assistant.home", tab="rules"))
