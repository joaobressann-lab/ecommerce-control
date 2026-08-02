"""Builder dos JSONs de saida (F1.5): produtos.json e diario.json.

Consome o conector Nuvemshop e produz o schema da secao 5 do CLAUDE.md.
Funil, views, origens e midia entram como placeholders vazios ate GA4 (F1.4)
e Meta/Google (F2/F3). O score usa renormalizacao parcial (ver scoring.py).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import StoreConfig
from .http import NuvemshopClient
from .nuvemshop import (
    LinhaVenda,
    ProdutoNorm,
    build_ean_ref_map,
    load_products,
    load_sales,
)
from .scoring import SIZE_ORDER, SinaisProduto, calcular_score

BRT = timezone(timedelta(hours=-3))


def _hoje_brt() -> date:
    return datetime.now(BRT).date()


def _iso(dt: date) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------- #
# Agregacao de vendas
# ---------------------------------------------------------------------- #
@dataclass
class VendasRef:
    periodo: int = 0
    d30: int = 0
    receita_periodo: float = 0.0
    valor_pago_total: float = 0.0     # para preco medio pago
    unidades_pagas: int = 0
    por_cor_tam: dict[tuple[str, str], int] = None       # (cor, tamanho) -> unidades periodo
    por_dia: dict[str, int] = None                       # data -> unidades (serie)

    def __post_init__(self) -> None:
        if self.por_cor_tam is None:
            self.por_cor_tam = defaultdict(int)
        if self.por_dia is None:
            self.por_dia = defaultdict(int)

    @property
    def preco_medio_pago(self) -> float | None:
        if self.unidades_pagas <= 0:
            return None
        return round(self.valor_pago_total / self.unidades_pagas, 2)


def _agregar_vendas(
    linhas: list[LinhaVenda], corte_30d: date, corte_periodo: date
) -> dict[str, VendasRef]:
    """Agrupa linhas de venda por ref, com recortes de 30d e do periodo do filtro."""
    por_ref: dict[str, VendasRef] = defaultdict(VendasRef)
    for l in linhas:
        if not l.ref:
            continue
        agg = por_ref[l.ref]
        try:
            d = date.fromisoformat(l.data)
        except ValueError:
            continue

        if d >= corte_30d:
            agg.d30 += l.quantidade
        if d >= corte_periodo:
            agg.periodo += l.quantidade
            agg.receita_periodo += l.receita
            agg.valor_pago_total += l.preco_unit_pago * l.quantidade
            agg.unidades_pagas += l.quantidade
            cor = (l.cor or "").strip()
            tam = (l.tamanho or "").strip()
            if cor or tam:
                agg.por_cor_tam[(cor, tam)] += l.quantidade
        agg.por_dia[l.data] += l.quantidade
    return por_ref


# ---------------------------------------------------------------------- #
# Grade e variantes
# ---------------------------------------------------------------------- #
def _montar_variantes(
    produto: ProdutoNorm, vendas: VendasRef | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Agrupa variantes por cor e calcula a grade (schema 5.1)."""
    por_cor: dict[str, dict[str, Any]] = {}
    por_tamanho_total: dict[str, int] = defaultdict(int)

    for var in produto.variantes:
        cor = (var.cor or "UNICA").strip()
        tam = (var.tamanho or "U").strip()
        entry = por_cor.setdefault(
            cor, {"cor": cor, "pantone": None, "estoque": {}, "vendas_por_tamanho": {}, "eans": {}}
        )
        entry["estoque"][tam] = entry["estoque"].get(tam, 0) + max(var.estoque, 0)
        if var.ean:
            entry["eans"][tam] = var.ean
        por_tamanho_total[tam] += max(var.estoque, 0)

    # Vendas por (cor, tamanho) do periodo.
    if vendas:
        for (cor, tam), qtd in vendas.por_cor_tam.items():
            cor_k = cor or "UNICA"
            tam_k = tam or "U"
            entry = por_cor.setdefault(
                cor_k,
                {"cor": cor_k, "pantone": None, "estoque": {}, "vendas_por_tamanho": {}, "eans": {}},
            )
            entry["vendas_por_tamanho"][tam_k] = entry["vendas_por_tamanho"].get(tam_k, 0) + qtd

    variantes = []
    for cor, entry in por_cor.items():
        vendas_cor = sum(entry["vendas_por_tamanho"].values())
        entry["vendas_periodo"] = vendas_cor
        variantes.append(entry)

    # Grade cheia: existe uma cor com P, M, G todos com estoque > 0.
    grade_cheia = any(
        all(entry["estoque"].get(t, 0) > 0 for t in ("P", "M", "G")) for entry in por_cor.values()
    )
    por_tamanho_ordenado = {
        t: por_tamanho_total[t] for t in SIZE_ORDER if t in por_tamanho_total
    }
    # Tamanhos fora da ordem canonica entram no fim.
    for t, q in por_tamanho_total.items():
        if t not in por_tamanho_ordenado:
            por_tamanho_ordenado[t] = q

    grade = {"cores": len(por_cor), "cheia": grade_cheia, "por_tamanho": por_tamanho_ordenado}
    return variantes, grade


def _serie_30d(vendas: VendasRef | None, corte_30d: date, hoje: date) -> list[dict[str, Any]]:
    """Serie diaria de compras (views entram quando GA4 existir)."""
    serie = []
    dia = corte_30d
    while dia <= hoje:
        chave = _iso(dia)
        compras = vendas.por_dia.get(chave, 0) if vendas else 0
        serie.append({"d": chave, "views": 0, "compras": compras})
        dia += timedelta(days=1)
    return serie


