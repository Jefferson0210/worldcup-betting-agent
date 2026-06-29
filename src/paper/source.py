"""Adaptador: partidos próximos del Mundial 2026 (con cuotas 1X2) desde OddsPapi
hacia los objetos que entiende el motor (`Fixture`, `MarketOdds`,
`FixtureAnalysis`).

Cuida la cuota: solo pide las cuotas de fixtures que NO estén ya apostados
(`skip_fixture_ids`) y cachea cada respuesta (el provider usa caché en disco).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

from config import CONFIG, Config
from src.data.models import Fixture, MarketOdds
from src.model.historical import normalize_team
from src.model.odds_history import OddsPapiProvider, OddsTriple
from src.model.poisson import PoissonModel
from src.service import FixtureAnalysis
from src.value.value_engine import find_value_bets


@dataclass
class UpcomingMeta:
    fixture_id: int
    fecha: str       # ISO YYYY-MM-DD
    home: str
    away: str


def fixture_int(oddspapi_id: str) -> int:
    """Convierte el id de OddsPapi ('id1000001666456904') a un entero estable."""
    digits = re.sub(r"\D", "", str(oddspapi_id))
    return int(digits) if digits else 0


def _parse_dt(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _market_odds(fixture_id: int, triple: OddsTriple) -> MarketOdds:
    return MarketOdds(
        fixture_id=fixture_id,
        bookmaker="oddspapi",
        selections={
            ("1X2", "HOME"): triple.home,
            ("1X2", "DRAW"): triple.draw,
            ("1X2", "AWAY"): triple.away,
        },
    )


def upcoming_analyses(
    provider: OddsPapiProvider,
    model: PoissonModel,
    *,
    config: Config = CONFIG,
    skip_fixture_ids: Optional[Iterable[int]] = None,
    max_fixtures: Optional[int] = None,
    on_progress: Optional[Callable[[dict, Optional[OddsTriple]], None]] = None,
) -> tuple[list[FixtureAnalysis], list[UpcomingMeta]]:
    """Construye análisis (probs + value bets 1X2) de los partidos próximos.

    Devuelve (analyses, meta). `meta` lleva fecha+equipos por fixture para poder
    liquidar luego contra `results.csv`.
    """
    skip = set(skip_fixture_ids or ())
    fixtures = provider.list_upcoming_fixtures()  # 1 petición
    analyses: list[FixtureAnalysis] = []
    meta: list[UpcomingMeta] = []
    usados = 0

    for fx in fixtures:
        fid = fixture_int(fx.get("fixtureId", ""))
        if not fid or fid in skip:
            continue  # ya apostado o id inválido: no se gasta cuota en sus cuotas
        if max_fixtures is not None and usados >= max_fixtures:
            break
        dt = _parse_dt(fx.get("startTime") or fx.get("trueStartTime") or "")
        if dt is None:
            continue
        triple = provider.historical_1x2(fx.get("fixtureId"))  # cacheada
        usados += 1
        if on_progress:
            on_progress(fx, triple)
        if triple is None or not triple.valid():
            continue

        home = normalize_team(fx.get("participant1Name", ""))
        away = normalize_team(fx.get("participant2Name", ""))
        if not home or not away:
            continue

        fixture = Fixture(
            fixture_id=fid, date_utc=dt, status_short="NS",
            round_name=fx.get("tournamentName", "FIFA World Cup"),
            home_team_id=0, home_team=home, away_team_id=0, away_team=away,
        )
        odds = _market_odds(fid, triple)
        probs = model.probabilities(
            fid, home, away, neutral=config.mundial_es_neutral
        )
        value_bets = find_value_bets(odds, probs, home, away, config=config)
        analyses.append(FixtureAnalysis(fixture=fixture, odds=odds, probs=probs, value_bets=value_bets))
        meta.append(UpcomingMeta(fixture_id=fid, fecha=dt.date().isoformat(), home=home, away=away))

    return analyses, meta
