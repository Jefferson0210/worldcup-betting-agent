"""Definición de herramientas (tool use) y su dispatcher determinista.

Cada herramienta envuelve una función de `BettingService`. La matemática vive
en Python; el LLM solo decide qué herramienta llamar y con qué argumentos, y
luego redacta la recomendación / reporte con los datos devueltos.

Las herramientas devuelven SIEMPRE dicts JSON-serializables.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.service import BettingService, FixtureAnalysis, RecommendedBet

# Esquema de herramientas (fuente única, agnóstica del LLM). Se convierte al
# formato de function calling de Gemini con `gemini_function_declarations()`.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_fixtures",
        "description": (
            "Lista los partidos del Mundial. Filtra por ronda (round_name) o por "
            "fecha (date_iso, formato YYYY-MM-DD). Sin filtros, devuelve todos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "round_name": {"type": "string", "description": "Ronda, p.ej. 'Group Stage - 1'"},
                "date_iso": {"type": "string", "description": "Fecha YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "analyze_round",
        "description": (
            "Para una ronda o fecha, calcula probabilidades del modelo y detecta "
            "selecciones de valor en cada partido próximo. Devuelve análisis por partido."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "round_name": {"type": "string"},
                "date_iso": {"type": "string", "description": "Fecha YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "find_value_bets",
        "description": (
            "Devuelve todas las selecciones de valor (edge > umbral) de una ronda/fecha, "
            "ordenadas por edge. Úsalo para ver el universo de apuestas individuales de valor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "round_name": {"type": "string"},
                "date_iso": {"type": "string"},
            },
        },
    },
    {
        "name": "build_recommendations",
        "description": (
            "Construye recomendaciones (singles de valor y combinadas de legs de valor) "
            "con staking Kelly fraccionado y tope. Devuelve la lista ordenada por edge, "
            "con stake sugerido para cada una. NO registra nada."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "round_name": {"type": "string"},
                "date_iso": {"type": "string"},
                "include_singles": {"type": "boolean", "default": True},
                "top_parlays": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "place_paper_bet",
        "description": (
            "Registra UNA apuesta paper (dinero ficticio) a partir del índice de una "
            "recomendación previamente generada por build_recommendations en esta sesión. "
            "Descuenta el stake del bankroll. Solo paper: nunca coloca apuestas reales."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recommendation_index": {
                    "type": "integer",
                    "description": "Índice (0-based) en la última lista de build_recommendations.",
                },
            },
            "required": ["recommendation_index"],
        },
    },
    {
        "name": "settle_bets",
        "description": (
            "Liquida las apuestas pendientes usando resultados reales (status FT). "
            "Actualiza estado, payout y bankroll."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "generate_report",
        "description": (
            "Genera el reporte de rendimiento (ROI, acierto, P&L, bankroll, progreso al "
            "objetivo y calibración). Devuelve el resumen y exporta markdown/CSV."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


# ─────────── conversión a function declarations de Gemini ───────────
# Gemini (google-genai) usa un esquema tipo OpenAPI con el `type` en MAYÚSCULAS
# y no admite ciertas claves (p.ej. `default`). Convertimos aquí el mismo
# TOOL_DEFINITIONS (fuente única de verdad, agnóstica del LLM) al formato que
# espera `types.FunctionDeclaration`. Devolvemos dicts simples para no importar
# el SDK aquí; el orquestador los envuelve en `types.Tool`.

_GEMINI_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "object": "OBJECT",
    "array": "ARRAY",
}
# Claves del JSON-schema que conserva Gemini (el resto, p.ej. `default`, se omite).
_GEMINI_KEEP = {"type", "description", "properties", "required", "items", "enum"}


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convierte un (sub)esquema JSON-schema al esquema de Gemini (recursivo)."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _GEMINI_KEEP:
            continue  # descarta claves no soportadas (p.ej. `default`)
        if key == "type" and isinstance(value, str):
            out["type"] = _GEMINI_TYPE_MAP.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            out["properties"] = {k: _to_gemini_schema(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out["items"] = _to_gemini_schema(value)
        else:
            out[key] = value
    return out


def gemini_function_declarations() -> list[dict[str, Any]]:
    """Las mismas herramientas, declaradas para el function calling de Gemini."""
    decls: list[dict[str, Any]] = []
    for tool in TOOL_DEFINITIONS:
        schema = tool.get("input_schema", {"type": "object", "properties": {}})
        decls.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": _to_gemini_schema(schema),
        })
    return decls


class ToolDispatcher:
    """Ejecuta las herramientas sobre un BettingService.

    Mantiene la última lista de recomendaciones para que `place_paper_bet`
    pueda referirse a ellas por índice.
    """

    def __init__(self, service: BettingService) -> None:
        self.service = service
        self._last_recommendations: list[RecommendedBet] = []

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"herramienta desconocida: {name}"}
        try:
            return handler(tool_input)
        except Exception as exc:  # noqa: BLE001 — el agente debe ver el error
            return {"error": f"{type(exc).__name__}: {exc}"}

    # ───────────────────────── handlers ─────────────────────────

    def _tool_get_fixtures(self, args: dict[str, Any]) -> dict[str, Any]:
        fixtures = self.service.get_fixtures(args.get("round_name"), args.get("date_iso"))
        return {
            "count": len(fixtures),
            "fixtures": [
                {
                    "fixture_id": f.fixture_id, "date": f.date_utc.isoformat(),
                    "status": f.status_short, "round": f.round_name,
                    "home": f.home_team, "away": f.away_team,
                    "home_goals": f.home_goals, "away_goals": f.away_goals,
                }
                for f in fixtures
            ],
        }

    def _tool_analyze_round(self, args: dict[str, Any]) -> dict[str, Any]:
        analyses = self.service.analyze_round(args.get("round_name"), args.get("date_iso"))
        return {"count": len(analyses), "analyses": [_analysis_json(a) for a in analyses]}

    def _tool_find_value_bets(self, args: dict[str, Any]) -> dict[str, Any]:
        analyses = self.service.analyze_round(args.get("round_name"), args.get("date_iso"))
        vbs = self.service.collect_value_bets(analyses)
        return {"count": len(vbs), "value_bets": [_vb_json(vb) for vb in vbs]}

    def _tool_build_recommendations(self, args: dict[str, Any]) -> dict[str, Any]:
        analyses = self.service.analyze_round(args.get("round_name"), args.get("date_iso"))
        recs = self.service.build_recommendations(
            analyses,
            include_singles=bool(args.get("include_singles", True)),
            top_parlays=int(args.get("top_parlays", 5)),
        )
        self._last_recommendations = recs
        return {
            "count": len(recs),
            "bankroll_actual": self.service.store.current_bankroll(),
            "recommendations": [_rec_json(i, r) for i, r in enumerate(recs)],
            "nota": "Usa place_paper_bet con recommendation_index para registrar una.",
        }

    def _tool_place_paper_bet(self, args: dict[str, Any]) -> dict[str, Any]:
        idx = int(args["recommendation_index"])
        if not (0 <= idx < len(self._last_recommendations)):
            return {"error": f"índice {idx} fuera de rango (hay {len(self._last_recommendations)} recomendaciones)."}
        rec = self._last_recommendations[idx]
        bet_id = self.service.place_paper_bet(rec)
        return {
            "ok": True, "bet_id": bet_id, "tipo": rec.tipo, "stake": rec.stake,
            "cuota": rec.combined_odds, "edge": rec.edge,
            "bankroll_actual": self.service.store.current_bankroll(),
            "modo": "PAPER (dinero ficticio, sin colocación real)",
        }

    def _tool_settle_bets(self, args: dict[str, Any]) -> dict[str, Any]:
        settled = self.service.settle()
        return {
            "liquidadas": len(settled),
            "detalle": [{"bet_id": b, "estado": e, "payout": p} for b, e, p in settled],
            "bankroll_actual": self.service.store.current_bankroll(),
        }

    def _tool_generate_report(self, args: dict[str, Any]) -> dict[str, Any]:
        paths = self.service.export_reports()
        return {"resumen": self.service.summary_dict(), "archivos": paths,
                "consola": self.service.report_console()}


# ─────────────────────── serializadores JSON ───────────────────────

def _vb_json(vb) -> dict[str, Any]:
    return {
        "fixture_id": vb.fixture_id, "partido": f"{vb.home_team} vs {vb.away_team}",
        "mercado": vb.market, "seleccion": vb.selection,
        "cuota": round(vb.odds, 3), "prob_modelo": round(vb.model_prob, 4),
        "edge": round(vb.edge, 4),
    }


def _analysis_json(a: FixtureAnalysis) -> dict[str, Any]:
    return {
        "fixture_id": a.fixture.fixture_id,
        "partido": f"{a.fixture.home_team} vs {a.fixture.away_team}",
        "fecha": a.fixture.date_utc.isoformat(),
        "prob": {
            "home": round(a.probs.p_home, 4), "draw": round(a.probs.p_draw, 4),
            "away": round(a.probs.p_away, 4), "over25": round(a.probs.p_over_25, 4),
            "under25": round(a.probs.p_under_25, 4),
        },
        "tiene_cuotas": a.odds is not None,
        "value_bets": [_vb_json(vb) for vb in a.value_bets],
    }


def _rec_json(index: int, r: RecommendedBet) -> dict[str, Any]:
    return {
        "index": index, "tipo": r.tipo,
        "legs": [_vb_json(vb) for vb in r.legs],
        "cuota_combinada": round(r.combined_odds, 3),
        "prob_combinada": round(r.combined_prob, 4),
        "edge": round(r.edge, 4), "stake_sugerido": r.stake,
        "stake_topado": r.capped,
    }
