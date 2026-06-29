"""Fixtures de pytest y respuestas mockeadas de la API (sin red)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Asegura que la raíz del proyecto está en sys.path al correr pytest.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─────────────── respuestas crudas estilo API-Football ───────────────

LEAGUES_RESPONSE = [
    {
        "league": {"id": 1, "name": "World Cup", "type": "Cup"},
        "country": {"name": "World"},
        "seasons": [
            {"year": 2022, "current": False},
            {"year": 2026, "current": True},
        ],
    }
]

ODDS_RESPONSE_FIXTURE_100 = {
    "fixture": {"id": 100},
    "bookmakers": [
        {
            "id": 8, "name": "Bet365",
            "bets": [
                {"name": "Match Winner", "values": [
                    {"value": "Home", "odd": "2.10"},
                    {"value": "Draw", "odd": "3.40"},
                    {"value": "Away", "odd": "3.60"},
                ]},
                {"name": "Goals Over/Under", "values": [
                    {"value": "Over 2.5", "odd": "2.00"},
                    {"value": "Under 2.5", "odd": "1.80"},
                ]},
            ],
        }
    ],
}


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Config aislada en tmp_path (db y caché propias)."""
    from config import Config

    cfg = Config(
        apisports_key="test-key",
        gemini_api_key="",
        bankroll_inicial=1000.0,
        objetivo_ganancia=500.0,
        umbral_valor=0.05,
        kelly_fraccion=0.25,
        stake_max_pct=0.05,
        max_legs=3,
    )
    # Redirige rutas a tmp_path (dataclass frozen -> usamos object.__setattr__).
    object.__setattr__(cfg, "cache_dir", tmp_path / "cache")
    object.__setattr__(cfg, "db_path", tmp_path / "betting.db")
    object.__setattr__(cfg, "reports_dir", tmp_path / "reports")
    return cfg


@pytest.fixture
def store(tmp_config):
    from src.storage.db import BettingStore

    s = BettingStore(tmp_config)
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset_active_ratings():
    """Aísla los tests del singleton de ratings activos (estado global)."""
    from src.model import ratings as ratings_mod

    ratings_mod.reset_active()
    yield
    ratings_mod.reset_active()


def synthetic_matches(n_days: int = 6000, step: int = 5, seed: int = 7):
    """Genera HistMatch sintéticos con fuerzas latentes (sin red).

    Equipos con distinta fuerza; goles ~ Poisson. Marca como 'FIFA World Cup'
    los partidos de junio/julio de años de Mundial para poder backtestearlos.
    """
    import math
    import random
    from datetime import date, timedelta

    from src.model.historical import HistMatch

    rnd = random.Random(seed)
    teams = [f"Team{i}" for i in range(8)]
    strength = {t: 0.7 + 0.13 * i for i, t in enumerate(teams)}  # determinista

    def pois(lam: float) -> int:
        L = math.exp(-lam)
        k, p = 0, 1.0
        while True:
            p *= rnd.random()
            if p <= L:
                return k
            k += 1

    start = date(2000, 1, 1)
    out = []
    for d in range(0, n_days, step):
        day = start + timedelta(days=d)
        a, b = rnd.sample(teams, 2)
        ga = pois(1.3 * strength[a] / strength[b])
        gb = pois(1.3 * strength[b] / strength[a])
        tour = "FIFA World Cup" if (day.year % 4 == 2 and day.month in (6, 7)) else "Friendly"
        out.append(HistMatch(
            fecha=day, home=a, away=b, home_goals=ga, away_goals=gb,
            tournament=tour, neutral=(d % 4 == 0),
        ))
    return teams, strength, out
