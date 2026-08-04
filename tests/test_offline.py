"""Teste offline do pipeline com payloads mock no formato da API Nuvemshop.

Nao toca a rede: exercita parse_product, mapa EAN->ref, parse de pedidos, agregacao,
montagem do produto, score e classe. Roda no CI antes da chamada real (sem Secrets).

    python -m tests.test_offline
"""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.build import _agregar_vendas, _montar_produto, _hoje_brt
from pipeline.nuvemshop import (
    _categoria_departamento,
    build_ean_ref_map,
    parse_order_lines,
    parse_product,
)

HOJE = _hoje_brt()
ONTEM_ISO = (HOJE - timedelta(days=1)).isoformat() + "T10:00:00-0300"


# --- Payloads mock -------------------------------------------------------- #
PRODUTO_ESTRELA = {
    "id": 1,
    "name": {"pt": "Vestido Midi Alfaiataria - CSV1234"},
    "published": True,
    "images": [{"src": "https://img/csv1234.jpg"}],
    "categories": [
        {"id": 100, "name": {"pt": "Categorias"}, "parent": None, "subcategories": [1, 2]},
        {"id": 101, "name": {"pt": "Vestidos"}, "parent": 100, "subcategories": [3]},
        {"id": 102, "name": {"pt": "Vestido midi"}, "parent": 101, "subcategories": []},
        {"id": 200, "name": {"pt": "ESTACOES"}, "parent": None, "subcategories": [4]},
        {"id": 201, "name": {"pt": "Vestidos"}, "parent": 200, "subcategories": []},
    ],
    "variants": [
        {"id": 11, "sku": "7890000000018", "price": "389.90", "promotional_price": "299.90",
         "stock": 3, "values": [{"pt": "Preto"}, {"pt": "P"}]},
        {"id": 12, "sku": "7890000000025", "price": "389.90", "promotional_price": "299.90",
         "stock": 2, "values": [{"pt": "Preto"}, {"pt": "M"}]},
        {"id": 13, "sku": "7890000000032", "price": "389.90", "promotional_price": "299.90",
         "stock": 4, "values": [{"pt": "Preto"}, {"pt": "G"}]},
    ],
}

PRODUTO_SEM_REF = {
    "id": 2,
    "name": {"pt": "BLUSA ML COM AMARRACAO"},
    "published": True,
    "variants": [{"id": 21, "sku": "7890000000100", "price": "120.0", "stock": 5,
                  "values": [{"pt": "Off White"}, {"pt": "U"}]}],
}

PRODUTO_NAO_PUBLICADO = {
    "id": 3,
    "name": {"pt": "Saia Longa - RLC4321"},
    "published": False,
    "variants": [{"id": 31, "sku": "7890000000200", "price": "200.0", "stock": 8,
                  "values": [{"pt": "Verde"}, {"pt": "M"}]}],
}

PEDIDO = {
    "id": 500,
    "payment_status": "paid",
    "status": "open",
    "storefront": "store",           # Loja virtual (web)
    "created_at": ONTEM_ISO,
    "gateway": "nuvem-pago",
    "products": [
        {"variant_id": 12, "sku": "7890000000025", "name": {"pt": "Vestido Midi - CSV1234"},
         "quantity": 2, "price": "299.90"},
    ],
}

PEDIDO_MOBILE = {
    "id": 502, "payment_status": "paid", "status": "open", "storefront": "mobile",
    "created_at": ONTEM_ISO,
    "products": [{"variant_id": 11, "sku": "7890000000018", "quantity": 1, "price": "299.90"}],
}

PEDIDO_ANYMARKET = {
    "id": 501, "payment_status": "paid", "status": "open", "storefront": "api", "app_id": 1382,
    "gateway": "not-provided", "created_at": ONTEM_ISO,
    "products": [{"variant_id": 12, "sku": "7890000000025", "quantity": 5, "price": "310.0"}],
}

PEDIDO_CANCELADO = {
    "id": 503, "payment_status": "paid", "status": "cancelled", "storefront": "store",
    "created_at": ONTEM_ISO,
    "products": [{"variant_id": 12, "sku": "7890000000025", "quantity": 3, "price": "299.90"}],
}


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_parse_product() -> None:
    p = parse_product(PRODUTO_ESTRELA, loja=1)
    check(p.ref == "CSV1234", f"ref esperada CSV1234, veio {p.ref}")
    check(p.marca == "Simone Gomes", f"marca errada: {p.marca}")
    check(p.estoque_total == 9, f"estoque_total esperado 9, veio {p.estoque_total}")
    check(p.preco_cheio == 389.90, f"preco_cheio errado: {p.preco_cheio}")
    check(p.foto == "https://img/csv1234.jpg", "foto nao extraida")
    check(p.categoria == "Vestidos", f"categoria errada: {p.categoria}")
    check(len(p.variantes) == 3, "esperava 3 variantes")


