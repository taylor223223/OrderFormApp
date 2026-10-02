"""Start AIS Sales Support: runs a private local server and opens it in a browser window."""
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from orderapp import __version__, create_app
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
            body = r.read()
            return b"AIS Sales Support" in body or b"Order Form App" in body
    except Exception:  # noqa: BLE001
        return False


def running_version():
    """Version of the copy already running on our port (None = older than 1.1.1 / unknown)."""
    try:
        with urllib.request.urlopen(URL + "__version", timeout=2) as r:
            return r.read().decode().strip()
    except Exception:  # noqa: BLE001
        return None


def stop_old_copy():
    """Shut down an older Order Form App still running in the background, so this version takes over."""
    try:
        import psutil
    except ImportError:
        return False
    stopped = False
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr and c.laddr.port == PORT and c.status == psutil.CONN_LISTEN and c.pid:
            try:
                p = psutil.Process(c.pid)
                name = (p.name() or "").lower()
                if "orderformapp" in name or "python" in name:
                    for child in p.children(recursive=True):
                        child.kill()
                    p.kill()
                    p.wait(5)
                    stopped = True
            except (psutil.Error, OSError):
                pass
    # PyInstaller one-file exes run as a parent + child; make sure no listener is left
    for _ in range(20):
        if not port_in_use():
            return True
        time.sleep(0.25)
    return stopped and not port_in_use()


def idle_watchdog(app, idle_seconds=180):
    """Close the background server once every app window has been closed.
    Open pages ping /__alive every 30 s; sleep/hibernate gaps are ignored."""
    last_loop = time.time()
    while True:
        time.sleep(15)
        now_t = time.time()
        if now_t - last_loop > 60:          # computer was asleep - don't count that as idle
            app.config["LAST_SEEN"] = now_t
        last_loop = now_t
        if now_t - app.config.get("LAST_SEEN", now_t) > idle_seconds:
            logging.info("No open windows for %s s - shutting down", idle_seconds)
            os._exit(0)


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


def serve_cloud():
    """Hosted mode (Render etc.): listen on $PORT, no browser."""
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get("ORDERAPP_SETUP_KEY"):
        logging.warning("ORDERAPP_SETUP_KEY is not set - first-time setup is disabled until it is.")
    app = create_app()
    port = int(os.environ.get("PORT", "10000"))
    logging.info("Order Form App (cloud) on port %s", port)
    # Render's proxy sets X-Forwarded-*; ProxyFix in create_app() reads them
    serve(app, host="0.0.0.0", port=port, threads=8)


def main():
    if os.environ.get("ORDERAPP_CLOUD") == "1":
        return serve_cloud()
    logging.basicConfig(filename=os.path.join(data_dir(), "app.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if port_in_use():
        ver = running_version()
        if ver == __version__:
            open_window()            # same version already running: just show it
            return
        if already_running() or ver:
            logging.info("Replacing running version %s with %s", ver or "old", __version__)
            if not stop_old_copy():
                logging.error("Could not stop the old copy on port %s", PORT)
                open_window()
                return
        else:
            print(f"Port {PORT} is used by another program. Set ORDERAPP_PORT to another number.")
            time.sleep(5)
            sys.exit(1)
    app = create_app()
    app.config["LAST_SEEN"] = time.time()
    threading.Thread(target=idle_watchdog, args=(app,), daemon=True).start()
    threading.Timer(1.0, open_window).start()
    from waitress import serve
    logging.info("Starting on %s", URL)
    serve(app, host=HOST, port=PORT, threads=6)


if __name__ == "__main__":
    main()
