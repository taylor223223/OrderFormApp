import os
import sys

APP_NAME = "OrderFormApp"


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
    os.makedirs(d, exist_ok=True)
    return d


def is_cloud():
    """True when running on the hosted (phone/tablet) server."""
    return os.environ.get("ORDERAPP_CLOUD") == "1"


def default_output_dir():
    if is_cloud():
        return os.path.join(data_dir(), "output")
    return os.path.join(os.path.expanduser("~"), "Documents", "Order Forms Output")


def ensure_dir(d):
    os.makedirs(d, exist_ok=True)
    return d
