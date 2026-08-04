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
    CANAIS_SITE,
    TZ_SP,
    LinhaVenda,
    ProdutoNorm,
    build_ean_ref_map,
    load_products,
    load_sales,
)
from .scoring import SIZE_ORDER, SinaisProduto, calcular_score

# Fuso canonico do negocio (America/Sao_Paulo; ver nuvemshop.TZ_SP).
BRT = TZ_SP

# Granularidade dupla (CLAUDE.md secao 6.5): serie diaria de 90d + mensal de 24 meses.
SERIE_DIAS = 90
MENSAL_MESES = 24


def _hoje_brt() -> date:
    return datetime.now(BRT).date()


def _iso(dt: date) -> str:
    return dt.isoformat()


def _mes_de(data_iso: str) -> str:
    return data_iso[:7]


def _inicio_mensal(hoje: date, meses: int = MENSAL_MESES) -> date:
    """Primeiro dia do mes, (meses-1) meses antes do mes corrente."""
    ano = hoje.year
    mes = hoje.month - (meses - 1)
    while mes <= 0:
        mes += 12
        ano -= 1
    return date(ano, mes, 1)


# ---------------------------------------------------------------------- #
# Agregacao de vendas
# ---------------------------------------------------------------------- #
@dataclass
class VendasRef:
    """Agregados de venda de uma ref.

    Os campos "invariantes" (periodo, d30, receita, preco medio, por_cor_tam)
    seguem a secao 3: SO SITE (Loja virtual + Mobile). Os diarios/mensais separam
    site (q/r) de fora do site (qm/rm = ANYMARKET + manual) para o filtro de canal
    no cliente.
    """

    periodo: int = 0
    d30: int = 0
    receita_periodo: float = 0.0
    valor_pago_total: float = 0.0     # para preco medio pago
    unidades_pagas: int = 0
    por_cor_tam: dict[tuple[str, str], int] = None       # (cor, tamanho) -> unidades periodo (site)
    por_dia: dict[str, int] = None                       # data -> unidades site (serie 90d)
    receita_dia: dict[str, float] = None                 # data -> receita site (serie 90d)
    por_dia_ext: dict[str, int] = None                   # data -> unidades fora do site
    receita_dia_ext: dict[str, float] = None             # data -> receita fora do site
    por_mes: dict[str, int] = None                       # YYYY-MM -> unidades site (24m)
    receita_mes: dict[str, float] = None                 # YYYY-MM -> receita site (24m)
    por_mes_ext: dict[str, int] = None                   # YYYY-MM -> unidades fora do site
    receita_mes_ext: dict[str, float] = None             # YYYY-MM -> receita fora do site

    def __post_init__(self) -> None:
        for campo in ("por_cor_tam", "por_dia", "por_dia_ext", "por_mes", "por_mes_ext"):
            if getattr(self, campo) is None:
                setattr(self, campo, defaultdict(int))
        for campo in ("receita_dia", "receita_dia_ext", "receita_mes", "receita_mes_ext"):
            if getattr(self, campo) is None:
                setattr(self, campo, defaultdict(float))

    @property
    def preco_medio_pago(self) -> float | None:
        if self.unidades_pagas <= 0:
            return None
        return round(self.valor_pago_total / self.unidades_pagas, 2)


def _agregar_vendas(
    linhas: list[LinhaVenda],
    corte_30d: date,
    corte_periodo: date,
    corte_serie: date | None = None,
) -> dict[str, VendasRef]:
    """Agrupa linhas de venda por ref (TODOS os canais), com recortes de 30d,
    do periodo do filtro, da serie diaria (90d) e mensal (24m).

    A invariante da secao 3 vale aqui: os agregados de score/KPIs (periodo, d30,
    receita_periodo, preco medio, por_cor_tam) contam SO SITE. Canais fora do site
    entram apenas nas series *_ext, para o filtro de canal do dashboard.
    """
    if corte_serie is None:
        corte_serie = corte_30d
    por_ref: dict[str, VendasRef] = defaultdict(VendasRef)
    for l in linhas:
        if not l.ref:
            continue
        agg = por_ref[l.ref]
        try:
            d = date.fromisoformat(l.data)
        except ValueError:
            continue
        site = l.canal in CANAIS_SITE

        if site:
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
            if d >= corte_serie:
                agg.por_dia[l.data] += l.quantidade
                agg.receita_dia[l.data] += l.receita
            agg.por_mes[_mes_de(l.data)] += l.quantidade
            agg.receita_mes[_mes_de(l.data)] += l.receita
        else:
            if d >= corte_serie:
                agg.por_dia_ext[l.data] += l.quantidade
                agg.receita_dia_ext[l.data] += l.receita
            agg.por_mes_ext[_mes_de(l.data)] += l.quantidade
            agg.receita_mes_ext[_mes_de(l.data)] += l.receita
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


