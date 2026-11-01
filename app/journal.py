from __future__ import annotations

import sqlite3
from pathlib import Path

FIELDS = ("trade_date", "symbol", "side", "qty", "entry", "exit", "stop", "target",
          "setup", "notes", "status")


class Journal:
    def __init__(self, path: Path):
        self.path = path
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('LONG','SHORT')),
                    qty REAL NOT NULL DEFAULT 1,
                    entry REAL NOT NULL,
                    exit REAL,
                    stop REAL,
                    target REAL,
                    setup TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    status TEXT DEFAULT 'OPEN',
                    created TEXT DEFAULT CURRENT_TIMESTAMP
                )""")

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _enrich(row: dict) -> dict:
        entry, exit_, qty = row["entry"], row["exit"], row["qty"] or 1
        sign = 1 if row["side"] == "LONG" else -1
        row["pnl"] = round((exit_ - entry) * sign * qty, 2) if exit_ is not None else None
        risk = abs(entry - row["stop"]) if row["stop"] else None
        row["r_multiple"] = round((exit_ - entry) * sign / risk, 2) if (exit_ is not None and risk) else None
        return row

    def list(self):
        with self._conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM trades ORDER BY trade_date DESC, id DESC")]
        return [self._enrich(r) for r in rows]

    def stats(self):
        rows = [r for r in self.list() if r["pnl"] is not None]
        wins = [r for r in rows if r["pnl"] > 0]
        losses = [r for r in rows if r["pnl"] <= 0]
        rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
        by_setup: dict[str, dict] = {}
        for r in rows:
            s = by_setup.setdefault(r["setup"] or "Unlabelled", {"trades": 0, "wins": 0, "pnl": 0.0})
            s["trades"] += 1
            s["wins"] += 1 if r["pnl"] > 0 else 0
            s["pnl"] = round(s["pnl"] + r["pnl"], 2)
        gross_win = sum(r["pnl"] for r in wins)
        gross_loss = -sum(r["pnl"] for r in losses)
        return {
            "closed": len(rows),
            "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else None,
            "net_pnl": round(sum(r["pnl"] for r in rows), 2),
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "by_setup": by_setup,
        }

    def _clean(self, data: dict) -> dict:
        out = {k: data.get(k) for k in FIELDS if k in data}
        for k in ("qty", "entry", "exit", "stop", "target"):
            if k in out:
                v = out[k]
                out[k] = float(v) if v not in (None, "") else None
        if "side" in out:
            out["side"] = (out["side"] or "LONG").upper()
        if "symbol" in out and out["symbol"]:
            out["symbol"] = out["symbol"].upper().strip()
        if "exit" in out:
            out["status"] = "CLOSED" if out["exit"] is not None else "OPEN"
        return out

    def add(self, data: dict) -> int:
        d = self._clean(data)
        cols = ",".join(d)
        with self._conn() as c:
            cur = c.execute(f"INSERT INTO trades ({cols}) VALUES ({','.join('?' * len(d))})", list(d.values()))
            return cur.lastrowid

    def update(self, tid: int, data: dict):
        d = self._clean(data)
        if not d:
            return
        with self._conn() as c:
            c.execute(f"UPDATE trades SET {','.join(f'{k}=?' for k in d)} WHERE id=?", [*d.values(), tid])

    def delete(self, tid: int):
        with self._conn() as c:
            c.execute("DELETE FROM trades WHERE id=?", (tid,))
