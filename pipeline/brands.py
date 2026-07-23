"""Extracao da referencia (ref) e atribuicao de marca por taxonomia de prefixo.

Regras congeladas em CLAUDE.md secao 2 e 4.2b. A ref e a chave universal de
cruzamento entre Nuvemshop, GA4, Meta e Google.
"""

from __future__ import annotations

import re
import unicodedata

# Ref no fim do nome do produto, apos hifen ou travessao.
# Ex.: "Vestido Midi Alfaiataria - CSV1234", "Blusa ... - REST12345.2".
# Cobre sufixos .2, .C, .1 (validada em 1.627/1.628 produtos, CLAUDE.md 4.2b).
REF_PATTERN = re.compile(r"[-–]\s*([A-Z]{1,6}\d{4,}(?:\.[A-Z0-9]+)?)\s*$")

# Prefixos de marca em ordem de prioridade (o primeiro que casar vence).
# Ordem importa: prefixos mais especificos antes dos genericos.
_BRAND_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("REST", "RESB"), "Ritmi Studio"),
    (("RIR",), "Ritmi Studio"),
    (("RIT",), "RIT"),
    (("RLC", "RCC", "RL"), "Sobras/PL"),
    (("CS",), "Simone Gomes"),
    (("C",), "Carlota Costa"),
)

MARCA_DESCONHECIDA = "Desconhecida"


def extrair_ref(nome_produto: str | None) -> str | None:
    """Extrai a ref do nome do produto. Retorna None se nao casar (vira excecao no relatorio)."""
    if not nome_produto:
        return None
    match = REF_PATTERN.search(nome_produto.strip())
    return match.group(1) if match else None


def marca_por_ref(ref: str | None) -> str:
    """Atribui a marca pela taxonomia de prefixo da ref."""
    if not ref:
        return MARCA_DESCONHECIDA
    ref_upper = ref.upper()
    for prefixos, marca in _BRAND_RULES:
        if ref_upper.startswith(prefixos):
            return marca
    return MARCA_DESCONHECIDA


def normalizar_cor(cor: str | None) -> str:
    """Normaliza nome de cor para match de variante: sem acento, sem espaco, sem 'TC', upper.

    Regra de match de variante do CLAUDE.md secao 3.
    """
    if not cor:
        return ""
    # Remove acentos.
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", cor) if not unicodedata.combining(c)
    )
    texto = sem_acento.upper()
    texto = texto.replace("TC", "")
    texto = re.sub(r"\s+", "", texto)
    return texto.strip()
