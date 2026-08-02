"""Builder dos JSONs de saida (F1.5): produtos.json e diario.json.

Consome o conector Nuvemshop e produz o schema da secao 5 do CLAUDE.md.
Funil, views, origens e midia entram como placeholders vazios ate GA4 (F1.4)
e Meta/Google (F2/F3). O score usa renormalizacao parcial (ver scoring.py).
"""

from __future__ import annotations

import os
import statistics
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


def _serie_30d(
    vendas: VendasRef | None,
    corte_30d: date,
    hoje: date,
    ga4_dia: dict[str, dict[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """Serie diaria: compras (Nuvemshop, site) + views (GA4, quando disponivel)."""
    serie = []
    dia = corte_30d
    while dia <= hoje:
        chave = _iso(dia)
        compras = vendas.por_dia.get(chave, 0) if vendas else 0
        views = ga4_dia.get(chave, {}).get("views", 0) if ga4_dia else 0
        serie.append({"d": chave, "views": views, "compras": compras})
        dia += timedelta(days=1)
    return serie


# ---------------------------------------------------------------------- #
# Montagem do produto
# ---------------------------------------------------------------------- #
def _montar_produto(
    produto: ProdutoNorm,
    vendas: VendasRef | None,
    corte_30d: date,
    hoje: date,
    ga4_funil: dict[str, Any] | None = None,
    ga4_origens: list[dict[str, Any]] | None = None,
    ga4_dia: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    variantes, grade = _montar_variantes(produto, vendas)

    v_periodo = vendas.periodo if vendas else 0
    v_30d = vendas.d30 if vendas else 0
    receita = round(vendas.receita_periodo, 2) if vendas else 0.0
    preco_medio = vendas.preco_medio_pago if vendas else None

    desconto_pct = None
    if preco_medio is not None and produto.preco_cheio:
        desconto_pct = round((1 - preco_medio / produto.preco_cheio) * 100, 1)

    # Funil: GA4 quando disponivel, senao placeholder com compras = vendas de site.
    if ga4_funil:
        funil = {
            "views": ga4_funil["views"], "add_cart": ga4_funil["add_cart"],
            "checkout": ga4_funil["checkout"], "compras": ga4_funil["compras"],
            "cv_view_cart": ga4_funil.get("cv_view_cart"),
            "cv_cart_compra": ga4_funil.get("cv_cart_compra"),
            "cv_geral": ga4_funil.get("cv_geral"),
        }
        views_30d = ga4_funil["views"]
        cv_geral = ga4_funil.get("cv_geral")
    else:
        funil = {"views": 0, "add_cart": 0, "checkout": 0, "compras": v_periodo,
                 "cv_view_cart": None, "cv_cart_compra": None, "cv_geral": None}
        views_30d = None
        cv_geral = None

    sinais = SinaisProduto(
        vendas_30d=v_30d,
        vendas_periodo=v_periodo,
        estoque_total=produto.estoque_total,
        grade_cheia=grade["cheia"],
        publicado=produto.publicado,
        views_30d=views_30d,
        cv_geral=cv_geral,
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
        "funil": funil,
        "vendas": {"periodo": v_periodo, "d30": v_30d, "receita_periodo": receita},
        "origens": ga4_origens or [],
        # Midia (Meta/Google) entra em F2/F3:
        "midia": {"google_custo": None, "google_roas": None, "meta_custo": None, "meta_roas": None},
        "serie_30d": _serie_30d(vendas, corte_30d, hoje, ga4_dia),
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
def _coletar_ga4_loja(loja: StoreConfig, ean_para_ref: dict[str, str], inicio: str, fim: str):
    """Coleta GA4 da loja se houver propriedade + service account. Import tardio (libs pesadas)."""
    from .config import GA4_CREDENTIALS_ENV

    if not os.environ.get(GA4_CREDENTIALS_ENV):
        return None
    if not loja.ga4_property:
        return None
    try:
        from .ga4 import coletar_ga4  # import tardio: so quando GA4 esta configurado
        return coletar_ga4(loja.ga4_property, ean_para_ref, inicio, fim)
    except Exception as exc:  # nao derruba o pipeline se o GA4 falhar
        print(f"AVISO: GA4 loja {loja.numero} falhou ({exc}). Seguindo sem GA4 nesta loja.")
        return None


def _aplicar_auditar_pdp(produtos_out: list[dict[str, Any]]) -> None:
    """Gatilho objetivo de AUDITAR PDP (secao 6): views acima da mediana da categoria
    e cv_geral abaixo de 40% da mediana. So aplica a produtos com dados GA4."""
    por_cat: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for p in produtos_out:
        if not p["score_parcial"] and p["funil"]["views"] > 0:
            por_cat[(p["loja"], p.get("categoria"))].append(p)

    for grupo in por_cat.values():
        views = [p["funil"]["views"] for p in grupo]
        cvs = [p["funil"]["cv_geral"] for p in grupo if p["funil"]["cv_geral"] is not None]
        if len(views) < 4 or not cvs:
            continue
        med_views = statistics.median(views)
        med_cv = statistics.median(cvs)
        for p in grupo:
            cv = p["funil"]["cv_geral"]
            # Nao sobrescreve estados fortes (esgotado, estoque critico, nao publicado).
            if p["classe"] in {"NAO PUBLICADO", "ESTOQUE CRITICO", "ESGOTADO",
                               "ESGOTADO c/ demanda"}:
                continue
            if p["funil"]["views"] > med_views and cv is not None and cv < 0.40 * med_cv:
                p["classe"] = "AUDITAR PDP"


def construir(
    lojas: list[StoreConfig], periodo_dias: int = 30, janela_dias: int = 30
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Baixa as lojas configuradas e monta (produtos_json, diario_json)."""
    hoje = _hoje_brt()
    corte_30d = hoje - timedelta(days=30)
    corte_periodo = hoje - timedelta(days=periodo_dias)
    janela_min = (hoje - timedelta(days=max(janela_dias, periodo_dias, 30))).isoformat()
    ga4_inicio = _iso(corte_30d)
    ga4_fim = _iso(hoje)

    produtos_out: list[dict[str, Any]] = []
    linhas_por_loja: dict[int, list[LinhaVenda]] = {}
    marca_por_ref_global: dict[str, str] = {}
    trafego_linhas: list[dict[str, Any]] = []
    ga4_nao_mapeados: dict[int, list[str]] = {}
    algum_ga4 = False

    for loja in lojas:
        client = NuvemshopClient(loja)
        produtos = load_products(client, loja.numero)
        mapa = build_ean_ref_map(produtos)
        vendas_linhas = load_sales(client, mapa, loja.numero, created_at_min=janela_min)
        linhas_por_loja[loja.numero] = vendas_linhas
        vendas_por_ref = _agregar_vendas(vendas_linhas, corte_30d, corte_periodo)

        ga4 = _coletar_ga4_loja(loja, mapa.ean_para_ref, ga4_inicio, ga4_fim)
        if ga4 is not None:
            algum_ga4 = True
            for linha in ga4.site_dia:
                trafego_linhas.append({**linha, "loja": loja.numero})
            if ga4.eans_nao_mapeados:
                ga4_nao_mapeados[loja.numero] = sorted(ga4.eans_nao_mapeados)

        for prod in produtos:
            if prod.ref:
                marca_por_ref_global[prod.ref] = prod.marca
            vendas = vendas_por_ref.get(prod.ref) if prod.ref else None
            gfun = ga4.funil.get(prod.ref) if ga4 and prod.ref else None
            gorig = ga4.origens.get(prod.ref) if ga4 and prod.ref else None
            gdia = ga4.por_dia.get(prod.ref) if ga4 and prod.ref else None
            produtos_out.append(
                _montar_produto(prod, vendas, corte_30d, hoje, gfun, gorig, gdia)
            )

    _aplicar_auditar_pdp(produtos_out)

    produtos_json = {
        "gerado_em": datetime.now(BRT).isoformat(),
        "periodo": {"inicio": _iso(corte_periodo), "fim": _iso(hoje)},
        "meta": {
            "ga4_disponivel": algum_ga4,
            "midia_disponivel": False,
            "score_parcial_global": not algum_ga4,
            "total_produtos": len(produtos_out),
            "ga4_eans_nao_mapeados": ga4_nao_mapeados,
        },
        "produtos": produtos_out,
    }
    diario_json = _montar_diario(linhas_por_loja, marca_por_ref_global)
    diario_json["trafego"] = sorted(trafego_linhas, key=lambda r: (r["d"], r["loja"]))
    return produtos_json, diario_json
