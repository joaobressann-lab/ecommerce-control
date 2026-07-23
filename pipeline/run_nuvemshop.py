"""CLI do conector Nuvemshop: valida credenciais, baixa o censo e emite diagnostico.

Uso:
    python -m pipeline.run_nuvemshop            # todas as lojas configuradas
    python -m pipeline.run_nuvemshop --loja 1   # so a loja 1
    python -m pipeline.run_nuvemshop --dias 30  # janela de pedidos (default 30)

Escreve um relatorio de excecoes em data/excecoes_nuvemshop.json (EANs duplicados,
produtos sem ref no nome) para correcao no cadastro Genesis.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR, configured_stores, stores
from .http import NuvemshopClient, NuvemshopError
from .nuvemshop import build_ean_ref_map, load_products, load_sales


def _janela_iso(dias: int) -> tuple[str, str]:
    # Sem depender de fuso local do runner; BRT = UTC-3.
    brt = timezone(timedelta(hours=-3))
    fim = datetime.now(brt)
    inicio = fim - timedelta(days=dias)
    return inicio.isoformat(), fim.isoformat()


def rodar_loja(numero: int, dias: int) -> dict:
    loja = next((s for s in stores() if s.numero == numero), None)
    if loja is None or not loja.configurada:
        raise NuvemshopError(f"Loja {numero} nao configurada. Preencha ID e TOKEN no .env/Secrets.")

    print(f"\n=== Loja {loja.numero} ({loja.nome}) ===")
    client = NuvemshopClient(loja)

    print("Baixando produtos...")
    produtos = load_products(client, loja.numero)
    total_variantes = sum(len(p.variantes) for p in produtos)

    mapa = build_ean_ref_map(produtos)

    inicio, fim = _janela_iso(dias)
    print(f"Baixando pedidos ({dias}d, so site)...")
    vendas = load_sales(client, mapa, loja.numero, created_at_min=inicio, created_at_max=fim)
    unidades = sum(v.quantidade for v in vendas)
    receita = round(sum(v.receita for v in vendas), 2)

    resumo = {
        "loja": loja.numero,
        "nome": loja.nome,
        "produtos": len(produtos),
        "variantes": total_variantes,
        "produtos_com_ref": sum(1 for p in produtos if p.ref),
        "produtos_sem_ref": len(mapa.produtos_sem_ref),
        "eans_mapeados": len(mapa.ean_para_ref),
        "eans_duplicados": len(mapa.eans_duplicados),
        "linhas_venda_site": len(vendas),
        "unidades_vendidas_site": unidades,
        "receita_site": receita,
    }

    for chave, valor in resumo.items():
        print(f"  {chave:24}: {valor}")

    excecoes = {
        "loja": loja.numero,
        "eans_duplicados": mapa.eans_duplicados,
        "produtos_sem_ref": [
            {"product_id": p.product_id, "nome": p.nome} for p in mapa.produtos_sem_ref
        ],
    }
    return {"resumo": resumo, "excecoes": excecoes}


def main() -> None:
    parser = argparse.ArgumentParser(description="Conector Nuvemshop - diagnostico F1")
    parser.add_argument("--loja", type=int, choices=[1, 2], help="Rodar so uma loja")
    parser.add_argument("--dias", type=int, default=30, help="Janela de pedidos em dias")
    args = parser.parse_args()

    alvo = [s.numero for s in configured_stores()]
    if args.loja:
        alvo = [args.loja]
    if not alvo:
        print("Nenhuma loja configurada. Preencha NUVEMSHOP_STORE*_ID/TOKEN no .env.")
        return

    DATA_DIR.mkdir(exist_ok=True)
    todas_excecoes = []
    for numero in alvo:
        resultado = rodar_loja(numero, args.dias)
        todas_excecoes.append(resultado["excecoes"])

    destino = DATA_DIR / "excecoes_nuvemshop.json"
    destino.write_text(
        json.dumps({"lojas": todas_excecoes}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nRelatorio de excecoes salvo em {destino}")


if __name__ == "__main__":
    main()
