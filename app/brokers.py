"""Broker registry: what the picker shows, what each needs, and how to build its provider."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .models import now_ist
from .providers.broker import token_valid_until_reset
from .providers.dhan import DhanProvider
from .providers.fyers import FyersProvider
from .providers.upstox import UpstoxProvider, token_still_valid
from .providers.zerodha import ZerodhaProvider

BASE = "http://127.0.0.1:8000"

BROKERS = {
    "upstox": {
        "name": "Upstox", "status": "ready", "auth": "oauth",
        "cost": "Free with an Upstox account",
        "fields": [
            {"key": "api_key", "label": "API key"},
            {"key": "api_secret", "label": "API secret", "secret": True},
            {"key": "redirect_uri", "label": "Redirect URL", "default": f"{BASE}/auth/callback"},
        ],
        "portal": "account.upstox.com/developer/apps",
        "steps": [
            "Open account.upstox.com/developer/apps and click New App.",
            f"Set Redirect URL to {BASE}/auth/callback",
            "Copy the API key and secret here, Save, then Connect. Login expires daily at 3:30 AM.",
        ],
    },
    "fyers": {
        "name": "Fyers", "status": "beta", "auth": "oauth",
        "cost": "Free with a Fyers account",
        "fields": [
            {"key": "app_id", "label": "App ID (looks like ABCD1234-100)"},
            {"key": "secret", "label": "Secret ID", "secret": True},
            {"key": "redirect_uri", "label": "Redirect URL", "default": f"{BASE}/auth/fyers/callback"},
        ],
        "portal": "myapi.fyers.in/dashboard",
        "steps": [
            "Open myapi.fyers.in/dashboard and click Create App.",
            f"Set Redirect URL to {BASE}/auth/fyers/callback and tick the data permissions.",
            "Copy the App ID (ends in -100) and Secret ID here, Save, then Connect. Login expires every morning.",
        ],
    },
    "zerodha": {
        "name": "Zerodha", "status": "beta", "auth": "oauth",
        "cost": "Needs paid Kite Connect (₹500/month); the free Personal plan has no market data",
        "fields": [
            {"key": "api_key", "label": "API key"},
            {"key": "api_secret", "label": "API secret", "secret": True},
        ],
        "portal": "developers.kite.trade",
        "steps": [
            "Open developers.kite.trade, create a Kite Connect app (paid plan).",
            f"Set Redirect URL to {BASE}/auth/zerodha/callback",
            "Copy the API key and secret here, Save, then Connect. Login expires every morning at 6 AM.",
        ],
    },
    "dhan": {
        "name": "Dhan", "status": "beta", "auth": "token",
        "cost": "Needs Dhan Data API subscription (₹499/month)",
        "fields": [
            {"key": "client_id", "label": "Client ID"},
            {"key": "token", "label": "Access token", "secret": True},
        ],
        "portal": "web.dhan.co",
        "steps": [
            "Log in to web.dhan.co → My Profile → Access DhanHQ APIs.",
            "Make sure the Data API subscription is active, then generate an Access Token.",
            "Paste your Client ID and the token here and click Save & connect. Tokens last 24 hours.",
        ],
    },
    "kotak": {
        "name": "Kotak Neo", "status": "unsupported", "auth": None, "cost": "",
        "reason": "Kotak Neo's API has no historical candle data (Kotak's support page says it is "
                  "\"unavailable at the moment\"), and order blocks need past candles. "
                  "It could be added later using candles recorded locally while the app runs.",
        "fields": [], "steps": [],
    },
    "sharekhan": {
        "name": "Sharekhan", "status": "unsupported", "auth": None, "cost": "",
        "reason": "Sharekhan's public API has no REST price-quote endpoint and doesn't document its intraday "
                  "candle intervals, so it can't be built reliably without a real account to test with.",
        "fields": [], "steps": [],
    },
}


def token_ok(key: str, creds: dict) -> bool:
    if not creds.get("token"):
        return False
    saved = creds.get("token_saved", "")
    if key == "upstox":
        return token_still_valid(saved)
    if key in ("fyers", "zerodha"):
        return token_valid_until_reset(saved, 6, 0)
    if key == "dhan":
        try:
            return now_ist() - datetime.fromisoformat(saved) < timedelta(hours=23, minutes=50)
        except Exception:
            return False
    return False


def make(key: str, data_dir: Path, creds: dict):
    c = dict(creds or {})
    if not token_ok(key, c):
        c["token"] = ""
    if key == "upstox":
        return UpstoxProvider(data_dir, c.get("token") or None)
    if key == "fyers":
        return FyersProvider(data_dir, c)
    if key == "zerodha":
        return ZerodhaProvider(data_dir, c)
    if key == "dhan":
        return DhanProvider(data_dir, c)
    return None


def public_info(key: str, creds: dict) -> dict:
    b = BROKERS[key]
    fields = []
    for f in b["fields"]:
        v = creds.get(f["key"]) or ""
        fields.append({**f, "value": ("" if f.get("secret") else (v or f.get("default", ""))),
                       "saved": bool(v)})
    return {"key": key, "name": b["name"], "status": b["status"], "auth": b["auth"], "cost": b["cost"],
            "reason": b.get("reason", ""), "steps": b["steps"], "fields": fields,
            "connected": token_ok(key, creds),
            "configured": all(creds.get(f["key"]) or f.get("default") for f in b["fields"]
                              if f["key"] != "token") if b["fields"] else False}