# ---------------------------------------------------------------------- #
# Montagem do produto
# ---------------------------------------------------------------------- #
def _montar_produto(
    produto: ProdutoNorm, vendas: VendasRef | None, corte_30d: date, hoje: date
) -> dict[str, Any]:
    variantes, grade = _montar_variantes(produto, vendas)

    v_periodo = vendas.periodo if vendas else 0
    v_30d = vendas.d30 if vendas else 0
    receita = round(vendas.receita_periodo, 2) if vendas else 0.0
    preco_medio = vendas.preco_medio_pago if vendas else None

    desconto_pct = None
    if preco_medio is not None and produto.preco_cheio:
        desconto_pct = round((1 - preco_medio / produto.preco_cheio) * 100, 1)

    sinais = SinaisProduto(
        vendas_30d=v_30d,
        vendas_periodo=v_periodo,
        estoque_total=produto.estoque_total,
        grade_cheia=grade["cheia"],
        publicado=produto.publicado,
    )
    resultado = calcular_score(sinais)

    return {
        "ref": produto.ref,
        "nome": produto.nome,
        "marca": produto.marca,
        "loja": produto.loja,
        "categoria": produto.categoria,
        "foto": produto.foto,
        "publicado": produto.publicado,
        "preco_cheio": produto.preco_cheio,
        "preco_promocional": produto.preco_promocional,
        "preco_medio_pago": preco_medio,
        "desconto_medio_pct": desconto_pct,
        "estoque_total": produto.estoque_total,
        "grade": grade,
        "variantes": variantes,
        # Placeholders ate GA4 (F1.4) e Meta/Google (F2/F3):
        "funil": {"views": 0, "add_cart": 0, "checkout": 0, "compras": v_periodo,
                  "cv_view_cart": None, "cv_cart_compra": None, "cv_geral": None},
        "vendas": {"periodo": v_periodo, "d30": v_30d, "receita_periodo": receita},
        "origens": [],
        "midia": {"google_custo": None, "google_roas": None, "meta_custo": None, "meta_roas": None},
        "serie_30d": _serie_30d(vendas, corte_30d, hoje),
        "score": resultado.score,
        "classe": resultado.classe,
        "score_parcial": resultado.score_parcial,
    }


# ---------------------------------------------------------------------- #
# diario.json
# ---------------------------------------------------------------------- #
def _montar_diario(
    linhas_por_loja: dict[int, list[LinhaVenda]], marca_por_ref: dict[str, str]
) -> dict[str, Any]:
    """Serie por dia x loja x marca x canal de venda (schema 5.2).

    Campos de GA4 (sessoes, canal_ga4) e midia entram nas fases seguintes.
    """
    acc: dict[tuple, dict[str, Any]] = {}
    pedidos_vistos: dict[tuple, set[int]] = defaultdict(set)

    for loja, linhas in linhas_por_loja.items():
        for l in linhas:
            marca = marca_por_ref.get(l.ref or "", "Desconhecida")
            chave = (l.data, loja, marca, l.canal)
            row = acc.setdefault(
                chave,
                {"d": l.data, "loja": loja, "marca": marca, "canal_venda": l.canal,
                 "canal_ga4": None, "sessoes": None, "transacoes": 0, "receita": 0.0,
                 "custo_meta": None, "custo_google": None},
            )
            row["receita"] = round(row["receita"] + l.receita, 2)
            if l.order_id not in pedidos_vistos[chave]:
                pedidos_vistos[chave].add(l.order_id)
                row["transacoes"] += 1

    return {"linhas": sorted(acc.values(), key=lambda r: (r["d"], r["loja"], r["marca"]))}


# ---------------------------------------------------------------------- #
# Orquestracao
# ---------------------------------------------------------------------- #
def construir(
    lojas: list[StoreConfig], periodo_dias: int = 30, janela_dias: int = 30
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Baixa as lojas configuradas e monta (produtos_json, diario_json)."""
    hoje = _hoje_brt()
    corte_30d = hoje - timedelta(days=30)
    corte_periodo = hoje - timedelta(days=periodo_dias)
    janela_min = (hoje - timedelta(days=max(janela_dias, periodo_dias, 30))).isoformat()

    produtos_out: list[dict[str, Any]] = []
    linhas_por_loja: dict[int, list[LinhaVenda]] = {}
    marca_por_ref_global: dict[str, str] = {}

    for loja in lojas:
        client = NuvemshopClient(loja)
        produtos = load_products(client, loja.numero)
        mapa = build_ean_ref_map(produtos)
        vendas_linhas = load_sales(client, mapa, loja.numero, created_at_min=janela_min)
        linhas_por_loja[loja.numero] = vendas_linhas

        vendas_por_ref = _agregar_vendas(vendas_linhas, corte_30d, corte_periodo)

        for prod in produtos:
            if prod.ref:
                marca_por_ref_global[prod.ref] = prod.marca
            vendas = vendas_por_ref.get(prod.ref) if prod.ref else None
            produtos_out.append(_montar_produto(prod, vendas, corte_30d, hoje))

    produtos_json = {
        "gerado_em": datetime.now(BRT).isoformat(),
        "periodo": {"inicio": _iso(corte_periodo), "fim": _iso(hoje)},
        "meta": {
            "ga4_disponivel": False,
            "midia_disponivel": False,
            "score_parcial_global": True,
            "total_produtos": len(produtos_out),
        },
        "produtos": produtos_out,
    }
    diario_json = _montar_diario(linhas_por_loja, marca_por_ref_global)
    return produtos_json, diario_json
