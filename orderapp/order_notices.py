"""Order-received notices: emails saying an order came in (order received / confirmation / acknowledgement).

Each one is matched to an open order by PO # or sales order # (same matching as the morning delivery list) and
that order moves forward to "Confirmed". A sales order # in the email is saved on the order when it has none.
Name-only matches are never automatic - they're listed for you to confirm. Never sends anything.
"""
import re
from datetime import datetime

from . import deliveries as DL
from .db import add_status, dumps, now, q, setting, x

TARGET = "Confirmed"
# how far along an order is - an email never moves an order backwards
RANK = {"Draft": 0, "Ready": 1, "Sent": 2, "Confirmed": 3, "Backordered": 4, "Shipped": 5, "Delivered": 6,
        "Installed": 7, "Completed": 8}
_SO = re.compile(r"(?:sales\s*order|order\s*(?:no|number|#)|s\.?o\.?)\s*[#:.]?\s*([A-Z]{0,3}-?\d{4,})", re.I)

DEFAULT_WORDS = "Order Received, Order Confirmation, Order Acknowledgement, Acknowledgment, Order Entered"
# a short reply ("got it") to an order you emailed counts as the order being received
DEFAULT_REPLY_WORDS = "got it, got this, received, recieved, will do, on it, entered, placed, confirmed, done, thanks, thank you, ok, okay, 10-4, sounds good"
# anything that sounds like a problem or a question waits for you instead
HOLD_WORDS = ("?", "missing", "need ", "needs ", "wrong", "can't", "cannot", "can not", "don't", "dont ", "isn't",
              "question", "issue", "problem", "backorder", "back order", "discontinued", "unable", "not ", "no longer",
              "clarify", "confirm the", "which ")
_RE = re.compile(r"^\s*((re|aw|antw)\s*:\s*)+", re.I)
_QUOTE = re.compile(r"^\s*(-{2,}\s*original message|from:\s|sent:\s|on .{4,80} wrote:|_{5,}|>)", re.I)


def words():
    raw = setting("notice_subject_words")
    raw = DEFAULT_WORDS if raw is None else raw
    return [w.strip() for w in raw.split(",") if w.strip()]


def is_notice(subject):
    s = (subject or "").lower()
    return any(w.lower() in s for w in words())


def reply_on():
    return (setting("reply_confirm") or "1") == "1"


def reply_words():
    raw = setting("reply_confirm_words")
    raw = DEFAULT_REPLY_WORDS if raw is None else raw
    return [w.strip().lower() for w in raw.split(",") if w.strip()]


def is_reply(subject):
    return bool(_RE.match(subject or ""))


def strip_re(subject):
    return _RE.sub("", subject or "").strip()


def reply_top(body):
    """Only the new part of a reply - stop at the quoted original."""
    out = []
    for ln in (body or "").splitlines():
        if _QUOTE.match(ln):
            break
        out.append(ln)
    return "\n".join(out).strip()


def _has_word(text, w):
    return re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text) is not None


def reply_verdict(body):
    """('yes', why) = clear "got it"; ('check', why) = a reply that needs you; ('no', '') = not an answer."""
    top = " ".join(reply_top(body).split())
    low = top.lower()
    if not low:
        return "no", ""
    hold = next((h.strip() for h in HOLD_WORDS if h in low + " "), None)
    hit = next((w for w in reply_words() if _has_word(low, w)), None)
    if hit and not hold and len(low) <= 300:
        return "yes", top[:140]
    if hit or hold:
        return "check", top[:140]
    return "no", ""


def _norm_subj(s):
    return re.sub(r"[^a-z0-9]+", " ", strip_re(s).lower()).strip()


def match_reply(subject):
    """Open orders this reply is about: the subject you sent it with, else the PO # in the subject."""
    want = _norm_subj(subject)
    if not want:
        return []
    from .routes.orders import _fmt
    from .db import loads
    tmpl = setting("subject_template") or ""
    found = []
    for o in DL.candidates():
        subs = [o["sent_subject"] or ""] if "sent_subject" in o.keys() else []
        try:
            subs.append(_fmt(tmpl, o, loads(o["data"], {})))
        except Exception:  # noqa: BLE001
            pass
        if any(_norm_subj(sb) == want for sb in subs if sb):
            found.append({"order_id": o["id"], "cname": o["cname"] or "", "po": o["po"] or "",
                          "vendor_ref": o["vendor_ref"] or "", "status": o["status"], "title": o["title"],
                          "how": "Reply to your order email", "sure": True, "line": strip_re(subject)[:140]})
    if found:
        return found
    res = [r for r in DL.match(strip_re(subject)) if r["sure"]]
    for r in res:
        r["how"] = "Reply (PO # in subject)"
    return res


