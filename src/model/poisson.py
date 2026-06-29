"""Modelo de probabilidad Poisson para 1X2 y Over/Under 2.5.

Idea:
  1. Estimar fuerza ofensiva/defensiva de cada equipo a partir de goles
     marcados/recibidos relativos a la media de la competición.
  2. Regularizar esas fuerzas con un prior de ratings (Elo/FIFA), con un peso
     que crece cuando hay pocos datos (shrinkage por tamaño muestral).
  3. Goles esperados:
        λ_local  = media · ataque_local · defensa_visit · ventaja_local
        λ_visit  = media · ataque_visit · defensa_local
  4. Con λ construir la matriz de marcadores (Poisson independiente por equipo)
     y derivar P(1/X/2) y P(Over/Under 2.5).

El modelo es modular: implementa `ProbabilityModel` y puede sustituirse.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np
from scipy.stats import poisson

from config import CONFIG, Config
from src.data.models import TeamStats
from src.model import ratings


@dataclass(frozen=True)
class TeamStrength:
    """Multiplicadores de ataque/defensa de un equipo (en torno a 1.0)."""

    team_name: str
    attack: float
    defense: float


@dataclass(frozen=True)
class MarketProbabilities:
    """Probabilidades del modelo para un partido."""

    fixture_id: int
    lambda_home: float
    lambda_away: float
    p_home: float
    p_draw: float
    p_away: float
    p_over_25: float
    p_under_25: float

    def as_dict(self) -> dict[tuple[str, str], float]:
        """Mapa (mercado, selección) -> probabilidad, alineado con MarketOdds."""
        return {
            ("1X2", "HOME"): self.p_home,
            ("1X2", "DRAW"): self.p_draw,
            ("1X2", "AWAY"): self.p_away,
            ("OU2.5", "OVER"): self.p_over_25,
            ("OU2.5", "UNDER"): self.p_under_25,
        }


class ProbabilityModel(Protocol):
    """Interfaz mínima para poder intercambiar el modelo."""

    def probabilities(
        self,
        fixture_id: int,
        home_team: str,
        away_team: str,
        home_stats: Optional[TeamStats],
        away_stats: Optional[TeamStats],
        *,
        neutral: bool = False,
    ) -> MarketProbabilities:
        ...


class PoissonModel:
    """Modelo Poisson regularizado con prior de ratings."""

    def __init__(self, config: Config = CONFIG) -> None:
        self.config = config

    # ───────────────────── estimación de fuerzas ─────────────────────

    def _shrink_weight(self, played: int) -> float:
        """Peso del prior según tamaño muestral.

        Con 0 partidos el prior pesa al máximo (peso_prior_ratings combinado);
        a más partidos, los datos pesan más. Usamos un shrink tipo
        n/(n+k): el prior efectivo = base · k/(n+k).
        """
        base = self.config.peso_prior_ratings
        k = 5.0  # nº de "partidos virtuales" del prior
        n = max(0, played)
        return base * (k / (n + k))

    def team_strength(self, team_name: str, stats: Optional[TeamStats]) -> TeamStrength:
        """Combina fuerzas observadas (datos) con el prior de ratings."""
        media = self.config.media_goles_liga
        prior_atk, prior_def = ratings.elo_strength_multipliers(team_name)

        if stats is None or stats.played == 0:
            # Sin datos: solo prior.
            return TeamStrength(team_name, prior_atk, prior_def)

        # Fuerzas observadas relativas a la media de la competición.
        obs_atk = (stats.gf_per_game / media) if media > 0 else 1.0
        obs_def = (stats.ga_per_game / media) if media > 0 else 1.0
        # Acotamos lo observado para evitar outliers con n pequeño.
        obs_atk = float(np.clip(obs_atk, 0.3, 3.0))
        obs_def = float(np.clip(obs_def, 0.3, 3.0))

        w = self._shrink_weight(stats.played)  # peso del prior
        attack = (1 - w) * obs_atk + w * prior_atk
        defense = (1 - w) * obs_def + w * prior_def
        return TeamStrength(team_name, attack, defense)

    # ───────────────────────── lambdas y matriz ─────────────────────────

    def expected_goals(
        self,
        home: TeamStrength,
        away: TeamStrength,
        *,
        neutral: bool = False,
    ) -> tuple[float, float]:
        media = self.config.media_goles_liga
        # En cancha neutral (la mayoría de partidos del Mundial) no hay ventaja
        # de localía; el "local" es solo una etiqueta del fixture.
        ventaja = 1.0 if neutral else self.config.ventaja_local
        lam_home = media * home.attack * away.defense * ventaja
        lam_away = media * away.attack * home.defense
        # Pequeño piso para evitar lambdas degeneradas.
        return max(0.05, lam_home), max(0.05, lam_away)

    def _score_matrix(self, lam_home: float, lam_away: float) -> np.ndarray:
        n = self.config.max_goles_matriz + 1
        home_pmf = poisson.pmf(np.arange(n), lam_home)
        away_pmf = poisson.pmf(np.arange(n), lam_away)
        # Matriz de probabilidad conjunta (independencia entre marcadores).
        matrix = np.outer(home_pmf, away_pmf)
        total = matrix.sum()
        if total > 0:
            matrix = matrix / total  # renormaliza la cola truncada
        return matrix

    # ─────────────────────────── API pública ───────────────────────────

    def probabilities(
        self,
        fixture_id: int,
        home_team: str,
        away_team: str,
        home_stats: Optional[TeamStats] = None,
        away_stats: Optional[TeamStats] = None,
        *,
        neutral: bool = False,
    ) -> MarketProbabilities:
        home_str = self.team_strength(home_team, home_stats)
        away_str = self.team_strength(away_team, away_stats)
        return self.probabilities_from_strengths(
            fixture_id, home_str, away_str, neutral=neutral
        )

    def probabilities_from_strengths(
        self,
        fixture_id: int,
        home_str: TeamStrength,
        away_str: TeamStrength,
        *,
        neutral: bool = False,
    ) -> MarketProbabilities:
        """Probabilidades a partir de fuerzas ya calculadas.

        Útil para el backtest, que construye TeamStrength directamente desde
        ratings derivados de datos (sin pasar por estadísticas de la API).
        """
        lam_home, lam_away = self.expected_goals(home_str, away_str, neutral=neutral)
        matrix = self._score_matrix(lam_home, lam_away)

        n = matrix.shape[0]
        idx = np.arange(n)
        home_idx = idx[:, None]
        away_idx = idx[None, :]

        p_home = float(matrix[home_idx > away_idx].sum())
        p_draw = float(np.trace(matrix))
        p_away = float(matrix[home_idx < away_idx].sum())

        # Over/Under 2.5: total de goles >= 3 -> Over.
        total_goals = home_idx + away_idx
        p_over = float(matrix[total_goals >= 3].sum())
        p_under = float(matrix[total_goals <= 2].sum())

        # Normalización defensiva (por si la truncación deja residuos).
        s_1x2 = p_home + p_draw + p_away
        if s_1x2 > 0:
            p_home, p_draw, p_away = p_home / s_1x2, p_draw / s_1x2, p_away / s_1x2
        s_ou = p_over + p_under
        if s_ou > 0:
            p_over, p_under = p_over / s_ou, p_under / s_ou

        return MarketProbabilities(
            fixture_id=fixture_id,
            lambda_home=lam_home,
            lambda_away=lam_away,
            p_home=p_home,
            p_draw=p_draw,
            p_away=p_away,
            p_over_25=p_over,
            p_under_25=p_under,
        )
