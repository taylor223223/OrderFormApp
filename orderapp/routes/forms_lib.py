"""Order Forms library: every blank form, company side and customer side.
Open / print, download, or email them (always as a draft you review - never sent automatically)."""
import glob
import io
import os

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for

from ..catalog import FORM_ORDER, FORMS
from ..db import q, setting
from ..paths import ensure_dir, resource_path
from ..security import login_required

bp = Blueprint("library", __name__)

CUSTOMER_PREFIX = "Customer Order Form - "


def company_forms():
    out = []
    from ..cabinet_layout import BLANK_ORDER_FORM
    for fk in FORM_ORDER:
        # the Cabinet Order Form is the kitchen layout: its paper version is the blank layout sheet
        fn = BLANK_ORDER_FORM if FORMS[fk].get("layout_editor") else FORMS[fk]["file"]
        p = resource_path(os.path.join("forms", fn))
        if os.path.exists(p):
            out.append({"kind": "company", "name": os.path.basename(p), "title": FORMS[fk]["title"],
                        "group": _group(fk)})
    return out


def customer_forms():
    from ..cabinet_layout import CUSTOMER_FORM
    from ..manager_forms import FORMS as CF
    order = [c.subtitle for c in CF] + [CUSTOMER_FORM[len(CUSTOMER_PREFIX):-4]]
    out = []
    for p in glob.glob(resource_path(os.path.join("forms", "manager", CUSTOMER_PREFIX + "*.pdf"))):
        sub = os.path.basename(p)[len(CUSTOMER_PREFIX):-4]
        if sub not in order:          # retired customer forms (Pre-Fab / Custom Cabinets -> Kitchen Cabinet Layout)
            continue
        out.append({"kind": "customer", "name": os.path.basename(p), "title": sub,
                    "rank": order.index(sub) if sub in order else 99})
    return sorted(out, key=lambda f: f["rank"])


def prefilled(path, cust):
    """A copy of a blank customer form with PROPERTY / YOUR NAME / PHONE / EMAIL filled in for this property
    (still fillable - the manager can change anything). Returns the copy's path."""
    import tempfile

    import pymupdf as fitz
    from .orders import customer_header
    h = customer_header(cust)
    vals = {"Property": h.get("name"), "Manager Name": h.get("contact"),
            "Manager Phone": h.get("contact_phone") or h.get("phone"), "Manager Email": h.get("contact_email")}
    doc = fitz.open(path)
    for page in doc:
        for w in page.widgets():
            v = vals.get(w.field_name)
            if v:
                w.field_value = v
                w.update()
    out_dir = tempfile.mkdtemp(prefix="ais_forms_")
    out = os.path.join(out_dir, os.path.basename(path))
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    return out


def _group(fk):
    if fk in ("horizontal_blind", "vertical_blind", "roller_shade"):
        return "Blinds & Shades"
    if fk in ("window_screen", "screen_door"):
        return "Screens"
    if fk in ("countertop", "cabinet_prefab", "cabinet_custom"):
        return "Cabinets & Countertops"
    if fk in ("trim", "door_hardware", "bath_hardware"):
        return "Trim & Hardware"
    return "Doors"


def _path(kind, name):
    name = os.path.basename(name)
    allowed = {f["name"] for f in (company_forms() if kind == "company" else customer_forms())}
    if name not in allowed:
        abort(404)
    sub = "forms" if kind == "company" else os.path.join("forms", "manager")
    return resource_path(os.path.join(sub, name))


@bp.route("/forms")
@login_required
def index():
    customers = q("SELECT id, name, email FROM customers ORDER BY name COLLATE NOCASE")
    return render_template("forms_lib.html", company=company_forms(), customer=customer_forms(),
                           customers=customers, pre=request.args.get("customer", type=int))


@bp.route("/forms/file/<kind>/<path:name>")
@login_required
def file(kind, name):
    if kind not in ("company", "customer"):
        abort(404)
    return send_file(_path(kind, name), mimetype="application/pdf",
                     as_attachment=bool(request.args.get("dl")), download_name=os.path.basename(name))


@bp.route("/forms/email", methods=["POST"])
@login_required
def email():
    """Make an email DRAFT with the picked blank forms attached (you review and press Send)."""
    from ..emailer import MailError, provider
    from .orders import output_dir
    picks = request.form.getlist("pick")
    files = []
    for p in picks:
        kind, _, name = p.partition("|")
        if kind in ("company", "customer"):
            files.append(_path(kind, name))
    to = request.form.get("to", "").strip()
    cust = q("SELECT * FROM customers WHERE id=?", (request.form.get("customer_id", type=int),), one=True) \
        if request.form.get("customer_id", "").isdigit() else None
    if cust:      # customer forms go out with the property + contact already filled in
        files = [prefilled(f, cust) if os.path.basename(f).startswith(CUSTOMER_PREFIX) else f for f in files]
    if not files:
        flash("Check at least one form to email.", "error")
        return redirect(url_for("library.index"))
    names = ", ".join(os.path.basename(f)[:-4].replace(CUSTOMER_PREFIX, "") for f in files)
    subject = request.form.get("subject", "").strip() or f"Order form{'s' if len(files) > 1 else ''} - {names}"
    body = request.form.get("body", "").strip() or (
        "Hi,\n\nAttached is the order form" + ("s" if len(files) > 1 else "") + " you asked for. Fill it in, "
        "click \"Email Form\" at the bottom (or save it and reply with it attached), and I'll get the order in.\n\n"
        f"Thank you,\n{setting('sales_rep') or ''}")
    prov = provider(setting)
    if prov.name == "eml" and current_app.config.get("CLOUD"):
        import mimetypes
        from email.message import EmailMessage
        m = EmailMessage()
        m["To"], m["Subject"], m["X-Unsent"] = to, subject, "1"
        m.set_content(body)
        for a in files:
            with open(a, "rb") as fh:
                m.add_attachment(fh.read(), maintype="application", subtype="pdf", filename=os.path.basename(a))
        return send_file(io.BytesIO(bytes(m)), mimetype="message/rfc822", as_attachment=True,
                         download_name="Order forms - email.eml")
    try:
        kw = {"out_dir": ensure_dir(os.path.join(output_dir(), "Blank forms"))} if prov.name == "eml" else {}
        msg = prov.send(to, "", subject, body, files, review=True, **kw)
    except MailError as e:
        flash(str(e), "error")
        return redirect(url_for("library.index"))
    except Exception as e:  # noqa: BLE001
        flash(f"Couldn't make the email: {e}", "error")
        return redirect(url_for("library.index"))
    flash(msg + " Nothing is sent until you press Send.", "ok")
    return redirect(url_for("library.index"))
