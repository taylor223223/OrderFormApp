"""v2.32.1: quote requests have their own steps on the Quotes tab (request sent -> quote received -> quote sent ->
approved / ordered), who the quote goes to on the form, Order or Quote picked first. All data is made up."""
import json
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
        from orderapp.db import x
        a.cid = x("INSERT INTO customers(name, address, city, state, zip, status) VALUES "
                  "('Sample Palms (made up)', '1 Test Rd', 'Phoenix', 'AZ', '85020', 'Active')")
    return a


@pytest.fixture(scope="module")
def c(app):
    cl = app.test_client()

    def tok(path):
        return re.search(r'name="csrf-token" content="([^"]+)"', cl.get(path).get_data(as_text=True)).group(1)
    cl.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456",
                            "csrf_token": tok("/setup")})
    cl.t = tok("/")
    return cl


def _order(app, oid):
    with app.app_context():
        from orderapp.db import q
        return dict(q("SELECT * FROM orders WHERE id=?", (oid,), one=True))


def _tap(c, oid, tap):
    return c.post(f"/orders/{oid}/status", data={"csrf_token": c.t, "tap": tap, "next": "/quotes"})


def test_pick_order_or_quote_first(c):
    page = c.get("/orders/new").get_data(as_text=True)
    assert 'data-kind="quote"' in page and "Order or quote?" in page and "Photo order" in page
    r = c.get("/orders/new?form=door&kind=quote")
    assert "kind=quote" in r.headers["Location"]
    ed = c.get("/orders/editor?form=door&kind=quote").get_data(as_text=True)
    assert 'id="quoteBox"' in ed and 'data-h="quote_to"' in ed and 'data-h="quote_via"' in ed
    assert 'id="quoteBox" hidden' not in ed
    assert 'id="quoteBox" hidden' in c.get("/orders/editor?form=door").get_data(as_text=True)


def test_quote_steps(app, c):
    payload = {"header": {"name": "Sample Palms (made up)", "po": "Q-7", "quote_to": "Pat Sample",
                          "quote_via": "Email", "quote_email": "pat@example.com"},
               "blocks": [{"qty": "1", "unit": "9", "width": "30", "height": "80"}]}
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "door", "customer_id": app.cid, "kind": "quote",
                                     "payload": json.dumps(payload), "action": "save"})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    page = c.get("/quotes").get_data(as_text=True)
    assert "Not sent yet" in page and "Pat Sample" in page and "mailto:pat@example.com" in page
    # quotes stay off the order board, with a link over
    trk = c.get("/tracking").get_data(as_text=True)
    assert f"#{oid}</a>" not in trk and "Quotes (1 open)" in trk
    for tap, st, heading in [("Request sent", "Sent", "waiting on pricing"),
                             ("Quote received", "Confirmed", "send it to the customer"),
                             ("Quote sent", "Quoted", "waiting on the customer")]:
        _tap(c, oid, tap)
        o = _order(app, oid)
        assert (o["kind"], o["status"]) == ("quote", st), tap
        assert heading in c.get("/quotes").get_data(as_text=True)
    assert _order(app, oid)["sent_at"]                         # the request went in
    _tap(c, oid, "Approved / ordered")
    o = _order(app, oid)
    assert (o["kind"], o["status"], o["title"]) == ("order", "Ready", "Door Order Form")
    assert o["quote_approved_at"] and not o["sent_at"]         # "sent" is now when the order goes in
    assert f"#{oid}</a>" in c.get("/tracking").get_data(as_text=True)
    assert "Approved / ordered (last 60 days)" in c.get("/quotes?stage=approved").get_data(as_text=True)
    assert "Quote approved" in c.get(f"/orders/{oid}").get_data(as_text=True)


def test_declined(app, c):
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "door", "customer_id": app.cid, "kind": "quote",
                                     "payload": json.dumps({"header": {}, "blocks": [{"qty": "1"}]}), "action": "save"})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    _tap(c, oid, "Declined")
    assert (_order(app, oid)["kind"], _order(app, oid)["status"]) == ("quote", "Cancelled")
    assert f"#{oid}</a>" in c.get("/quotes?stage=declined").get_data(as_text=True)


def test_sales_and_items_are_top_tabs_again(c):
    nav = c.get("/").get_data(as_text=True).split("<nav>")[1].split("</nav>")[0]
    assert "Sales</a>" in nav and "Items &amp; Stock</a>" in nav
    comp = c.get("/company").get_data(as_text=True).split("</nav>", 1)[1]
    assert "My Sales" not in comp and "Items &amp; Stock" not in comp
