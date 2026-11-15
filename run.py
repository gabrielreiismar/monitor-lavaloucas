#!/usr/bin/env python3
"""Monitor de lava-louças 220V.

  python run.py --once                  uma varredura completa
  python run.py --loop                  roda sozinho até Ctrl+C (intervalo muda na semana da BF)
  python run.py --once --store amazon   só uma loja (útil pra calibrar seletores)
  python run.py --once --show           abre o navegador visível (debug / captcha)
  python run.py --test-telegram         manda mensagem de teste
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from lavaloucas.notify import format_message, send_telegram
from lavaloucas.rules import Offer, detect_features, detect_finish, evaluate, is_new, looks_like_accessory, norm
from lavaloucas.scrapers.stores import SCRAPERS
from lavaloucas.storage import Storage

ROOT = Path(__file__).parent
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def matches(title: str, model: dict) -> bool:
    n = norm(title)
    return all(norm(tok) in n for tok in model["match"])


def jobs(cfg: dict, only_store: str | None):
    """(loja, nome, url_fixa|None, query, modelo|None). 'categoria' = 1 página por loja, modelo resolvido por título."""
    stores = [only_store] if only_store else cfg["stores"]
    for st in stores:
        if st == "categoria":
            for pg in cfg.get("category_pages", []):
                yield st, pg["name"], pg["url"], "", None
            continue
        for m in cfg["models"]:
            yield st, SCRAPERS[st].name, None, m["query"], m
        for q in cfg.get("radar_queries", []):
            yield st, SCRAPERS[st].name, None, q, None


def servicos(title: str):
    """Capacidade em 'serviços' lida do título (None se não informada)."""
    import re
    m = re.search(r"(\d{1,2})\s*(?:servicos|lugares|talheres)", norm(title))
    return int(m.group(1)) if m else None


def model_for(title: str, cfg: dict):
    return next((m for m in cfg["models"] if matches(title, m)), None)


def sweep(cfg: dict, db: Storage, pw_ctx, only_store=None) -> int:
    b = cfg["budget"]
    page = pw_ctx.new_page()
    seen_urls: set[str] = set()
    sent = 0
    for st, store_name, url_tpl, query, model in jobs(cfg, only_store):
        scraper = SCRAPERS[st](page, cfg)
        if url_tpl:
            scraper.search_url = url_tpl
        try:
            cands = scraper.search(query)
        except Exception as e:
            print(f"[{store_name}] busca falhou ({query!r}): {e}")
            continue
        for c in cands:
            if not c.url or c.url in seen_urls:
                continue
            if st == "categoria":
                model = model_for(c.title, cfg)  # None = radar (só 🟢)
            elif model and not matches(c.title, model):
                continue
            if not model and "lava" not in norm(c.title):
                continue
            # gate barato: preço de vitrine muito acima do teto nunca vai virar alerta
            if c.list_price and c.list_price > b["opportunity_cap"] * 1.6:
                continue
            if c.list_price and looks_like_accessory(c.title, c.list_price, b["min_plausible_price"]):
                continue
            cap = servicos(c.title)
            if cap is not None and cap < b.get("min_servicos", 0):
                continue  # ex.: 8 e 10 serviços
            seen_urls.add(c.url)
            try:
                d = scraper.detail(c.url)
            except Exception as e:
                print(f"[{store_name}] detalhe falhou {c.url}: {e}")
                continue
            if d.price is None or looks_like_accessory(c.title, d.price, b["min_plausible_price"]):
                continue
            key = model["key"] if model else "radar"
            ref = db.reference_price(key) if model else None
            offer = Offer(store=store_name, model_key=key, title=c.title, url=c.url, price=d.price,
                          voltage=d.voltage, new=is_new(c.title, d.condition, d.seller),
                          in_stock=d.in_stock, premium=bool(model and model.get("premium")),
                          reference_price=ref, finish=detect_finish(c.title),
                          features=detect_features(c.title + " " + d.text) if len(d.text) > 800 else None)
            if offer.voltage in ("220", "bivolt") and offer.new:
                db.add_price(store_name, key, c.url, d.price, offer.voltage)  # histórico só de 220V novo
            v = evaluate(offer, b["meta"], b["opportunity_cap"], b["opportunity_min_discount"],
                         b["accept_bivolt"], b.get("require_inox", False),
                         b.get("inox_unknown_ok", True), tuple(b.get("required_features", [])))
            print(f"  {store_name:14} R$ {d.price:>8,.0f} {str(offer.voltage):>6}V  "
                  f"{'ALERTA ' + v.tier if v.alert else 'ignorado: ' + v.reason:40} {c.title[:55]}")
            if v.alert and db.should_alert(c.url, v.tier, d.price, cfg["schedule"]["alert_cooldown_hours"]):
                send_telegram(format_message(offer, v))
                db.mark_alerted(c.url, v.tier, d.price)
                sent += 1
        scraper.human_pause(2, 5)
    page.close()
    return sent


def heartbeat(cfg: dict) -> None:
    """Avisa o GitHub que o Mac está vivo (o reserva na nuvem só assume se este sinal envelhecer)."""
    import shutil
    import subprocess
    repo = (cfg.get("backup") or {}).get("repo")
    gh = shutil.which("gh") or str(Path.home() / ".local/bin/gh")
    if not repo or not Path(gh).exists():
        return
    try:
        subprocess.run([gh, "variable", "set", "MAC_HEARTBEAT", "--body", str(int(time.time())), "-R", repo],
                       capture_output=True, timeout=40)
    except Exception:
        pass


def gate(cfg: dict) -> bool:
    """Roda no GitHub: True = esta execução deve varrer. Falso se o Mac está vivo, na madrugada, ou fora do ritmo."""
    import os
    from zoneinfo import ZoneInfo
    if os.environ.get("FORCE") == "1":
        return True
    stale = (cfg.get("backup") or {}).get("stale_minutes", 45)
    hb = os.environ.get("MAC_HEARTBEAT", "").strip()
    if hb.isdigit() and time.time() - int(hb) < stale * 60:
        print(f"Mac vivo (sinal há {(time.time() - int(hb)) / 60:.0f} min): reserva não varre.")
        return False
    now = dt.datetime.now(ZoneInfo("America/Sao_Paulo"))
    q0, q1 = cfg["schedule"]["quiet_hours"]
    if q0 <= now.hour < q1:
        print("Madrugada: não varre.")
        return False
    a, z = (dt.date.fromisoformat(x) for x in cfg["schedule"]["blackfriday_window"])
    step = 1 if a <= now.date() <= z else 4  # grade de 30 min: toda rodada na BF, 1 a cada 2 h fora dela
    if int(time.time() // 1800) % step:
        print("Fora do ritmo normal (2 h): não varre.")
        return False
    return True


def interval_minutes(cfg: dict) -> int:
    s = cfg["schedule"]
    a, z = (dt.date.fromisoformat(x) for x in s["blackfriday_window"])
    return s["blackfriday_minutes"] if a <= dt.date.today() <= z else s["normal_minutes"]


def main():
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--store", choices=list(SCRAPERS))
    ap.add_argument("--show", action="store_true", help="navegador visível")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--gate", action="store_true", help="(GitHub) escreve run=true/false em GITHUB_OUTPUT")
    args = ap.parse_args()

    if args.gate:
        import os
        ok = gate(load_config())
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"run={'true' if ok else 'false'}\n")
        print("varrer" if ok else "pular")
        return

    if args.test_telegram:
        print("enviado" if send_telegram("✅ Monitor de lava-louças conectado.") else "falhou/sem config")
        return
    if not (args.once or args.loop):
        ap.error("use --once ou --loop")

    from playwright.sync_api import sync_playwright
    cfg = load_config()
    db = Storage(str(ROOT / "data" / "precos.sqlite"))

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(ROOT / "data" / "perfil"), headless=not args.show, user_agent=UA,
            locale="pt-BR", timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 850},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            while True:
                hour = dt.datetime.now().hour
                q0, q1 = cfg["schedule"]["quiet_hours"]
                if not (q0 <= hour < q1):
                    print(f"\n=== varredura {dt.datetime.now():%d/%m %H:%M} ===")
                    heartbeat(cfg)
                    n = sweep(cfg, db, ctx, args.store)
                    print(f"=== {n} alerta(s) enviado(s) ===")
                if not args.loop:
                    break
                wait = interval_minutes(cfg) * 60 * random.uniform(0.85, 1.15)
                print(f"próxima em {wait/60:.0f} min")
                end = time.time() + wait
                while time.time() < end:  # espera em fatias, avisando o GitHub que o Mac está vivo
                    heartbeat(cfg)
                    time.sleep(min(600, max(1, end - time.time())))
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
