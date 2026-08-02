"""CLI principal do pipeline (F1.5): monta e grava data/produtos.json e data/diario.json.

Uso:
    python -m pipeline.run_build                # todas as lojas configuradas
    python -m pipeline.run_build --periodo 30   # janela do filtro em dias

Tambem grava data/excecoes_nuvemshop.json (EANs duplicados, produtos sem ref).
"""

from __future__ import annotations

import argparse
import json

from .build import construir
from .config import DATA_DIR, configured_stores
from .http import NuvemshopClient
from .nuvemshop import build_ean_ref_map, load_products


def _gravar(nome: str, conteudo: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    destino = DATA_DIR / nome
    destino.write_text(json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8")
    tamanho_kb = destino.stat().st_size / 1024
    print(f"  gravado {destino.name} ({tamanho_kb:,.0f} KB)")


def _excecoes(lojas) -> dict:
    """Recoleta o relatorio de excecoes por loja (leve; produtos ja vem da API na build)."""
    saida = []
    for loja in lojas:
        produtos = load_products(NuvemshopClient(loja), loja.numero)
        mapa = build_ean_ref_map(produtos)
        saida.append(
            {
                "loja": loja.numero,
                "eans_duplicados": mapa.eans_duplicados,
                "produtos_sem_ref": [
                    {"product_id": p.product_id, "nome": p.nome} for p in mapa.produtos_sem_ref
                ],
            }
        )
    return {"lojas": saida}


def main() -> None:
    parser = argparse.ArgumentParser(description="Builder do pipeline - F1.5")
    parser.add_argument("--periodo", type=int, default=30, help="Janela do filtro em dias")
    parser.add_argument(
        "--sem-excecoes", action="store_true", help="Pular o relatorio de excecoes"
    )
    args = parser.parse_args()

    lojas = configured_stores()
    if not lojas:
        print("Nenhuma loja configurada. Preencha NUVEMSHOP_STORE*_ID/TOKEN nos Secrets/.env.")
        return

    print(f"Lojas configuradas: {[l.numero for l in lojas]}")
    print("Montando produtos.json e diario.json...")
    produtos_json, diario_json = construir(lojas, periodo_dias=args.periodo)

    print(
        f"  produtos: {produtos_json['meta']['total_produtos']} | "
        f"linhas diario: {len(diario_json['linhas'])}"
    )
    _gravar("produtos.json", produtos_json)
    _gravar("diario.json", diario_json)

    if not args.sem_excecoes:
        _gravar("excecoes_nuvemshop.json", _excecoes(lojas))

    print("OK.")


if __name__ == "__main__":
    main()
