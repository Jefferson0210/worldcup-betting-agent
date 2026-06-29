"""Tests del bucle de function calling de Gemini con cliente mockeado (sin red).

Verifican que el agente: (1) traduce las herramientas a function declarations de
Gemini, (2) ejecuta las funciones deterministas cuando el modelo las pide, y
(3) devuelve el texto final del modelo. No se usa red ni clave real.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.agent.orchestrator import BettingAgent
from src.agent.tools import gemini_function_declarations
from src.data.models import Fixture


# ─────────────── declaraciones de función para Gemini ───────────────

def test_gemini_declarations_formato():
    decls = gemini_function_declarations()
    nombres = {d["name"] for d in decls}
    assert {"get_fixtures", "build_recommendations", "place_paper_bet"} <= nombres
    for d in decls:
        assert set(d) == {"name", "description", "parameters"}
        params = d["parameters"]
        # Tipos en MAYÚSCULAS (formato Gemini) y sin claves no soportadas.
        assert params.get("type") == "OBJECT"
        for prop in params.get("properties", {}).values():
            assert prop["type"] in {"STRING", "INTEGER", "NUMBER", "BOOLEAN", "OBJECT", "ARRAY"}
            assert "default" not in prop


# ─────────────────────── cliente Gemini mockeado ───────────────────────

def _fcall_response(name, args):
    part = SimpleNamespace(function_call=SimpleNamespace(name=name, args=args), text=None)
    content = SimpleNamespace(role="model", parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _text_response(text):
    part = SimpleNamespace(function_call=None, text=text)
    content = SimpleNamespace(role="model", parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


class FakeGeminiClient:
    """Imita client.models.generate_content devolviendo respuestas en cola."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.models = self  # client.models.generate_content -> este mismo objeto

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents})
        return self._responses.pop(0)


class FakeService:
    """Servicio mínimo para que el ToolDispatcher ejecute get_fixtures."""

    def __init__(self):
        self.get_fixtures_args = None

    def get_fixtures(self, round_name=None, date_iso=None):
        self.get_fixtures_args = (round_name, date_iso)
        return [Fixture(
            fixture_id=100, date_utc=datetime(2026, 6, 28, 18, tzinfo=timezone.utc),
            status_short="NS", round_name="Group Stage - 1",
            home_team_id=1, home_team="Brazil", away_team_id=2, away_team="Qatar",
        )]


def test_loop_ejecuta_funcion_y_devuelve_texto(tmp_config):
    service = FakeService()
    client = FakeGeminiClient([
        _fcall_response("get_fixtures", {"date_iso": "2026-06-28"}),
        _text_response("Recomiendo Brazil. Recuerda: educativo, no asesoría financiera."),
    ])
    agent = BettingAgent(tmp_config, service=service, client=client)

    out = agent.run("¿Qué partidos hay el 2026-06-28?", verbose=False)

    # Se ejecutó la herramienta determinista con los argumentos del modelo.
    assert service.get_fixtures_args == (None, "2026-06-28")
    # Hubo dos llamadas al modelo (petición de función + texto final).
    assert len(client.calls) == 2
    # La segunda llamada incluye la respuesta de la función en el historial.
    assert len(client.calls[1]["contents"]) == 3  # user + model(fcall) + function_response
    # Texto final del modelo.
    assert "asesoría" in out.lower()


def test_loop_sin_llamadas_devuelve_texto_directo(tmp_config):
    service = FakeService()
    client = FakeGeminiClient([_text_response("Hola, soy tu analista. No asesoría financiera.")])
    agent = BettingAgent(tmp_config, service=service, client=client)
    out = agent.run("Hola", verbose=False)
    assert "analista" in out.lower()
    assert len(client.calls) == 1
    assert service.get_fixtures_args is None  # no se ejecutó ninguna herramienta


def test_agent_sin_clave_gemini_lanza(tmp_config):
    # Sin cliente inyectado y sin GEMINI_API_KEY -> error claro.
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        BettingAgent(tmp_config)
