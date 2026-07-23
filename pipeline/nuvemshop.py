"""Conector Nuvemshop: produtos, variantes, pedidos e o mapa EAN -> ref (auditado).

Normaliza o payload cru da API em estruturas limpas que o builder (F1.5) consome
para montar produtos.json e diario.json. Nada de regra de negocio de score aqui,
so ingestao e normalizacao fiel.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator

from .brands import extrair_ref, marca_por_ref, normalizar_cor
from .config import StoreConfig
from .http import NuvemshopClient

# Tokens que identificam o atributo de TAMANHO (o outro atributo vira COR).
_TOKENS_TAMANHO = {
    "PP", "P", "M", "G", "GG", "XG", "XGG", "EG", "U", "UNICO", "ÚNICO",
    "36", "38", "40", "42", "44", "46", "48", "50", "52",
}


def localized(value: Any, lang: str = "pt") -> str:
    """Nuvemshop devolve campos textuais como {'pt': '...', 'es': '...'} ou string pura."""
    if isinstance(value, dict):
        return str(value.get(lang) or next(iter(value.values()), "") or "").strip()
    return str(value or "").strip()


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------- #
# Estruturas normalizadas
# ---------------------------------------------------------------------- #
@dataclass
class VarianteNorm:
    variant_id: int
    ean: str | None          # sku na Nuvemshop = EAN-13 da variante (CLAUDE.md 4.2b)
    cor: str | None
    cor_norm: str | None
    tamanho: str | None
    preco_cheio: float | None
    preco_promocional: float | None
    estoque: int
    publicada: bool


@dataclass
class ProdutoNorm:
    product_id: int
    ref: str | None
    nome: str
    marca: str
    loja: int
    categoria: str | None
    foto: str | None
    publicado: bool
    preco_cheio: float | None
    preco_promocional: float | None
    estoque_total: int
    variantes: list[VarianteNorm] = field(default_factory=list)
    sem_ref: bool = False  # entra no relatorio de excecoes


# ---------------------------------------------------------------------- #
# Ingestao crua
# ---------------------------------------------------------------------- #
def iter_products(client: NuvemshopClient) -> Iterator[dict[str, Any]]:
    """Todos os produtos da loja, incluindo variantes embutidas."""
    yield from client.paginate("products", params={"fields": None})


def iter_orders(
    client: NuvemshopClient,
    created_at_min: str | None = None,
    created_at_max: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Pedidos da loja no intervalo (ISO 8601). Sem filtro de canal aqui: isso e regra de negocio."""
    params: dict[str, Any] = {"per_page": 200}
    if created_at_min:
        params["created_at_min"] = created_at_min
    if created_at_max:
        params["created_at_max"] = created_at_max
    yield from client.paginate("orders", params=params)


# ---------------------------------------------------------------------- #
# Normalizacao de produtos
# ---------------------------------------------------------------------- #
def _classificar_valores(valores: list[str]) -> tuple[str | None, str | None]:
    """A partir dos valores de atributos de uma variante, separa (cor, tamanho).

    A Nuvemshop nao rotula qual atributo e cor e qual e tamanho de forma garantida,
    entao inferimos pelo conteudo: valores que parecem tamanho vao para tamanho,
    o restante e tratado como cor.
    """
    cor: str | None = None
    tamanho: str | None = None
    for v in valores:
        if not v:
            continue
        if v.strip().upper() in _TOKENS_TAMANHO:
            tamanho = v.strip()
        else:
            cor = v.strip() if cor is None else f"{cor} {v.strip()}"
    return cor, tamanho


