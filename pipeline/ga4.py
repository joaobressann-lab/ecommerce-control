"""Conector GA4 Data API (F1.4): os 4 relatorios da secao 4.2 do CLAUDE.md.

O item_id do GA4 e o EAN-13 da variante (auditado, secao 4.2b). Todos os dados de
item (views, add_cart, compras, origens) sao agregados na REF PAI via o mapa
EAN -> ref construido pela Nuvemshop. itemIds sem EAN conhecido vao para um bucket
de nao mapeados (relatorio de excecoes).

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
        self, dimensions: list[str], metrics: list[str], inicio: str, fim: str
    ) -> list[dict[str, Any]]:
        """Roda um runReport paginando por offset. Retorna linhas como dicts nome->valor."""
        linhas: list[dict[str, Any]] = []
        offset = 0
        while True:
            req = RunReportRequest(
                property=self.property,
                dimensions=[Dimension(name=d) for d in dimensions],
                metrics=[Metric(name=m) for m in metrics],
                date_ranges=[DateRange(start_date=inicio, end_date=fim)],
                limit=PAGE_SIZE,
                offset=offset,
            )
            resp = self.client.run_report(req)
            for row in resp.rows:
                d = {dimensions[i]: row.dimension_values[i].value for i in range(len(dimensions))}
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
@dataclass
class Ga4Loja:
    """Dados GA4 de uma loja, ja agregados na ref pai."""

    funil: dict[str, dict[str, float]] = field(default_factory=dict)      # ref -> metricas item
    por_dia: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)  # ref -> data -> {views,compras}
    origens: dict[str, list[dict[str, Any]]] = field(default_factory=dict)       # ref -> [origens]
    site_dia: list[dict[str, Any]] = field(default_factory=list)          # linhas site x dia x canal
    eans_nao_mapeados: set[str] = field(default_factory=set)


def coletar_ga4(
    property_id: str, ean_para_ref: dict[str, str], inicio: str, fim: str
) -> Ga4Loja:
    """Coleta os 4 relatorios e agrega tudo na ref pai via ean_para_ref."""
    cli = Ga4Client(property_id)
    out = Ga4Loja()

    def ref_de(item_id: str) -> str | None:
        return ean_para_ref.get((item_id or "").strip())

    # 1) Item x metricas (funil por produto).
    r1 = cli._run(
        ["itemId"],
        ["itemsViewed", "itemsAddedToCart", "itemsCheckedOut", "itemsPurchased", "itemRevenue"],
        inicio, fim,
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

    # 2) Item x origem (top 8 + outros por produto).
    r2 = cli._run(
        ["itemId", "sessionSource", "sessionMedium"],
        ["itemsViewed", "itemsAddedToCart", "itemsPurchased", "itemRevenue"],
        inicio, fim,
    )
    origens_tmp: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for row in r2:
        ref = ref_de(row["itemId"])
        if ref is None:
            continue
        chave = (row.get("sessionSource", "(none)"), row.get("sessionMedium", "(none)"))
        o = origens_tmp[ref].setdefault(
            chave,
            {"source": chave[0], "medium": chave[1], "views": 0, "add_cart": 0,
             "compras": 0, "receita": 0.0},
        )
        o["views"] += _i(row["itemsViewed"])
        o["add_cart"] += _i(row["itemsAddedToCart"])
        o["compras"] += _i(row["itemsPurchased"])
        o["receita"] += _f(row["itemRevenue"])
    for ref, mapa in origens_tmp.items():
        ordenadas = sorted(mapa.values(), key=lambda x: x["receita"], reverse=True)
        top = ordenadas[:TOP_ORIGENS]
        resto = ordenadas[TOP_ORIGENS:]
        if resto:
            outros = {"source": "outros", "medium": "", "views": 0, "add_cart": 0,
                      "compras": 0, "receita": 0.0}
            for o in resto:
                for k in ("views", "add_cart", "compras", "receita"):
                    outros[k] += o[k]
            outros["receita"] = round(outros["receita"], 2)
            top.append(outros)
        for o in top:
            o["receita"] = round(o["receita"], 2)
        out.origens[ref] = top

    # 3) Item x dia (sparkline: views + compras).
    r3 = cli._run(["itemId", "date"], ["itemsViewed", "itemsPurchased"], inicio, fim)
    for row in r3:
        ref = ref_de(row["itemId"])
        if ref is None:
            continue
        data = _fmt_date(row["date"])
        dia = out.por_dia.setdefault(ref, {}).setdefault(data, {"views": 0, "compras": 0})
        dia["views"] += _i(row["itemsViewed"])
        dia["compras"] += _i(row["itemsPurchased"])

    # 4) Site x dia x canal de aquisicao.
    r4 = cli._run(
        ["date", "sessionDefaultChannelGroup"],
        ["sessions", "totalUsers", "transactions", "purchaseRevenue"],
        inicio, fim,
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
