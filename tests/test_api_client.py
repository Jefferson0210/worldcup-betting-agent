"""Tests del cliente API con sesión mockeada (sin red) y manejo de 429."""
from __future__ import annotations

from typing import Any

import pytest

from src.data.api_client import ApiFootballClient
from tests.conftest import LEAGUES_RESPONSE, ODDS_RESPONSE_FIXTURE_100


class FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


class FakeSession:
    """Sesión que devuelve respuestas programadas por endpoint."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        return self._responses.pop(0)


def _ok(response_list) -> FakeResponse:
    return FakeResponse(200, {"response": response_list, "errors": []})


def test_resuelve_liga_y_temporada(tmp_config):
    session = FakeSession([_ok(LEAGUES_RESPONSE)])
    client = ApiFootballClient(tmp_config, session=session)
    league_id, season = client.resolve_league_and_season()
    assert league_id == 1
    assert season == 2026  # la marcada current=True


def test_get_odds_normaliza(tmp_config):
    session = FakeSession([
        _ok(LEAGUES_RESPONSE),                 # resolve league (no necesario aquí pero seguro)
        _ok([ODDS_RESPONSE_FIXTURE_100]),
    ])
    client = ApiFootballClient(tmp_config, session=session)
    # Forzamos league ya resuelta para que get_odds no gaste una llamada extra.
    client._league_id, client._season = 1, 2026
    session._responses.pop(0)  # descartamos la de leagues no usada
    odds = client.get_odds(100)
    assert odds is not None
    assert odds.get("1X2", "HOME") == pytest.approx(2.10)


def test_backoff_en_429(tmp_config, monkeypatch):
    # Evita esperas reales.
    monkeypatch.setattr("src.data.api_client.time.sleep", lambda *_: None)
    object.__setattr__(tmp_config, "rate_limit_max_reintentos", 2)
    session = FakeSession([
        FakeResponse(429, {}),
        FakeResponse(429, {}),
        _ok(LEAGUES_RESPONSE),
    ])
    client = ApiFootballClient(tmp_config, session=session)
    league_id, _ = client.resolve_league_and_season()
    assert league_id == 1
    assert len(session.calls) == 3  # dos 429 + el exitoso


def test_usa_cache_en_segunda_llamada(tmp_config):
    session = FakeSession([_ok(LEAGUES_RESPONSE)])
    client = ApiFootballClient(tmp_config, session=session)
    client.resolve_league_and_season()
    # Segunda resolución no debe pegar a la sesión (memoria + caché disco).
    client2 = ApiFootballClient(tmp_config, session=FakeSession([]))
    league_id, season = client2.resolve_league_and_season()
    assert (league_id, season) == (1, 2026)