def test_categoria_departamento() -> None:
    # Departamento = filho direto do root "Categorias", ignorando ESTACOES e Sale.
    cats = [
        {"id": 1, "name": {"pt": "Categorias"}, "parent": None, "subcategories": [10, 11]},
        {"id": 10, "name": {"pt": "Sale"}, "parent": 1, "subcategories": []},
        {"id": 11, "name": {"pt": "Blusas"}, "parent": 1, "subcategories": [12]},
        {"id": 12, "name": {"pt": "Blusa manga curta"}, "parent": 11, "subcategories": []},
        {"id": 20, "name": {"pt": "ESTACOES"}, "parent": None, "subcategories": [21]},
        {"id": 21, "name": {"pt": "Blusas e Camisetas"}, "parent": 20, "subcategories": []},
    ]
    dep = _categoria_departamento(cats)
    check(dep == "Blusas", f"departamento esperado Blusas, veio {dep}")
    check(_categoria_departamento([]) is None, "lista vazia deveria dar None")


def test_ean_map_e_excecoes() -> None:
    produtos = [parse_product(r, 1) for r in (PRODUTO_ESTRELA, PRODUTO_SEM_REF, PRODUTO_NAO_PUBLICADO)]
    mapa = build_ean_ref_map(produtos)
    check(mapa.ean_para_ref["7890000000025"] == "CSV1234", "EAN nao mapeou para ref")
    check(len(mapa.ean_para_ref) == 4, f"esperava 4 EANs mapeados, veio {len(mapa.ean_para_ref)}")
    check(mapa.eans_duplicados == [], f"nao deveria haver duplicados: {mapa.eans_duplicados}")
    check(len(mapa.produtos_sem_ref) == 1, "esperava 1 produto sem ref")
    check(mapa.produtos_sem_ref[0].product_id == 2, "produto sem ref errado")


def test_pedido_e_canal() -> None:
    produtos = [parse_product(PRODUTO_ESTRELA, 1)]
    mapa = build_ean_ref_map(produtos)

    linhas = parse_order_lines(PEDIDO, mapa, loja=1)
    check(len(linhas) == 1, "esperava 1 linha de venda")
    l = linhas[0]
    check(l.ref == "CSV1234", f"ref da venda errada: {l.ref}")
    check(l.quantidade == 2, "quantidade errada")
    check(abs(l.receita - 599.80) < 0.001, f"receita errada: {l.receita}")
    check(l.cor == "Preto" and l.tamanho == "M", f"cor/tam errados: {l.cor}/{l.tamanho}")
    check(l.canal == "Loja virtual", f"canal esperado Loja virtual, veio {l.canal}")

    mob = parse_order_lines(PEDIDO_MOBILE, mapa, loja=1)
    check(mob[0].canal == "Mobile", f"canal esperado Mobile, veio {mob[0].canal}")

    any_lines = parse_order_lines(PEDIDO_ANYMARKET, mapa, loja=1)
    check(any_lines[0].canal == "ANYMARKET", f"canal esperado ANYMARKET, veio {any_lines[0].canal}")

    # Pedido cancelado (mesmo pago) nao gera linha de venda.
    canc = parse_order_lines(PEDIDO_CANCELADO, mapa, loja=1)
    check(canc == [], f"pedido cancelado nao deveria gerar linha, veio {len(canc)}")


def test_build_produto_estrela() -> None:
    produtos = [parse_product(PRODUTO_ESTRELA, 1)]
    mapa = build_ean_ref_map(produtos)
    linhas = parse_order_lines(PEDIDO, mapa, loja=1)  # canal site, entra

    corte_30d = HOJE - timedelta(days=30)
    vendas = _agregar_vendas(linhas, corte_30d, corte_30d)
    prod_json = _montar_produto(produtos[0], vendas.get("CSV1234"), corte_30d, HOJE)

    check(prod_json["ref"] == "CSV1234", "ref no json errada")
    check(prod_json["grade"]["cheia"] is True, "grade deveria ser cheia (P/M/G no Preto)")
    check(prod_json["vendas"]["d30"] == 2, f"vendas d30 esperada 2, veio {prod_json['vendas']['d30']}")
    check(prod_json["classe"] == "ESTRELA", f"classe esperada ESTRELA, veio {prod_json['classe']}")
    check(prod_json["score"] > 0, "score deveria ser > 0")
    check(prod_json["score_parcial"] is True, "score deveria ser parcial (sem GA4)")
    # desconto medio real: pagou 299.90 sobre cheio 389.90 -> ~23.1%
    check(abs(prod_json["desconto_medio_pct"] - 23.1) < 0.2,
          f"desconto medio errado: {prod_json['desconto_medio_pct']}")
    # variante Preto com vendas_por_tamanho M=2
    preto = next(v for v in prod_json["variantes"] if v["cor"] == "Preto")
    check(preto["vendas_por_tamanho"].get("M") == 2, "vendas por tamanho M deveria ser 2")


