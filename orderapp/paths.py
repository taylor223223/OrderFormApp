import os
import sys

APP_NAME = "OrderFormApp"


def is_demo():
    """Demo copy: made-up data in its own folder. On when the exe's name has "demo" in it
    (the AIS-Sales-Support-Demo.exe download) or ORDERAPP_DEMO=1."""
    if os.environ.get("ORDERAPP_DEMO") == "1":
        return True
    return bool(getattr(sys, "frozen", False)) and "demo" in os.path.basename(sys.executable).lower()


def resource_path(rel):
    """Path to a bundled file (works from source and inside the PyInstaller exe)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "orderapp", rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def data_dir():
    """Where the database and private files live. Never inside the git repo."""
    d = os.environ.get("ORDERAPP_DATA")
    if not d:
        if os.name == "nt":
            d = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
        else:
            d = os.path.join(os.path.expanduser("~"), "." + APP_NAME.lower())
        if is_demo():
            d += " Demo"          # never mixes with the real database
    os.makedirs(d, exist_ok=True)
    return d


def is_cloud():
    """True when running on the hosted (phone/tablet) server."""
    return os.environ.get("ORDERAPP_CLOUD") == "1"


def default_output_dir():
    if is_cloud() or is_demo():
        return os.path.join(data_dir(), "output")
    return os.path.join(os.path.expanduser("~"), "Documents", "Order Forms Output")


def ensure_dir(d):
    os.makedirs(d, exist_ok=True)
    return d
