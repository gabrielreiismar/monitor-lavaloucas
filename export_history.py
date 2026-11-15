"""Exporta o histórico de preços (SQLite) para CSV legível. Uso: python export_history.py saida.csv"""
from __future__ import annotations

import csv
import datetime as dt
import sqlite3
import sys
from pathlib import Path

db = sqlite3.connect(str(Path(__file__).parent / "data" / "precos.sqlite"))
rows = db.execute("SELECT ts, store, model_key, price, voltage, url FROM prices ORDER BY ts").fetchall()
with open(sys.argv[1], "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["data_hora_utc", "loja", "modelo", "preco", "voltagem", "url"])
    for ts, store, key, price, volt, url in rows:
        w.writerow([dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M"), store, key, price, volt, url])
print(f"{len(rows)} linhas exportadas")
