"""Ratings de selecciones para el modelo Poisson.

El fútbol de selecciones no es una liga: hay pocos partidos por equipo y por
torneo, así que las fuerzas no pueden estimarse solo con los datos recientes.
Este módulo aprende, de TODO el histórico internacional (ver
`src/model/historical.py`), dos cosas por selección:

  * fuerza ofensiva (`attack`) y defensiva (`defense`) — multiplicadores en
    torno a 1.0 para un ajuste Poisson independiente;
  * un `elo` aproximado (ranking de fuerza global).

Ambos se calculan con **decaimiento temporal** (los partidos recientes pesan
más) y respetando el flag de **cancha neutral** (sin ventaja de localía).

Compatibilidad
--------------
El modelo Poisson y los tests usan las funciones de módulo `get_elo`,
`elo_expected_score` y `elo_strength_multipliers`. Estas delegan en un objeto
`Ratings` "activo". Por defecto el activo se construye desde `DEFAULT_ELO` (un
prior de relleno editable); cuando hay histórico, `BettingService` lo sustituye
por ratings derivados de datos vía `set_active(...)`. Así nada se rompe sin CSV,
y con CSV el modelo usa fuerzas reales.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:  # evita import circular en runtime
    from src.model.historical import HistMatch

BASE_ELO: float = 1500.0

# Ratings Elo orientativos (no oficiales; ajustables). Prior de relleno usado
# SOLO cuando no se ha cargado el histórico. Con CSV, se reemplazan por datos.
DEFAULT_ELO: dict[str, float] = {
    "Argentina": 2140,
    "France": 2080,
    "Spain": 2070,
    "England": 2010,
    "Brazil": 2000,
    "Portugal": 1990,
    "Netherlands": 1980,
    "Belgium": 1940,
    "Germany": 1930,
    "Italy": 1920,
    "Croatia": 1900,
    "Uruguay": 1890,
    "Colombia": 1870,
    "Morocco": 1860,
    "Mexico": 1790,
    "USA": 1780,
    "Japan": 1770,
    "Senegal": 1760,
    "Switzerland": 1760,
    "Denmark": 1750,
    "Korea Republic": 1730,
    "Ecuador": 1720,
    "Australia": 1700,
    "Canada": 1690,
    "Iran": 1680,
    "Nigeria": 1670,
    "Egypt": 1660,
    "Ghana": 1650,
    "Saudi Arabia": 1620,
    "Qatar": 1600,
}


# ───────────────────────── modelo de datos ─────────────────────────

@dataclass(frozen=True)
class TeamRating:
    """Rating derivado de datos (o del prior) para una selección."""

    team: str
    attack: float        # multiplicador ofensivo (>1 marca más que la media)
    defense: float       # multiplicador defensivo (<1 concede menos)
    elo: float
    n_matches: int = 0   # nº de partidos (informativo)


def elo_to_multipliers(elo: float) -> tuple[float, float]:
    """Convierte un Elo en multiplicadores (ataque, defensa) suaves y acotados."""
    delta = (elo - BASE_ELO) / 400.0
    delta = max(-0.6, min(0.6, delta))
    ataque = math.exp(delta * 0.6)       # >1 si es fuerte
    defensa = math.exp(-delta * 0.6)     # <1 si es fuerte (concede menos)
    return ataque, defensa


class Ratings:
    """Colección de ratings por selección, con fallbacks razonables.

    `mean_goals` es la media (ponderada) de goles por equipo y partido del
    histórico; el modelo Poisson puede usarla como ancla, aunque por defecto
    usa `config.media_goles_liga`.
    """

    def __init__(
        self,
        teams: Optional[dict[str, TeamRating]] = None,
        *,
        mean_goals: float = 1.35,
        source: str = "prior",
    ) -> None:
        self._teams: dict[str, TeamRating] = teams or {}
        self._lower: dict[str, TeamRating] = {k.lower(): v for k, v in self._teams.items()}
        self.mean_goals = mean_goals
        self.source = source

    # ── acceso ──
    def get(self, team_name: str) -> Optional[TeamRating]:
        r = self._teams.get(team_name)
        if r is not None:
            return r
        return self._lower.get((team_name or "").strip().lower())

    def elo(self, team_name: str) -> float:
        r = self.get(team_name)
        if r is not None:
            return r.elo
        # Fallback al prior Elo de relleno por nombre.
        if team_name in DEFAULT_ELO:
            return DEFAULT_ELO[team_name]
        lowered = (team_name or "").strip().lower()
        for name, elo in DEFAULT_ELO.items():
            if name.lower() == lowered:
                return elo
        return BASE_ELO

    def strength_multipliers(self, team_name: str) -> tuple[float, float]:
        r = self.get(team_name)
        if r is not None:
            return r.attack, r.defense
        # Sin rating de datos: deriva del Elo (prior).
        return elo_to_multipliers(self.elo(team_name))

    def __len__(self) -> int:
        return len(self._teams)


def build_default_ratings() -> Ratings:
    """Ratings de relleno a partir de `DEFAULT_ELO` (sin datos históricos)."""
    teams: dict[str, TeamRating] = {}
    for name, elo in DEFAULT_ELO.items():
        atk, dfn = elo_to_multipliers(elo)
        teams[name] = TeamRating(team=name, attack=atk, defense=dfn, elo=elo)
    return Ratings(teams, mean_goals=1.35, source="default_elo")


# ─────────────── cálculo de ratings desde el histórico ───────────────

def _decay_weight(match_date: date, ref_date: date, half_life_days: float) -> float:
    """Peso por decaimiento temporal: 0.5 a una vida media de antigüedad."""
    age_days = (ref_date - match_date).days
    if age_days <= 0:
        return 1.0
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def compute_ratings(
    matches: Iterable["HistMatch"],
    *,
    half_life_days: float = 1460.0,
    iterations: int = 50,
    home_advantage: float = 1.10,
    as_of: Optional[date] = None,
) -> Ratings:
    """Aprende attack/defense (ajuste Poisson iterativo) y un Elo, de todo el
    histórico, con decaimiento temporal y manejo de cancha neutral.

    Parameters
    ----------
    matches : partidos históricos (HistMatch).
    half_life_days : vida media del decaimiento temporal (partidos recientes
        pesan más).
    iterations : iteraciones del ajuste ataque/defensa.
    home_advantage : factor de localía aplicado SOLO cuando el partido no es
        en cancha neutral.
    as_of : fecha de referencia para el decaimiento y para excluir partidos
        posteriores (evita fuga de información en backtest). Por defecto, la
        fecha del último partido.

    Returns
    -------
    Ratings con un TeamRating por selección observada.
    """
    matches = [m for m in matches if as_of is None or m.fecha <= as_of]
    if not matches:
        return build_default_ratings()

    ref_date = as_of or max(m.fecha for m in matches)

    # Pesos por partido.
    weighted = [
        (m, _decay_weight(m.fecha, ref_date, half_life_days)) for m in matches
    ]

    teams = sorted({m.home for m, _ in weighted} | {m.away for m, _ in weighted})

    # Media ponderada de goles por equipo y partido (mu).
    w_total = sum(w for _, w in weighted)
    if w_total <= 0:
        return build_default_ratings()
    goals_total = sum(w * (m.home_goals + m.away_goals) for m, w in weighted)
    mu = goals_total / (2.0 * w_total)  # por equipo
    mu = max(0.2, mu)

    # ── ajuste iterativo ataque/defensa ──
    attack = {t: 1.0 for t in teams}
    defense = {t: 1.0 for t in teams}

    for _ in range(max(1, iterations)):
        # numeradores / denominadores por equipo
        gf_num = {t: 0.0 for t in teams}   # goles a favor ponderados
        ga_num = {t: 0.0 for t in teams}   # goles en contra ponderados
        att_den = {t: 0.0 for t in teams}  # exposición ofensiva esperada
        def_den = {t: 0.0 for t in teams}  # exposición defensiva esperada

        for m, w in weighted:
            h, a = m.home, m.away
            gamma = 1.0 if m.neutral else home_advantage
            # goles a favor del local: mu * att[h] * def[a] * gamma
            gf_num[h] += w * m.home_goals
            att_den[h] += w * mu * defense[a] * gamma
            ga_num[a] += w * m.home_goals
            def_den[a] += w * mu * attack[h] * gamma
            # goles a favor del visitante: mu * att[a] * def[h]
            gf_num[a] += w * m.away_goals
            att_den[a] += w * mu * defense[h]
            ga_num[h] += w * m.away_goals
            def_den[h] += w * mu * attack[a]

        for t in teams:
            if att_den[t] > 0:
                attack[t] = gf_num[t] / att_den[t]
            if def_den[t] > 0:
                defense[t] = ga_num[t] / def_den[t]
            # Acotamos para evitar valores degenerados con poca exposición.
            attack[t] = min(3.0, max(0.25, attack[t]))
            defense[t] = min(3.0, max(0.25, defense[t]))

        # Normaliza para que la media geométrica sea 1 (identificabilidad).
        _renormalize(attack)
        _renormalize(defense)

    # ── Elo secuencial (recencia natural; localía si no es neutral) ──
    elo = _sequential_elo(matches, home_advantage=home_advantage)

    n_by_team: dict[str, int] = {t: 0 for t in teams}
    for m in matches:
        n_by_team[m.home] += 1
        n_by_team[m.away] += 1

    out: dict[str, TeamRating] = {}
    for t in teams:
        out[t] = TeamRating(
            team=t,
            attack=attack[t],
            defense=defense[t],
            elo=elo.get(t, BASE_ELO),
            n_matches=n_by_team.get(t, 0),
        )
    return Ratings(out, mean_goals=mu, source="historico")


def _renormalize(values: dict[str, float]) -> None:
    """Escala los valores para que su media geométrica sea 1.0 (in place)."""
    positivos = [v for v in values.values() if v > 0]
    if not positivos:
        return
    log_mean = sum(math.log(v) for v in positivos) / len(positivos)
    factor = math.exp(log_mean)
    if factor <= 0:
        return
    for k in values:
        values[k] = values[k] / factor


def _sequential_elo(
    matches: Iterable["HistMatch"],
    *,
    k: float = 24.0,
    home_advantage_elo: float = 65.0,
    home_advantage: float = 1.10,  # no usado aquí; firma homogénea
) -> dict[str, float]:
    """Elo clásico recorriendo los partidos en orden cronológico.

    Incluye ventaja de localía en puntos Elo (solo si el partido no es neutral)
    y un multiplicador por diferencia de goles (margen de victoria).
    """
    ratings: dict[str, float] = {}
    ordered = sorted(matches, key=lambda m: m.fecha)
    for m in ordered:
        ra = ratings.get(m.home, BASE_ELO)
        rb = ratings.get(m.away, BASE_ELO)
        adv = 0.0 if m.neutral else home_advantage_elo
        exp_home = 1.0 / (1.0 + 10 ** ((rb - (ra + adv)) / 400.0))
        if m.home_goals > m.away_goals:
            score = 1.0
        elif m.home_goals == m.away_goals:
            score = 0.5
        else:
            score = 0.0
        # Multiplicador por margen de goles (acota partidos abultados).
        gd = abs(m.home_goals - m.away_goals)
        mult = math.log1p(gd) + 1.0
        delta = k * mult * (score - exp_home)
        ratings[m.home] = ra + delta
        ratings[m.away] = rb - delta
    return ratings


# ─────────────────── ratings "activos" (singleton) ───────────────────

_ACTIVE: Ratings = build_default_ratings()


def get_active() -> Ratings:
    """Devuelve los ratings actualmente en uso por el modelo."""
    return _ACTIVE


def set_active(ratings: Ratings) -> None:
    """Sustituye los ratings activos (p.ej. tras cargar el histórico)."""
    global _ACTIVE
    _ACTIVE = ratings


def reset_active() -> None:
    """Restaura el prior de relleno (útil para aislar tests)."""
    set_active(build_default_ratings())


# ─────────────── API de módulo (compatibilidad hacia atrás) ───────────────

def get_elo(team_name: str) -> float:
    """Rating Elo del equipo (case-insensitive) según los ratings activos."""
    return _ACTIVE.elo(team_name)


def elo_expected_score(elo_a: float, elo_b: float) -> float:
    """Probabilidad esperada (1=victoria A) según la fórmula Elo estándar."""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def elo_strength_multipliers(team_name: str) -> tuple[float, float]:
    """Multiplicadores (ataque, defensa) del equipo según los ratings activos.

    Con histórico cargado, devuelve las fuerzas derivadas de datos; sin él,
    las deriva del prior Elo de relleno.
    """
    return _ACTIVE.strength_multipliers(team_name)