def _serie_diaria(
    vendas: VendasRef | None,
    corte_serie: date,
    hoje: date,
    ga4_dia: dict[str, dict[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """serie_90d ESPARSA: so dias com atividade, so campos nao-zero (orcamento de
    tamanho da secao 6.5). Chaves curtas, documentadas no schema 5.1:
      v=views  a=add_cart  k=checkout  c=compras GA4
      q=un. site  r=receita site  qm=un. fora site  rm=receita fora site
    """
    dias: set[str] = set()
    if vendas:
        dias.update(vendas.por_dia)
        dias.update(vendas.por_dia_ext)
    if ga4_dia:
        dias.update(ga4_dia)

    serie = []
    for chave in sorted(dias):
        try:
            d = date.fromisoformat(chave)
        except ValueError:
            continue
        if d < corte_serie or d > hoje:
            continue
        g = ga4_dia.get(chave, {}) if ga4_dia else {}
        entry: dict[str, Any] = {"d": chave}
        for k, valor in (
            ("v", g.get("views", 0)),
            ("a", g.get("cart", 0)),
            ("k", g.get("checkout", 0)),
            ("c", g.get("compras", 0)),
            ("q", vendas.por_dia.get(chave, 0) if vendas else 0),
            ("qm", vendas.por_dia_ext.get(chave, 0) if vendas else 0),
        ):
            if valor:
                entry[k] = valor
        for k, valor in (
            ("r", vendas.receita_dia.get(chave, 0.0) if vendas else 0.0),
            ("rm", vendas.receita_dia_ext.get(chave, 0.0) if vendas else 0.0),
        ):
            if valor:
                entry[k] = round(valor, 2)
        if len(entry) > 1:
            serie.append(entry)
    return serie


def _mensal_24m(
    vendas: VendasRef | None,
    inicio_mensal: date,
    ga4_mes: dict[str, dict[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """mensal_24m ESPARSA: so meses com atividade, so campos nao-zero.
    Chaves: v=views  c=compras GA4  q=un. site  r=receita site
            qm=un. fora site  rm=receita fora site
    """
    meses: set[str] = set()
    if vendas:
        meses.update(vendas.por_mes)
        meses.update(vendas.por_mes_ext)
    if ga4_mes:
        meses.update(ga4_mes)

    piso = _mes_de(_iso(inicio_mensal))
    saida = []
    for mes in sorted(meses):
        if mes < piso:
            continue
        g = ga4_mes.get(mes, {}) if ga4_mes else {}
        entry: dict[str, Any] = {"m": mes}
        for k, valor in (
            ("v", g.get("views", 0)),
            ("c", g.get("compras", 0)),
            ("q", vendas.por_mes.get(mes, 0) if vendas else 0),
            ("qm", vendas.por_mes_ext.get(mes, 0) if vendas else 0),
        ):
            if valor:
                entry[k] = valor
        for k, valor in (
            ("r", vendas.receita_mes.get(mes, 0.0) if vendas else 0.0),
            ("rm", vendas.receita_mes_ext.get(mes, 0.0) if vendas else 0.0),
        ):
            if valor:
                entry[k] = round(valor, 2)
        if len(entry) > 1:
            saida.append(entry)
    return saida


# ---------------------------------------------------------------------- #
# Montagem do produto
# ---------------------------------------------------------------------- #
def _montar_produto(
    produto: ProdutoNorm,
    vendas: VendasRef | None,
    corte_30d: date,
    hoje: date,
    ga4_funil: dict[str, Any] | None = None,
    ga4_origens: dict[str, list[dict[str, Any]]] | None = None,
    ga4_dia: dict[str, dict[str, int]] | None = None,
    ga4_mes: dict[str, dict[str, int]] | None = None,
    corte_serie: date | None = None,
    inicio_mensal: date | None = None,
) -> dict[str, Any]:
    if corte_serie is None:
        corte_serie = hoje - timedelta(days=SERIE_DIAS)
    if inicio_mensal is None:
        inicio_mensal = _inicio_mensal(hoje)
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
        "origens": ga4_origens or {},
        # Midia (Meta/Google) entra em F2/F3:
        "midia": {"google_custo": None, "google_roas": None, "meta_custo": None, "meta_roas": None},
        "serie_90d": _serie_diaria(vendas, corte_serie, hoje, ga4_dia),
        "mensal_24m": _mensal_24m(vendas, inicio_mensal, ga4_mes),
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
def _coletar_ga4_loja(
    loja: StoreConfig, ean_para_ref: dict[str, str], hoje: str, inicio_mensal: str
):
    """Coleta GA4 da loja se houver propriedade + service account. Import tardio (libs pesadas)."""
    from .config import GA4_CREDENTIALS_ENV

    if not os.environ.get(GA4_CREDENTIALS_ENV):
        return None
    if not loja.ga4_property:
        return None
    try:
        from .ga4 import coletar_ga4  # import tardio: so quando GA4 esta configurado
        return coletar_ga4(loja.ga4_property, ean_para_ref, hoje, inicio_mensal)
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
        # Mediana de conversao so entre quem converte (cv>0). Incluir os zeros puxaria
        # a mediana para 0 num catalogo onde a maioria dos vistos nao vende em 30d,
        # e o gatilho "abaixo de 40% da mediana" nunca dispararia.
        cvs = [p["funil"]["cv_geral"] for p in grupo
               if p["funil"]["cv_geral"] is not None and p["funil"]["cv_geral"] > 0]
        if len(views) < 4 or len(cvs) < 4:
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
    lojas: list[StoreConfig], periodo_dias: int = 30
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Baixa as lojas configuradas e monta (produtos_json, diario_json).

    Janela de pedidos: 24 meses (mensal_24m + diario.json diario). Serie diaria
    por produto: 90d. Score e funil baseline: 30d.
    """
    hoje = _hoje_brt()
    corte_30d = hoje - timedelta(days=30)
    corte_periodo = hoje - timedelta(days=periodo_dias)
    corte_serie = hoje - timedelta(days=SERIE_DIAS)
    inicio_mensal = _inicio_mensal(hoje)
    pedidos_min = _iso(inicio_mensal)

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
        # TODOS os canais: o site continua sendo a invariante do score, mas as
        # series carregam ANYMARKET/manual separados para o filtro de canal.
        vendas_linhas = load_sales(
            client, mapa, loja.numero, created_at_min=pedidos_min, somente_site=False
        )
        linhas_por_loja[loja.numero] = vendas_linhas
        vendas_por_ref = _agregar_vendas(
            vendas_linhas, corte_30d, corte_periodo, corte_serie
        )

        ga4 = _coletar_ga4_loja(loja, mapa.ean_para_ref, _iso(hoje), pedidos_min)
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
            gmes = ga4.por_mes.get(prod.ref) if ga4 and prod.ref else None
            produtos_out.append(
                _montar_produto(
                    prod, vendas, corte_30d, hoje, gfun, gorig, gdia, gmes,
                    corte_serie, inicio_mensal,
                )
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
            "serie_dias": SERIE_DIAS,
            "mensal_meses": MENSAL_MESES,
            "origens_presets": ["7d", "30d", "90d"],
        },
        "produtos": produtos_out,
    }
    diario_json = _montar_diario(linhas_por_loja, marca_por_ref_global)
    diario_json["trafego"] = sorted(trafego_linhas, key=lambda r: (r["d"], r["loja"]))
    return produtos_json, diario_json