def parse_product(raw: dict[str, Any], loja: int) -> ProdutoNorm:
    """Converte um produto cru da API na estrutura normalizada, com ref e marca resolvidas."""
    nome = localized(raw.get("name"))
    ref = extrair_ref(nome)
    marca = marca_por_ref(ref)

    # Foto principal: primeira imagem.
    imagens = raw.get("images") or []
    foto = None
    if imagens:
        foto = imagens[0].get("src") or imagens[0].get("url")

    # Categoria: primeira categoria, se houver.
    categorias = raw.get("categories") or []
    categoria = localized(categorias[0].get("name")) if categorias else None

    variantes: list[VarianteNorm] = []
    estoque_total = 0
    precos_cheios: list[float] = []
    precos_promo: list[float] = []

    for var in raw.get("variants") or []:
        valores = [localized(val) for val in (var.get("values") or [])]
        cor, tamanho = _classificar_valores(valores)
        estoque = _to_int(var.get("stock"))
        preco = _to_float(var.get("price"))
        promo = _to_float(var.get("promotional_price"))

        variantes.append(
            VarianteNorm(
                variant_id=_to_int(var.get("id")),
                ean=(str(var.get("sku")).strip() if var.get("sku") else None),
                cor=cor,
                cor_norm=normalizar_cor(cor),
                tamanho=tamanho,
                preco_cheio=preco,
                preco_promocional=promo,
                estoque=estoque,
                publicada=True,
            )
        )
        estoque_total += max(estoque, 0)
        if preco is not None:
            precos_cheios.append(preco)
        if promo is not None:
            precos_promo.append(promo)

    return ProdutoNorm(
        product_id=_to_int(raw.get("id")),
        ref=ref,
        nome=nome,
        marca=marca,
        loja=loja,
        categoria=categoria,
        foto=foto,
        publicado=bool(raw.get("published", False)),
        preco_cheio=max(precos_cheios) if precos_cheios else None,
        preco_promocional=min(precos_promo) if precos_promo else None,
        estoque_total=estoque_total,
        variantes=variantes,
        sem_ref=ref is None,
    )


def load_products(client: NuvemshopClient, loja: int) -> list[ProdutoNorm]:
    """Baixa e normaliza o censo completo de produtos de uma loja."""
    return [parse_product(raw, loja) for raw in iter_products(client)]


# ---------------------------------------------------------------------- #
# Mapa EAN -> ref (peca critica auditada, CLAUDE.md 4.2b)
# ---------------------------------------------------------------------- #
@dataclass
class MapaEan:
    ean_para_ref: dict[str, str]                       # EAN -> ref pai
    ean_para_variante: dict[str, VarianteNorm]         # EAN -> variante
    eans_duplicados: list[str]                         # EANs em mais de uma variante
    produtos_sem_ref: list[ProdutoNorm]                # excecoes de cadastro


def build_ean_ref_map(produtos: list[ProdutoNorm]) -> MapaEan:
    """Constroi o mapa EAN -> variante -> ref pai a partir da API, a cada execucao.

    Nunca depende de export manual. Detecta EANs duplicados (12 conhecidos no cadastro)
    e produtos sem ref no nome, ambos para o relatorio de excecoes.
    """
    ean_para_ref: dict[str, str] = {}
    ean_para_variante: dict[str, VarianteNorm] = {}
    ocorrencias: dict[str, int] = defaultdict(int)
    sem_ref: list[ProdutoNorm] = []

    for prod in produtos:
        if prod.sem_ref or not prod.ref:
            sem_ref.append(prod)
            continue
        for var in prod.variantes:
            if not var.ean:
                continue
            ocorrencias[var.ean] += 1
            # Primeira ocorrencia define o mapa; duplicatas ficam registradas mas nao sobrescrevem.
            ean_para_ref.setdefault(var.ean, prod.ref)
            ean_para_variante.setdefault(var.ean, var)

    duplicados = sorted(ean for ean, n in ocorrencias.items() if n > 1)
    return MapaEan(
        ean_para_ref=ean_para_ref,
        ean_para_variante=ean_para_variante,
        eans_duplicados=duplicados,
        produtos_sem_ref=sem_ref,
    )


# ---------------------------------------------------------------------- #
# Normalizacao de pedidos: vendas por variante, preco medio pago, canal
# ---------------------------------------------------------------------- #
# Canais de venda (CLAUDE.md secao 3 e 6.5). Default de calculo: site.
CANAL_LOJA_VIRTUAL = "Loja virtual"
CANAL_MOBILE = "Mobile"
CANAL_ANYMARKET = "ANYMARKET"
CANAL_MANUAL = "Pedidos manuais"
CANAL_OUTRO = "Outro"
CANAIS_SITE = frozenset({CANAL_LOJA_VIRTUAL, CANAL_MOBILE})


