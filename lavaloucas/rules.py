"""Regras de negócio puras (sem rede): voltagem, condição e faixa de alerta.

Tudo aqui é testável offline. É o coração do bot: se uma regra estiver
errada, o alerta é falso. Por isso fica isolado dos scrapers.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


def norm(text: str) -> str:
    """minúsculo, sem acento, espaços colapsados."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


# ---------------------------------------------------------------- voltagem
_V220 = re.compile(r"(?<!\d)220\s*-?\s*v(?:olts?)?\b|(?<!\d)220\s*v\b|\bvoltagem\W{0,3}220")
_V127 = re.compile(r"(?<!\d)(?:110|115|120|127)\s*-?\s*v(?:olts?)?\b|\bvoltagem\W{0,3}(?:110|127)")
_BIVOLT = re.compile(r"\bbivolt\b|\b110\s*/\s*220\b|\b127\s*/\s*220\b|\b127\s*e\s*220\b")


def detect_voltage(*texts: str) -> Optional[str]:
    """Retorna '220', '127', 'bivolt' ou None (indeterminado).

    Passe título, URL, valor da variação selecionada e a ficha técnica.
    Se houver sinal de 220 e de 127 no MESMO texto (ex.: "disponível em 127V
    e 220V") o resultado é None: ambíguo não vale como 220V confirmado.
    Textos mais específicos devem vir primeiro; o primeiro que decidir ganha.
    """
    for t in texts:
        n = norm(t)
        if not n:
            continue
        if _BIVOLT.search(n):
            return "bivolt"
        has220, has127 = bool(_V220.search(n)), bool(_V127.search(n))
        if has220 and not has127:
            return "220"
        if has127 and not has220:
            return "127"
    return None


# ---------------------------------------------------------------- acabamento
_INOX = re.compile(r"\binox\b|\baco inox\b|\bstainless\b|\bescovado\b")
_NOT_INOX = re.compile(r"\b(preta|preto|branca|branco|cinza|grafite|vermelha|azul|bege)\b")


def detect_finish(*texts: str) -> Optional[str]:
    """'inox', 'outra' (cor sem inox) ou None (título não diz).

    'Black Inox' (Samsung) conta como inox. 'Preta'/'Branca'/'Cinza' sem a palavra
    inox contam como 'outra'.
    """
    for t in texts:
        n = norm(t)
        if _INOX.search(n):
            return "inox"
        if _NOT_INOX.search(n):
            return "outra"
    return None


# ---------------------------------------------------------------- funções exigidas
FEATURE_PATTERNS = {
    "ciclo_rapido": re.compile(
        r"ciclo rapido|lavagem rapida|programa rapido|rapid wash|quick wash|\bexpress\b|"
        r"\brapido\b|\b(?:30|40|45|50)\s*(?:min|minutos)\b"),
    "sensor_sujeira": re.compile(
        r"smart ?sensor|auto ?sense|sensor de sujeira|sensor inteligente|sensor de turbidez|"
        r"detecta.{0,30}sujeira|nivel de sujeira|\bia\b|inteligencia artificial"),
    "meia_carga": re.compile(
        r"meia[- ]carga|half[- ]load|carga parcial|meia lavagem|1/2 carga|metade da carga|"
        r"lavagem parcial"),
    "higienizacao": re.compile(r"higieniz|sanitiz|sanitary|alta temperatura|\bhigiene\b"),
}


def detect_features(text: str) -> set:
    """Quais das funções essenciais aparecem no texto da página (ficha técnica + descrição)."""
    n = norm(text)
    return {k for k, rx in FEATURE_PATTERNS.items() if rx.search(n)}


# ---------------------------------------------------------------- condição
_NOT_NEW = (
    "open box", "openbox", "caixa aberta", "usado", "seminovo", "semi novo",
    "recondicionado", "renewed", "reembalado", "vitrine", "mostruario",
    "outlet", "avariado", "com avaria", "defeito", "retirada de pecas",
    "para pecas", "sucata", "amassado", "detalhe", "arranhado", "usada",
)


def is_new(title: str, condition_field: str = "", seller: str = "") -> bool:
    """True só se nada indica produto não-novo."""
    cond = norm(condition_field)
    if cond and cond not in ("novo", "new", "nuevo"):
        return False
    hay = f"{norm(title)} {norm(seller)}"
    return not any(tok in hay for tok in _NOT_NEW)


def looks_like_accessory(title: str, price: float, min_price: float) -> bool:
    """Descarta pastilha, cesto, peça de reposição etc. (preço absurdo ou palavra)."""
    n = norm(title)
    words = ("pastilha", "detergente", "cesto", "peca de reposicao", "kit limpeza",
             "mangueira", "capa para", "suporte para", "sal regenerador", "abrilhantador",
             "limpa maquina", "filtro", "porta talher")
    return price < min_price or any(w in n for w in words)


# ---------------------------------------------------------------- faixas
@dataclass
class Offer:
    store: str
    model_key: str
    title: str
    url: str
    price: float
    voltage: Optional[str]
    new: bool
    in_stock: bool = True
    premium: bool = False
    reference_price: Optional[float] = None  # preço "normal" (mediana do nosso histórico)
    finish: Optional[str] = None  # 'inox' | 'outra' | None
    features: Optional[set] = None  # None = texto da página não foi lido (não bloquear)


@dataclass
class Verdict:
    tier: Optional[str]   # "META" | "OPORTUNIDADE" | None
    reason: str

    @property
    def alert(self) -> bool:
        return self.tier is not None


def evaluate(o: Offer, budget: float, opp_cap: float, opp_min_discount: float,
             accept_bivolt: bool = True, require_inox: bool = False,
             inox_unknown_ok: bool = True, required_features: tuple = ()) -> Verdict:
    """Decide se uma oferta merece alerta.

    Filtros eliminatórios (qualquer falha = sem alerta):
      novo, em estoque, voltagem 220V (ou bivolt) CONFIRMADA.
    Depois:
      META          preço <= orçamento
      OPORTUNIDADE  modelo premium com preço <= teto de oportunidade E
                    (queda >= X% sobre o preço de referência, quando existir).
    """
    if not o.new:
        return Verdict(None, "não é novo")
    if not o.in_stock:
        return Verdict(None, "sem estoque")
    if o.voltage == "220" or (accept_bivolt and o.voltage == "bivolt"):
        pass
    else:
        return Verdict(None, f"voltagem {o.voltage or 'não confirmada'}")
    if require_inox:
        if o.finish == "outra":
            return Verdict(None, "acabamento não é inox")
        if o.finish is None and not inox_unknown_ok:
            return Verdict(None, "acabamento não confirmado")
    if required_features and o.features is not None:
        missing = [f for f in required_features if f not in o.features]
        if missing:
            return Verdict(None, "sem " + ", ".join(missing))

    if o.price <= budget:
        return Verdict("META", f"R$ {o.price:,.0f} ≤ orçamento R$ {budget:,.0f}")

    if o.premium and o.price <= opp_cap:
        if o.reference_price:
            drop = 1 - o.price / o.reference_price
            if drop >= opp_min_discount:
                return Verdict(
                    "OPORTUNIDADE",
                    f"premium {drop:.0%} abaixo do preço normal (R$ {o.reference_price:,.0f})",
                )
            return Verdict(None, f"premium, mas queda de só {drop:.0%}")
        return Verdict("OPORTUNIDADE", f"premium ≤ teto R$ {opp_cap:,.0f} (sem histórico ainda)")

    return Verdict(None, f"R$ {o.price:,.0f} acima do alvo")
