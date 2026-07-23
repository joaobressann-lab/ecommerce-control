"""Configuracao central do pipeline: leitura de credenciais e definicao das lojas.

Le tudo de variaveis de ambiente (GitHub Secrets em producao, .env em dev).
Nada de credencial hardcoded, conforme CLAUDE.md secao 1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Carrega .env se existir, sem dependencia externa (evita python-dotenv no runtime).
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Nao sobrescreve o que ja veio do ambiente real (Secrets tem prioridade).
        os.environ.setdefault(key, value)


_load_dotenv()

# Base da API Nuvemshop. O ID da loja entra no path.
NUVEMSHOP_API_BASE = "https://api.nuvemshop.com.br/v1"

# User-Agent obrigatorio pela Nuvemshop (nome do app + contato de suporte).
USER_AGENT = os.environ.get(
    "NUVEMSHOP_USER_AGENT", "EcommerceControl (joaobressann@gmail.com)"
)


@dataclass(frozen=True)
class StoreConfig:
    """Uma loja Nuvemshop com suas credenciais e marcas atendidas."""

    numero: int  # 1 ou 2, usado no campo "loja" dos JSONs
    nome: str
    store_id: str
    token: str
    marcas: tuple[str, ...] = field(default_factory=tuple)

    @property
    def configurada(self) -> bool:
        return bool(self.store_id and self.token)


def _store(numero: int, nome: str, marcas: tuple[str, ...]) -> StoreConfig:
    return StoreConfig(
        numero=numero,
        nome=nome,
        store_id=os.environ.get(f"NUVEMSHOP_STORE{numero}_ID", "").strip(),
        token=os.environ.get(f"NUVEMSHOP_STORE{numero}_TOKEN", "").strip(),
        marcas=marcas,
    )


def stores() -> list[StoreConfig]:
    """Retorna as lojas definidas. Loja 2 tem composicao a confirmar (CLAUDE.md secao 9)."""
    return [
        _store(1, "Carlota Costa + Simone Gomes", ("Carlota Costa", "Simone Gomes")),
        _store(2, "Ritmi Studio (+ mix)", ("Ritmi Studio", "RIT", "Sobras/PL")),
    ]


def configured_stores() -> list[StoreConfig]:
    """Somente lojas com credenciais preenchidas, para rodar parcial na F1."""
    return [s for s in stores() if s.configurada]


# Raiz do repo e diretorio de saida dos JSONs.
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