def classificar_canal(raw_order: dict[str, Any]) -> str:
    """Classifica o canal de venda de um pedido.

    ATENCAO (a auditar na F1 com payload real, CLAUDE.md secao 9): a API de /orders
    nao expoe a coluna "Canal" do export CSV de forma garantida. Esta heuristica usa
    os sinais disponiveis no payload e deve ser calibrada quando virem os primeiros
    pedidos reais das duas lojas. Ate la, o default assume site.
    """
    # Pedido criado manualmente no admin: sem gateway de pagamento online.
    gateway = str(raw_order.get("gateway") or "").lower()
    if gateway in {"offline", "manual", ""} and raw_order.get("gateway_name") is None:
        # Sinal fraco; mantemos como manual so quando explicito.
        if raw_order.get("gateway") in {"offline", "manual"}:
            return CANAL_MANUAL

    # Integracao de marketplace (ANYMARKET) normalmente carrega app_id de integracao.
    app_id = raw_order.get("app_id")
    if app_id:
        return CANAL_ANYMARKET

    # Loja virtual x Mobile: quando a API expuser device/channel, refinar aqui.
    canal_bruto = str(raw_order.get("channel") or raw_order.get("sales_channel") or "").lower()
    if "mobile" in canal_bruto or "app" in canal_bruto:
        return CANAL_MOBILE
    if canal_bruto in {"web", "store", "form"} or canal_bruto == "":
        return CANAL_LOJA_VIRTUAL
    return CANAL_OUTRO


def _pedido_pago(raw_order: dict[str, Any]) -> bool:
    return str(raw_order.get("payment_status") or "").lower() == "paid"


@dataclass
class LinhaVenda:
    data: str                 # YYYY-MM-DD (created_at do pedido)
    ref: str | None
    ean: str | None
    variant_id: int
    cor: str | None
    tamanho: str | None
    quantidade: int
    receita: float            # total pago da linha (unit * qtd, ja com desconto do item)
    preco_unit_pago: float    # receita / quantidade
    canal: str
    loja: int
    order_id: int


def parse_order_lines(
    raw_order: dict[str, Any],
    mapa: MapaEan,
    loja: int,
    somente_pagos: bool = True,
) -> list[LinhaVenda]:
    """Extrai as linhas de venda de um pedido, resolvendo ref pela variante/EAN.

    O preco unitario pago vem do proprio item do pedido (ja reflete promo vigente),
    base para o "desconto medio real" do produtos.json.
    """
    if somente_pagos and not _pedido_pago(raw_order):
        return []

    canal = classificar_canal(raw_order)
    data = str(raw_order.get("created_at") or "")[:10]
    linhas: list[LinhaVenda] = []

    for item in raw_order.get("products") or []:
        ean = str(item.get("sku")).strip() if item.get("sku") else None
        variant = mapa.ean_para_variante.get(ean) if ean else None
        ref = mapa.ean_para_ref.get(ean) if ean else None
        # Fallback: se o EAN nao mapeou, tenta a ref pelo nome do item.
        if ref is None:
            ref = extrair_ref(localized(item.get("name")))

        qtd = _to_int(item.get("quantity"))
        preco_unit = _to_float(item.get("price")) or 0.0
        receita = preco_unit * qtd

        linhas.append(
            LinhaVenda(
                data=data,
                ref=ref,
                ean=ean,
                variant_id=_to_int(item.get("variant_id")),
                cor=variant.cor if variant else None,
                tamanho=variant.tamanho if variant else None,
                quantidade=qtd,
                receita=round(receita, 2),
                preco_unit_pago=round(preco_unit, 2),
                canal=canal,
                loja=loja,
                order_id=_to_int(raw_order.get("id")),
            )
        )
    return linhas


def load_sales(
    client: NuvemshopClient,
    mapa: MapaEan,
    loja: int,
    created_at_min: str | None = None,
    created_at_max: str | None = None,
    somente_site: bool = True,
) -> list[LinhaVenda]:
    """Baixa pedidos e devolve as linhas de venda normalizadas.

    somente_site=True aplica a regra invariante (Canal in Loja virtual, Mobile),
    excluindo ANYMARKET e manual, conforme CLAUDE.md secao 3.
    """
    linhas: list[LinhaVenda] = []
    for raw in iter_orders(client, created_at_min, created_at_max):
        for linha in parse_order_lines(raw, mapa, loja):
            if somente_site and linha.canal not in CANAIS_SITE:
                continue
            linhas.append(linha)
    return linhas
