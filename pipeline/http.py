"""Cliente HTTP para a API Nuvemshop: auth, retry com backoff, rate limit e paginacao.

A Nuvemshop:
- autentica via header `Authentication: bearer <token>`;
- exige `User-Agent` identificando o app;
- pagina com `page`/`per_page` e devolve a proxima pagina no header `Link` (rel="next");
- limita taxa e devolve 429 com `Retry-After` quando estoura o bucket.
"""

from __future__ import annotations

import time
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import requests

from .config import NUVEMSHOP_API_BASE, USER_AGENT, StoreConfig

# Nuvemshop aceita ate 200 por pagina em /products e /orders.
DEFAULT_PER_PAGE = 200

# Tentativas em erros transitorios (429, 5xx, timeout).
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.5
REQUEST_TIMEOUT = 60


class NuvemshopError(RuntimeError):
    """Erro nao recuperavel vindo da API Nuvemshop."""


class NuvemshopClient:
    """Cliente por loja. Reusa uma sessao HTTP e trata paginacao por Link header."""

    def __init__(self, store: StoreConfig) -> None:
        if not store.configurada:
            raise NuvemshopError(
                f"Loja {store.numero} sem credenciais (ID/token). Configure o .env ou os Secrets."
            )
        self.store = store
        self.base = f"{NUVEMSHOP_API_BASE}/{store.store_id}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authentication": f"bearer {store.token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------ #
    # Requisicao unica com retry/backoff
    # ------------------------------------------------------------------ #
    def _request(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:  # timeout, conexao, DNS
                last_exc = exc
                self._sleep_backoff(attempt)
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", BACKOFF_BASE_SECONDS * attempt))
                time.sleep(retry_after)
                continue
            if 500 <= resp.status_code < 600:
                self._sleep_backoff(attempt)
                continue
            if resp.status_code == 404:
                # Recurso inexistente: devolve resposta para o chamador decidir.
                return resp
            if not resp.ok:
                raise NuvemshopError(
                    f"Loja {self.store.numero} {resp.status_code} em {url}: {resp.text[:300]}"
                )
            return resp

        raise NuvemshopError(
            f"Loja {self.store.numero}: falha apos {MAX_RETRIES} tentativas em {url} ({last_exc})"
        )

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

    # ------------------------------------------------------------------ #
    # Paginacao: itera todas as paginas de um endpoint de lista
    # ------------------------------------------------------------------ #
    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Itera cada item de um endpoint de lista (/products, /orders), seguindo o Link header."""
        params = dict(params or {})
        params.setdefault("per_page", DEFAULT_PER_PAGE)
        url: str | None = f"{self.base}/{path.lstrip('/')}"

        while url:
            resp = self._request(url, params=params)
            if resp.status_code == 404:
                return
            batch = resp.json()
            if not isinstance(batch, list):
                raise NuvemshopError(f"Esperava lista em {url}, veio {type(batch).__name__}")
            yield from batch

            url = _next_link(resp.headers.get("Link", ""))
            # Depois da primeira pagina os params ja estao embutidos no next url.
            params = None

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET de um recurso unico (nao paginado)."""
        resp = self._request(f"{self.base}/{path.lstrip('/')}", params=params)
        if resp.status_code == 404:
            return None
        return resp.json()


def _next_link(link_header: str) -> str | None:
    """Extrai a URL rel="next" do header Link, se houver."""
    if not link_header:
        return None
    for part in link_header.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url = segments[0].strip().strip("<>")
        rel = segments[1].strip()
        if rel == 'rel="next"':
            return url
    return None


def page_from_url(url: str) -> int | None:
    """Utilitario de debug: numero da pagina embutido numa URL de next."""
    qs = parse_qs(urlparse(url).query)
    value = qs.get("page", [None])[0]
    return int(value) if value else None
