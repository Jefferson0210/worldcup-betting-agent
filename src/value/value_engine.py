"""Motor de valor: convierte cuotas a probabilidad implícita, calcula el margen
de la casa y detecta selecciones con valor positivo (edge > umbral).

Definiciones
------------
  prob_implícita  = 1 / cuota_decimal
  overround       = (Σ prob_implícita por mercado) − 1   (margen de la casa)
  edge            = prob_modelo · cuota_decimal − 1
  es de valor     ⇔ edge > umbral_valor
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from config import CONFIG, Config
from src.data.models import MarketOdds
from src.model.poisson import MarketProbabilities


@dataclass(frozen=True)
class ValueBet:
    """Una selección de valor detectada."""

    fixture_id: int
    home_team: str
    away_team: str
    market: str
    selection: str
    odds: float
    model_prob: float
    edge: float

    @property
    def description(self) -> str:
        return f"{self.home_team} vs {self.away_team} | {self.market}:{self.selection} @ {self.odds:.2f}"


def decimal_to_implied_prob(odds: float) -> float:
    """Probabilidad implícita (sin descontar margen) de una cuota decimal."""
    if odds <= 1.0:
        raise ValueError(f"Cuota decimal inválida: {odds!r} (debe ser > 1.0)")
    return 1.0 / odds


def overround(odds_list: Iterable[float]) -> float:
    """Margen de la casa de un mercado: Σ(1/cuota) − 1.

    > 0 indica el margen incorporado (la casa "cobra" ese exceso).
    """
    total = sum(decimal_to_implied_prob(o) for o in odds_list)
    return total - 1.0


def edge(model_prob: float, odds: float) -> float:
    """Edge (valor esperado por unidad apostada) = p·d − 1."""
    if odds <= 1.0:
        raise ValueError(f"Cuota decimal inválida: {odds!r} (debe ser > 1.0)")
    if not 0.0 <= model_prob <= 1.0:
        raise ValueError(f"Probabilidad fuera de rango: {model_prob!r}")
    return model_prob * odds - 1.0


def find_value_bets(
    odds: MarketOdds,
    probs: MarketProbabilities,
    home_team: str,
    away_team: str,
    *,
    config: Config = CONFIG,
    markets: Optional[Iterable[str]] = None,
) -> list[ValueBet]:
    """Cruza cuotas y probabilidades del modelo y devuelve las de valor.

    Solo considera los mercados configurados. Devuelve la lista ordenada por
    edge descendente.
    """
    allowed = set(markets) if markets is not None else set(config.mercados)
    prob_map = probs.as_dict()
    value_bets: list[ValueBet] = []

    for (market, selection), odd in odds.selections.items():
        if market not in allowed:
            continue
        p = prob_map.get((market, selection))
        if p is None:
            continue
        e = edge(p, odd)
        if e > config.umbral_valor:
            value_bets.append(
                ValueBet(
                    fixture_id=odds.fixture_id,
                    home_team=home_team,
                    away_team=away_team,
                    market=market,
                    selection=selection,
                    odds=odd,
                    model_prob=p,
                    edge=e,
                )
            )

    value_bets.sort(key=lambda vb: vb.edge, reverse=True)
    return value_bets
