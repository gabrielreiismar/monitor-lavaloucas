"""SQLite: histórico de preços (base do 'desconto real') e deduplicação de alertas."""
from __future__ import annotations

import sqlite3
import statistics
import time
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(
  ts INTEGER, store TEXT, model_key TEXT, url TEXT, price REAL, voltage TEXT
);
CREATE INDEX IF NOT EXISTS ix_prices ON prices(model_key, voltage, ts);
CREATE TABLE IF NOT EXISTS alerts(
  url TEXT, tier TEXT, price REAL, ts INTEGER
);
"""


class Storage:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def add_price(self, store, model_key, url, price, voltage):
        self.db.execute("INSERT INTO prices VALUES(?,?,?,?,?,?)",
                        (int(time.time()), store, model_key, url, price, voltage))
        self.db.commit()

    def reference_price(self, model_key: str, days: int = 30) -> Optional[float]:
        """Mediana dos preços 220V vistos nos últimos N dias (mín. 5 amostras).

        Mediana, não máximo: o 'de R$ X' riscado das lojas é inflado. O que vale
        é o que a gente mesmo viu o produto custando antes da Black Friday.
        """
        since = int(time.time()) - days * 86400
        rows = self.db.execute(
            "SELECT price FROM prices WHERE model_key=? AND voltage IN ('220','bivolt') AND ts>=?",
            (model_key, since)).fetchall()
        vals = [r[0] for r in rows]
        return statistics.median(vals) if len(vals) >= 5 else None

    def should_alert(self, url: str, tier: str, price: float, cooldown_h: int = 12,
                     min_drop: float = 0.03) -> bool:
        """Evita spam: só re-alerta a mesma URL se passou o cooldown ou o preço caiu >=3%."""
        row = self.db.execute(
            "SELECT price, ts FROM alerts WHERE url=? AND tier=? ORDER BY ts DESC LIMIT 1",
            (url, tier)).fetchone()
        if not row:
            return True
        last_price, ts = row
        return (time.time() - ts > cooldown_h * 3600) or price <= last_price * (1 - min_drop)

    def mark_alerted(self, url, tier, price):
        self.db.execute("INSERT INTO alerts VALUES(?,?,?,?)", (url, tier, price, int(time.time())))
        self.db.commit()
