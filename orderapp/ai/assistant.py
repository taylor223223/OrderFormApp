"""The AI email assistant.

Every new email in the inbox:
  1. Haiku sorts it (customer order / quote / reorder / measure or walk request / question / quote approval,
     coworker task or visit request, quote returned from the office, confirmations, FYI, junk).
  2. Orders and quotes: Sonnet AND Haiku each read it on their own against the order form for the product, the
     customer's record (contacts, units) and their past orders. The app then checks the result itself.
  3. Confident (both models agree, nothing missing, customer known, order/quote known, contact name + phone,
     no photos, not an unconfirmed reorder) -> the order is made in the app, the PDF goes to the order desk and
     the customer gets a confirmation. Anything missing -> the customer is asked for exactly that. Anything else
     -> held for Taylor with the reason.
  4. Measure / walk requests and coworker tasks -> route stops (or "to schedule"), tasks, and a short reply.
Every reply to a customer has the catalog attached. Practice mode does all the app work but holds every email
(except replies to the test addresses, and order-desk emails redirected to the practice address).
"""
import json
import os
import re
from datetime import date, datetime, timedelta

from ..catalog import FORM_ORDER, FORMS, ORDER_SETTINGS, editor_fields
from ..db import add_status, dumps, find_customer, loads, now, q, set_setting, setting, x
from .client import AIError, AITransient, ask, ask_json

SIG_IMAGE = re.compile(r"^(image\d{3}|outlook-[\w-]+|logo[\w-]*)\.(png|jpe?g|gif|bmp)$", re.I)
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff", ".webp")

CUSTOMER_CATS = {"order", "quote", "order_or_quote", "reorder", "measure_request", "walk_request", "question",
                 "quote_approval", "quote_declined", "other"}
COWORKER_CATS = {"coworker_task", "coworker_question", "visit_request", "quote_returned", "order_confirmation", "delivery_notice", "fyi"}
ORDERISH = {"order", "quote", "order_or_quote", "reorder"}


# ------------------------------------------------------------------ small helpers
def fast():
    return setting("ai_model_fast") or "claude-haiku-5-5"


def smart():
    return setting("ai_model_smart") or "claude-sonnet-5-5"


def practice():
    return (setting("ai_mode") or "practice") != "live"


def _addr_list(s):
    return [a.strip().lower() for a in re.split(r"[,;\s]+", s or "") if "@" in a]


def _norm_subject(s):
    s = (s or "").strip()
    while True:
        t = re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "", s, flags=re.I)
        if t == s:
            return s.lower()
        s = t


def conv_key(sender, subject):
    return f"{(sender or '').strip().lower()}|{_norm_subject(subject)}"


def is_coworker(sender):
    s = (sender or "").lower()
    dom = (setting("ai_coworker_domain") or "").strip().lower().lstrip("@")
    if dom and s.endswith("@" + dom):
        return True
    return s in _addr_list(setting("ai_coworkers"))


def is_me(sender):
    s = (sender or "").lower()
    return bool(s) and s in _addr_list(setting("ai_my_email"))


def photo_names(names):
    return [n for n in names or [] if n.lower().endswith(PHOTO_EXT) and not SIG_IMAGE.match(n)]


def signature():
    return (setting("ai_signature") or setting("sales_rep") or "").strip()


def catalog_file():
    """The catalog PDF to attach to customer replies (a compressed copy if the original is big)."""
    p = (setting("ai_catalog_path") or "").strip().strip('"')
    if not p or not os.path.exists(p):
        return None
    if os.path.getsize(p) <= 6 * 1024 * 1024:
        return p
    from ..paths import data_dir
    small = os.path.join(data_dir(), "catalog-email.pdf")
    if os.path.exists(small) and os.path.getmtime(small) >= os.path.getmtime(p):
        return small
    try:
        import pymupdf as fitz
        doc = fitz.open(p)
        try:
            doc.rewrite_images(dpi_threshold=110, dpi_target=100, quality=65)
        except Exception:  # noqa: BLE001 - older PyMuPDF: just re-save compressed
            pass
        doc.save(small, garbage=4, deflate=True)
        doc.close()
        return small if os.path.getsize(small) < os.path.getsize(p) else p
    except Exception:  # noqa: BLE001
        return p


def _num(v):
    s = str(v or "").replace('"', "").strip()
    try:
        if re.match(r"^\d+(\.\d+)?$", s):
            return round(float(s), 3)
        m = re.match(r"^(\d+)\s*[- ]\s*(\d+)/(\d+)$", s)
        if m:
            return round(int(m.group(1)) + int(m.group(2)) / int(m.group(3)), 3)
    except (ValueError, ZeroDivisionError):
        pass
    return s.lower()


# ------------------------------------------------------------------ what Claude is told about the forms
def form_menu():
    return "\n".join(f"- {k}: {FORMS[k]['title']}" for k in FORM_ORDER if k in FORMS)


def _visible(fd, line):
    cond = fd.get("show_if")
    if not cond:
        return True
    k, allowed = cond
    v = str(line.get(k) or "").strip().lower()
    return v in [str(a).lower() for a in allowed] if v else "" in allowed


# the sizes a line can't go without (any one group is enough). Hinge spots, bore sizes etc. are optional.
REQUIRED = {"vertical_blind": [("width", "height"), ("om_headrail", "om_slat")],
            "closet_bifold": [("width", "height"), ("opening_width", "opening_height")],
            "roller_shade": [("width", "length")], "trim": [("length",)], "countertop": [("length", "depth")],
            "drawer_box": [("width", "height", "depth")], "mirror_frame": [("mirror_length", "mirror_height")],
            "door_hardware": [()], "bath_hardware": [()]}
# forms the assistant never finishes alone (drawn layouts)
ALWAYS_REVIEW = {k for k, f in FORMS.items() if f.get("layout_editor")}


def required_groups(form_key):
    if form_key in REQUIRED:
        return REQUIRED[form_key]
    keys = {fd["key"] for fd in editor_fields(form_key)}
    return [("width", "height")] if {"width", "height"} <= keys else [()]


def missing_sizes(form_key, line):
    """[] if one of the form's size groups is filled in, else the first group's keys."""
    groups = required_groups(form_key)
    for g in groups:
        if all(str(line.get(k) or "").strip() for k in g):
            return []
    return list(groups[0])


def measure_keys(form_key):
    return sorted({k for g in required_groups(form_key) for k in g})


# how Taylor's customers write sizes for a product, and what's assumed unless they say otherwise
FORM_NOTES = {
    "drawer_box": ('"A x B" = width (side to side) x depth (front to back): "11x18" = width 11, depth 18. "3 deep", '
                   '"3 tall", "3 high" or a third number = the box height (side height). Sizes are the box size as '
                   'given (Box outside size) - never sized to fit an opening. Slides are Side mount unless they say '
                   'otherwise. Never ask about side thickness, corners, bottom, slide length, soft close or notch & '
                   'bore - leave them blank unless the customer says.'),
    "roller_shade": '"A x B" = width x length.',
    "countertop": '"A x B" = length x depth.',
}
FORM_DEFAULTS = {"drawer_box": {"db_measure": "Box outside size", "db_slide": "Side mount"}}


def apply_defaults(r):
    """Fill the assumed values (FORM_DEFAULTS) on every line that left them blank."""
    for f in r.get("forms") or []:
        for ln in f.get("lines") or []:
            for k, v in FORM_DEFAULTS.get(f.get("form_key"), {}).items():
                if not str(ln.get(k) or "").strip():
                    ln[k] = v
    return r


def form_spec_text(form_key):
    out = [f"FORM {form_key} - {FORMS[form_key]['title']}", "Line fields (key | label | type | choices):"]
    for fd in editor_fields(form_key):
        if fd["kind"] in ("lines",):
            continue
        opts = ""
        if fd["kind"] == "choice":
            opts = " | " + ", ".join(str(o[0]) for o in fd["options"] if o[0])[:400]
        req = " | REQUIRED" if fd["key"] in measure_keys(form_key) or fd["key"] == "qty" else ""
        cond = f" | only when {fd['show_if'][0]} is {'/'.join(map(str, fd['show_if'][1]))}" if fd.get("show_if") else ""
        out.append(f"  {fd['key']} | {fd['label']} | {fd['kind']}{opts}{req}{cond}")
    if FORM_NOTES.get(form_key):
        out.append("  NOTES: " + FORM_NOTES[form_key])
    if ORDER_SETTINGS.get(form_key):
        out.append(f"  (usually the same for every line at a property: {', '.join(ORDER_SETTINGS[form_key])})")
    return "\n".join(out)


