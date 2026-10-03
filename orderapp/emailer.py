"""Outlook email: read order requests and send completed forms.

Providers
---------
outlook_desktop : the Outlook app installed on this PC (classic Outlook). No setup,
                  uses whatever account Outlook is signed in to. Windows only.
graph           : Microsoft 365 / Outlook.com through Microsoft Graph. Works with
                  "new Outlook" too. Needs an Azure app registration client ID once.
eml             : no connection. Creates an email draft file with the PDF attached
                  and opens it in your default mail app for you to press Send.
"""
import base64
import mimetypes
import email
import email.policy
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage

from .paths import data_dir

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite", "Mail.Send", "User.Read"]


class MailError(Exception):
    pass


def open_file(path):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return                       # never pop windows during automated tests
    if os.name == "nt":
        os.startfile(path)  # noqa: S606  (Windows only)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# --------------------------------------------------------------------------
# Outlook desktop (COM)
# --------------------------------------------------------------------------
class OutlookDesktop:
    name = "outlook_desktop"

    @staticmethod
    def available():
        if os.name != "nt":
            return False, "Outlook desktop automation only works on Windows."
        try:
            import win32com.client  # noqa: F401
            return True, ""
        except ImportError:
            return False, "pywin32 is not installed."

    def _ns(self):
        ok, why = self.available()
        if not ok:
            raise MailError(why)
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            app = win32com.client.Dispatch("Outlook.Application")
            return app, app.GetNamespace("MAPI")
        except Exception as e:  # noqa: BLE001
            raise MailError("Could not open Outlook. Is classic Outlook installed and set up? "
                            "(The 'new Outlook' app does not support this - use Microsoft 365 sign-in "
                            f"in Settings instead.) Details: {e}") from e

    @staticmethod
    def _sender(item):
        try:
            if item.SenderEmailType == "EX":
                ex = item.Sender.GetExchangeUser()
                if ex is not None:
                    return ex.PrimarySmtpAddress
            return item.SenderEmailAddress
        except Exception:  # noqa: BLE001
            return ""

    def list_messages(self, days=14, limit=75, unread_only=False, search="", subject_word=""):
        _, ns = self._ns()
        inbox = ns.GetDefaultFolder(6)
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)
        since = (datetime.now() - timedelta(days=int(days))).strftime("%m/%d/%Y %H:%M %p")
        flt = f"[ReceivedTime] >= '{since}'"
        if unread_only:
            flt += " AND [UnRead] = True"
        items = items.Restrict(flt)
        out = []
        it = items.GetFirst()
        s = (search or "").lower()
        sw = (subject_word or "").strip().lower()
        while it is not None and len(out) < int(limit):
            try:
                if it.Class == 43:  # MailItem
                    subj = it.Subject or ""
                    if sw and sw not in subj.lower():     # cheap check first - skip the body entirely
                        it = items.GetNext()
                        continue
                    body = it.Body or ""
                    if not s or s in subj.lower() or s in body[:3000].lower() or s in (it.SenderName or "").lower():
                        out.append({
                            "msg_id": it.EntryID, "subject": subj, "sender": self._sender(it),
                            "sender_name": it.SenderName or "", "received": str(it.ReceivedTime)[:16],
                            "preview": " ".join(body.split())[:180], "has_attachments": it.Attachments.Count > 0,
                        })
            except Exception:  # noqa: BLE001
                pass
            it = items.GetNext()
        return out

    def get_message(self, msg_id):
        _, ns = self._ns()
        it = ns.GetItemFromID(msg_id)
        atts = []
        tmp = os.path.join(data_dir(), "tmp")
        os.makedirs(tmp, exist_ok=True)
        for i in range(1, it.Attachments.Count + 1):
            a = it.Attachments.Item(i)
            fn = a.FileName or f"attachment{i}"
            if fn.lower().endswith((".pdf", ".csv", ".xlsx")):
                p = os.path.join(tmp, f"att_{i}_{fn}")
                a.SaveAsFile(p)
                with open(p, "rb") as fh:
                    atts.append((fn, fh.read()))
                os.remove(p)
        return {"msg_id": msg_id, "subject": it.Subject or "", "sender": self._sender(it),
                "sender_name": it.SenderName or "", "received": str(it.ReceivedTime)[:16],
                "body": it.Body or "", "attachments": atts}

    def send(self, to, cc, subject, body, attachments, review=True):
        app, _ = self._ns()
        mail = app.CreateItem(0)
        mail.To = to
        if cc:
            mail.CC = cc
        mail.Subject = subject
        mail.Body = body
        for p in attachments:
            mail.Attachments.Add(os.path.abspath(p))
        if review:
            mail.Display(False)
            return "Opened in Outlook for you to review and send."
        mail.Send()
        return "Sent from Outlook."


