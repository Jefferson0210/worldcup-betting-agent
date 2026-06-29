"""Constructor de combinadas (parlays).

Reglas:
  * Solo se combinan selecciones que individualmente superan el umbral de valor
    (las que devuelve `find_value_bets`).
  * Hasta `max_legs` legs, preferentemente de partidos DISTINTOS para evitar
    legs correlacionados del mismo partido.
  * Cuota combinada = producto de cuotas.
  * Prob combinada  = producto de probabilidades (SUPOSICIÓN DE INDEPENDENCIA).
  * Edge combinado  = prob_combinada · cuota_combinada − 1.
  * Solo se devuelven combinadas con edge positivo, ordenadas por edge.

⚠️ Advertencia documentada: asumir independencia entre legs es una
simplificación. En la práctica los resultados pueden estar correlacionados
(clima, árbitro, dinámica del torneo). Si los legs están positivamente
correlacionados, la probabilidad real de acierto conjunto es MENOR que el
producto, por lo que el edge mostrado puede estar sobreestimado. Además, cada
leg arrastra el margen de la casa, de modo que las combinadas acumulan margen.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from config import CONFIG, Config
from src.value.value_engine import ValueBet


@dataclass(frozen=True)
class Parlay:
    """Una combinada candidata."""

    legs: tuple[ValueBet, ...]
    combined_odds: float
    combined_prob: float
    edge: float
    independence_assumed: bool = field(default=True)

    @property
    def n_legs(self) -> int:
        return len(self.legs)

    @property
    def description(self) -> str:
        parts = [f"{vb.market}:{vb.selection}@{vb.odds:.2f}" for vb in self.legs]
        return f"PARLAY[{self.n_legs}] " + " + ".join(parts)


def _combined(legs: tuple[ValueBet, ...]) -> tuple[float, float, float]:
    odds = math.prod(vb.odds for vb in legs)
    prob = math.prod(vb.model_prob for vb in legs)
    return odds, prob, prob * odds - 1.0


def build_parlays(
    value_bets: list[ValueBet],
    *,
    config: Config = CONFIG,
    distinct_fixtures: bool = True,
) -> list[Parlay]:
    """Construye combinadas de valor a partir de selecciones de valor.

    Parameters
    ----------
    value_bets : selecciones que ya superan el umbral individual.
    distinct_fixtures : si True, ninguna combinada repite fixture (evita
        legs correlacionados del mismo partido).

    Returns
    -------
    Lista de combinadas con edge > 0, ordenadas por edge descendente.
    """
    max_legs = max(2, config.max_legs)
    parlays: list[Parlay] = []

    # Tamaños de 2..max_legs (una combinada necesita al menos 2 legs).
    for size in range(2, max_legs + 1):
        for combo in itertools.combinations(value_bets, size):
            if distinct_fixtures:
                fixtures = {vb.fixture_id for vb in combo}
                if len(fixtures) != size:
                    continue  # hay dos legs del mismo partido -> descartar
            odds, prob, e = _combined(combo)
            if e > 0:
                parlays.append(
                    Parlay(
                        legs=combo,
                        combined_odds=odds,
                        combined_prob=prob,
                        edge=e,
                    )
                )

    parlays.sort(key=lambda p: p.edge, reverse=True)
    return parlays
