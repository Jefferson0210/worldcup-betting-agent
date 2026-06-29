"""Cliente de API-Football (api-sports.io, v3) con caché en disco y backoff.

Diseñado para el plan gratuito (~100 req/día):
  * Toda petición pasa primero por la caché de disco.
  * El id de la liga "World Cup" y la temporada vigente se resuelven
    dinámicamente vía /leagues (no se hardcodean ids).
  * El error 429 (rate limit) se maneja con backoff exponencial.

Si no hay clave o la API no responde, los métodos lanzan excepciones claras;
los tests NO golpean la red (usan respuestas mockeadas).
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

from config import CONFIG, Config
from src.data.cache import DiskCache
from src.data.models import Fixture, MarketOdds, TeamStats


class ApiError(RuntimeError):
    """Error al hablar con API-Football."""


class RateLimitError(ApiError):
    """Se agotó el rate limit incluso tras los reintentos."""


class ApiFootballClient:
    """Cliente fino sobre los endpoints que usa el agente."""

    def __init__(self, config: Config = CONFIG, session: Optional[requests.Session] = None) -> None:
        self.config = config
        self.cache = DiskCache(config.cache_dir, config.cache_ttl_horas)
        self.session = session or requests.Session()
        self._league_id: Optional[int] = None
        self._season: Optional[int] = None

    # ───────────────────────── núcleo HTTP ─────────────────────────

    def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = True,
    ) -> Any:
        """GET con caché + backoff. Devuelve la lista `response` de la API."""
        if use_cache:
            cached = self.cache.get(endpoint, params)
            if cached is not None:
                return cached

        self.config.validar_claves()
        url = f"{self.config.api_base_url}/{endpoint.lstrip('/')}"
        headers = {"x-apisports-key": self.config.apisports_key}

        last_exc: Optional[Exception] = None
        for intento in range(self.config.rate_limit_max_reintentos + 1):
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=30)
            except requests.RequestException as exc:  # red caída, timeout, etc.
                last_exc = exc
                time.sleep(self.config.rate_limit_backoff_base_seg * (2 ** intento))
                continue

            if resp.status_code == 429:
                # Rate limit: backoff exponencial y reintento.
                espera = self.config.rate_limit_backoff_base_seg * (2 ** intento)
                time.sleep(espera)
                continue

            if resp.status_code >= 400:
                raise ApiError(f"{endpoint} -> HTTP {resp.status_code}: {resp.text[:200]}")

            body = resp.json()
            # API-Football reporta errores aplicativos en body["errors"].
            errors = body.get("errors")
            if errors:
                # Si es por rate/quota, tratamos como 429.
                if _is_rate_error(errors):
                    espera = self.config.rate_limit_backoff_base_seg * (2 ** intento)
                    time.sleep(espera)
                    continue
                raise ApiError(f"{endpoint} -> errores API: {errors}")

            data = body.get("response", [])
            if use_cache:
                self.cache.set(endpoint, params, data)
            return data

        if last_exc is not None:
            raise ApiError(f"{endpoint} -> fallo de red tras reintentos: {last_exc}")
        raise RateLimitError(f"{endpoint} -> 429 tras {self.config.rate_limit_max_reintentos} reintentos.")

    # ─────────────────── resolución dinámica de liga ───────────────────

    def resolve_league_and_season(self) -> tuple[int, int]:
        """Resuelve (league_id, season) de la World Cup de forma dinámica.

        Busca por nombre en /leagues y elige la temporada más reciente
        marcada como `current=True`; si no hay, la de mayor año.
        Cachea el resultado en memoria para no repetir llamadas.
        """
        if self._league_id is not None and self._season is not None:
            return self._league_id, self._season

        response = self._request("leagues", {"search": self.config.liga_nombre})
        # (la temporada puede sobrescribirse con config.temporada_forzada más abajo)
        candidatos = [
            lg for lg in response
            if self.config.liga_nombre.lower() in lg.get("league", {}).get("name", "").lower()
        ] or response

        if not candidatos:
            raise ApiError(f"No se encontró la liga '{self.config.liga_nombre}' en /leagues.")

        # Preferimos la entrada de tipo 'Cup' si la hay.
        cups = [c for c in candidatos if c.get("league", {}).get("type") == "Cup"]
        elegido = (cups or candidatos)[0]
        league_id = elegido["league"]["id"]

        # Override explícito (p.ej. plan free → TEMPORADA=2022). Tiene prioridad.
        if self.config.temporada_forzada:
            season: Any = self.config.temporada_forzada
        else:
            seasons = elegido.get("seasons", []) or []
            season = None
            current = [s for s in seasons if s.get("current")]
            if current:
                season = max(s["year"] for s in current)
            elif seasons:
                season = max(s["year"] for s in seasons)
            else:
                season = self.config.temporada_fallback

        self._league_id, self._season = league_id, int(season)
        return self._league_id, self._season

    # ─────────────────────────── endpoints ───────────────────────────

    def get_fixtures(self, round_name: Optional[str] = None) -> list[Fixture]:
        """Partidos del Mundial. Si `round_name` se da, filtra por ronda."""
        league_id, season = self.resolve_league_and_season()
        params: dict[str, Any] = {"league": league_id, "season": season}
        if round_name:
            params["round"] = round_name
        response = self._request("fixtures", params)
        return [Fixture.from_api(item) for item in response]

    def get_fixtures_by_date(self, date_iso: str) -> list[Fixture]:
        """Partidos del Mundial en una fecha concreta (YYYY-MM-DD)."""
        league_id, season = self.resolve_league_and_season()
        params = {"league": league_id, "season": season, "date": date_iso}
        response = self._request("fixtures", params)
        return [Fixture.from_api(item) for item in response]

    def get_results(self, fixture_ids: list[int]) -> dict[int, Fixture]:
        """Resultados (estado actual) para una lista de fixture ids."""
        # La API admite ids separados por '-'.
        if not fixture_ids:
            return {}
        params = {"ids": "-".join(str(i) for i in fixture_ids)}
        response = self._request("fixtures", params, use_cache=False)
        out: dict[int, Fixture] = {}
        for item in response:
            fx = Fixture.from_api(item)
            out[fx.fixture_id] = fx
        return out

    def get_team_stats(self, team_id: int) -> Optional[TeamStats]:
        league_id, season = self.resolve_league_and_season()
        params = {"league": league_id, "season": season, "team": team_id}
        response = self._request("teams/statistics", params)
        if not response:
            return None
        # /teams/statistics devuelve un objeto (no lista) en `response`.
        data = response if isinstance(response, dict) else (response[0] if response else None)
        if not data:
            return None
        return TeamStats.from_api(data)

    def get_odds(self, fixture_id: int) -> Optional[MarketOdds]:
        params = {"fixture": fixture_id}
        response = self._request("odds", params)
        if not response:
            return None
        first = response[0] if isinstance(response, list) else response
        return MarketOdds.from_api(first)


def _is_rate_error(errors: Any) -> bool:
    text = str(errors).lower()
    return "rate" in text or "limit" in text or "quota" in text or "requests" in text
