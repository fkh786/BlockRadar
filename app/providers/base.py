from __future__ import annotations

from ..models import StockData


class Provider:
    """A data source. Keeps `self.stocks` up to date for the scanner."""

    key = "base"
    label = "Base"
    refresh_seconds = 60

    def __init__(self):
        self.stocks: dict[str, StockData] = {}
        self.ready = False
        self.message = ""
        self.progress = [0, 0]   # done, total (initial load)
        self.needs_login = False
        self.priority: list[str] = []
        self.watch_keys: set[str] = set()   # option keys the paper-trading book needs prices for

    async def start(self):
        """Initial load. May take a while; update self.progress as you go."""

    async def refresh(self):
        """Called every refresh_seconds after start() finished."""

    async def stop(self):
        pass

    def market(self) -> dict:
        """{'open': bool, 'clock': 'HH:MM', 'session': 'YYYY-MM-DD', 'label': str}"""
        return {"open": False, "clock": "", "session": "", "label": ""}

    # ---- options
    def expiries(self, symbol: str) -> list[str]:
        return []

    async def option_chain(self, symbol: str, expiry: str) -> dict:
        """{'expiry', 'spot', 'rows': [{'strike', 'CE': {...}, 'PE': {...}}]}"""
        return {"expiry": expiry, "spot": 0, "rows": []}

    def option_price(self, key: str) -> float | None:
        """Latest premium for an option key we were asked to watch."""
        return None
