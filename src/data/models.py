"""Estructuras de datos normalizadas (dataclasses) para desacoplar el resto
del sistema del formato crudo de API-Football.

Las funciones `from_api_*` toman el JSON de la API y devuelven objetos limpios.
Mantenerlas aquí concentra el parseo en un solo lugar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ───────────────────────────── Fixtures ─────────────────────────────

@dataclass(frozen=True)
class Fixture:
    """Un partido. `home_goals`/`away_goals` solo presentes si terminó."""

    fixture_id: int
    date_utc: datetime
    status_short: str           # "NS", "FT", "PST", "CANC", ...
    round_name: str
    home_team_id: int
    home_team: str
    away_team_id: int
    away_team: str
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None

    @property
    def is_finished(self) -> bool:
        # FT = full time, AET = tras prórroga, PEN = tras penaltis.
        return self.status_short in {"FT", "AET", "PEN"}

    @property
    def is_void(self) -> bool:
        # Partidos cancelados/abandonados/pospuestos -> void para liquidación.
        return self.status_short in {"CANC", "ABD", "PST", "AWD", "WO"}

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Fixture":
        fx = item["fixture"]
        teams = item["teams"]
        goals = item.get("goals", {})
        league = item.get("league", {})
        return cls(
            fixture_id=fx["id"],
            date_utc=_parse_dt(fx["date"]),
            status_short=fx.get("status", {}).get("short", "NS"),
            round_name=league.get("round", "") or "",
            home_team_id=teams["home"]["id"],
            home_team=teams["home"]["name"],
            away_team_id=teams["away"]["id"],
            away_team=teams["away"]["name"],
            home_goals=goals.get("home"),
            away_goals=goals.get("away"),
        )


# ──────────────────────────── Team stats ────────────────────────────

@dataclass(frozen=True)
class TeamStats:
    """Estadísticas agregadas de un equipo en la competición."""

    team_id: int
    team_name: str
    played: int
    goals_for: int
    goals_against: int

    @property
    def gf_per_game(self) -> float:
        return self.goals_for / self.played if self.played else 0.0

    @property
    def ga_per_game(self) -> float:
        return self.goals_against / self.played if self.played else 0.0

    @classmethod
    def from_api(cls, response: dict[str, Any]) -> "TeamStats":
        team = response.get("team", {})
        fixtures = response.get("fixtures", {})
        goals = response.get("goals", {})
        played = (fixtures.get("played", {}) or {}).get("total", 0) or 0
        gf = ((goals.get("for", {}) or {}).get("total", {}) or {}).get("total", 0) or 0
        ga = ((goals.get("against", {}) or {}).get("total", {}) or {}).get("total", 0) or 0
        return cls(
            team_id=team.get("id", 0),
            team_name=team.get("name", ""),
            played=int(played),
            goals_for=int(gf),
            goals_against=int(ga),
        )


# ─────────────────────────────── Odds ───────────────────────────────

@dataclass(frozen=True)
class Selection:
    """Una selección concreta dentro de un mercado, con su cuota decimal."""

    market: str        # clave normalizada: "1X2" | "OU2.5"
    selection: str     # "HOME"|"DRAW"|"AWAY" | "OVER"|"UNDER"
    odds: float        # cuota decimal


@dataclass(frozen=True)
class MarketOdds:
    """Cuotas normalizadas de un partido para los mercados soportados.

    `selections` mapea (market, selection) -> cuota decimal media de las casas.
    """

    fixture_id: int
    bookmaker: str
    selections: dict[tuple[str, str], float] = field(default_factory=dict)

    def get(self, market: str, selection: str) -> Optional[float]:
        return self.selections.get((market, selection))

    @classmethod
    def from_api(cls, response: dict[str, Any]) -> Optional["MarketOdds"]:
        """Normaliza la respuesta de /odds. Promedia cuotas entre casas para
        cada (mercado, selección) soportado. Devuelve None si no hay datos."""
        if not response:
            return None
        fixture_id = response.get("fixture", {}).get("id", 0)
        bookmakers = response.get("bookmakers", []) or []
        if not bookmakers:
            return None

        # Acumuladores para promediar entre casas.
        acc: dict[tuple[str, str], list[float]] = {}

        for bm in bookmakers:
            for bet in bm.get("bets", []) or []:
                bet_name = (bet.get("name") or "").strip().lower()
                values = bet.get("values", []) or []
                for v in values:
                    parsed = _parse_odds_value(bet_name, v)
                    if parsed is None:
                        continue
                    key, odd = parsed
                    acc.setdefault(key, []).append(odd)

        if not acc:
            return None
        selections = {k: sum(vs) / len(vs) for k, vs in acc.items()}
        return cls(
            fixture_id=fixture_id,
            bookmaker=f"avg_of_{len(bookmakers)}_bookmakers",
            selections=selections,
        )


# ─────────────────────────── helpers internos ───────────────────────

def _parse_dt(value: str) -> datetime:
    """Parsea ISO-8601 de la API a datetime con tz UTC."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_odds_value(bet_name: str, value: dict[str, Any]) -> Optional[tuple[tuple[str, str], float]]:
    """Mapea un valor crudo de la API a (clave normalizada, cuota).

    Soporta los bets típicos de API-Football:
      - "Match Winner" / "1x2"  -> 1X2 (Home/Draw/Away)
      - "Goals Over/Under"      -> OU2.5 (Over 2.5 / Under 2.5)
    Devuelve None para lo no soportado.
    """
    raw_val = (str(value.get("value", "")) or "").strip().lower()
    try:
        odd = float(value.get("odd"))
    except (TypeError, ValueError):
        return None
    if odd <= 1.0:
        return None

    # 1X2
    if bet_name in {"match winner", "1x2", "fulltime result"}:
        mapping = {
            "home": "HOME", "1": "HOME",
            "draw": "DRAW", "x": "DRAW",
            "away": "AWAY", "2": "AWAY",
        }
        sel = mapping.get(raw_val)
        if sel:
            return ("1X2", sel), odd
        return None

    # Over/Under — nos quedamos solo con la línea 2.5
    if bet_name in {"goals over/under", "over/under", "total - over/under"}:
        if raw_val in {"over 2.5", "over2.5"}:
            return ("OU2.5", "OVER"), odd
        if raw_val in {"under 2.5", "under2.5"}:
            return ("OU2.5", "UNDER"), odd
        return None

    return None
