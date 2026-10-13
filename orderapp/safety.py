"""Keeping your data safe.

Where it lives: the database (customers, orders, measurements, sales...) is ONE file, orderapp.db, in the app's
data folder (Windows: %LOCALAPPDATA%\\OrderFormApp). The installer only replaces the program in
%LOCALAPPDATA%\\Programs\\AIS Sales Support - it never touches the data folder, and uninstalling leaves it too.
New versions only ADD tables/columns (never drop or rewrite data).

Belt and braces: every time the app starts on a new version it first copies the database to
<data folder>\\backups\\before-v<version>-<date>.db, and it keeps a daily copy (last 14 days).
"""
import glob
import os
import sqlite3
from datetime import date


def _copy(src, dst):
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _stored_version(path):
    try:
        con = sqlite3.connect(path)
        try:
            r = con.execute("SELECT value FROM settings WHERE key='app_version'").fetchone()
            return r[0] if r else None
        finally:
            con.close()
    except sqlite3.Error:
        return None


def backups_dir(path):
    d = os.path.join(os.path.dirname(os.path.abspath(path)), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def protect(path, version):
    """Call before the database is opened/upgraded. Returns the backup made (or None)."""
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    made = None
    bdir = backups_dir(path)
    today = date.today().isoformat()
    old = _stored_version(path)
    try:
        if old != version:
            made = os.path.join(bdir, f"before-v{version}-{today}.db")
            if not os.path.exists(made):
                _copy(path, made)
        daily = os.path.join(bdir, f"daily-{today}.db")
        if not os.path.exists(daily):
            _copy(path, daily)
    except (sqlite3.Error, OSError):
        return None
    for pattern, keep in (("daily-*.db", 14), ("before-v*.db", 10)):
        files = sorted(glob.glob(os.path.join(bdir, pattern)), key=os.path.getmtime, reverse=True)
        for f in files[keep:]:
            try:
                os.remove(f)
            except OSError:
                pass
    return made


def mark_version(con, version):
    con.execute("INSERT INTO settings(key, value) VALUES('app_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (version,))
    con.commit()


def status(path):
    """Facts for the Settings page: where the data is, how big, what's in it, latest backups."""
    out = {"path": os.path.abspath(path), "folder": os.path.dirname(os.path.abspath(path)), "size": 0,
           "backups": [], "counts": {}}
    if os.path.exists(path):
        out["size"] = os.path.getsize(path)
    bdir = os.path.join(out["folder"], "backups")
    if os.path.isdir(bdir):
        files = sorted(glob.glob(os.path.join(bdir, "*.db")), key=os.path.getmtime, reverse=True)
        out["backups"] = [{"name": os.path.basename(f), "size": os.path.getsize(f),
                           "when": date.fromtimestamp(os.path.getmtime(f)).isoformat()} for f in files[:6]]
        out["backup_count"] = len(files)
    try:
        con = sqlite3.connect(path)
        for t, label in (("customers", "customers"), ("orders", "orders"), ("measurements", "measurements"),
                         ("units", "units"), ("quotes", "quotes"), ("sales", "my invoices"),
                         ("co_docs", "company invoices"), ("activities", "CRM activities")):
            try:
                out["counts"][label] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                pass
        con.close()
    except sqlite3.Error:
        pass
    return out
