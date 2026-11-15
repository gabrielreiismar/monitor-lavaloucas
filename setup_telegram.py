#!/usr/bin/env python3
"""Configura o Telegram em 1 minuto.

Antes: no Telegram, fale com @BotFather -> /newbot -> copie o token.
Depois: abra o seu bot novo e mande qualquer mensagem (ex.: "oi").
Rode:   python setup_telegram.py
O script pede o token, descobre o chat_id sozinho, grava o .env e manda uma mensagem de teste.
"""
from __future__ import annotations

import getpass
from pathlib import Path

import requests

ENV = Path(__file__).parent / ".env"


def main():
    token = getpass.getpass("Cole o token do bot (não aparece na tela): ").strip()
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    if not r.get("ok"):
        raise SystemExit("Token inválido. Copie de novo do @BotFather.")
    chats = [u["message"]["chat"] for u in r["result"] if "message" in u]
    if not chats:
        raise SystemExit("Nenhuma mensagem encontrada. Abra o bot no Telegram, mande 'oi' e rode de novo.")
    chat_id = chats[-1]["id"]
    try:
        ENV.write_text(f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n")
        ENV.chmod(0o600)
    except OSError as e:
        raise SystemExit(f"Não consegui gravar {ENV}: {e}")
    if not ENV.exists():
        raise SystemExit(f"Gravei, mas o arquivo não aparece em {ENV}")
    print(f"Gravado em: {ENV}")
    ok = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                       data={"chat_id": chat_id, "text": "✅ Monitor de lava-louças conectado."}).json().get("ok")
    print("Pronto! Mensagem de teste enviada." if ok else "Gravei o .env, mas o envio de teste falhou.")


if __name__ == "__main__":
    main()