def customer_facts(c):
    """What the app knows about the customer, for Claude: contacts, units, past orders and purchases."""
    if not c:
        return "Customer: NOT FOUND in the app (new or unknown customer)."
    lines = [f"Customer in the app: id {c['id']} - {c['name']} - {c['address'] or ''} {c['city'] or ''} - "
             f"phone {c['phone'] or '-'} - email {c['email'] or '-'}"]
    for k in q("SELECT name, role, email, phone FROM contacts WHERE customer_id=? LIMIT 15", (c["id"],)):
        lines.append(f"  contact: {k['name'] or ''} ({k['role'] or ''}) email {k['email'] or '-'} phone {k['phone'] or '-'}")
    units = [u["unit_number"] for u in q("SELECT unit_number FROM units WHERE customer_id=? LIMIT 60", (c["id"],))]
    if units:
        lines.append("  units on file: " + ", ".join(units))
    for o in q("""SELECT id, title, po, order_date, kind, status, data FROM orders WHERE customer_id=?
                  ORDER BY id DESC LIMIT 40""", (c["id"],)):
        d = loads(o["data"]) or {}
        items = "; ".join(", ".join(f"{k}={v}" for k, v in b.items() if v and k in
                                    ("qty", "width", "height", "length", "color", "style", "unit", "room"))
                          for b in (d.get("blocks") or [])[:12])
        lines.append(f"  app order #{o['id']} {o['order_date'] or ''} {o['title']} PO {o['po'] or '-'} "
                     f"({o['kind'] or 'order'}, {o['status']}): {items[:600]}")
    try:
        from ..order_insights import purchases
        for p in purchases(c["id"])[:150]:
            lines.append(f"  invoice {p.get('invoice') or ''} {p.get('date') or ''}: {p.get('qty') or ''} x "
                         f"{(p.get('desc') or '')[:90]}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


# ------------------------------------------------------------------ step 1: sort the email
CLASSIFY_SYSTEM = """You sort emails for {me}, a sales rep for a supplier of doors, blinds, screens, cabinets,
countertops and other apartment interior products. Customers are apartment properties (managers, maintenance
supervisors). Reply with ONE JSON object only.

Coworkers (internal staff):
{coworkers}

Categories - customers: order, quote, order_or_quote (wants product but unclear if order or quote), reorder
(same as / repeat of a past order), measure_request (wants the rep to come measure), walk_request (wants the rep
to walk the property / come by / meet), question, quote_approval (approves a quote we sent), quote_declined, other.
Coworkers: coworker_task (asks the rep to do something), coworker_question (asks the rep a question he has to
answer, e.g. "is this built-in glass?", "who is closest to X?"), visit_request (asks the rep to visit a property, e.g. to
fill out a document), quote_returned (the office sends back pricing for a quote request), order_confirmation,
delivery_notice, fyi. Anyone: junk (ads, newsletters, automated).

Order forms (key: title):
{forms}

JSON keys: category, summary (one line, who + what), needs_reply (bool), property (property / customer name
mentioned, or ""), date (YYYY-MM-DD if a day is given or clearly implied, else ""), time (or ""), task (what
{me} has to do, short, or ""), forms (list of form keys for the products wanted, [] if none), po (PO # or ""),
urgent (bool), reply (for coworker_task / visit_request / measure_request / walk_request only: a short, friendly
reply from {me} confirming he got it and will take care of it - plain text, no signature; else "")."""


def rules_text():
    """Taylor's own rules, typed in the app (Assistant -> Rules) - added to every instruction Claude gets."""
    r = (setting("ai_rules") or "").strip()
    return f"\n\n{signature() or 'THE REP'}'S OWN RULES - always follow these, they override anything above:\n{r}" if r else ""


_NOTE = {"on": False, "email_customer": False}   # set while a phone / text note typed in the app is handled


def classify(m):
    me = signature() or "the rep"
    system = CLASSIFY_SYSTEM.format(me=me, coworkers=setting("ai_coworkers") or "-", forms=form_menu()) + rules_text()
    today = date.today()
    prompt = (f"Today is {today:%A %Y-%m-%d}.\nFrom: {m.get('sender_name') or ''} <{m.get('sender') or ''}>"
              f"{' (COWORKER)' if is_coworker(m.get('sender')) else ''}\nSubject: {m.get('subject') or ''}\n"
              f"Attachments: {', '.join(m.get('attachment_names') or []) or 'none'}\n\n{(m.get('body') or '')[:6000]}")
    r = ask_json(fast(), system, prompt, max_tokens=900, purpose="sort")
    r["category"] = str(r.get("category") or "other").strip().lower()
    r["forms"] = [f for f in (r.get("forms") or []) if f in FORMS]
    return r


# ------------------------------------------------------------------ step 2: read the order (both models)
EXTRACT_SYSTEM = """You turn a customer's email into order form lines for {me} (sales rep). Be exact: only use what
the customer wrote or what the app's records clearly confirm. Never invent sizes, colors or options. Reply with ONE
JSON object only.

Rules:
- Sizes are inches (convert feet: 6' = 72). "A x B" is ALWAYS width first, then height (or length / depth - see the
  form's notes). Never ask the customer which number is which.
- ONLY ask for: order or quote (if not said), a contact name and phone (if not given and not on file), and the
  quantity and REQUIRED sizes. NEVER ask about optional choices (thickness, corners, bottom, hardware, finishes the
  form doesn't mark REQUIRED, etc.) - leave them blank or use the defaults in the form's notes. Ask each thing ONCE.
- If the customer refers to a past order ("same as last time", "reorder", "like unit 204", "the 34 and 36 inch
  screens we got before"), search ALL the app records below for the order / invoice lines that match what they
  describe - the product AND any sizes, unit, color or date they mention. Never just take the most recent order:
  a match must fit every detail they gave. If several fit, list them and ask which one. Fill the lines from it ONLY
  if the customer already confirmed that exact order in this email thread; otherwise set is_reorder true, describe
  the match (date, invoice/order #, items and sizes) in reorder_match, and ask them to confirm it (or ask what
  product they mean if nothing matches).
- kind: "order" or "quote" if the customer said so; "unknown" if not clear (then ask).
- Contact: we need a contact name AND phone number for every order and quote. Use the email/signature, else a
  contact on file. For a quote we also need an email for the quote (use the sender or one on file). Only ask for
  what is still missing.
- missing: only the REQUIRED things above that are still missing, one short question each, no repeats. Don't ask
  for things that are on file or in the email.
- reply_to_customer: the email body to send the customer, from {me}, friendly and short. If anything is missing
  or unclear, ask for exactly that (and ask order or quote if unknown). If nothing is missing, confirm what will be
  ordered/quoted (list the lines). Plain text, end with "Thank you," and the name {me}. Mention that the product
  catalog is attached.
- certain: true only if every line and option is directly stated (or confirmed) and nothing is missing.

JSON keys: kind, customer_name, contact_name, contact_phone, contact_email, po, unit, is_reorder (bool),
reorder_confirmed (bool), reorder_match (text or ""), forms (list of {{"form_key", "lines": [{{field key: value}}],
"comments"}}), missing (list of strings), reply_to_customer, notes_for_rep (anything odd), certain (bool)."""


def thread_text(thread_id, current):
    parts = []
    if thread_id:
        for e in q("""SELECT e.sender, e.received, e.body FROM ai_log l JOIN email_messages e ON e.id=l.email_id
                      WHERE l.thread_id=? ORDER BY l.id""", (thread_id,)):
            parts.append(f"--- {e['received'] or ''} from {e['sender'] or ''}:\n{(e['body'] or '')[:4000]}")
        for o in q("SELECT ts, to_addr, body FROM ai_outbox WHERE log_id IN (SELECT id FROM ai_log WHERE thread_id=?) "
                   "AND kind='customer' ORDER BY id", (thread_id,)):
            parts.append(f"--- {o['ts']} WE REPLIED to {o['to_addr']}:\n{(o['body'] or '')[:2000]}")
    parts.append(f"--- NEW EMAIL from {current.get('sender') or ''}:\nSubject: {current.get('subject') or ''}\n"
                 f"{(current.get('body') or '')[:6000]}")
    return "\n\n".join(parts)


def extract(model, m, c, form_keys, thread_id, purpose):
    me = signature() or "the rep"
    keys = form_keys or []
    specs = "\n\n".join(form_spec_text(k) for k in keys) if keys else \
        "(product not clear yet - available forms:)\n" + form_menu()
    prompt = (f"Today is {date.today():%Y-%m-%d}.\n\nAPP RECORDS:\n{customer_facts(c)}\n\nORDER FORMS:\n{specs}\n\n"
              f"EMAIL THREAD (oldest first):\n{thread_text(thread_id, m)}")
    r = ask_json(model, EXTRACT_SYSTEM.format(me=me) + rules_text(), prompt, max_tokens=4000, purpose=purpose)
    r["forms"] = [f for f in (r.get("forms") or []) if isinstance(f, dict) and f.get("form_key") in FORMS]
    r["kind"] = str(r.get("kind") or "unknown").lower()
    return apply_defaults(r)


def compare(a, b):
    """Differences between the two models' reads (empty list = they agree)."""
    diffs = []
    fa = {f["form_key"]: f for f in a.get("forms") or []}
    fb = {f["form_key"]: f for f in b.get("forms") or []}
    if set(fa) != set(fb):
        return [f"The two AI reads picked different forms ({', '.join(fa) or 'none'} vs {', '.join(fb) or 'none'})."]
    if (a.get("kind") or "") != (b.get("kind") or ""):
        diffs.append(f"Order vs quote read differently ({a.get('kind')} vs {b.get('kind')}).")
    for k, f in fa.items():
        la, lb = f.get("lines") or [], fb[k].get("lines") or []
        if len(la) != len(lb):
            diffs.append(f"{FORMS[k]['title']}: {len(la)} lines vs {len(lb)} lines.")
            continue
        strict = ["qty", "unit"] + measure_keys(k)
        choices = {fd["key"] for fd in editor_fields(k) if fd["kind"] == "choice"}
        for i, (x1, x2) in enumerate(zip(la, lb), 1):
            for key in strict:
                if _num(x1.get(key)) != _num(x2.get(key)):
                    diffs.append(f"{FORMS[k]['title']} line {i} {key}: {x1.get(key)!r} vs {x2.get(key)!r}.")
            for key in choices:
                v1, v2 = str(x1.get(key) or "").strip().lower(), str(x2.get(key) or "").strip().lower()
                if v1 and v2 and v1 != v2:
                    diffs.append(f"{FORMS[k]['title']} line {i} {key}: {x1.get(key)!r} vs {x2.get(key)!r}.")
    return diffs


def app_checks(r, c, m):
    """The app's own checks on the (Sonnet) read. Returns (ask_customer, hold_reasons)."""
    ask_customer, hold = [], []
    asked = " ".join(str(s) for s in r.get("missing") or []).lower()

    def need(item, *words):          # the app's own item, unless the AI's questions already cover it
        if not any(w in asked for w in words):
            ask_customer.append(item)
    if photo_names(m.get("attachment_names")):
        hold.append("Photos attached - check them yourself.")
    if not c:
        hold.append("Customer isn't in the app (new account? they'll need the New Account Set-Up form).")
    if r.get("kind") not in ("order", "quote"):
        need("Order or quote?", "quote")
    if r.get("is_reorder") and not r.get("reorder_confirmed"):
        need("Confirm the past order / product they mean.", "is this", "confirm", "which one", "the one")
    if not str(r.get("contact_name") or "").strip():
        need("Contact name", "name")
    if not str(r.get("contact_phone") or "").strip():
        need("Contact phone number", "phone", "number to reach", "call")
    if r.get("kind") == "quote" and not str(r.get("contact_email") or m.get("sender") or "").strip():
        need("Email for the quote", "email")
    if not r.get("forms"):
        need("Which product they need", "product", "what ")
    for f in r.get("forms") or []:
        k = f["form_key"]
        if not f.get("lines"):
            ask_customer.append(f"{FORMS[k]['title']}: the items / sizes")
        if k in ALWAYS_REVIEW:
            hold.append(f"{FORMS[k]['title']} needs the layout drawn - finish it yourself.")
        for i, ln in enumerate(f.get("lines") or [], 1):
            gone = ([] if str(ln.get("qty") or "").strip() else ["qty"]) + missing_sizes(k, ln)
            if gone:
                need(f"{FORMS[k]['title']} line {i}: {', '.join(gone)}", "size", "width", "height", "depth",
                     "length", "dimension", "how many", "quantity", "qty")
    for s in r.get("missing") or []:
        ask_customer.append(str(s))
    return ask_customer, hold


# ------------------------------------------------------------------ sending (practice mode aware)
def queue_email(kind, to, subject, body, attachments=None, log_id=None, order_id=None, hold_reason=""):
    """kind: customer | order | coworker. Sends now, or stores it held / as practice. Returns the outbox status."""
    attachments = [a for a in (attachments or []) if a and os.path.exists(a)]
    to = (to or "").strip()
    flag = {"customer": "ai_auto_customer", "quote": "ai_auto_customer", "order": "ai_auto_orders",
            "coworker": "ai_auto_coworker"}[kind]
    status, note, real_to = "held", hold_reason, to
    if hold_reason:
        status = "held"
    elif practice():
        tests = _addr_list(setting("ai_test_senders"))
        ptarget = (setting("ai_practice_to") or "").strip()
        if kind in ("customer", "quote", "coworker") and to.lower() in tests:
            status, note = "send", "Practice mode: sent because this is a test address."
        elif kind == "order" and ptarget:
            status, note, real_to = "send", f"Practice mode: sent to {ptarget} instead of {to}.", ptarget
            subject = "[PRACTICE] " + subject
        else:
            status, note = "practice", "Practice mode: not sent."
    elif setting(flag) == "0":
        status, note = "held", "Auto-send is off for this kind of email."
    else:
        status = "send"
    oid = x("""INSERT INTO ai_outbox(ts, log_id, kind, to_addr, cc, subject, body, attachments, status, note, order_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (now(), log_id, kind, real_to, "", subject, body, json.dumps(attachments),
             "pending" if status == "send" else status, note, order_id))
    if status == "send":
        return send_outbox(oid)
    return status


_PROV = {"p": None}     # the mail connection the current check is using


def send_outbox(out_id, prov=None):
    o = q("SELECT * FROM ai_outbox WHERE id=?", (out_id,), one=True)
    from ..emailer import provider
    prov = prov or _PROV["p"] or provider(setting)
    try:
        prov.send(o["to_addr"], o["cc"] or "", o["subject"], o["body"], json.loads(o["attachments"] or "[]"),
                  review=False)
    except Exception as e:  # noqa: BLE001
        x("UPDATE ai_outbox SET status='failed', note=? WHERE id=?", (f"Send failed: {e}"[:300], out_id))
        return "failed"
    x("UPDATE ai_outbox SET status='sent', sent_at=? WHERE id=?", (now(), out_id))
    if o["order_id"] and o["kind"] == "order":
        _mark_sent(o["order_id"], o["to_addr"])
    if o["order_id"] and o["kind"] == "quote":      # the priced quote went to the customer: Quote sent
        first = q("SELECT * FROM orders WHERE id=?", (o["order_id"],), one=True)
        for mbr in (quote_group(first) if first else []):
            if mbr["status"] == "Confirmed":
                x("UPDATE orders SET status='Quoted', updated=? WHERE id=?", (now(), mbr["id"]))
                add_status(mbr["id"], "Quoted", f"AI assistant: quote emailed to {o['to_addr']}")
    return "sent"


def _mark_sent(oid, to):
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    if not o:
        return
    x("UPDATE orders SET status='Sent', sent_at=coalesce(sent_at, ?), sent_to=?, updated=? WHERE id=?",
      (now(), to, now(), oid))
    add_status(oid, "Sent", f"AI assistant emailed it to {to}")


def reply_subject(subject):
    s = (subject or "").strip()
    return s if re.match(r"^\s*re\s*:", s, re.I) else f"Re: {s}"


def customer_reply(to, subject, body, log_id, order_id=None, hold_reason=""):
    if _NOTE["on"] and not hold_reason:
        if not _NOTE["email_customer"]:
            hold_reason = "From your phone / text note - send it only if you want the customer to get it."
        elif not (to or "").strip():
            hold_reason = "No email for this customer - add one, then send."
    return queue_email("customer", to, reply_subject(subject), body, [catalog_file()], log_id, order_id, hold_reason)


# ------------------------------------------------------------------ making orders in the app
def make_order(r, f, c, m, kind, status_note):
    from ..routes.orders import _make_pdf, customer_header
    from ..order_kind import kind_title
    h = customer_header(c) if c else {"name": r.get("customer_name") or ""}
    h.update({k: v for k, v in {"contact": r.get("contact_name"), "contact_phone": r.get("contact_phone"),
                                "contact_email": r.get("contact_email"), "po": r.get("po"),
                                "unit": r.get("unit"), "date": date.today().strftime("%m/%d/%Y")}.items() if v})
    if kind == "quote":
        h.update({"quote_to": r.get("contact_name") or "", "quote_email": r.get("contact_email") or m.get("sender") or "",
                  "quote_phone": r.get("contact_phone") or "", "quote_via": "Email"})
    lines = [{k: ("" if v is None else str(v)) for k, v in ln.items()} for ln in f.get("lines") or []]
    data = {"header": h, "blocks": lines, "comments": f.get("comments") or ""}
    title = kind_title(FORMS[f["form_key"]]["title"], kind)
    oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, notes, created,
               updated, kind) VALUES (?,?,?,?,?,'Draft',?,'ai',?,?,?,?)""",
            (c["id"] if c else None, f["form_key"], title, r.get("po") or "", date.today().isoformat(), dumps(data),
             f"From email: {m.get('subject') or ''}"[:200], now(), now(), kind))
    add_status(oid, "Draft", status_note)
    try:
        _make_pdf(oid)
    except Exception as e:  # noqa: BLE001
        add_status(oid, "Draft", f"PDF couldn't be made: {e}")
    return oid


def order_desk_email(oid):
    """(subject, body, pdf path) for the order desk, same as Send order in the app."""
    from ..routes.orders import _fmt
    from ..order_kind import is_quote
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    data = loads(o["data"])
    body = _fmt(setting("body_template"), o, data)
    if is_quote(o):
        body = body.replace("Please see the attached", "Please price the attached", 1)
        if "price" not in body.lower():
            body = "Quote request - please price this and send the quote back (not an order yet).\n\n" + body
    return _fmt(setting("subject_template"), o, data), body, o["pdf_path"]



# ------------------------------------------------------------------ keeping the app's records up to date
def _digits(s):
    return re.sub(r"\D", "", s or "")[-10:]


def save_contact(c, name, phone, email, source=""):
    """Put the person who emailed on the customer's contacts. New person -> added. Known person -> blank phone /
    email / name filled in. Something different from what's on file (e.g. a new phone) -> the Review tab, never
    overwritten. Returns a short note of what changed, or ""."""
    if not c:
        return ""
    name, phone, email = (name or "").strip(), (phone or "").strip(), (email or "").strip().lower()
    if not (name or email):
        return ""
    from ..changes import _same
    for k in q("SELECT * FROM contacts WHERE customer_id=?", (c["id"],)):
        if (email and (k["email"] or "").lower() == email) or (name and k["name"] and _same(k["name"], name)):
            fill = {f: v for f, v in (("name", name), ("phone", phone), ("email", email)) if v and not (k[f] or "").strip()}
            for f, v in fill.items():
                x(f"UPDATE contacts SET {f}=? WHERE id=?", (v, k["id"]))
            differ = {}
            if phone and k["phone"] and _digits(phone) != _digits(k["phone"]):
                differ["phone"] = phone
            if email and k["email"] and email != k["email"].lower():
                differ["email"] = email
            if differ:
                from ..changes import add_change, new_batch
                b = new_batch("ai", f"AI assistant: {k['name'] or email} at {c['name']} {source}".strip())
                add_change(b, "new_contact", customer_id=c["id"], payload={"contact_id": k["id"], **differ},
                           new=", ".join(f"{f}: {v}" for f, v in differ.items()), old=k["name"] or k["email"])
            out = []
            if fill:
                out.append(f"filled in {', '.join(fill)} for {k['name'] or email}")
            if differ:
                out.append(f"new {', '.join(differ)} for {k['name'] or email} waiting in Review")
            return "; ".join(out)
    x("INSERT INTO contacts(customer_id, name, role, email, phone, notes) VALUES (?,?,?,?,?,?)",
      (c["id"], name or email, "", email, phone, f"Added by the AI assistant from an email {source}".strip()))
    return f"added {name or email} to {c['name']}'s contacts"


def save_to_app(a, c, m, oids):
    """After an order / quote: the contact and the unit sizes go into the app, like doing it by hand."""
    notes = []
    try:
        n = save_contact(c, a.get("contact_name"), a.get("contact_phone"),
                         a.get("contact_email") or (m.get("sender") if not is_coworker(m.get("sender")) else ""),
                         f"({m.get('subject') or ''})"[:80])
        if n:
            notes.append(n)
    except Exception:  # noqa: BLE001 - never lose an order over a contact
        pass
    try:
        from ..unit_sizes import save_order_sizes
        added = updated = 0
        for oid in oids or []:
            ad, up = save_order_sizes(oid)
            added, updated = added + ad, updated + up
        if added or updated:
            notes.append(f"unit sizes saved ({added} new, {updated} updated)")
    except Exception:  # noqa: BLE001
        pass
    return "; ".join(notes)


# ------------------------------------------------------------------ the log
def log(m, email_id, thread_id, cls, action, status, reasons=None, detail="", order_ids=None, stop_id=None,
        task_id=None, customer_id=None):
    return x("""INSERT INTO ai_log(ts, msg_id, email_id, thread_id, sender, sender_name, subject, received, category,
                summary, action, status, reasons, detail, order_ids, stop_id, task_id, customer_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (now(), m.get("msg_id"), email_id, thread_id, m.get("sender"), m.get("sender_name"), m.get("subject"),
              m.get("received"), cls.get("category"), cls.get("summary") or "", action, status,
              json.dumps(reasons or []), detail, ",".join(map(str, order_ids or [])), stop_id, task_id, customer_id))


def _thread(m, cls, c):
    key = conv_key(m.get("sender"), m.get("subject"))
    t = q("SELECT * FROM ai_threads WHERE conv_key=? AND status='waiting' ORDER BY id DESC LIMIT 1", (key,), one=True)
    if t:
        return t["id"], t
    tid = x("""INSERT INTO ai_threads(conv_key, sender, subject, customer_id, category, status, created, updated)
               VALUES (?,?,?,?,?,'open',?,?)""",
            (key, m.get("sender"), _norm_subject(m.get("subject")), c["id"] if c else None, cls.get("category"),
             now(), now()))
    return tid, None


def _set_thread(tid, status, reason="", order_ids=None, customer_id=None):
    x("UPDATE ai_threads SET status=?, reason=?, updated=?, order_ids=coalesce(?, order_ids), "
      "customer_id=coalesce(?, customer_id) WHERE id=?",
      (status, reason, now(), ",".join(map(str, order_ids)) if order_ids else None, customer_id, tid))


# ------------------------------------------------------------------ handlers
def handle_order(m, email_id, cls, c, tid, prior):
    form_keys = cls.get("forms") or []
    if prior:      # a customer answering our questions: keep the forms we already settled on
        form_keys = form_keys or [o for o in (prior["category"] or "").split(",") if o in FORMS]
    a = extract(smart(), m, c, form_keys, tid, "read order (Sonnet)")
    if not c and a.get("customer_name"):
        c = find_customer(name=a["customer_name"])
    try:
        b = extract(fast(), m, c, form_keys or [f["form_key"] for f in a.get("forms") or []], tid,
                    "cross-check (Haiku)")
        diffs = compare(a, b)
    except AIError as e:
        diffs = [f"The cross-check read failed ({e})."]
    ask_customer, hold = app_checks(a, c, m)
    if diffs:
        hold.append("The two AI reads disagree: " + " ".join(diffs[:6]))

    cid = c["id"] if c else None
    to = m.get("sender")
    kind = a.get("kind") if a.get("kind") in ("order", "quote") else "order"

    if hold:
        # held: save what it read as a draft so it's one click to finish, and park the suggested reply
        oids = [make_order(a, f, c, m, kind, "AI assistant: held for your review") for f in a.get("forms") or []
                if f.get("lines")] if c else []
        lid = log(m, email_id, tid, cls, "Held for your review", "held", hold + ask_customer,
                  detail=json.dumps(a)[:20000], order_ids=oids, customer_id=cid)
        if a.get("reply_to_customer"):
            customer_reply(to, m.get("subject"), a["reply_to_customer"], lid, oids[0] if oids else None,
                           hold_reason="Held with the order - review, edit if needed, then send.")
        _set_thread(tid, "held", "; ".join(hold)[:500], oids, cid)
        return

    if ask_customer:
        oids = [make_order(a, f, c, m, kind, "AI assistant: waiting on the customer for: " + "; ".join(ask_customer))
                for f in a.get("forms") or [] if f.get("lines")]
        is_note = str(m.get("msg_id") or "").startswith("note-")
        saved = save_to_app(dict(a, contact_name=a.get("contact_name") or ("" if is_note else m.get("sender_name"))),
                            c, m, [])
        lid = log(m, email_id, tid, cls, "Asked the customer for: " + "; ".join(ask_customer)[:300]
                  + (f" - saved to the app: {saved}" if saved else ""), "waiting",
                  ask_customer, detail=json.dumps(a)[:20000], order_ids=oids, customer_id=cid)
        st = customer_reply(to, m.get("subject"), a.get("reply_to_customer") or "", lid, oids[0] if oids else None)
        x("UPDATE ai_threads SET category=? WHERE id=?",
          (",".join(f["form_key"] for f in a.get("forms") or []) or cls.get("category"), tid))
        _set_thread(tid, "waiting", "; ".join(ask_customer)[:500], oids, cid)
        if oids:   # these drafts are replaced when the customer answers
            for oid in oids:
                x("UPDATE orders SET notes=? WHERE id=?", (f"AI: waiting on customer ({st})", oid))
        return

    # confident: make it, send it to the order desk, confirm with the customer
    if prior and prior["order_ids"]:   # drop the half-finished drafts from the earlier round
        from .. import order_trash
        for o in (prior["order_ids"] or "").split(","):
            if o.isdigit() and q("SELECT 1 FROM orders WHERE id=? AND status IN ('Draft','Ready')", (int(o),), one=True):
                order_trash.delete(int(o))
    oids = [make_order(a, f, c, m, kind, "AI assistant: made from the customer's email") for f in a["forms"]]
    if kind == "quote" and len(oids) > 1:     # one project, several forms: the quotes go back as ONE document
        for oid in oids:
            x("UPDATE orders SET quote_group=? WHERE id=?", (f"t{tid}", oid))
    saved = save_to_app(a, c, m, oids)
    lid = log(m, email_id, tid, cls, f"{'Quote request' if kind == 'quote' else 'Order'} made and sent to the order desk"
              + (f" - saved to the app: {saved}" if saved else ""),
              "done", [], detail=json.dumps(a)[:20000], order_ids=oids, customer_id=cid)
    desk = (setting("order_to") or "").strip()
    for oid in oids:
        subj, body, pdf = order_desk_email(oid)
        st = queue_email("order", desk, subj, body, [pdf], lid, oid) if desk else \
            queue_email("order", desk, subj, body, [pdf], lid, oid, hold_reason="No order desk address in Settings.")
        if st != "sent":
            add_status(oid, "Ready", f"AI assistant: order desk email {st} (see Assistant -> Outbox)")
    customer_reply(to, m.get("subject"), a.get("reply_to_customer") or "", lid, oids[0] if oids else None)
    _set_thread(tid, "done", "", oids, cid)


def _route_or_schedule(cls, c, m, purpose, who):
    """A visit: a stop on that day's route (with notes), or a 'to schedule' task when no day was given."""
    notes = (f"From {who}: {cls.get('summary') or ''}\nWhat: {cls.get('task') or purpose}"
             f"{(' at ' + cls['time']) if cls.get('time') else ''}\nEmail: {m.get('subject') or ''}")
    day = (cls.get("date") or "").strip()
    try:
        ok_day = bool(day) and date.fromisoformat(day) >= date.today()
    except ValueError:
        ok_day = False
    if ok_day:
        from ..routes.routes_plan import add_stop
        try:
            sid = add_stop(day, customer_id=c["id"] if c else None,
                           label="" if c else (cls.get("property") or "Visit"), purpose=purpose,
                           source="ai-email", notes=notes, smart=True)
        except Exception:  # noqa: BLE001
            sid = add_stop(day, customer_id=c["id"] if c else None, label="" if c else (cls.get("property") or "Visit"),
                           purpose=purpose, source="ai-email", notes=notes, smart=False)
        return sid, None
    tid = x("INSERT INTO tasks(customer_id, title, due_date, owner, created) VALUES (?,?,?,?,?)",
            (c["id"] if c else None, f"To schedule: {purpose} - {cls.get('property') or (c['name'] if c else '')}"
             f" ({who})", None, "ai-schedule", now()))
    return None, tid


def handle_visit(m, email_id, cls, c, tid, coworker):
    purpose = {"measure_request": "Measure", "walk_request": "Property walk"}.get(cls["category"], "Visit")
    if coworker:
        purpose = cls.get("task") or "Visit"
    who = m.get("sender_name") or m.get("sender")
    sid, task_id = _route_or_schedule(cls, c, m, purpose[:80], who)
    hold = [] if (c or coworker) else ["Property isn't in the app - check the stop's address."]
    action = f"Added to Routes on {cls['date']}" if sid else "Added to 'To schedule'"
    lid = log(m, email_id, tid, cls, action, "held" if hold else "done", hold, stop_id=sid, task_id=task_id,
              customer_id=c["id"] if c else None)
    reply = (cls.get("reply") or "").strip()
    if reply:
        body = f"{reply}\n\nThank you,\n{signature()}"
        if coworker:
            queue_email("coworker", m.get("sender"), reply_subject(m.get("subject")), body, [], lid,
                        hold_reason="Property isn't in the app." if hold else "")
        else:
            customer_reply(m.get("sender"), m.get("subject"), body, lid, hold_reason="; ".join(hold))
    _set_thread(tid, "done" if not hold else "held", "; ".join(hold))


def handle_coworker_task(m, email_id, cls, c, tid):
    if cls.get("property") and (cls.get("date") or "visit" in (cls.get("task") or "").lower()):
        return handle_visit(m, email_id, cls, c, tid, coworker=True)
    task_id = x("INSERT INTO tasks(customer_id, title, due_date, owner, created) VALUES (?,?,?,?,?)",
                (c["id"] if c else None, f"{cls.get('task') or cls.get('summary') or 'Task'} "
                 f"({m.get('sender_name') or m.get('sender')})"[:200], cls.get("date") or None, "ai", now()))
    lid = log(m, email_id, tid, cls, "Added to your tasks", "done", [], task_id=task_id,
              customer_id=c["id"] if c else None)
    if (cls.get("reply") or "").strip():
        queue_email("coworker", m.get("sender"), reply_subject(m.get("subject")),
                    f"{cls['reply'].strip()}\n\nThank you,\n{signature()}", [], lid)
    _set_thread(tid, "done")


def _open_quote(c, status, po=""):
    if po:
        r = q("SELECT * FROM orders WHERE kind='quote' AND status=? AND po=? ORDER BY id DESC", (status, po))
        if len(r) == 1:
            return r[0]
    if c:
        r = q("SELECT * FROM orders WHERE kind='quote' AND status=? AND customer_id=? ORDER BY id DESC", (status, c["id"]))
        if len(r) == 1:
            return r[0]
    return None


def quote_group(o):
    """Every quote request of the same project: same group (made together from one email), else same customer + PO."""
    if o["quote_group"]:
        return q("SELECT * FROM orders WHERE quote_group=? AND kind='quote' AND status<>'Cancelled' ORDER BY id",
                 (o["quote_group"],))
    if (o["po"] or "").strip() and o["customer_id"]:
        return q("""SELECT * FROM orders WHERE kind='quote' AND status<>'Cancelled' AND customer_id=? AND po=?
                    ORDER BY id""", (o["customer_id"], o["po"]))
    return [o]


def _returned_matches(c, cls, n_pdfs):
    """Which open quote request(s) (status Request sent) this returned quote is for."""
    po = (cls.get("po") or "").strip()
    rows = []
    if po:
        rows = q("SELECT * FROM orders WHERE kind='quote' AND status='Sent' AND po=? ORDER BY id", (po,))
    if not rows and c:
        rows = q("SELECT * FROM orders WHERE kind='quote' AND status='Sent' AND customer_id=? ORDER BY id", (c["id"],))
    if len(rows) > 1 and cls.get("forms"):           # "countertop quote" -> the countertop request
        narrowed = [r for r in rows if r["form_key"] in cls["forms"]]
        rows = narrowed or rows
    if len(rows) <= 1:
        return rows
    groups = {tuple(sorted(g["id"] for g in quote_group(r))) for r in rows}
    if len(groups) == 1 and n_pdfs >= len(rows):     # one email carrying the whole project's quotes
        return rows
    return []


def combine_quotes(members, folder):
    """One PDF: a cover page listing the parts, then every returned quote, in order."""
    import pymupdf as fitz
    files = []
    for mbr in members:
        for f in json.loads(mbr["quote_file"] or "[]"):
            if f not in files and os.path.exists(f):
                files.append(f)
    if len(files) == 1:
        return files[0]
    out = fitz.open()
    cover = out.new_page()
    first = members[0]
    h = (loads(first["data"]) or {}).get("header", {})
    lines = [f"Quote - {h.get('name') or ''}", f"{(' PO ' + first['po']) if first['po'] else ''}".strip(), "",
             "This quote includes:"] + [f"  - {mbr['title']}" for mbr in members] + \
            ["", f"Prepared by {signature()}", date.today().strftime("%m/%d/%Y")]
    y = 90
    for i, ln in enumerate(lines):
        cover.insert_text((72, y), ln, fontsize=20 if i == 0 else 12)
        y += 30 if i == 0 else 18
    for f in files:
        try:
            src = fitz.open(f)
            out.insert_pdf(src)
            src.close()
        except Exception:  # noqa: BLE001 - a bad file: skip it, the rest still go
            pass
    path = os.path.join(folder, f"Combined quote - #{first['id']}{(' - PO ' + first['po']) if first['po'] else ''}.pdf")
    out.save(path, garbage=3, deflate=True)
    return path


def handle_quote_returned(m, email_id, cls, c, tid):
    pdfs = [(fn, raw) for fn, raw in m.get("attachments") or [] if fn.lower().endswith(".pdf")]
    rows = _returned_matches(c, cls, len(pdfs))
    if not rows:
        log(m, email_id, tid, cls, "Quote came back - couldn't tell which quote request it's for", "held",
            ["Match it to the quote yourself (Quotes tab -> Quote received)."], customer_id=c["id"] if c else None)
        return _set_thread(tid, "held")
    folder = os.path.dirname(rows[0]["pdf_path"] or "") or None
    if folder:
        os.makedirs(folder, exist_ok=True)
    saved = []
    for fn, raw in pdfs:
        if folder:
            p = os.path.join(folder, f"Quote received - #{rows[0]['id']} - {os.path.basename(fn)}")
            with open(p, "wb") as fh:
                fh.write(raw)
            saved.append(p)
    for i, o in enumerate(rows):
        mine = saved if len(rows) == 1 or len(saved) != len(rows) else [saved[i]]
        if len(rows) > 1 and len(saved) != len(rows):
            mine = saved if i == 0 else []
        x("UPDATE orders SET status='Confirmed', quote_file=?, updated=? WHERE id=?", (json.dumps(mine), now(), o["id"]))
        add_status(o["id"], "Confirmed", f"AI assistant: quote received from {m.get('sender')}")
    o = q("SELECT * FROM orders WHERE id=?", (rows[0]["id"],), one=True)
    members = quote_group(o)
    waiting = [mbr for mbr in members if mbr["status"] in ("Draft", "Ready", "Sent")]
    ids = [mbr["id"] for mbr in members]
    if waiting:      # a big project: wait for every part, then send them as one quote
        log(m, email_id, tid, cls, f"Quote #{o['id']} received - waiting on {len(waiting)} more quote(s) for this "
            f"project ({', '.join('#' + str(w['id']) for w in waiting)}), then it goes out as one combined quote",
            "done", [], order_ids=[r["id"] for r in rows], customer_id=o["customer_id"])
        return _set_thread(tid, "done")
    h = (loads(o["data"]) or {}).get("header", {})
    to = h.get("quote_email") or h.get("contact_email") or ""
    files = [f for mbr in members for f in json.loads(mbr["quote_file"] or "[]")]
    hold = ([] if to else ["No email on the quote for the customer - add it, then send."]) + \
        ([] if files else ["The office's email had no PDF quote attached - check it, then send."])
    doc = None
    if files and folder:
        try:
            doc = combine_quotes(members, folder)
        except Exception as e:  # noqa: BLE001
            hold.append(f"Couldn't combine the quotes into one PDF ({e}).")
    many = len(members) > 1
    lid = log(m, email_id, tid, cls,
              (f"All {len(members)} quotes for this project received" if many else f"Quote #{o['id']} received")
              + (" - held" if hold else " - sending " + ("one combined quote" if many else "it") + " to the customer"),
              "held" if hold else "done", hold, order_ids=ids, customer_id=o["customer_id"])
    what = ("the quote for " + ", ".join(mbr["title"].replace(" Quote Request", "") for mbr in members)) if many \
        else "the quote you asked for"
    body = (f"Hello {h.get('quote_to') or h.get('contact') or ''},\n\nHere is {what}"
            f"{(' (PO ' + o['po'] + ')') if o['po'] else ''}{', all in one document' if many else ''}. The product "
            f"catalog is attached too. Just reply to let me know if you'd like to go ahead.\n\nThank you,\n{signature()}")
    st = queue_email("quote", to, f"Your quote - {h.get('name') or o['title']}" if many else f"Your quote - {o['title']}",
                     body, ([doc] if doc else files) + [catalog_file()], lid, o["id"], hold_reason="; ".join(hold))
    if st != "sent":
        for i in ids:
            add_status(i, "Confirmed", f"AI assistant: quote email to the customer {st} (Assistant -> Emails)")
    _set_thread(tid, "held" if hold else "done")


def handle_quote_approval(m, email_id, cls, c, tid):
    o = _open_quote(c, "Quoted", cls.get("po") or "")
    if not o:
        log(m, email_id, tid, cls, "Customer approved a quote - couldn't tell which one", "held",
            ["Find the quote and tap Approved / ordered."], customer_id=c["id"] if c else None)
        return _set_thread(tid, "held")
    from ..order_kind import kind_title
    x("UPDATE orders SET kind='order', title=?, status='Ready', quote_approved_at=?, sent_at=NULL, updated=? WHERE id=?",
      (kind_title(o["title"], "order"), now(), now(), o["id"]))
    add_status(o["id"], "Ready", "AI assistant: customer approved the quote - now an order")
    try:
        from ..routes.orders import _make_pdf
        _make_pdf(o["id"])
    except Exception:  # noqa: BLE001
        pass
    lid = log(m, email_id, tid, cls, f"Quote #{o['id']} approved - sent in as an order", "done", [],
              order_ids=[o["id"]], customer_id=o["customer_id"])
    subj, body, pdf = order_desk_email(o["id"])
    queue_email("order", setting("order_to") or "", subj, body, [pdf], lid, o["id"],
                hold_reason="" if setting("order_to") else "No order desk address in Settings.")
    customer_reply(m.get("sender"), m.get("subject"),
                   f"Thank you! Your order is in and I'll keep you posted on delivery.\n\nThank you,\n{signature()}",
                   lid, o["id"])
    _set_thread(tid, "done", "", [o["id"]])


def handle_question(m, email_id, cls, c, tid, coworker=False):
    lid = log(m, email_id, tid, cls, "Question - reply drafted for you", "held", ["Questions wait for your answer."],
              customer_id=c["id"] if c else None)
    try:
        body = ask(smart(), f"You draft email replies for {signature() or 'a sales rep'} (apartment interior "
                   "products supplier). Write a short, friendly reply. If you don't know a fact (price, stock, "
                   "dates), say he'll check and follow up - never guess. Plain text, sign off with his name."
                   + rules_text(),
                   f"APP RECORDS:\n{customer_facts(c)}\n\nEMAIL:\nFrom {m.get('sender')}\nSubject: {m.get('subject')}\n"
                   f"{(m.get('body') or '')[:5000]}", max_tokens=700, purpose="draft reply")
    except AIError:
        body = ""
    if body.strip() and coworker:
        queue_email("coworker", m.get("sender"), reply_subject(m.get("subject")), body.strip(), [], lid,
                    hold_reason="Questions wait for you - edit and send.")
    elif body.strip():
        customer_reply(m.get("sender"), m.get("subject"), body.strip(), lid,
                       hold_reason="Questions wait for you - edit and send.")
    _set_thread(tid, "held")


# ------------------------------------------------------------------ one email
def note(text, customer_id=None, contact_email="", email_customer=False):
    """A phone call / text Taylor types into the app: handled like an email from that customer."""
    from ..routes.orders import customer_header  # noqa: F401 - keeps imports warm for make_order
    c = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True) if customer_id else None
    to = (contact_email or "").strip() or (c["email"] if c and c["email"] else "")
    if not to and c:
        k = q("SELECT email FROM contacts WHERE customer_id=? AND coalesce(email,'')<>'' LIMIT 1", (c["id"],), one=True)
        to = k["email"] if k else ""
    me = signature() or "the rep"
    first = " ".join((text or "").split())
    first = first if len(first) <= 70 else first[:70].rsplit(" ", 1)[0] + "…"
    m = {"msg_id": f"note-{datetime.now():%Y%m%d%H%M%S%f}", "sender": to, "sender_name": f"{me} (phone / text note)",
         "subject": f"Phone/text: {first}", "received": now()[:16], "attachments": [], "attachment_names": [],
         "body": (f"NOTE TYPED BY {me.upper()} about a phone call or text from the customer"
                  f"{(' at ' + c['name']) if c else ''}. Everything in it was told to {me} by the customer; if it says "
                  f"reorder / same as before, {me} still wants to see the matched past order before it is placed. The subject "
                  f"line is just a short label - the full note is below.\n\n"
                  f"{text}")}
    _NOTE.update(on=True, email_customer=bool(email_customer))
    try:
        process(m, None, customer=c)
    finally:
        _NOTE.update(on=False, email_customer=False)
    return q("SELECT * FROM ai_log WHERE msg_id=? ORDER BY id DESC LIMIT 1", (m["msg_id"],), one=True)


def process(m, email_id=None, customer=None):
    """Sort one fetched email and act on it. Returns the ai_log id."""
    sender = m.get("sender") or ""
    coworker = is_coworker(sender)
    prior_key = conv_key(sender, m.get("subject"))
    prior = q("SELECT * FROM ai_threads WHERE conv_key=? AND status='waiting' ORDER BY id DESC LIMIT 1",
              (prior_key,), one=True)
    try:
        cls = classify(m)
    except AITransient:
        raise                 # offline / busy: leave this email for the next check
    except AIError as e:
        return log(m, email_id, None, {"category": "error", "summary": m.get("subject")}, f"Couldn't read it: {e}",
                   "error", [str(e)])
    cat = cls["category"]
    if prior and not coworker and cat not in ("quote_approval",):   # answering our questions: back to the order
        cat = cls["category"] = cat if cat in ORDERISH else "order"
    from ..email_parse import match_customer
    c = customer
    if not coworker and c is None:
        c, _ = match_customer(sender, f"{m.get('subject') or ''} {m.get('body') or ''}"[:4000])
    if (not c) and cls.get("property"):
        c = find_customer(name=cls["property"])
    tid, prior = _thread(m, cls, c)
    try:
        if coworker:
            if cat in ("coworker_task", "visit_request"):
                return handle_coworker_task(m, email_id, cls, c, tid) if cat == "coworker_task" else \
                    handle_visit(m, email_id, cls, c, tid, coworker=True)
            if cat == "quote_returned":
                return handle_quote_returned(m, email_id, cls, c, tid)
            if cat == "coworker_question":
                return handle_question(m, email_id, cls, c, tid, coworker=True)
            log(m, email_id, tid, cls, "Noted for your summary", "info", customer_id=c["id"] if c else None)
            return _set_thread(tid, "done")
        if cat in ORDERISH:
            return handle_order(m, email_id, cls, c, tid, prior)
        if cat in ("measure_request", "walk_request"):
            return handle_visit(m, email_id, cls, c, tid, coworker=False)
        if cat == "quote_approval":
            return handle_quote_approval(m, email_id, cls, c, tid)
        if cat == "question":
            return handle_question(m, email_id, cls, c, tid)
        log(m, email_id, tid, cls, "Noted for your summary" if cat != "junk" else "Ignored (junk)", "info",
            customer_id=c["id"] if c else None)
        _set_thread(tid, "done")
    except AITransient:
        x("DELETE FROM ai_threads WHERE id=? AND status='open'", (tid,))
        raise
    except AIError as e:
        log(m, email_id, tid, cls, f"Stopped: {e}", "error", [str(e)], customer_id=c["id"] if c else None)
        _set_thread(tid, "held", str(e)[:300])


# ------------------------------------------------------------------ the inbox check
def run(prov=None, limit=60):
    """Read new inbox emails since the last check (catches up after the computer was off / offline).
    The result (or the reason it couldn't) is kept in ai_last_result for the Assistant page."""
    res = {"read": 0, "new": 0, "error": "", "skipped_me": 0, "skipped_old": 0, "seen": 0}
    try:
        res = _run_checked(prov, res, limit)
    except Exception as e:  # noqa: BLE001 - show it, never die silently
        import traceback
        res["error"] = f"{type(e).__name__}: {e}"
        try:
            from ..paths import data_dir
            with open(os.path.join(data_dir(), "app.log"), "a", encoding="utf-8") as fh:
                fh.write(f"\n{now()} AI assistant check failed:\n{traceback.format_exc()}\n")
        except Exception:  # noqa: BLE001
            pass
    res["when"] = now()[:16]
    set_setting("ai_last_result", json.dumps(res))
    return res


def _run_checked(prov, res, limit):
    from ..emailer import provider
    if setting("ai_enabled") != "1":
        res["error"] = "The AI assistant is off - turn it on in Settings -> AI Assistant."
        return res
    prov = prov or provider(setting)
    if prov.name == "eml":
        res["error"] = "Email isn't connected - Settings -> Email: pick Microsoft 365 and sign in."
        return res
    ok, why = prov.available()
    if not ok:
        res["error"] = why
        return res
    _PROV["p"] = prov
    try:
        return _run(prov, res, limit)
    finally:
        _PROV["p"] = None


def _run(prov, res, limit):
    last = setting("ai_last_check") or ""
    try:
        back = int(setting("ai_lookback_days") or 3)
    except ValueError:
        back = 3
    if last:
        try:
            gap = (datetime.now() - datetime.strptime(last[:16], "%Y-%m-%d %H:%M")).days + 1
            back = max(1, min(back if gap > back else gap, 14))
        except ValueError:
            pass
    try:
        msgs = prov.list_messages(days=back, limit=200)
    except Exception as e:  # noqa: BLE001
        res["error"] = f"Couldn't read the inbox: {e}"
        return res
    seen = {r["msg_id"] for r in q("SELECT msg_id FROM ai_log WHERE msg_id IS NOT NULL")}
    started = (setting("ai_started") or "")[:16]
    todo = []
    for mm in msgs:
        if mm["msg_id"] in seen:
            res["seen"] += 1
        elif is_me(mm.get("sender")):
            res["skipped_me"] += 1
        elif started and (mm.get("received") or "")[:16] < started:
            res["skipped_old"] += 1
        else:
            todo.append(mm)
    todo.sort(key=lambda mm: mm.get("received") or "")
    res["read"] = len(msgs)
    errs = getattr(type(prov), "errors", None)
    if not msgs and errs:
        res["error"] = "Outlook wouldn't let the app read the emails: " + errs[0]
    from ..routes.emails import _store
    for mm in todo[:limit]:
        try:
            full = prov.get_message(mm["msg_id"])
        except Exception as e:  # noqa: BLE001
            res["error"] = f"Couldn't open an email ({mm.get('subject') or ''}): {e}"
            continue
        full.setdefault("msg_id", mm["msg_id"])
        full.setdefault("attachment_names", [a[0] for a in full.get("attachments") or []])
        eid = _store(full, prov.name)
        try:
            process(full, eid)
        except AITransient as e:          # no internet / Claude busy: stop here, these get retried next check
            res["error"] = f"{e} - {len(todo) - res['new']} email(s) will be tried again on the next check."
            break
        res["new"] += 1
    set_setting("ai_last_check", datetime.now().strftime("%Y-%m-%d %H:%M"))
    return res


# ------------------------------------------------------------------ morning / end-of-day summary
STATUS_WORD = {"done": "done", "held": "HELD for you", "waiting": "waiting on them", "info": "noted",
               "error": "ERROR - check it"}


def build_summary(period, day=None):
    """The morning / end-of-day report: what came in, who reached out, everything the assistant did, what's open."""
    day = day or date.today().isoformat()
    since = (date.fromisoformat(day) - timedelta(days=1)).isoformat() + " " + \
        (setting("ai_summary_evening") or "17:00") if period == "morning" else day + " 00:00"
    L = []

    def sect(title, rows):
        if rows:
            L.append(f"{title}\n" + "\n".join(f"  - {r}" for r in rows))

    logs = q("SELECT * FROM ai_log WHERE ts>=? ORDER BY id", (since,))
    real = [g for g in logs if g["category"] != "junk"]
    junk = len(logs) - len(real)
    outbox = q("SELECT * FROM ai_outbox WHERE ts>=? ORDER BY id", (since,))
    sent = [o for o in outbox if o["status"] in ("sent", "practice")]
    orders = q("SELECT * FROM orders WHERE source='ai' AND created>=? ORDER BY id", (since,))
    stops = q("SELECT * FROM route_stops WHERE source='ai-email' AND created>=? ORDER BY day", (since,))
    new_tasks = q("SELECT * FROM tasks WHERE owner LIKE 'ai%' AND created>=?", (since,))
    held = q("SELECT * FROM ai_log WHERE status IN ('held','error') AND reviewed=0 ORDER BY id")
    waiting = q("SELECT * FROM ai_log WHERE status='waiting' AND id IN (SELECT MAX(l.id) FROM ai_log l JOIN ai_threads t "
                "ON t.id=l.thread_id WHERE t.status='waiting' GROUP BY l.thread_id)")
    people = {}
    for g in real:
        people.setdefault(g["sender_name"] or g["sender"] or "?", []).append(g)
    tag = " (practice - not really sent)" if practice() else ""

    # at a glance
    glance = [f"{len(logs)} email(s) read from {len(people)} people" + (f" (+{junk} junk ignored)" if junk else ""),
              f"{sum(1 for o in orders if (o['kind'] or 'order') == 'order')} order(s) and "
              f"{sum(1 for o in orders if o['kind'] == 'quote')} quote request(s) made",
              f"{len(sent)} email(s) sent for you{tag}",
              f"{len(held)} waiting for you, {len(waiting)} waiting on customers",
              f"{len(stops)} route stop(s) and {len(new_tasks)} task(s) added"]
    sect("At a glance", glance)
    sect(f"Needs you ({len(held)})", [f"{h['sender_name'] or h['sender']}: {h['summary'] or h['subject']} -> "
                                      f"{'; '.join(json.loads(h['reasons'] or '[]'))[:160]}" for h in held])
    # every person who reached out, and what the assistant did with each email
    rows = []
    for who, items in people.items():
        for g in items:
            rows.append(f"{who} ({(g['received'] or g['ts'] or '')[11:16]}): {g['summary'] or g['subject'] or ''}"
                        f" -> {g['action'] or ''} [{STATUS_WORD.get(g['status'], g['status'])}]")
    sect("Who reached out and what your assistant did", rows)
    sect("Emails sent for you" + tag,
         [f"{'[practice] ' if s['status'] == 'practice' else ''}{ {'customer': 'Customer', 'quote': 'Quote to customer', 'order': 'Order desk', 'coworker': 'Coworker'}.get(s['kind'], s['kind'])}"
          f" - {s['to_addr']}: {s['subject']}" for s in sent])
    unsent = [o for o in outbox if o["status"] in ("held", "failed")]
    sect("Emails written but NOT sent (held / failed)", [f"{o['to_addr']}: {o['subject']} ({o['note'] or o['status']})"
                                                       for o in unsent])
    sect("Orders and quotes it made", [f"#{o['id']} {o['title']} PO {o['po'] or '-'} - now {o['status']}" for o in orders])
    saved = [g for g in logs if "saved to the app:" in (g["action"] or "")]
    sect("Saved to the app", [f"{g['sender_name'] or g['sender']}: {g['action'].split('saved to the app:', 1)[1].strip()}"
                              for g in saved])
    sect("Waiting on customers", [f"{w['sender_name'] or w['sender']}: {w['action']}" for w in waiting])
    sect("New route stops", [f"{s['day']}: {s['label'] or ''} - {s['purpose'] or ''}" for s in stops])
    tasks = q("SELECT * FROM tasks WHERE done=0 AND (owner LIKE 'ai%' OR due_date<=?) ORDER BY due_date", (day,))
    sect("Open tasks / to schedule", [f"{t['title']}{(' (due ' + t['due_date'] + ')') if t['due_date'] else ''}"
                                      for t in tasks])
    today_stops = q("SELECT label, purpose FROM route_stops WHERE day=? ORDER BY position", (day,))
    if period == "morning" and today_stops:
        sect("Today's route", [f"{s['label'] or ''}{(' - ' + s['purpose']) if s['purpose'] else ''}" for s in today_stops])
    body = "\n\n".join(L) or "Nothing new."
    head = ("Good morning - here's what came in overnight, what your assistant did, and what's on today."
            if period == "morning" else "End of day - everyone who reached out, everything your assistant did, "
                                         "and what's still open.")
    body = head + "\n\n" + body
    x("INSERT INTO ai_summaries(day, period, body, created) VALUES (?,?,?,?)", (day, period, body, now()))
    return body


def due_summaries():
    """Make the morning / evening summary once its time has passed today."""
    nowt = datetime.now()
    day = nowt.date().isoformat()
    made = []
    for period, key in (("morning", "ai_summary_morning"), ("evening", "ai_summary_evening")):
        t = (setting(key) or "").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", t):
            continue
        if nowt.strftime("%H:%M") >= t.zfill(5) and \
                not q("SELECT 1 FROM ai_summaries WHERE day=? AND period=?", (day, period), one=True):
            build_summary(period, day)
            made.append(period)
    return made


def retry(log_id):
    """Run an email that errored through again (Assistant -> Needs you -> Retry)."""
    g = q("SELECT * FROM ai_log WHERE id=?", (log_id,), one=True)
    if not g or not g["email_id"]:
        return None
    e = q("SELECT * FROM email_messages WHERE id=?", (g["email_id"],), one=True)
    if not e:
        return None
    m = {"msg_id": e["msg_id"], "subject": e["subject"], "sender": e["sender"], "sender_name": e["sender_name"],
         "received": e["received"], "body": e["body"], "attachments": [], "attachment_names": []}
    x("DELETE FROM ai_log WHERE id=?", (log_id,))
    x("UPDATE ai_outbox SET status='cancelled' WHERE log_id=? AND status IN ('held','practice','failed')", (log_id,))
    process(m, e["id"])
    return q("SELECT * FROM ai_log WHERE msg_id=? ORDER BY id DESC LIMIT 1", (e["msg_id"],), one=True)