# --------------------------------------------------------------------------
# Microsoft Graph (Microsoft 365 / Outlook.com / new Outlook)
# --------------------------------------------------------------------------
class GraphMail:
    name = "graph"
    _flows = {}
    _lock = threading.Lock()

    def __init__(self, client_id, tenant="common"):
        self.client_id = (client_id or "").strip()
        self.tenant = (tenant or "common").strip()
        self.cache_path = os.path.join(data_dir(), "graph_token_cache.json")

    def available(self):
        if not self.client_id:
            return False, "Enter your Microsoft app (client) ID in Settings first."
        try:
            import msal  # noqa: F401
            import requests  # noqa: F401
            return True, ""
        except ImportError:
            return False, "msal / requests not installed."

    def _app(self):
        import msal
        cache = msal.SerializableTokenCache()
        if os.path.exists(self.cache_path):
            with open(self.cache_path, encoding="utf-8") as fh:
                cache.deserialize(fh.read())
        app = msal.PublicClientApplication(self.client_id, authority=f"https://login.microsoftonline.com/{self.tenant}",
                                           token_cache=cache)
        return app, cache

    def _save(self, cache):
        if cache.has_state_changed:
            with open(self.cache_path, "w", encoding="utf-8") as fh:
                fh.write(cache.serialize())

    def signed_in_as(self):
        ok, _ = self.available()
        if not ok:
            return None
        app, _ = self._app()
        accts = app.get_accounts()
        return accts[0].get("username") if accts else None

    def start_sign_in(self):
        app, cache = self._app()
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise MailError("Could not start Microsoft sign-in: " + json.dumps(flow))
        state = {"status": "waiting", "message": flow.get("message"), "code": flow["user_code"],
                 "url": flow.get("verification_uri")}
        GraphMail._flows[self.client_id] = state

        def worker():
            res = app.acquire_token_by_device_flow(flow)
            with GraphMail._lock:
                self._save(cache)
                if "access_token" in res:
                    state["status"] = "done"
                else:
                    state["status"] = "error"
                    state["message"] = res.get("error_description", "Sign-in failed")
        threading.Thread(target=worker, daemon=True).start()
        return state

    def sign_in_state(self):
        return GraphMail._flows.get(self.client_id)

    def sign_out(self):
        if os.path.exists(self.cache_path):
            os.remove(self.cache_path)

    def _token(self):
        ok, why = self.available()
        if not ok:
            raise MailError(why)
        app, cache = self._app()
        accts = app.get_accounts()
        if not accts:
            raise MailError("Not signed in to Microsoft. Go to Settings > Email and sign in.")
        res = app.acquire_token_silent(SCOPES, account=accts[0])
        self._save(cache)
        if not res or "access_token" not in res:
            raise MailError("Microsoft sign-in expired. Sign in again in Settings.")
        return res["access_token"]

    def _req(self, method, path, **kw):
        import requests
        h = {"Authorization": "Bearer " + self._token()}
        r = requests.request(method, GRAPH + path, headers=h, timeout=60, **kw)
        if r.status_code >= 400:
            raise MailError(f"Microsoft Graph error {r.status_code}: {r.text[:300]}")
        return r.json() if r.content and "json" in r.headers.get("Content-Type", "") else {}

    def list_messages(self, days=14, limit=75, unread_only=False, search="", subject_word=""):
        since = (datetime.utcnow() - timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%SZ")
        flt = f"receivedDateTime ge {since}"
        if unread_only:
            flt += " and isRead eq false"
        params = {"$top": str(min(int(limit), 100)), "$orderby": "receivedDateTime desc", "$filter": flt,
                  "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments"}
        if search:
            params = {"$top": str(min(int(limit), 100)), "$search": f'"{search}"',
                      "$select": "id,subject,from,receivedDateTime,bodyPreview,hasAttachments"}
        data = self._req("GET", "/me/mailFolders/inbox/messages", params=params)
        out = []
        for m in data.get("value", []):
            fr = (m.get("from") or {}).get("emailAddress", {})
            out.append({"msg_id": m["id"], "subject": m.get("subject") or "", "sender": fr.get("address", ""),
                        "sender_name": fr.get("name", ""), "received": (m.get("receivedDateTime") or "")[:16].replace("T", " "),
                        "preview": m.get("bodyPreview", "")[:180], "has_attachments": m.get("hasAttachments")})
        sw = (subject_word or "").strip().lower()
        if sw:
            out = [m for m in out if sw in (m["subject"] or "").lower()]
        return out

    def get_message(self, msg_id):
        import html as _html
        import re
        m = self._req("GET", f"/me/messages/{msg_id}",
                      params={"$select": "id,subject,from,receivedDateTime,body,hasAttachments"})
        body = (m.get("body") or {}).get("content", "")
        if (m.get("body") or {}).get("contentType") == "html":
            body = re.sub(r"<(br|/p|/div|/tr)[^>]*>", "\n", body, flags=re.I)
            body = _html.unescape(re.sub(r"<[^>]+>", "", body))
        atts = []
        if m.get("hasAttachments"):
            for a in self._req("GET", f"/me/messages/{msg_id}/attachments").get("value", []):
                fn = a.get("name", "")
                if a.get("contentBytes") and fn.lower().endswith((".pdf", ".csv", ".xlsx")):
                    atts.append((fn, base64.b64decode(a["contentBytes"])))
        fr = (m.get("from") or {}).get("emailAddress", {})
        return {"msg_id": msg_id, "subject": m.get("subject") or "", "sender": fr.get("address", ""),
                "sender_name": fr.get("name", ""), "received": (m.get("receivedDateTime") or "")[:16],
                "body": body, "attachments": atts}

    def send(self, to, cc, subject, body, attachments, review=True):
        def rcpts(s):
            return [{"emailAddress": {"address": a.strip()}} for a in (s or "").replace(";", ",").split(",") if a.strip()]
        msg = {"subject": subject, "body": {"contentType": "Text", "content": body},
               "toRecipients": rcpts(to), "ccRecipients": rcpts(cc), "attachments": []}
        for p in attachments:
            with open(p, "rb") as fh:
                msg["attachments"].append({"@odata.type": "#microsoft.graph.fileAttachment",
                                           "name": os.path.basename(p),
                                           "contentType": mimetypes.guess_type(p)[0] or "application/octet-stream",
                                           "contentBytes": base64.b64encode(fh.read()).decode()})
        if review:
            self._req("POST", "/me/messages", json=msg)
            return "Saved to your Outlook Drafts folder - open Outlook to review and send."
        self._req("POST", "/me/sendMail", json={"message": msg, "saveToSentItems": True})
        return "Sent through Microsoft 365."


# --------------------------------------------------------------------------
# Draft file fallback
# --------------------------------------------------------------------------
class EmlDraft:
    name = "eml"

    @staticmethod
    def available():
        return True, ""

    def list_messages(self, **_kw):
        raise MailError("Inbox reading needs Outlook desktop or Microsoft 365 sign-in (Settings). "
                        "You can still drop an .eml/.msg file below.")

    def get_message(self, _msg_id):
        raise MailError("Not supported")

    def send(self, to, cc, subject, body, attachments, review=True, out_dir=None):
        m = EmailMessage()
        m["To"] = to
        if cc:
            m["Cc"] = cc
        m["Subject"] = subject
        m["X-Unsent"] = "1"
        m.set_content(body)
        for p in attachments:
            with open(p, "rb") as fh:
                mt = (mimetypes.guess_type(p)[0] or "application/octet-stream").split("/")
                m.add_attachment(fh.read(), maintype=mt[0], subtype=mt[1], filename=os.path.basename(p))
        folder = out_dir or (os.path.dirname(attachments[0]) if attachments else data_dir())
        base = os.path.splitext(os.path.basename(attachments[0]))[0] if attachments else "draft"
        path = os.path.join(folder, base + " - email.eml")
        with open(path, "wb") as fh:
            fh.write(bytes(m))
        try:
            open_file(path)
        except Exception:  # noqa: BLE001
            pass
        return f"Email draft created ({os.path.basename(path)}) and opened in your mail app."


def provider(settings_get):
    p = settings_get("email_provider")
    if p == "graph":
        return GraphMail(settings_get("graph_client_id"), settings_get("graph_tenant"))
    if p == "outlook_desktop":
        return OutlookDesktop()
    return EmlDraft()


def parse_uploaded_message(filename, raw):
    """Parse a dropped .eml or .msg file -> message dict."""
    fn = filename.lower()
    if fn.endswith(".msg"):
        try:
            import extract_msg
        except ImportError as e:
            raise MailError("Reading .msg files needs the extract-msg package.") from e
        tmp = os.path.join(data_dir(), "tmp")
        os.makedirs(tmp, exist_ok=True)
        p = os.path.join(tmp, "upload.msg")
        with open(p, "wb") as fh:
            fh.write(raw)
        m = extract_msg.Message(p)
        atts = []
        for a in m.attachments:
            name = getattr(a, "longFilename", None) or getattr(a, "shortFilename", None) or "att"
            if str(name).lower().endswith((".pdf", ".csv", ".xlsx")) and isinstance(a.data, bytes):
                atts.append((name, a.data))
        sender = m.sender or ""
        name, addr = email.utils.parseaddr(sender)
        res = {"msg_id": None, "subject": m.subject or "", "sender": addr, "sender_name": name,
               "received": str(m.date or "")[:16], "body": m.body or "", "attachments": atts}
        m.close()
        os.remove(p)
        return res
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        body = part.get_content()
        if part.get_content_type() == "text/html":
            import html as _html
            import re
            body = _html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<(br|/p|/div)[^>]*>", "\n", body, flags=re.I)))
    atts = []
    for a in msg.iter_attachments():
        fnm = a.get_filename() or ""
        if fnm.lower().endswith((".pdf", ".csv", ".xlsx")):
            atts.append((fnm, a.get_payload(decode=True)))
    name, addr = email.utils.parseaddr(msg.get("From", ""))
    return {"msg_id": None, "subject": msg.get("Subject", ""), "sender": addr, "sender_name": name,
            "received": msg.get("Date", "")[:25], "body": body, "attachments": atts}
