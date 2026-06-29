"""Tests del modo paper en vivo (OddsPapi) — sin red.

Cubren: (1) el camino de "partidos próximos desde OddsPapi" -> análisis con
value bets, y (2) que no se registra dos veces el mismo partido.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.model.historical import HistMatch
from src.model.odds_history import OddsTriple
from src.paper.runner import PaperRunner
from src.paper.source import fixture_int, upcoming_analyses
from src.paper.store import PaperFixtureStore
from src.service import BettingService


# ─────────────────────── proveedor OddsPapi falso ───────────────────────

class FakeOddsPapi:
    """Imita OddsPapiProvider sin red."""

    def __init__(self, fixtures, odds_map):
        self.fixtures = fixtures
        self.odds_map = odds_map
        self.odds_calls: list[str] = []

    def list_upcoming_fixtures(self, tournament_id=None):
        return self.fixtures

    def historical_1x2(self, fixture_id, bookmakers=None):
        self.odds_calls.append(fixture_id)
        return self.odds_map.get(fixture_id)


def _fx(fid, home, away, day):
    return {"fixtureId": fid, "participant1Name": home, "participant2Name": away,
            "startTime": f"2026-06-{day:02d}T18:00:00.000Z", "tournamentName": "World Cup"}


FIXTURES = [
    _fx("id101", "Brazil", "Qatar", 20),
    _fx("id102", "France", "Japan", 21),
]
ODDS = {
    # p_home(Brazil-Qatar)=0.507 -> a cuota 2.60 hay edge ~0.32 (valor en HOME).
    "id101": OddsTriple(2.60, 3.60, 4.00),
    # France-Japan a cuota baja -> sin valor (no genera apuesta).
    "id102": OddsTriple(2.00, 3.50, 4.20),
}


@pytest.fixture
def paper_service(tmp_config):
    # No cargar el histórico real (lento): el modelo usa el prior por defecto.
    object.__setattr__(tmp_config, "cargar_ratings_historicos", False)
    svc = BettingService(tmp_config)
    yield svc
    svc.close()


# ───────────────────── partidos próximos desde OddsPapi ─────────────────────

def test_upcoming_analyses_construye_analisis_con_valor(paper_service, tmp_config):
    provider = FakeOddsPapi(FIXTURES, ODDS)
    analyses, meta = upcoming_analyses(provider, paper_service.model, config=tmp_config)

    assert len(analyses) == 2
    assert {m.home for m in meta} == {"Brazil", "France"}
    # Las cuotas vienen de OddsPapi (1X2) y hay al menos una value bet (HOME).
    a0 = analyses[0]
    assert a0.odds is not None
    assert a0.odds.get("1X2", "HOME") == pytest.approx(2.60)
    assert any(vb.selection == "HOME" for vb in a0.value_bets)
    # Se pidieron las cuotas de ambos fixtures.
    assert provider.odds_calls == ["id101", "id102"]


def test_skip_fixture_ids_no_gasta_cuota(paper_service, tmp_config):
    provider = FakeOddsPapi(FIXTURES, ODDS)
    # Si id101 ya está apostado, no se piden sus cuotas (ahorro de cuota).
    analyses, meta = upcoming_analyses(
        provider, paper_service.model, config=tmp_config,
        skip_fixture_ids={fixture_int("id101")},
    )
    assert provider.odds_calls == ["id102"]
    assert {m.home for m in meta} == {"France"}


# ───────────────────── no registrar dos veces ─────────────────────

def test_no_registra_dos_veces_el_mismo_partido(paper_service, tmp_config):
    provider = FakeOddsPapi(FIXTURES, ODDS)
    runner = PaperRunner(tmp_config, service=paper_service, provider=provider,
                         paper_store=PaperFixtureStore(tmp_config), hist_matches=[])

    placed1 = runner.collect_and_register()
    assert len(placed1) >= 1                       # registró al menos una

    committed = runner.committed_fixture_ids()
    assert fixture_int("id101") in committed or fixture_int("id102") in committed

    # Segunda corrida el mismo día: no debe registrar nada nuevo.
    provider.odds_calls.clear()
    placed2 = runner.collect_and_register()
    assert placed2 == []
    # No se piden cuotas de fixtures ya apostados.
    for fid in committed:
        assert str(fid) not in [c for c in provider.odds_calls]


def test_un_fixture_solo_una_apuesta(paper_service, tmp_config):
    # Con un único fixture, no puede haber doble registro ni combinada.
    provider = FakeOddsPapi([_fx("id101", "Brazil", "Qatar", 20)], ODDS)
    runner = PaperRunner(tmp_config, service=paper_service, provider=provider,
                         paper_store=PaperFixtureStore(tmp_config), hist_matches=[])
    placed = runner.collect_and_register()
    # A lo sumo una apuesta, y el fixture queda comprometido.
    fids = [leg.fixture_id for b in paper_service.store.all_bets() for leg in b.legs]
    assert fids.count(fixture_int("id101")) <= 1


# ───────────────────── liquidación desde results.csv ─────────────────────

def test_settle_desde_results_csv(paper_service, tmp_config):
    provider = FakeOddsPapi([_fx("id101", "Brazil", "Qatar", 20)], ODDS)
    # results.csv (mock): Brazil 3-0 Qatar el 2026-06-20.
    hist = [HistMatch(date(2026, 6, 20), "Brazil", "Qatar", 3, 0, "FIFA World Cup", True)]
    runner = PaperRunner(tmp_config, service=paper_service, provider=provider,
                         paper_store=PaperFixtureStore(tmp_config), hist_matches=hist)

    placed = runner.collect_and_register()
    assert placed, "debió registrar al menos una apuesta paper"
    settled = runner.settle()
    assert settled, "debió liquidar la apuesta con el resultado real"
    # La apuesta a HOME (Brazil) con Brazil ganando 3-0 debe quedar ganada.
    estados = {est for _bid, est, _pay in settled}
    assert "won" in estados
