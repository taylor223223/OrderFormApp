"""Hosted (phone/tablet) mode."""
import io
import os
import re
import sqlite3
import tempfile

import pytest


@pytest.fixture()
def cloud(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("ORDERAPP_DATA", d)
    monkeypatch.setenv("ORDERAPP_CLOUD", "1")
    monkeypatch.setenv("ORDERAPP_SETUP_KEY", "letmein-123")
    from orderapp import create_app
    app = create_app(db_path=os.path.join(d, "orderapp.db"))
    c = app.test_client()
    c.app = app
    return c


def tok(c, path):
    return re.search(r'csrf-token" content="([^"]+)', c.get(path, base_url="https://orders.example.com",
                                                          follow_redirects=True).get_data(as_text=True)).group(1)


def post(c, path, data, page):
    data = dict(data, csrf_token=tok(c, page))
    return c.post(path, data=data, base_url="https://orders.example.com",
                  content_type="multipart/form-data" if any(isinstance(v, tuple) for v in data.values()) else None)


def test_cloud_setup_needs_key_and_pwa(cloud):
    c = cloud
    # public host allowed in cloud mode
    assert c.get("/login", base_url="https://orders.example.com").status_code in (200, 302)
    r = post(c, "/setup", {"username": "taylor", "password": "averylongpass", "password2": "averylongpass"}, "/setup")
    assert "Wrong setup key" in r.get_data(as_text=True)
    r = post(c, "/setup", {"setup_key": "letmein-123", "username": "taylor", "password": "short123",
                           "password2": "short123"}, "/setup")
    assert "at least 10" in r.get_data(as_text=True)
    r = post(c, "/setup", {"setup_key": "letmein-123", "username": "taylor", "password": "averylongpass",
                           "password2": "averylongpass"}, "/setup")
    assert r.status_code == 302
    assert c.get("/sw.js", base_url="https://orders.example.com").headers["Service-Worker-Allowed"] == "/"
    assert b"standalone" in c.get("/manifest.webmanifest", base_url="https://orders.example.com").data
    page = c.get("/settings", base_url="https://orders.example.com").get_data(as_text=True)
    assert "Outlook on this PC" not in page and "Restore" in page


def test_restore_backup(cloud, tmp_path):
    c = cloud
    post(c, "/setup", {"setup_key": "letmein-123", "username": "taylor", "password": "averylongpass",
                       "password2": "averylongpass"}, "/setup")
    # build a "PC" backup with its own login and a customer
    os.environ["ORDERAPP_DATA"] = str(tmp_path)
    from orderapp.db import init_db
    from werkzeug.security import generate_password_hash
    pc = str(tmp_path / "pc.db")
    init_db(pc)
    con = sqlite3.connect(pc)
    con.execute("INSERT INTO users(username, password_hash) VALUES (?,?)", ("pcuser", generate_password_hash("pcpassword1")))
    con.execute("INSERT INTO customers(name) VALUES ('Sunrise Villas')")
    con.commit()
    con.close()
    r = post(c, "/settings/restore", {"backup": (io.BytesIO(open(pc, "rb").read()), "backup.db")}, "/settings")
    assert "/login" in r.headers["Location"]
    r = post(c, "/login", {"username": "pcuser", "password": "pcpassword1"}, "/login")
    assert r.status_code == 302
    assert "Sunrise Villas" in c.get("/customers", base_url="https://orders.example.com").get_data(as_text=True)
