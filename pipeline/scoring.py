"""Score e classificacao de produtos (CLAUDE.md secao 6).

Score completo (com GA4):
  Vendas 30% (60% do peso nos ultimos 30d) . Conversao 20% . Views PDP 10%
  Estoque 15% . Grade P.M.G 25% . Penalidade 50% se nao publicado.

Enquanto o GA4 nao existe (F1.4), Conversao e Views ficam indisponiveis. Nesse caso
renormalizamos o score sobre os componentes disponiveis (Vendas, Estoque, Grade) e
marcamos score_parcial=True. Quando o GA4 entrar, basta passar os sinais de funil.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ordem canonica de tamanhos para grade e matriz.
SIZE_ORDER = ("PP", "P", "M", "G", "GG", "XG", "U")
GRADE_CORE = ("P", "M", "G")  # grade cheia exige P, M, G numa mesma cor

# Pesos do score completo (somam 1.0).
PESO_VENDAS = 0.30
PESO_CONVERSAO = 0.20
PESO_VIEWS = 0.10
PESO_ESTOQUE = 0.15
PESO_GRADE = 0.25

# Escalas de saturacao (dao credito total ao atingir o alvo).
ALVO_VENDAS_30D = 8.0     # 8 un/30d = credito cheio de vendas
ALVO_VIEWS_30D = 400.0    # 400 views PDP/30d = credito cheio de views
ESTOQUE_CRITICO = 2       # <= 2 pecas: estoque critico (secao 3)
PARADO_MIN_ESTOQUE = 6    # >= 6 pecas sem venda: PARADO (secao 6)

# Limiares de classe por vendas em 30d.
ESTRELA_MIN_VENDAS_30D = 2  # >= 2 un/30d + grade cheia = ESTRELA


def _sat(valor: float, alvo: float) -> float:
    """Satura em [0, 1]."""
    if alvo <= 0:
        return 0.0
    return max(0.0, min(valor / alvo, 1.0))


@dataclass
class SinaisProduto:
    """Entrada para o score. Campos de GA4 podem ser None ate a F1.4."""

    vendas_30d: int
    vendas_periodo: int
    estoque_total: int
    grade_cheia: bool
    publicado: bool
    # GA4 (None => indisponivel, componente sai do calculo):
    views_30d: int | None = None
    cv_geral: float | None = None       # conversao geral do produto (%), 0..100
    # Contexto de categoria para AUDITAR PDP:
    views_mediana_categoria: float | None = None
    cv_mediana_categoria: float | None = None


@dataclass
class ResultadoScore:
    score: float
    classe: str
    score_parcial: bool  # True quando calculado sem os componentes de GA4


def _componente_grade(sinais: SinaisProduto) -> float:
    """Credito de grade: cheia = 1.0, caso contrario proporcional a presenca de P/M/G."""
    return 1.0 if sinais.grade_cheia else 0.0


def _componente_estoque(sinais: SinaisProduto) -> float:
    """Estoque saudavel pontua; critico penaliza; zerado com demanda zera o componente."""
    if sinais.estoque_total <= 0:
        return 0.0
    if sinais.estoque_total <= ESTOQUE_CRITICO:
        return 0.35
    # Satura: estoque confortavel (>= 12) da credito cheio.
    return _sat(float(sinais.estoque_total), 12.0)


def calcular_score(sinais: SinaisProduto) -> ResultadoScore:
    """Score 0..100 renormalizado sobre os componentes disponiveis."""
    componentes: list[tuple[float, float]] = []  # (peso, valor 0..1)

    # Vendas: 60% do peso nos ultimos 30d, 40% no periodo do filtro.
    v30 = _sat(float(sinais.vendas_30d), ALVO_VENDAS_30D)
    vper = _sat(float(sinais.vendas_periodo), ALVO_VENDAS_30D)
    valor_vendas = 0.60 * v30 + 0.40 * vper
    componentes.append((PESO_VENDAS, valor_vendas))

    # Estoque e grade sempre disponiveis.
    componentes.append((PESO_ESTOQUE, _componente_estoque(sinais)))
    componentes.append((PESO_GRADE, _componente_grade(sinais)))

    parcial = True
    if sinais.views_30d is not None and sinais.cv_geral is not None:
        parcial = False
        componentes.append((PESO_VIEWS, _sat(float(sinais.views_30d), ALVO_VIEWS_30D)))
        # Conversao: satura em 5% (cv_geral tipico de e-commerce de moda).
        componentes.append((PESO_CONVERSAO, _sat(sinais.cv_geral, 5.0)))

    peso_total = sum(p for p, _ in componentes)
    bruto = sum(p * v for p, v in componentes) / peso_total if peso_total else 0.0

    # Penalidade de 50% se nao publicado.
    if not sinais.publicado:
        bruto *= 0.50

    score = round(bruto * 100, 1)
    return ResultadoScore(score=score, classe=classificar(sinais), score_parcial=parcial)


def classificar(sinais: SinaisProduto) -> str:
    """Classe do produto pelos gatilhos objetivos da secao 6."""
    if not sinais.publicado:
        return "NAO PUBLICADO"

    tem_demanda = sinais.vendas_30d > 0

    if sinais.estoque_total <= 0:
        return "ESGOTADO c/ demanda" if tem_demanda else "ESGOTADO"

    # ESTRELA: >= 2 un/30d + grade cheia.
    if sinais.vendas_30d >= ESTRELA_MIN_VENDAS_30D and sinais.grade_cheia:
        return "ESTRELA"

    if sinais.estoque_total <= ESTOQUE_CRITICO:
        return "ESTOQUE CRITICO"

    # AUDITAR PDP: so com GA4. Views acima da mediana da categoria e cv abaixo de 40% da mediana.
    if (
        sinais.views_30d is not None
        and sinais.cv_geral is not None
        and sinais.views_mediana_categoria
        and sinais.cv_mediana_categoria
    ):
        if (
            sinais.views_30d > sinais.views_mediana_categoria
            and sinais.cv_geral < 0.40 * sinais.cv_mediana_categoria
        ):
            return "AUDITAR PDP"

    if not tem_demanda:
        # PARADO: >= 6 pecas e nenhuma venda.
        if sinais.estoque_total >= PARADO_MIN_ESTOQUE:
            return "PARADO"
        return "BAIXO GIRO"

    if sinais.vendas_30d >= ESTRELA_MIN_VENDAS_30D:
        return "VENDEDOR"
    return "BOM"
