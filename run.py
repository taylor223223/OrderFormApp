"""Start the Order Form App: runs a private local server and opens it in a browser window."""
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from orderapp import create_app
from orderapp.paths import data_dir

HOST = "127.0.0.1"
PORT = int(os.environ.get("ORDERAPP_PORT", "8765"))
URL = f"http://{HOST}:{PORT}/"


def port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0


def already_running():
    try:
        with urllib.request.urlopen(URL + "login", timeout=2) as r:
            return b"Order Form App" in r.read()
    except Exception:  # noqa: BLE001
        return False


def open_window():
    """Prefer an app-style Edge/Chrome window; fall back to the default browser."""
    if os.name == "nt" and not os.environ.get("ORDERAPP_BROWSER_TAB"):
        candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for exe in candidates:
            if os.path.exists(exe):
                try:
                    subprocess.Popen([exe, f"--app={URL}", "--window-size=1320,900"])
                    return
                except OSError:
                    pass
    webbrowser.open(URL)


def main():
    logging.basicConfig(filename=os.path.join(data_dir(), "app.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if port_in_use():
        if already_running():
            open_window()
            return
        print(f"Port {PORT} is used by another program. Set ORDERAPP_PORT to another number.")
        time.sleep(5)
        sys.exit(1)
    app = create_app()
    threading.Timer(1.0, open_window).start()
    from waitress import serve
    logging.info("Starting on %s", URL)
    serve(app, host=HOST, port=PORT, threads=6)


if __name__ == "__main__":
    main()