def test_merge_ga4() -> None:
    """Com sinais GA4, funil e origens sao preenchidos e o score deixa de ser parcial."""
    produtos = [parse_product(PRODUTO_ESTRELA, 1)]
    mapa = build_ean_ref_map(produtos)
    linhas = parse_order_lines(PEDIDO, mapa, loja=1)
    corte_30d = HOJE - timedelta(days=30)
    vendas = _agregar_vendas(linhas, corte_30d, corte_30d)

    ontem = (HOJE - timedelta(days=1)).isoformat()
    ga4_funil = {"views": 500, "add_cart": 60, "checkout": 25, "compras": 20,
                 "receita": 5990.0, "cv_view_cart": 12.0, "cv_cart_compra": 33.33, "cv_geral": 4.0}
    ga4_origens = {"30d": [{"source": "facebook", "medium": "cpc", "views": 300, "add_cart": 40,
                            "compras": 12, "receita": 3600.0}]}
    ga4_dia = {ontem: {"views": 40, "cart": 6, "checkout": 3, "compras": 2}}
    ga4_mes = {ontem[:7]: {"views": 900, "compras": 20}}

    prod_json = _montar_produto(produtos[0], vendas.get("CSV1234"), corte_30d, HOJE,
                                ga4_funil, ga4_origens, ga4_dia, ga4_mes)
    check(prod_json["funil"]["views"] == 500, "funil views deveria vir do GA4")
    check(prod_json["funil"]["cv_geral"] == 4.0, "cv_geral deveria vir do GA4")
    check(prod_json["score_parcial"] is False, "com GA4 o score nao deveria ser parcial")
    check(len(prod_json["origens"]["30d"]) == 1, "origens[30d] deveria ter a linha GA4")

    # serie_90d esparsa, chaves curtas: v/a/k/c (GA4) + q/r (Nuvemshop site).
    dia = next(d for d in prod_json["serie_90d"] if d["d"] == ontem)
    check(dia["v"] == 40 and dia["a"] == 6 and dia["k"] == 3 and dia["c"] == 2,
          f"serie deveria ter funil diario do GA4: {dia}")
    check(dia["q"] == 2, "serie deveria manter unidades site da Nuvemshop")
    check(abs(dia["r"] - 599.80) < 0.001, f"receita site do dia errada: {dia.get('r')}")
    check(len(prod_json["serie_90d"]) == 1, "serie esparsa: so dias com atividade")

    # mensal_24m: GA4 (v/c) + Nuvemshop site (q/r).
    mes = next(m for m in prod_json["mensal_24m"] if m["m"] == ontem[:7])
    check(mes["v"] == 900 and mes["c"] == 20, f"mensal deveria ter views/compras GA4: {mes}")
    check(mes["q"] == 2 and abs(mes["r"] - 599.80) < 0.001, f"mensal site errado: {mes}")


def test_canais_serie_e_mensal() -> None:
    """Canais fora do site (ANYMARKET/manual) nao entram nos agregados invariantes,
    mas aparecem separados (qm/rm) na serie diaria e na mensal."""
    produtos = [parse_product(PRODUTO_ESTRELA, 1)]
    mapa = build_ean_ref_map(produtos)
    linhas = parse_order_lines(PEDIDO, mapa, loja=1) + parse_order_lines(
        PEDIDO_ANYMARKET, mapa, loja=1
    )
    corte_30d = HOJE - timedelta(days=30)
    vendas = _agregar_vendas(linhas, corte_30d, corte_30d)
    agg = vendas["CSV1234"]

    check(agg.periodo == 2, f"periodo (site) esperado 2, veio {agg.periodo}")
    check(agg.d30 == 2, f"d30 (site) esperado 2, veio {agg.d30}")
    check(abs(agg.receita_periodo - 599.80) < 0.001, "receita periodo deveria ser so site")

    prod_json = _montar_produto(produtos[0], agg, corte_30d, HOJE)
    ontem = (HOJE - timedelta(days=1)).isoformat()
    dia = next(d for d in prod_json["serie_90d"] if d["d"] == ontem)
    check(dia["q"] == 2 and dia["qm"] == 5, f"split site/fora do site errado no dia: {dia}")
    check(abs(dia["rm"] - 1550.0) < 0.001, f"receita fora do site errada: {dia.get('rm')}")
    mes = next(m for m in prod_json["mensal_24m"] if m["m"] == ontem[:7])
    check(mes["q"] == 2 and mes["qm"] == 5, f"split site/fora do site errado no mes: {mes}")
    check(prod_json["classe"] == "ESTRELA", "classe nao deveria mudar com canais externos")


def test_nao_publicado_penalizado() -> None:
    prod = parse_product(PRODUTO_NAO_PUBLICADO, 1)
    corte_30d = HOJE - timedelta(days=30)
    prod_json = _montar_produto(prod, None, corte_30d, HOJE)
    check(prod_json["classe"] == "NAO PUBLICADO", f"classe errada: {prod_json['classe']}")


def main() -> None:
    testes = [
        test_parse_product,
        test_categoria_departamento,
        test_ean_map_e_excecoes,
        test_pedido_e_canal,
        test_build_produto_estrela,
        test_merge_ga4,
        test_canais_serie_e_mensal,
        test_nao_publicado_penalizado,
    ]
    for t in testes:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(testes)} testes passaram.")


if __name__ == "__main__":
    main()
