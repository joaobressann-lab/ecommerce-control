"""Diagnostico da estrutura de categorias dos produtos (para corrigir a atribuicao).

Imprime, sem PII, como vem o campo `categories` de alguns produtos e a distribuicao
de nomes de categoria por posicao na lista.

    python -m pipeline.diag_products
"""

from __future__ import annotations

from collections import Counter

from .config import configured_stores
from .http import NuvemshopClient
from .nuvemshop import iter_products, localized

LIMITE = 600


def main() -> None:
    lojas = configured_stores()
    if not lojas:
        print("Nenhuma loja configurada.")
        return

    for loja in lojas:
        print(f"\n=== Loja {loja.numero} ===")
        client = NuvemshopClient(loja)
        n = 0
        tamanhos = Counter()
        nome_por_posicao: dict[int, Counter] = {}
        amostras = []

        for raw in iter_products(client):
            n += 1
            cats = raw.get("categories") or []
            tamanhos[len(cats)] += 1
            for i, c in enumerate(cats):
                nome = localized(c.get("name"))
                nome_por_posicao.setdefault(i, Counter())[nome] += 1
            if len(amostras) < 8 and cats:
                amostras.append(
                    {
                        "nome_produto": localized(raw.get("name"))[:40],
                        "categorias": [
                            {
                                "id": c.get("id"),
                                "nome": localized(c.get("name")),
                                "parent": c.get("parent"),
                                "subcats": len(c.get("subcategories") or []),
                            }
                            for c in cats
                        ],
                    }
                )
            if n >= LIMITE:
                break

        print(f"produtos amostrados: {n}")
        print(f"qtd de categorias por produto (tamanho da lista): {dict(tamanhos)}")
        for pos in sorted(nome_por_posicao):
            print(f"  posicao {pos}: {nome_por_posicao[pos].most_common(8)}")
        print("\namostras de estrutura:")
        for a in amostras:
            print(f"  {a['nome_produto']!r}: {a['categorias']}")


if __name__ == "__main__":
    main()
