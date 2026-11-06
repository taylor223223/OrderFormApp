"""v2.31.1: fewer top tabs - Photo order inside New order, Sales and Items & Stock under Company Data, Account Holds
under Customers, Import / Export as one tab. All data is made up."""
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


@pytest.fixture(scope="module")
def c():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    cl = a.test_client()

    def tok(path):
        return re.search(r'name="csrf-token" content="([^"]+)"', cl.get(path).get_data(as_text=True)).group(1)
    cl.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456",
                            "csrf_token": tok("/setup")})
    return cl


def _nav(html):
    return html.split("<nav>")[1].split("</nav>")[0]


def _on(html):
    return re.findall(r'class="on"[^>]*><span class="ic">[^<]*</span>([^<]+)', _nav(html))


def test_top_tabs(c):
    labels = re.findall(r'</span>([^<]+)', _nav(c.get("/").get_data(as_text=True)))
    for gone in ("Photo Order", "Sales", "Items & Stock", "Items &amp; Stock", "Account Holds", "Export", "Import CSV"):
        assert gone not in labels, gone
    assert {"Dashboard", "Routes", "New Order", "Customers", "Company Data", "Import / Export"} <= set(labels)


@pytest.mark.parametrize("url,tab", [("/orders/new", "New Order"), ("/field-order", "New Order"),
                                     ("/sales", "Company Data"), ("/items", "Company Data"),
                                     ("/holds", "Customers"), ("/export", "Import / Export"),
                                     ("/import", "Import / Export")])
def test_pages_light_up_their_tab(c, url, tab):
    r = c.get(url, follow_redirects=True)
    assert r.status_code == 200, url
    assert _on(r.get_data(as_text=True)) == [tab], url


def test_where_things_moved(c):
    assert "Photo order" in c.get("/orders/new").get_data(as_text=True)
    sales = c.get("/sales").get_data(as_text=True)
    assert "My Sales" in sales and "Items &amp; Stock" in sales            # Company Data sub-tabs
    assert "Account Holds" in c.get("/customers").get_data(as_text=True)
    exp = c.get("/export").get_data(as_text=True)
    assert "Import CSV / Excel" in exp and "📤 Export" in exp
