"""Lojas. Os seletores CSS são o ponto frágil: calibrar com `python run.py --debug <loja>`
na primeira execução e ajustar aqui. JSON-LD é usado primeiro porque quebra menos."""
from __future__ import annotations

from typing import List

from .base import Candidate, Detail, Scraper, jsonld_offer, ld_price, parse_brl


class MercadoLivre(Scraper):
    name = "Mercado Livre"
    search_url = "https://lista.mercadolivre.com.br/{q}_NoIndex_True?ITEM_CONDITION=2230284"  # 2230284 = Novo

    def parse_search(self) -> List[Candidate]:
        out = []
        for card in self.page.locator("li.ui-search-layout__item, div.poly-card").all()[:40]:
            try:
                a = card.locator("a.poly-component__title, a.ui-search-link").first
                price = card.locator(".andes-money-amount__fraction").first.inner_text(timeout=500)
                out.append(Candidate(a.inner_text(), a.get_attribute("href"), parse_brl(price)))
            except Exception:
                continue
        return out

    def read_detail(self) -> Detail:
        self.select_220()
        off = jsonld_offer(self.page) or {}
        price = ld_price(off)
        if price is None:
            try:
                price = parse_brl(self.page.locator(
                    ".ui-pdp-price__second-line .andes-money-amount__fraction").first.inner_text(timeout=800))
            except Exception:
                pass
        cond = "novo" if "NewCondition" in str(off.get("itemCondition", "NewCondition")) else "usado"
        stock = "OutOfStock" not in str(off.get("availability", ""))
        seller = ""
        try:
            seller = self.page.locator(".ui-pdp-seller__header__title").first.inner_text(timeout=400)
        except Exception:
            pass
        return Detail(price, self.voltage_from_page(), cond, seller, stock)


class Amazon(Scraper):
    name = "Amazon"
    search_url = "https://www.amazon.com.br/s?k={q}&rh=p_n_condition-type%3A13862762011"  # só novos

    def parse_search(self) -> List[Candidate]:
        out = []
        for card in self.page.locator('div[data-component-type="s-search-result"]').all()[:30]:
            try:
                # layout atual: o <a> NÃO fica dentro do <h2>; patrocinados usam /sspa/click (ignorar)
                title = card.locator("h2").first.inner_text(timeout=500)
                href = card.locator('a[href*="/dp/"]').first.get_attribute("href", timeout=500)
                price = card.locator(".a-price .a-offscreen").first.inner_text(timeout=500)
                href = href if href.startswith("http") else "https://www.amazon.com.br" + href
                out.append(Candidate(title, href.split("/ref=")[0].split("?")[0], parse_brl(price)))
            except Exception:
                continue
        return out

    def read_detail(self) -> Detail:
        self.select_220()
        price = None
        try:
            price = parse_brl(self.page.locator(
                "#corePrice_feature_div .a-offscreen, .priceToPay .a-offscreen").first.inner_text(timeout=800))
        except Exception:
            off = jsonld_offer(self.page) or {}
            price = ld_price(off)
        txt = ""
        try:
            txt = self.page.locator("#availability").first.inner_text(timeout=400).lower()
        except Exception:
            pass
        seller = ""
        try:
            seller = self.page.locator("#merchant-info").first.inner_text(timeout=400)
        except Exception:
            pass
        cond = "usado" if "renewed" in self.page.content().lower()[:200000] and "recondicionado" in seller.lower() else "novo"
        return Detail(price, self.voltage_from_page(), cond, seller,
                      "indispon" not in txt and "out of stock" not in txt)


class Categoria(Scraper):
    """Fast Shop e lojas oficiais (VTEX): lê a página de CATEGORIA 'lava-louças' (uma por loja).
    Título vem do texto do link ou, se for link de imagem, do slug da URL
    (ex.: lava-louca-electrolux-14-servicos-inox-...-ls14e) - o slug já traz inox e o código."""
    name = "Categoria"
    search_url = "{q}"

    def parse_search(self) -> List[Candidate]:
        from urllib.parse import urljoin
        for attempt in range(2):  # lojas VTEX carregam a lista com atraso
            try:
                self.page.wait_for_selector("a[href*='lava-lou']", timeout=9000)
                break
            except Exception:
                if attempt == 0:
                    self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_timeout(1000)
        out, seen = [], set()
        for a in self.page.locator("a[href*='lava-lou']").all()[:120]:
            try:
                href = a.get_attribute("href") or ""
                full = urljoin(self.page.url, href).split("?")[0]
                if full in seen or "/p" not in full and "_prd" not in full:
                    continue
                seen.add(full)
                slug = full.rstrip("/").split("/")[-1] if full.endswith("/p") is False else full.rstrip("/").split("/")[-2]
                lines = [l.strip() for l in (a.inner_text(timeout=300) or "").split("\n") if l.strip()]
                title = max(lines, key=len) if lines else ""
                if len(title) < 15:
                    title = slug.replace("-", " ")
                out.append(Candidate(title, full, None))
            except Exception:
                continue
        return out

    def read_detail(self) -> Detail:
        self.select_220()
        off = jsonld_offer(self.page) or {}
        price = self.dom_price() or ld_price(off)
        stock = "OutOfStock" not in str(off.get("availability", "")) and \
            "indisponível" not in self.product_segment(1500).lower()
        return Detail(price, self.voltage_from_page(), "novo", self.page.url.split("/")[2], stock)


SCRAPERS = {"mercadolivre": MercadoLivre, "amazon": Amazon, "categoria": Categoria}
