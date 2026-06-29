"""Orquestador con Google Gemini (google-genai) vía function calling.

El agente recibe una instrucción en lenguaje natural (p.ej. "analiza los
partidos de mañana y recomienda qué jugar") y, mediante un bucle de function
calling, llama a las herramientas deterministas de `ToolDispatcher`. El LLM
razona y redacta; NUNCA hace la matemática (eso vive en Python).

Modelo: un *Flash* de Gemini (tier gratuito), configurable en `config.py`. La
capa es opcional: el resto del sistema funciona sin Gemini (vía main.py).

Bucle de function calling
-------------------------
1. Se envía la conversación + las declaraciones de función (las mismas tools).
2. Si el modelo pide llamadas a función, se ejecutan en Python (deterministas) y
   se devuelven los resultados como `function_response`.
3. Se repite hasta que el modelo responde con texto final (sin más llamadas).

El desactivado del *automatic function calling* del SDK es deliberado: queremos
controlar el bucle nosotros para mantener la matemática y la auditoría en código.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from config import CONFIG, Config
from src.agent.tools import ToolDispatcher, gemini_function_declarations
from src.service import BettingService

SYSTEM_PROMPT = """\
Eres un agente analista de apuestas deportivas para el Mundial 2026, operando \
EXCLUSIVAMENTE en modo recomendación / paper trading (dinero ficticio). \
NUNCA colocas apuestas reales ni automatizas navegadores: eso está fuera de alcance.

Tu trabajo:
1. Traer partidos y cuotas con las herramientas.
2. Obtener probabilidades del modelo y detectar valor (edge > umbral).
3. Construir combinadas SOLO con selecciones que individualmente son de valor.
4. Razonar cuál(es) recomendar, considerando contexto (lesiones, bajas, \
   correlación entre legs de combinadas).
5. Registrar la(s) apuesta(s) paper elegidas y redactar una recomendación clara.
6. Cuando se pida, liquidar y redactar el reporte.

Reglas no negociables:
- Toda la matemática (probabilidades, edge, Kelly, payout) la hacen las \
  herramientas en Python. Tú NO recalculas números; usas los que devuelven.
- El staking es Kelly fraccionado con tope. NUNCA sugieras subir el stake para \
  "alcanzar el objetivo": el objetivo es solo informativo.
- Las combinadas asumen independencia entre legs; advierte sobre la correlación \
  y sobre que acumulan el margen de la casa (valor esperado tiende a negativo).
- Recuerda al usuario que es una herramienta educativa, no asesoría financiera.

Sé conciso y concreto. Explica el porqué de cada recomendación con sus números."""


class BettingAgent:
    """Bucle de orquestación con function calling de Gemini."""

    def __init__(
        self,
        config: Config = CONFIG,
        service: Optional[BettingService] = None,
        max_turns: int = 12,
        *,
        client: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.service = service or BettingService(config)
        self.dispatcher = ToolDispatcher(self.service)
        self.max_turns = max_turns

        if client is not None:
            # Inyección para tests (cliente mockeado, sin red ni clave).
            self.client = client
        else:
            self.config.validar_claves(requiere_gemini=True)
            # Import diferido para no exigir el SDK si no se usa el agente.
            from google import genai

            self.client = genai.Client(api_key=config.gemini_api_key)

    # ───────────────────────────── bucle ─────────────────────────────

    def _build_config(self) -> Any:
        from google.genai import types

        tool = types.Tool(function_declarations=gemini_function_declarations())
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[tool],
            # Controlamos el bucle a mano (sin ejecución automática del SDK).
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.3,
        )

    def run(self, user_instruction: str, *, verbose: bool = True) -> str:
        """Ejecuta el bucle agente y devuelve la respuesta final en texto."""
        from google.genai import types

        contents: list[Any] = [
            types.Content(role="user", parts=[types.Part.from_text(text=user_instruction)])
        ]
        gen_config = self._build_config()

        for _turn in range(self.max_turns):
            response = self.client.models.generate_content(
                model=self.config.modelo_orquestador,
                contents=contents,
                config=gen_config,
            )

            parts = _response_parts(response)
            calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

            if not calls:
                # Respuesta final: concatena los bloques de texto.
                return _extract_text(parts)

            # Acumula el turno del modelo (con las function_call) y resuelve cada una.
            contents.append(_response_content(response))
            response_parts: list[Any] = []
            for fc in calls:
                args = dict(fc.args) if fc.args else {}
                if verbose:
                    print(f"  · herramienta: {fc.name}({json.dumps(args, ensure_ascii=False, default=str)})")
                result = self.dispatcher.dispatch(fc.name, args)
                response_parts.append(
                    types.Part.from_function_response(name=fc.name, response=_as_response(result))
                )
            contents.append(types.Content(role="user", parts=response_parts))

        return ("Se alcanzó el límite de turnos del agente sin respuesta final. "
                "Revisa la instrucción o auméntalo (max_turns).")


# ─────────────────────────── helpers ───────────────────────────

def _response_content(response: Any) -> Any:
    """Devuelve el `Content` del primer candidato de la respuesta."""
    return response.candidates[0].content


def _response_parts(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    content = getattr(candidates[0], "content", None)
    return list(getattr(content, "parts", None) or [])


def _extract_text(parts: list[Any]) -> str:
    texts = [p.text for p in parts if getattr(p, "text", None)]
    return "\n".join(texts).strip() or "(el agente no devolvió texto)"


def _as_response(result: Any) -> dict[str, Any]:
    """Gemini espera un dict en `function_response.response`."""
    if isinstance(result, dict):
        return result
    return {"result": result}