def record_reply(full, msg_id=None, auto_apply=True):
    """A reply to an order email. Returns (check_id or None, results, applied)."""
    verdict, why = reply_verdict(full.get("body", ""))
    if verdict == "no":
        return None, [], 0
    res = match_reply(full.get("subject", ""))
    if not res:
        return None, [], 0
    for r in res:
        r["line"] = (why or r["line"])[:140]
        if verdict != "yes":
            r["sure"] = False
            r["how"] += " - reply needs a look"
    applied = apply(res, "") if (auto_apply and verdict == "yes") else 0
    cid = x("""INSERT INTO delivery_checks(msg_id, source, subject, received, body, results, applied, created, kind)
               VALUES (?,?,?,?,?,?,?,?,'received')""",
            (msg_id, "reply" + (f" from {full.get('sender_name') or full.get('sender')}" if full.get("sender") or
                                full.get("sender_name") else ""), full.get("subject", ""), full.get("received", ""),
             (full.get("body") or "")[:200000], dumps(res),
             dumps([r["order_id"] for r in res if r["sure"]] if applied else []), now()))
    return cid, res, applied


def apply(results, text=""):
    """Move the sure matches to Confirmed (only forward). Returns how many moved."""
    so = None
    m = _SO.search(text or "")
    if m:
        so = m.group(1).upper()
    n = 0
    for r in results:
        if not r["sure"]:
            continue
        o = q("SELECT status, vendor_ref FROM orders WHERE id=?", (r["order_id"],), one=True)
        if not o or o["status"] in ("On Hold", "Cancelled"):
            continue
        if so and not (o["vendor_ref"] or "").strip() and r["how"] == "PO #":
            x("UPDATE orders SET vendor_ref=?, updated=? WHERE id=?", (so, now(), r["order_id"]))
        if RANK.get(o["status"], 99) >= RANK[TARGET]:
            continue
        x("UPDATE orders SET status=?, updated=? WHERE id=?", (TARGET, now(), r["order_id"]))
        note = "Reply: " + r["line"][:60] if r.get("how", "").startswith("Reply") else "Order-received email"
        add_status(r["order_id"], TARGET, f"{note} {datetime.now():%m/%d/%Y}" + (f" (SO {so})" if so else ""))
        n += 1
    return n


def record(text, source, subject="", received="", msg_id=None, auto_apply=True):
    res = DL.match(text)
    applied = apply(res, text) if auto_apply else 0
    cid = x("""INSERT INTO delivery_checks(msg_id, source, subject, received, body, results, applied, created, kind)
               VALUES (?,?,?,?,?,?,?,?,'received')""",
            (msg_id, source, subject, received, (text or "")[:200000], dumps(res),
             dumps([r["order_id"] for r in res if r["sure"]] if auto_apply else []), now()))
    return cid, res, applied


def check_email(prov=None, days=3):
    """Read recent order-received emails from Outlook and confirm the orders they name."""
    from .emailer import provider
    prov = prov or provider(setting)
    out = {"found": 0, "new": 0, "matched": 0, "applied": 0, "error": ""}
    if not words() and not reply_on():
        return out
    if prov.name == "eml":
        out["error"] = "Email isn't connected (Settings -> Email)."
        return out
    ok, why = prov.available()
    if not ok:
        out["error"] = why
        return out
    seen = {r["msg_id"] for r in q("SELECT msg_id FROM delivery_checks WHERE msg_id IS NOT NULL")}
    auto = (setting("notice_auto") or "1") == "1"
    msgs = []
    reply_ids = set()
    if reply_on():
        try:
            for m in prov.list_messages(days=days, limit=60, subject_word="re:"):
                if is_reply(m.get("subject")) and m["msg_id"] not in seen:
                    reply_ids.add(m["msg_id"])
                    msgs.append(m)
        except Exception as e:  # noqa: BLE001
            out["error"] = f"Couldn't read the inbox: {e}"
            return out
    for w in words():
        try:
            msgs += prov.list_messages(days=days, limit=30, subject_word=w)
        except Exception as e:  # noqa: BLE001
            out["error"] = f"Couldn't read the inbox: {e}"
            return out
    uniq = {m["msg_id"]: m for m in msgs}
    out["found"] = len(uniq)
    for mid, m in uniq.items():
        if mid in seen:
            continue
        try:
            full = prov.get_message(mid)
        except Exception:  # noqa: BLE001
            continue
        if mid in reply_ids and not is_notice(full.get("subject")):
            cid, res, applied = record_reply(full, mid, auto)
            if cid:
                out["new"] += 1
                out["matched"] += len(res)
                out["applied"] += applied
            else:   # not about an order - remember it so it isn't read again
                x("""INSERT INTO delivery_checks(msg_id, source, subject, received, body, results, applied, created,
                     kind) VALUES (?,?,?,?,?,?,?,?,'skipped')""", (mid, "reply", full.get("subject", ""),
                                                                   full.get("received", ""), "", "[]", "[]", now()))
            continue
        text = (full.get("subject") or "") + "\n" + (full.get("body") or "") + "\n" + \
            DL.attachment_text(full.get("attachments"))
        _, res, applied = record(text, "email", full.get("subject", ""), full.get("received", ""), mid, auto)
        out["new"] += 1
        out["matched"] += len(res)
        out["applied"] += applied
    return out
