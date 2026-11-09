"""Talk to Claude (Anthropic Messages API) with the key from Settings.

ask(model, system, prompt) -> text; ask_json(...) -> dict. Every call's token use is logged in ai_usage with an
estimated cost, so Settings can show the month's spend and stop at the monthly limit.
"""
import json
import re
from datetime import datetime

from ..db import now, q, setting, x

API = "https://api.anthropic.com/v1/messages"
# $ per million tokens (in, out) - from Anthropic's pricing page, 11/2026
PRICES = {"haiku": (0.10, 0.50), "sonnet": (2.0, 10.0), "opus": (5.0, 25.0)}


class AIError(Exception):
    pass


class AITransient(AIError):
    """Offline / Claude busy - try the same email again on the next check instead of giving up on it."""


def _price(model):
    for k, v in PRICES.items():
        if k in (model or ""):
            return v
    return PRICES["sonnet"]


def month_spend():
    r = q("SELECT COALESCE(SUM(cost), 0) c FROM ai_usage WHERE substr(ts, 1, 7)=?",
          (datetime.now().strftime("%Y-%m"),), one=True)
    return round(r["c"] or 0, 4)


def over_limit():
    try:
        lim = float(setting("ai_month_limit") or 0)
    except ValueError:
        lim = 0
    return lim > 0 and month_spend() >= lim


def _post(body, key, version):
    import requests
    return requests.post(API, json=body, timeout=120, headers={
        "x-api-key": key, "anthropic-version": version, "content-type": "application/json"})


def ask(model, system, prompt, max_tokens=2000, purpose=""):
    """One message to Claude; returns the reply text. If Claude ran out of room before writing its answer (long
    emails make it think longer), it's asked again with more room."""
    text, data = _ask_once(model, system, prompt, max_tokens, purpose)
    if not text.strip() and data.get("stop_reason") == "max_tokens" and max_tokens < 16000:
        text, data = _ask_once(model, system, prompt, min(max_tokens * 4, 16000), purpose + " (more room)")
    if not text.strip():
        kinds = ", ".join(sorted({b.get("type", "?") for b in data.get("content") or []})) or "nothing"
        raise AIError(f"Claude gave no written answer (stopped: {data.get('stop_reason')}; got: {kinds}).")
    return text


def _ask_once(model, system, prompt, max_tokens, purpose):
    key = (setting("ai_api_key") or "").strip()
    if not key:
        raise AIError("No Anthropic API key - add it in Settings -> AI Assistant.")
    if over_limit():
        raise AIError("This month's AI spending limit is reached (Settings -> AI Assistant).")
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": prompt}]}
    version = setting("ai_api_version") or "2026-10-21"
    try:
        r = _post(body, key, version)
        if r.status_code == 400 and "version" in r.text.lower() and version != "2023-06-01":
            r = _post(body, key, "2023-06-01")
    except Exception as e:  # noqa: BLE001
        raise AITransient(f"Couldn't reach Claude (no internet?): {e}") from e
    if r.status_code in (408, 429, 500, 502, 503, 504, 529):
        raise AITransient(f"Claude is busy right now ({r.status_code}) - will try again.")
    if r.status_code >= 400:
        raise AIError(f"Claude API error {r.status_code}: {r.text[:300]}")
    data = r.json()
    u = data.get("usage") or {}
    pin, pout = _price(model)
    tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    x("INSERT INTO ai_usage(ts, model, purpose, in_tok, out_tok, cost) VALUES (?,?,?,?,?,?)",
      (now(), model, purpose, tin, tout, (tin * pin + tout * pout) / 1e6))
    return "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text"), data


def call(model, system, messages, tools=None, max_tokens=2000, purpose=""):
    """One Messages API call with a full message list (and tools). Returns the response dict."""
    key = (setting("ai_api_key") or "").strip()
    if not key:
        raise AIError("No Anthropic API key - add it in Settings -> AI Assistant.")
    if over_limit():
        raise AIError("This month's AI spending limit is reached (Settings -> AI Assistant).")
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools:
        body["tools"] = tools
    version = setting("ai_api_version") or "2026-10-21"
    try:
        r = _post(body, key, version)
        if r.status_code == 400 and "version" in r.text.lower() and version != "2023-06-01":
            r = _post(body, key, "2023-06-01")
    except Exception as e:  # noqa: BLE001
        raise AITransient(f"Couldn't reach Claude (no internet?): {e}") from e
    if r.status_code in (408, 429, 500, 502, 503, 504, 529):
        raise AITransient(f"Claude is busy right now ({r.status_code}) - will try again.")
    if r.status_code >= 400:
        raise AIError(f"Claude API error {r.status_code}: {r.text[:300]}")
    data = r.json()
    u = data.get("usage") or {}
    pin, pout = _price(model)
    tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    x("INSERT INTO ai_usage(ts, model, purpose, in_tok, out_tok, cost) VALUES (?,?,?,?,?,?)",
      (now(), model, purpose, tin, tout, (tin * pin + tout * pout) / 1e6))
    return data


def parse_json(text):
    """The JSON object in a reply (tolerates ```json fences and a sentence around it)."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j < i:
            raise AIError(f"Claude's answer had no JSON in it (it said: {t[:160]!r}).")
        t = t[i:j + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise AIError(f"Claude's answer wasn't valid JSON: {e}") from e


def ask_json(model, system, prompt, max_tokens=3000, purpose=""):
    """Ask for a JSON answer; if Claude answers in sentences, ask once more, firmly."""
    try:
        return parse_json(ask(model, system, prompt, max_tokens=max_tokens, purpose=purpose))
    except AITransient:
        raise
    except AIError:
        return parse_json(ask(model, system + "\n\nIMPORTANT: answer with ONE JSON object only - no other text. "
                              "If the email is unclear, still answer in JSON (use your best guess and say so).",
                              prompt, max_tokens=max_tokens, purpose=purpose + " (retry)"))


def test_key():
    """(ok, message) - a tiny call to check the key works."""
    try:
        txt = ask(setting("ai_model_fast") or "claude-haiku-5-5", "Reply with the single word OK.", "Test",
                  max_tokens=5, purpose="test")
        return True, f"Connected - Claude replied \"{txt.strip()[:20]}\"."
    except AIError as e:
        return False, str(e)
