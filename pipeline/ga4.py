"""Conector GA4 Data API (F1.4): os relatorios da secao 4.2 do CLAUDE.md.

O item_id do GA4 e o EAN-13 da variante (auditado, secao 4.2b). Todos os dados de
item (views, add_cart, compras, origens) sao agregados na REF PAI via o mapa
EAN -> ref construido pela Nuvemshop. itemIds sem EAN conhecido vao para um bucket
de nao mapeados (relatorio de excecoes).

Janelas (secao 6.5, granularidade dupla):
- funil por item: 30d (baseline do score);
- origens por item: presets 7/30/90d num unico runReport com 3 date_ranges;
- item x dia (serie_90d): 90d, com add_cart e checkout para o funil recalculavel;
- item x mes (mensal_24m): 24 meses via dimensao yearMonth;
- site x dia x canal: 24 meses (alimenta diario.json).

Credencial: service account JSON, passado como string no env GA4_SERVICE_ACCOUNT_JSON
(GitHub Secret) ou como caminho de arquivo em dev. Propriedade por loja em
GA4_PROPERTY_STORE1 / GA4_PROPERTY_STORE2.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google.oauth2 import service_account

from .config import GA4_CREDENTIALS_ENV

GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
PAGE_SIZE = 100000  # GA4 aceita ate 250k linhas por pagina; 100k cobre ~21k EANs com folga
TOP_ORIGENS = 8     # top 8 origens por produto, resto agregado em "outros" (secao 4.2)


def _load_credentials() -> service_account.Credentials:
    """Carrega o service account do env: JSON inline (Secret) ou caminho de arquivo (dev)."""
    raw = os.environ.get(GA4_CREDENTIALS_ENV, "").strip()
    if not raw:
        raise RuntimeError(f"{GA4_CREDENTIALS_ENV} ausente (Secret/env).")
    if raw.startswith("{"):
        info = json.loads(raw)
    else:
        # Caminho de arquivo em dev.
        info = json.loads(Path(raw).read_text(encoding="utf-8"))
    return service_account.Credentials.from_service_account_info(info, scopes=GA4_SCOPES)


class Ga4Client:
    """Cliente GA4 para uma propriedade."""

    def __init__(self, property_id: str) -> None:
        if not property_id:
            raise RuntimeError("property_id GA4 vazio.")
        self.property = f"properties/{property_id}"
        self.client = BetaAnalyticsDataClient(credentials=_load_credentials())

    def _run(
        self,
        dimensions: list[str],
        metrics: list[str],
        date_ranges: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        """Roda um runReport paginando por offset. Retorna linhas como dicts nome->valor.

        Com mais de um date_range, a API acrescenta a dimensao implicita dateRange
        no FIM de dimension_values (valores date_range_0, date_range_1, ...); ela
        entra no dict como "dateRange".
        """
        linhas: list[dict[str, Any]] = []
        offset = 0
        multi = len(date_ranges) > 1
        while True:
            req = RunReportRequest(
                property=self.property,
                dimensions=[Dimension(name=d) for d in dimensions],
                metrics=[Metric(name=m) for m in metrics],
                date_ranges=[DateRange(start_date=i, end_date=f) for i, f in date_ranges],
                limit=PAGE_SIZE,
                offset=offset,
            )
            resp = self.client.run_report(req)
            for row in resp.rows:
                d = {dimensions[i]: row.dimension_values[i].value for i in range(len(dimensions))}
                if multi and len(row.dimension_values) > len(dimensions):
                    d["dateRange"] = row.dimension_values[len(dimensions)].value
                for i, m in enumerate(metrics):
                    d[m] = row.metric_values[i].value
                linhas.append(d)
            total = resp.row_count or 0
            offset += len(resp.rows)
            if len(resp.rows) == 0 or offset >= total:
                break
        return linhas


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _i(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------- #
# Estruturas agregadas por ref
# ---------------------------------------------------------------------- #
PRESETS_ORIGENS = (7, 30, 90)  # janelas pre-agregadas do breakdown de origens (secao 6.5)


@dataclass
class Ga4Loja:
    """Dados GA4 de uma loja, ja agregados na ref pai."""

    funil: dict[str, dict[str, float]] = field(default_factory=dict)      # ref -> metricas item (30d)
    por_dia: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)  # ref -> data -> {views,cart,checkout,compras}
    por_mes: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)  # ref -> YYYY-MM -> {views,compras}
    origens: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)  # ref -> preset ("7d"/"30d"/"90d") -> [origens]
    site_dia: list[dict[str, Any]] = field(default_factory=list)          # linhas site x dia x canal (24m)
    eans_nao_mapeados: set[str] = field(default_factory=set)


def coletar_ga4(
    property_id: str,
    ean_para_ref: dict[str, str],
    hoje: str,
    inicio_mensal: str,
) -> Ga4Loja:
    """Coleta os relatorios e agrega tudo na ref pai via ean_para_ref.

    hoje: data final (YYYY-MM-DD). inicio_mensal: inicio da janela de 24 meses.
    """
    from datetime import date, timedelta

    cli = Ga4Client(property_id)
    out = Ga4Loja()
    fim_d = date.fromisoformat(hoje)

    def ini(dias: int) -> str:
        return (fim_d - timedelta(days=dias)).isoformat()

    fim = fim_d.isoformat()

    def ref_de(item_id: str) -> str | None:
        return ean_para_ref.get((item_id or "").strip())

    # 1) Item x metricas (funil por produto, 30d: baseline do score).
    r1 = cli._run(
        ["itemId"],
        ["itemsViewed", "itemsAddedToCart", "itemsCheckedOut", "itemsPurchased", "itemRevenue"],
        [(ini(30), fim)],
    )
    for row in r1:
        ref = ref_de(row["itemId"])
        if ref is None:
            if row["itemId"]:
                out.eans_nao_mapeados.add(row["itemId"])
            continue
        agg = out.funil.setdefault(
            ref, {"views": 0, "add_cart": 0, "checkout": 0, "compras": 0, "receita": 0.0}
        )
        agg["views"] += _i(row["itemsViewed"])
        agg["add_cart"] += _i(row["itemsAddedToCart"])
        agg["checkout"] += _i(row["itemsCheckedOut"])
        agg["compras"] += _i(row["itemsPurchased"])
        agg["receita"] += _f(row["itemRevenue"])

    # 2) Item x origem nos 3 presets (um unico runReport com 3 date_ranges).
    ranges = [(ini(d), fim) for d in PRESETS_ORIGENS]
    rotulos = {f"date_range_{i}": f"{d}d" for i, d in enumerate(PRESETS_ORIGENS)}
    r2 = cli._run(
        ["itemId", "sessionSource", "sessionMedium"],
        ["itemsViewed", "itemsAddedToCart", "itemsPurchased", "itemRevenue"],
        ranges,
    )
    # ref -> preset -> (source, medium) -> acumulado
    origens_tmp: dict[str, dict[str, dict[tuple[str, str], dict[str, Any]]]] = (
        defaultdict(lambda: defaultdict(dict))
    )
    for row in r2:
        ref = ref_de(row["itemId"])
        if ref is None:
            continue
        preset = rotulos.get(row.get("dateRange", "date_range_0"), f"{PRESETS_ORIGENS[0]}d")
        chave = (row.get("sessionSource", "(none)"), row.get("sessionMedium", "(none)"))
        o = origens_tmp[ref][preset].setdefault(
            chave,
            {"source": chave[0], "medium": chave[1], "views": 0, "add_cart": 0,
             "compras": 0, "receita": 0.0},
        )
        o["views"] += _i(row["itemsViewed"])
        o["add_cart"] += _i(row["itemsAddedToCart"])
        o["compras"] += _i(row["itemsPurchased"])
        o["receita"] += _f(row["itemRevenue"])
    for ref, por_preset in origens_tmp.items():
        out.origens[ref] = {}
        for preset, mapa in por_preset.items():
            ordenadas = sorted(mapa.values(), key=lambda x: x["receita"], reverse=True)
            top = ordenadas[:TOP_ORIGENS]
            resto = ordenadas[TOP_ORIGENS:]
            if resto:
                outros = {"source": "outros", "medium": "", "views": 0, "add_cart": 0,
                          "compras": 0, "receita": 0.0}
                for o in resto:
                    for k in ("views", "add_cart", "compras", "receita"):
                        outros[k] += o[k]
                top.append(outros)
            for o in top:
                o["receita"] = round(o["receita"], 2)
            out.origens[ref][preset] = top

    # 3) Item x dia, 90d (serie_90d: funil diario recalculavel no cliente).
    r3 = cli._run(
        ["itemId", "date"],
        ["itemsViewed", "itemsAddedToCart", "itemsCheckedOut", "itemsPurchased"],
        [(ini(90), fim)],
    )
    for row in r3:
        ref = ref_de(row["itemId"])
        if ref is None:
            continue
        data = _fmt_date(row["date"])
        dia = out.por_dia.setdefault(ref, {}).setdefault(
            data, {"views": 0, "cart": 0, "checkout": 0, "compras": 0}
        )
        dia["views"] += _i(row["itemsViewed"])
        dia["cart"] += _i(row["itemsAddedToCart"])
        dia["checkout"] += _i(row["itemsCheckedOut"])
        dia["compras"] += _i(row["itemsPurchased"])

    # 3b) Item x mes, 24 meses (mensal_24m).
    r5 = cli._run(
        ["itemId", "yearMonth"], ["itemsViewed", "itemsPurchased"], [(inicio_mensal, fim)]
    )
    for row in r5:
        ref = ref_de(row["itemId"])
        if ref is None:
            continue
        mes = _fmt_month(row["yearMonth"])
        agg = out.por_mes.setdefault(ref, {}).setdefault(mes, {"views": 0, "compras": 0})
        agg["views"] += _i(row["itemsViewed"])
        agg["compras"] += _i(row["itemsPurchased"])

    # 4) Site x dia x canal de aquisicao (24 meses: alimenta o diario).
    r4 = cli._run(
        ["date", "sessionDefaultChannelGroup"],
        ["sessions", "totalUsers", "transactions", "purchaseRevenue"],
        [(inicio_mensal, fim)],
    )
    for row in r4:
        out.site_dia.append(
            {
                "d": _fmt_date(row["date"]),
                "canal_ga4": row.get("sessionDefaultChannelGroup", "(other)"),
                "sessoes": _i(row["sessions"]),
                "usuarios": _i(row["totalUsers"]),
                "transacoes": _i(row["transactions"]),
                "receita": round(_f(row["purchaseRevenue"]), 2),
            }
        )

    # Finaliza funil: taxas de conversao.
    for ref, agg in out.funil.items():
        agg["receita"] = round(agg["receita"], 2)
        agg["cv_view_cart"] = _pct(agg["add_cart"], agg["views"])
        agg["cv_cart_compra"] = _pct(agg["compras"], agg["add_cart"])
        agg["cv_geral"] = _pct(agg["compras"], agg["views"])

    return out


def _pct(num: int, den: int) -> float | None:
    if not den:
        return None
    return round(100.0 * num / den, 2)


def _fmt_date(ga4_date: str) -> str:
    """GA4 devolve data como YYYYMMDD; converte para YYYY-MM-DD."""
    s = (ga4_date or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _fmt_month(ga4_month: str) -> str:
    """GA4 devolve yearMonth como YYYYMM; converte para YYYY-MM."""
    s = (ga4_month or "").strip()
    if len(s) == 6 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}"
    return s
