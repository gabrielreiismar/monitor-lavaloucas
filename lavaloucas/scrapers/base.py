"""Base dos scrapers (Playwright).

Fluxo por loja:
  1. busca  -> lista de candidatos (título, url, preço de vitrine)
  2. detalhe (só dos que passam no filtro de modelo e podem estar no alvo)
     -> preço real, voltagem confirmada pela VARIAÇÃO, condição, estoque.

O preço de vitrine quase sempre é o da variação padrão (127V). Por isso a
voltagem NUNCA é decidida na busca: sempre abre o detalhe e tenta selecionar 220V.
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from typing import List, Optional

from ..rules import detect_voltage, norm


@dataclass
class Candidate:
    title: str
    url: str
    list_price: Optional[float]


@dataclass
class Detail:
    price: Optional[float]
    voltage: Optional[str]
    condition: str = ""
    seller: str = ""
    in_stock: bool = True
    text: str = ""  # texto da página (ficha técnica) p/ detectar funções


def parse_brl(text: str) -> Optional[float]:
    """'R$ 2.499,90' -> 2499.9"""
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{2}))?", text or "")
    if not m:
        return None
    whole = m.group(1).replace(".", "")
    return float(f"{whole}.{m.group(2) or '00'}")


def to_price(value) -> Optional[float]:
    """JSON-LD: número/str em formato US ('3268.9', 3229.05). Texto de tela: formato BR ('3.268,90')."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    v = str(value).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", v):
        return float(v)
    return parse_brl(v)


def ld_price(off: Optional[dict]) -> Optional[float]:
    off = off or {}
    return to_price(off.get("price") if off.get("price") not in (None, "") else off.get("lowPrice"))


def jsonld_offer(page) -> Optional[dict]:
    """Preço/disponibilidade/condição via schema.org JSON-LD (mais estável que CSS)."""
    for raw in page.eval_on_selector_all(
            'script[type="application/ld+json"]', "els => els.map(e => e.textContent)"):
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "Product":
                off = node.get("offers")
                off = off[0] if isinstance(off, list) and off else off
                if isinstance(off, dict):
                    return off
    return None


class Scraper:
    name = "base"
    search_url = "{q}"

    def __init__(self, page, settings: dict):
        self.page = page
        self.s = settings

    # ---- a implementar por loja
    def parse_search(self) -> List[Candidate]:
        raise NotImplementedError

    def read_detail(self) -> Detail:
        raise NotImplementedError

    # ---- comum
    def human_pause(self, a=1.2, b=3.2):
        time.sleep(random.uniform(a, b))

    def search(self, query: str) -> List[Candidate]:
        from urllib.parse import quote_plus
        self.page.goto(self.search_url.format(q=quote_plus(query)), wait_until="domcontentloaded")
        self.human_pause()
        self.dismiss_popups()
        return self.parse_search()

    def detail(self, url: str) -> Detail:
        self.page.goto(url, wait_until="domcontentloaded")
        self.human_pause()
        self.dismiss_popups()
        d = self.read_detail()
        try:  # texto completo da página: título, descrição e ficha técnica
            d.text = self.page.inner_text("body", timeout=3000)[:80000]
        except Exception:
            d.text = ""
        return d

    def dismiss_popups(self):
        for label in ("Aceitar", "Aceitar cookies", "Entendi", "Fechar"):
            try:
                self.page.get_by_role("button", name=label).first.click(timeout=800)
            except Exception:
                pass

    def select_220(self) -> bool:
        """Seleciona a variação 220V. Retorna True se achou e trocou.

        1) Se a opção é um link (<a href>) para outro SKU, navega direto - robusto a banner
           de cookies e garante preço/estoque do SKU 220V.
        2) Senão, clica via JS (não é bloqueado por overlay).
        """
        self.selected_220 = False
        opts = self.page.get_by_text(re.compile(r"^\s*220\s?-?\s?(?:v|volts)?\s*$", re.I))
        try:
            n = min(opts.count(), 6)
        except Exception:
            return False
        for i in range(n):
            el = opts.nth(i)
            try:
                href = el.evaluate(
                    "e => { const a = e.closest('a') || (e.closest('li') && e.closest('li').querySelector('a[href]')) "
                    "|| (e.parentElement && e.parentElement.querySelector('a[href]')); return a ? a.href : ''; }")
                if href and href.split('#')[0] != self.page.url.split('#')[0]:
                    self.page.goto(href, wait_until="domcontentloaded")
                    self.human_pause(1.5, 2.5)
                    self.selected_220 = True
                    return True
                if el.is_visible():
                    el.evaluate("e => e.click()")
                    self.human_pause(1.2, 2.2)
                    self.selected_220 = True
                    return True
            except Exception:
                continue
        return False

    def product_segment(self, size: int = 2500) -> str:
        """Texto da área do produto: do título (h1) em diante. Evita pegar preços de carrosséis."""
        try:
            body = self.page.inner_text("body", timeout=3000)
            h1 = self.page.locator("h1").first.inner_text(timeout=1500).strip()
            i = body.find(h1[:30])
            return body[i:i + size] if i >= 0 else body[:size]
        except Exception:
            return ""

    def dom_price(self, min_price: float = 900, max_price: float = 30000) -> Optional[float]:
        """Menor preço à vista exibido na área do produto (ignora parcelas e economia de Pix)."""
        best = None
        for line in self.product_segment().split("\n"):
            low = line.lower()
            if "x de" in low or "em até" in low or "juros" in low or "economize" in low:
                continue
            for m in re.finditer(r"R\$\s?([\d\.]+,\d{2})", line.replace("\xa0", " ")):
                v = parse_brl(m.group(1))
                if v and min_price <= v <= max_price and (best is None or v < best):
                    best = v
        return best

    def voltage_from_page(self) -> Optional[str]:
        """Sinais do mais específico ao mais genérico.

        Específico = elemento marcado como selecionado/cabeçalho do SKU (não a lista de opções).
        Se nada específico existe mas selecionamos a opção '220V' com sucesso, vale 220 - a menos
        que o cabeçalho do SKU contradiga.
        """
        specific = []
        for sel in ("[class*=header-number-ref]", "[aria-checked=true]", "[aria-selected=true]",
                    "[aria-current=true]", "[class*=selected]", "[class*=active]",
                    "tr:has(th:has-text('Voltagem')) td", "#variation_voltage_name .selection"):
            try:
                for t in self.page.locator(sel).all_inner_texts()[:6]:
                    if len(t) < 40 and detect_voltage(t):
                        specific.append(t)
            except Exception:
                pass
        found = detect_voltage(*specific)
        if found:
            return found
        if getattr(self, "selected_220", False):
            return "220"
        title = ""
        try:
            title = self.page.title()
        except Exception:
            pass
        return detect_voltage(self.page.url, title)
