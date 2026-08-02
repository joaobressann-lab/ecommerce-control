"""Diagnostico estrutural de pedidos para calibrar a classificacao de canal (F1, secao 9).

NAO imprime PII: apenas as chaves do payload e os valores de baixa cardinalidade de
campos candidatos a "canal de venda". Serve para descobrir como a API distingue
Loja virtual x Mobile x ANYMARKET x manual.

    python -m pipeline.diag_orders
"""

from __future__ import annotations

from collections import Counter

from .config import configured_stores
from .http import NuvemshopClient
from .nuvemshop import iter_orders

# Campos que podem carregar o canal/origem do pedido.
CANDIDATOS = [
    "gateway", "gateway_name", "payment_status", "status", "app_id",
    "channel", "sales_channel", "storefront", "origin", "source", "device",
    "from", "client_details",
]
LIMITE = 400  # pedidos a amostrar


def main() -> None:
    lojas = configured_stores()
    if not lojas:
        print("Nenhuma loja configurada.")
        return

    for loja in lojas:
        print(f"\n=== Loja {loja.numero} ===")
        client = NuvemshopClient(loja)
        chaves_uniao: set[str] = set()
        contadores: dict[str, Counter] = {c: Counter() for c in CANDIDATOS}
        n = 0
        primeiro_dump = None

        for raw in iter_orders(client):
            n += 1
            chaves_uniao |= set(raw.keys())
            if primeiro_dump is None:
                primeiro_dump = sorted(raw.keys())
            for c in CANDIDATOS:
                if c in raw:
                    v = raw[c]
                    # Registra so tipos/valores de baixa cardinalidade, nunca PII.
                    if isinstance(v, (dict, list)):
                        contadores[c][f"<{type(v).__name__}:{len(v)} chaves/itens>"] += 1
                    else:
                        contadores[c][str(v)[:40]] += 1
            if n >= LIMITE:
                break

        print(f"pedidos amostrados: {n}")
        print(f"chaves do payload (uniao): {sorted(chaves_uniao)}")
        print("\ndistribuicao dos campos candidatos:")
        for c in CANDIDATOS:
            if contadores[c]:
                top = contadores[c].most_common(12)
                print(f"  {c}: {top}")
            else:
                print(f"  {c}: <ausente>")


if __name__ == "__main__":
    main()
