"""Alertas via Telegram (grátis, instantâneo, com link clicável)."""
from __future__ import annotations

import os
import re

import requests

from .rules import Offer, Verdict

ICON = {"META": "🟢", "OPORTUNIDADE": "🔥"}


def brl(v: float) -> str:
    return f"{v:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")  # 2.435,90


def format_message(o: Offer, v: Verdict) -> str:
    head = {"META": "DENTRO DO ORÇAMENTO", "OPORTUNIDADE": "OPORTUNIDADE RARA"}[v.tier]
    reason = re.sub(r"R\$ ([\d,\.]+)", lambda m: "R$ " + brl(float(m.group(1).replace(",", ""))), v.reason)
    return (
        f"{ICON[v.tier]} <b>{head}</b>\n"
        f"{o.title}\n"
        f"<b>R$ {brl(o.price)}</b> · {o.voltage or '?'}V · {o.store}\n"
        f"{reason}\n"
        f"{o.url}"
    )


def send_telegram(text: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[notify] Telegram não configurado. Mensagem:\n" + text)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": False},
        timeout=20,
    )
    return r.ok
