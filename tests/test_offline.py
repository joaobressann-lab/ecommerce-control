"""Teste offline do pipeline com payloads mock no formato da API Nuvemshop.

Nao toca a rede: exercita parse_product, mapa EAN->ref, parse de pedidos, agregacao,
montagem do produto, score e classe. Roda no CI antes da chamada real (sem Secrets).

    python -m tests.test_offline
"""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.build import _agregar_vendas, _montar_produto, _hoje_brt
from pipeline.nuvemshop import (
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
    "categories": [{"name": {"pt": "Vestidos"}}],
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
    "created_at": ONTEM_ISO,
    "gateway": "mercadopago",
    "products": [
        {"variant_id": 12, "sku": "7890000000025", "name": {"pt": "Vestido Midi - CSV1234"},
         "quantity": 2, "price": "299.90"},
    ],
}

PEDIDO_ANYMARKET = {
    "id": 501, "payment_status": "paid", "created_at": ONTEM_ISO, "app_id": 999,
    "products": [{"variant_id": 12, "sku": "7890000000025", "quantity": 5, "price": "310.0"}],
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

    any_lines = parse_order_lines(PEDIDO_ANYMARKET, mapa, loja=1)
    check(any_lines[0].canal == "ANYMARKET", f"canal esperado ANYMARKET, veio {any_lines[0].canal}")


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


def test_nao_publicado_penalizado() -> None:
    prod = parse_product(PRODUTO_NAO_PUBLICADO, 1)
    corte_30d = HOJE - timedelta(days=30)
    prod_json = _montar_produto(prod, None, corte_30d, HOJE)
    check(prod_json["classe"] == "NAO PUBLICADO", f"classe errada: {prod_json['classe']}")


def main() -> None:
    testes = [
        test_parse_product,
        test_ean_map_e_excecoes,
        test_pedido_e_canal,
        test_build_produto_estrela,
        test_nao_publicado_penalizado,
    ]
    for t in testes:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(testes)} testes passaram.")


if __name__ == "__main__":
    main()
