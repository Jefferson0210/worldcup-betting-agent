"""Tests del proveedor OddsPapi: parseo de su respuesta a OddsBook (sin red)."""
from __future__ import annotations

from datetime import date

import pytest

from src.model.odds_history import (
    OddsBook,
    OddsPapiProvider,
    OddsTriple,
    parse_oddspapi_1x2,
    write_odds_csv,
)


# Respuesta /v4/historical-odds con la forma REAL observada en la API:
# bookmakers -> markets -> "101" (1X2) -> outcomes 101/102/103 -> players "0" -> serie.
def _snap(price, created, active=True):
    return {"createdAt": created, "price": price, "active": active, "limit": None}


SAMPLE = {
    "fixtureId": "id1000001666456904",
    "bookmakers": {
        "bet365": {"markets": {
            "101": {"outcomes": {
                "101": {"players": {"0": [_snap(1.50, "2026-03-17"), _snap(1.45, "2026-06-11")]}},
                "102": {"players": {"0": [_snap(4.20, "2026-03-17"), _snap(4.50, "2026-06-11")]}},
                "103": {"players": {"0": [_snap(6.00, "2026-03-17"), _snap(7.00, "2026-06-11")]}},
            }},
            "104": {"outcomes": {"104": {"players": {"0": [_snap(1.9, "2026-06-11")]}}}},  # O/U: ignorado
        }},
        "pinnacle": {"markets": {
            "101": {"outcomes": {
                "101": {"players": {"0": [_snap(1.55, "2026-06-11")]}},
                "102": {"players": {"0": [_snap(4.40, "2026-06-11")]}},
                "103": {"players": {"0": [_snap(6.80, "2026-06-11")]}},
            }},
        }},
    },
}


def test_parse_oddspapi_promedia_cierre_entre_casas():
    triple = parse_oddspapi_1x2(SAMPLE)
    assert triple is not None
    # Cierre = última cotización (mayor createdAt). bet365: 1.45/4.50/7.00; pinnacle 1.55/4.40/6.80.
    assert triple.home == pytest.approx((1.45 + 1.55) / 2)
    assert triple.draw == pytest.approx((4.50 + 4.40) / 2)
    assert triple.away == pytest.approx((7.00 + 6.80) / 2)
    assert triple.valid()


def test_parse_oddspapi_filtra_por_casa():
    triple = parse_oddspapi_1x2(SAMPLE, bookmakers=["pinnacle"])
    assert triple.home == pytest.approx(1.55)
    assert triple.away == pytest.approx(6.80)


def test_parse_oddspapi_sin_mercado_1x2_devuelve_none():
    data = {"fixtureId": "x", "bookmakers": {"bet365": {"markets": {
        "104": {"outcomes": {"104": {"players": {"0": [_snap(1.9, "2026-06-11")]}}}},
    }}}}
    assert parse_oddspapi_1x2(data) is None


def test_parse_oddspapi_outcome_incompleto_devuelve_none():
    # Falta el Away (103) -> no se puede formar el 1X2.
    data = {"fixtureId": "x", "bookmakers": {"bet365": {"markets": {
        "101": {"outcomes": {
            "101": {"players": {"0": [_snap(1.5, "2026-06-11")]}},
            "102": {"players": {"0": [_snap(4.0, "2026-06-11")]}},
        }},
    }}}}
    assert parse_oddspapi_1x2(data) is None


def test_parse_oddspapi_ignora_cotizaciones_invalidas():
    data = {"fixtureId": "x", "bookmakers": {"bet365": {"markets": {
        "101": {"outcomes": {
            "101": {"players": {"0": [_snap(1.0, "2026-06-11")]}},   # <=1.0 inválida
            "102": {"players": {"0": [_snap(4.0, "2026-06-11")]}},
            "103": {"players": {"0": [_snap(6.0, "2026-06-11")]}},
        }},
    }}}}
    assert parse_oddspapi_1x2(data) is None  # Home inválido -> sin triple


# ─────────────── provider con sesión mockeada (sin red) ───────────────

class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, routes):
        # routes: lista de (endpoint_substr, body) en orden de consumo por endpoint
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        for sub, body in self.routes:
            if sub in url:
                return _FakeResp(200, body)
        return _FakeResp(404, {"error": "no route"})


def test_provider_build_book_y_csv(tmp_config, tmp_path):
    fixtures_body = [
        {"fixtureId": "f1", "participant1Name": "Mexico", "participant2Name": "South Korea",
         "startTime": "2026-06-11T19:00:00.000Z", "statusId": 2},
        {"fixtureId": "f2", "participant1Name": "Brazil", "participant2Name": "Qatar",
         "startTime": "2026-06-12T19:00:00.000Z", "statusId": 2},
    ]
    # historical-odds: misma respuesta de muestra para ambos (mockeada).
    session = _FakeSession([
        ("v4/fixtures", fixtures_body),
        ("v4/historical-odds", SAMPLE),
    ])
    object.__setattr__(tmp_config, "odds_api_key", "k")
    provider = OddsPapiProvider(tmp_config, session=session)
    book = provider.build_book(tournament_id=16)
    assert len(book) == 2
    # "South Korea" se normaliza a "Korea Republic" (alias del dataset).
    t = book.lookup(date(2026, 6, 11), "Mexico", "Korea Republic")
    assert t is not None and t.home == pytest.approx((1.45 + 1.55) / 2)

    # Vuelca a CSV y recarga -> mismo nº de partidos.
    from src.model.odds_history import load_odds_csv
    p = write_odds_csv(book, tmp_path / "odds.csv")
    reloaded = load_odds_csv(p)
    assert len(reloaded) == 2


def test_provider_requiere_clave(tmp_config):
    object.__setattr__(tmp_config, "odds_api_key", "")
    with pytest.raises(RuntimeError, match="ODDS_API_KEY"):
        OddsPapiProvider(tmp_config)
