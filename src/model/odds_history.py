"""Cuotas históricas 1X2 para calcular el **ROI real** del backtest.

Dos fuentes, la que el usuario tenga (ver `build_odds_book`):

  * **CSV local** (`data/odds.csv`): columnas mínimas
        date, home_team, away_team, odds_home, odds_draw, odds_away
    Los nombres de columna se **autodetectan** (acepta alias comunes) o se pasan
    explícitos con `column_map`.
  * **API (The Odds API)**: si hay `ODDS_API_KEY`, se pueden pullar cuotas
    históricas con caché en disco + backoff. El proveedor está **desacoplado**
    (`OddsProvider`) para poder cambiarlo.

Emparejado
----------
Las cuotas y `results.csv` vienen de fuentes distintas: los nombres NO coinciden
exactos. `OddsBook.lookup` normaliza (alias + comparación difusa) y empareja por
**fecha (±1 día) + equipos**, probando también la orientación invertida
(local/visitante intercambiados). Reporta emparejados vs no emparejados.
"""
from __future__ import annotations

import csv
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional

from config import CONFIG, Config
from src.model.historical import normalize_team

# Alias de nombres de columnas del CSV de cuotas (todo en minúsculas, sin
# espacios alrededor). Cada destino lista las variantes aceptadas.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "fecha", "match_date", "commence_time", "datetime", "kickoff"),
    "home_team": ("home_team", "home", "hometeam", "local", "team_home", "home_name"),
    "away_team": ("away_team", "away", "awayteam", "visitante", "team_away", "away_name"),
    "odds_home": ("odds_home", "home_odds", "odd_home", "1", "home_win", "b365h",
                  "psh", "avgh", "oddh", "cuota_home", "home_price"),
    "odds_draw": ("odds_draw", "draw_odds", "odd_draw", "x", "draw", "b365d",
                  "psd", "avgd", "oddd", "cuota_draw", "draw_price"),
    "odds_away": ("odds_away", "away_odds", "odd_away", "2", "away_win", "b365a",
                  "psa", "avga", "odda", "cuota_away", "away_price"),
}

_MATCH_THRESHOLD = 0.82  # similitud mínima de nombre para aceptar un emparejado


# ───────────────────────────── modelos ─────────────────────────────

@dataclass(frozen=True)
class OddsTriple:
    """Cuotas decimales 1X2."""

    home: float
    draw: float
    away: float

    def swapped(self) -> "OddsTriple":
        """Invierte local/visitante (empate intacto)."""
        return OddsTriple(home=self.away, draw=self.draw, away=self.home)

    def valid(self) -> bool:
        return all(o > 1.0 for o in (self.home, self.draw, self.away))


@dataclass(frozen=True)
class OddsEntry:
    fecha: date
    home: str            # normalizado
    away: str            # normalizado
    triple: OddsTriple
    home_raw: str = ""
    away_raw: str = ""


@dataclass
class MatchStats:
    matched: int = 0
    unmatched: int = 0


# ───────────────────────── normalización difusa ─────────────────────────

def _canon(name: str) -> str:
    """Forma canónica para comparar: alias + minúsculas + sin acentos/puntuación."""
    name = normalize_team(name)
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    out = "".join(c.lower() if c.isalnum() else " " for c in ascii_name)
    return " ".join(out.split())


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


# ─────────────────────────────── OddsBook ───────────────────────────────

class OddsBook:
    """Índice de cuotas históricas con emparejado por fecha + equipos."""

    def __init__(self, entries: Iterable[OddsEntry]) -> None:
        self.entries: list[OddsEntry] = list(entries)
        self._by_date: dict[date, list[OddsEntry]] = {}
        for e in self.entries:
            self._by_date.setdefault(e.fecha, []).append(e)
        self.stats = MatchStats()

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(
        self,
        fecha: date,
        home: str,
        away: str,
        *,
        day_tol: int = 1,
        threshold: float = _MATCH_THRESHOLD,
        record: bool = False,
    ) -> Optional[OddsTriple]:
        """Devuelve las cuotas (orientadas a `home`/`away`) o None si no empareja.

        Prueba la orientación directa e invertida; toma el mejor emparejado por
        encima del umbral. Con `record=True` actualiza `self.stats`.
        """
        nhome, naway = _canon(home), _canon(away)
        best: Optional[OddsTriple] = None
        best_score = threshold

        for delta in range(-day_tol, day_tol + 1):
            for e in self._by_date.get(fecha + timedelta(days=delta), ()):  # noqa: B020
                eh, ea = _canon(e.home), _canon(e.away)
                s_direct = min(_similarity(nhome, eh), _similarity(naway, ea))
                if s_direct > best_score or (best is None and s_direct >= threshold):
                    best_score, best = s_direct, e.triple
                s_swap = min(_similarity(nhome, ea), _similarity(naway, eh))
                if s_swap > best_score:
                    best_score, best = s_swap, e.triple.swapped()

        if record:
            if best is not None:
                self.stats.matched += 1
            else:
                self.stats.unmatched += 1
        return best


# ─────────────────────────── carga desde CSV ───────────────────────────

def _build_column_map(
    fieldnames: list[str], column_map: Optional[dict[str, str]] = None
) -> dict[str, str]:
    """Resuelve qué columna del CSV corresponde a cada campo lógico."""
    lower = {fn.strip().lower(): fn for fn in fieldnames}
    resolved: dict[str, str] = {}
    if column_map:
        # column_map: campo_logico -> nombre_real_de_columna (explícito).
        for logical, real in column_map.items():
            if real in fieldnames:
                resolved[logical] = real
            elif real.strip().lower() in lower:
                resolved[logical] = lower[real.strip().lower()]
    for logical, aliases in _COLUMN_ALIASES.items():
        if logical in resolved:
            continue
        for alias in aliases:
            if alias in lower:
                resolved[logical] = lower[alias]
                break
    return resolved


def _parse_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw:
        return None
    # ISO con hora (commence_time) -> recorta a fecha.
    head = raw.replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            from datetime import datetime
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(raw: Any) -> Optional[float]:
    try:
        v = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return v if v > 1.0 else None


def parse_odds_rows(
    rows: Iterable[dict[str, str]],
    column_map: Optional[dict[str, str]] = None,
    *,
    fieldnames: Optional[list[str]] = None,
) -> list[OddsEntry]:
    """Convierte filas del CSV en OddsEntry válidos (salta las incompletas)."""
    rows = list(rows)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    cols = _build_column_map(fieldnames, column_map)
    required = {"date", "home_team", "away_team", "odds_home", "odds_draw", "odds_away"}
    missing = required - set(cols)
    if missing:
        raise ValueError(
            f"El CSV de cuotas no tiene columnas para: {sorted(missing)}. "
            f"Columnas detectadas: {sorted(cols)}. Usa column_map para mapearlas."
        )

    out: list[OddsEntry] = []
    for row in rows:
        fecha = _parse_date(row.get(cols["date"], ""))
        oh = _parse_float(row.get(cols["odds_home"]))
        od = _parse_float(row.get(cols["odds_draw"]))
        oa = _parse_float(row.get(cols["odds_away"]))
        home_raw = (row.get(cols["home_team"]) or "").strip()
        away_raw = (row.get(cols["away_team"]) or "").strip()
        if fecha is None or oh is None or od is None or oa is None:
            continue
        if not home_raw or not away_raw:
            continue
        out.append(OddsEntry(
            fecha=fecha,
            home=normalize_team(home_raw),
            away=normalize_team(away_raw),
            triple=OddsTriple(oh, od, oa),
            home_raw=home_raw, away_raw=away_raw,
        ))
    return out


def write_odds_csv(book: OddsBook, path: str | Path) -> Path:
    """Vuelca un OddsBook a CSV con el esquema estándar (date,home,away,1,X,2)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "home_team", "away_team", "odds_home", "odds_draw", "odds_away"])
        for e in sorted(book.entries, key=lambda x: (x.fecha, x.home)):
            w.writerow([e.fecha.isoformat(), e.home_raw or e.home, e.away_raw or e.away,
                        f"{e.triple.home:.4f}", f"{e.triple.draw:.4f}", f"{e.triple.away:.4f}"])
    return path


def load_odds_csv(
    path: str | Path, column_map: Optional[dict[str, str]] = None
) -> OddsBook:
    """Carga el CSV de cuotas a un OddsBook. Lanza FileNotFoundError si no existe."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el CSV de cuotas en {path}.")
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        entries = parse_odds_rows(reader, column_map, fieldnames=reader.fieldnames or [])
    return OddsBook(entries)


# ─────────────────── proveedor de cuotas (desacoplado) ───────────────────

class OddsProvider:
    """Interfaz de un proveedor de cuotas históricas (cambiable)."""

    name = "abstract"

    def build_book(self, *, desde: Optional[date] = None, hasta: Optional[date] = None) -> OddsBook:
        raise NotImplementedError


class TheOddsApiProvider(OddsProvider):
    """Pull de cuotas históricas 1X2 desde The Odds API, con caché + backoff.

    Endpoint histórico: /v4/historical/sports/{sport}/odds (mercado h2h). El tier
    gratuito es limitado; por eso se cachea cada snapshot en disco. La lógica de
    paginación por fechas se deja como punto de extensión documentado.

    ⚠️ Requiere ODDS_API_KEY. Sin clave, no se instancia.
    """

    name = "the_odds_api"

    def __init__(self, config: Config = CONFIG, session: Any = None) -> None:
        if not config.odds_api_key:
            raise RuntimeError("Falta ODDS_API_KEY para usar The Odds API.")
        import requests

        from src.data.cache import DiskCache

        self.config = config
        self.session = session or requests.Session()
        self.cache = DiskCache(config.cache_dir / "odds", config.cache_ttl_horas)

    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        cache_params = {k: v for k, v in params.items() if k != "apiKey"}
        cached = self.cache.get(endpoint, cache_params, ignore_ttl=True)
        if cached is not None:
            return cached
        url = f"{self.config.odds_api_base_url}/{endpoint.lstrip('/')}"
        for intento in range(self.config.rate_limit_max_reintentos + 1):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(self.config.rate_limit_backoff_base_seg * (2 ** intento))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"The Odds API {endpoint} -> HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            self.cache.set(endpoint, cache_params, data)
            return data
        raise RuntimeError(f"The Odds API {endpoint} -> 429 tras reintentos.")

    @staticmethod
    def _entries_from_snapshot(data: Any) -> list[OddsEntry]:
        """Normaliza un snapshot histórico (h2h) a OddsEntry."""
        events = data.get("data", data) if isinstance(data, dict) else data
        out: list[OddsEntry] = []
        for ev in events or []:
            home_raw = ev.get("home_team", "")
            away_raw = ev.get("away_team", "")
            commence = ev.get("commence_time", "")
            fecha = _parse_date(commence)
            if not (home_raw and away_raw and fecha):
                continue
            # Promedia las cuotas h2h entre casas.
            acc: dict[str, list[float]] = {"home": [], "draw": [], "away": []}
            for bm in ev.get("bookmakers", []) or []:
                for market in bm.get("markets", []) or []:
                    if market.get("key") != "h2h":
                        continue
                    for oc in market.get("outcomes", []) or []:
                        nm = (oc.get("name") or "").strip().lower()
                        price = oc.get("price")
                        if price is None:
                            continue
                        if nm == home_raw.strip().lower():
                            acc["home"].append(float(price))
                        elif nm == away_raw.strip().lower():
                            acc["away"].append(float(price))
                        elif nm in {"draw", "tie"}:
                            acc["draw"].append(float(price))
            if not (acc["home"] and acc["draw"] and acc["away"]):
                continue
            triple = OddsTriple(
                home=sum(acc["home"]) / len(acc["home"]),
                draw=sum(acc["draw"]) / len(acc["draw"]),
                away=sum(acc["away"]) / len(acc["away"]),
            )
            out.append(OddsEntry(
                fecha=fecha, home=normalize_team(home_raw), away=normalize_team(away_raw),
                triple=triple, home_raw=home_raw, away_raw=away_raw,
            ))
        return out

    def build_book(self, *, desde: Optional[date] = None, hasta: Optional[date] = None) -> OddsBook:  # pragma: no cover - red
        """Construye el OddsBook desde snapshots históricos.

        Implementación mínima: un snapshot por la fecha `hasta` (o hoy). Para un
        backtest amplio, extiende esto iterando snapshots por fecha y cacheando
        cada uno (el límite del tier gratuito obliga a ser parsimonioso).
        """
        snap_date = (hasta or date.today()).isoformat() + "T12:00:00Z"
        data = self._get(
            f"historical/sports/{self.config.odds_api_sport}/odds",
            {"apiKey": self.config.odds_api_key, "regions": self.config.odds_api_regions,
             "markets": "h2h", "oddsFormat": "decimal", "date": snap_date},
        )
        return OddsBook(self._entries_from_snapshot(data))


# ─────────────────────────── OddsPapi (oddspapi.io) ───────────────────────────

# 1X2 (Full Time Result) en OddsPapi: mercado "101"; outcomes 101=Home, 102=Draw,
# 103=Away. La respuesta de /v4/historical-odds es una serie temporal de precios.
ODDSPAPI_MARKET_1X2 = "101"
ODDSPAPI_OUTCOMES_1X2 = {"home": "101", "draw": "102", "away": "103"}


def _closing_price(snapshots: Any) -> Optional[float]:
    """Precio de cierre = última cotización activa (mayor createdAt) de la serie."""
    if not isinstance(snapshots, list) or not snapshots:
        return None
    actives = [s for s in snapshots if isinstance(s, dict) and s.get("active")]
    pool = actives or [s for s in snapshots if isinstance(s, dict)]
    if not pool:
        return None
    last = max(pool, key=lambda s: str(s.get("createdAt", "")))
    try:
        price = float(last.get("price"))
    except (TypeError, ValueError):
        return None
    return price if price > 1.0 else None


def _outcome_closing(outcome: Any) -> Optional[float]:
    """Precio de cierre de un outcome (atraviesa players -> serie)."""
    if not isinstance(outcome, dict):
        return None
    players = outcome.get("players")
    if not isinstance(players, dict):
        return None
    precios = [p for p in (_closing_price(serie) for serie in players.values()) if p]
    if not precios:
        return None
    return sum(precios) / len(precios)


def parse_oddspapi_1x2(
    data: dict[str, Any],
    *,
    bookmakers: Optional[Iterable[str]] = None,
) -> Optional[OddsTriple]:
    """Extrae el 1X2 de cierre de una respuesta /v4/historical-odds.

    Promedia el precio de cierre entre las casas presentes (o las indicadas).
    Devuelve None si falta alguna de las tres cotizaciones (Home/Draw/Away).
    Función pura -> testeable sin red.
    """
    books = data.get("bookmakers")
    if not isinstance(books, dict):
        return None
    allow = {b.lower() for b in bookmakers} if bookmakers else None
    acc: dict[str, list[float]] = {"home": [], "draw": [], "away": []}

    for slug, bdata in books.items():
        if allow is not None and slug.lower() not in allow:
            continue
        markets = (bdata or {}).get("markets", {}) if isinstance(bdata, dict) else {}
        market = markets.get(ODDSPAPI_MARKET_1X2)
        if not isinstance(market, dict):
            continue
        outcomes = market.get("outcomes", {})
        for lado, code in ODDSPAPI_OUTCOMES_1X2.items():
            price = _outcome_closing(outcomes.get(code))
            if price:
                acc[lado].append(price)

    if not (acc["home"] and acc["draw"] and acc["away"]):
        return None
    return OddsTriple(
        home=sum(acc["home"]) / len(acc["home"]),
        draw=sum(acc["draw"]) / len(acc["draw"]),
        away=sum(acc["away"]) / len(acc["away"]),
    )


class OddsPapiProvider(OddsProvider):
    """Proveedor de cuotas históricas 1X2 de OddsPapi (oddspapi.io).

    Cobertura: internacionales (World Cup, eliminatorias, Euro, Copa América…),
    con histórico de cuotas desde ~enero 2026. Auth por query param `apiKey`.
    Caché en disco + backoff; el tier gratuito son 250 req/mes, así que el pull
    de un libro completo se hace bajo demanda (comando explícito), no en cada
    backtest.
    """

    name = "oddspapi"

    def __init__(self, config: Config = CONFIG, session: Any = None) -> None:
        if not config.odds_api_key:
            raise RuntimeError("Falta ODDS_API_KEY para usar OddsPapi.")
        import requests

        from src.data.cache import DiskCache

        self.config = config
        self.session = session or requests.Session()
        self.cache = DiskCache(config.cache_dir / "oddspapi", config.cache_ttl_horas)

    def _get(
        self, endpoint: str, params: dict[str, Any], *, use_cache: bool = True, allow_404: bool = False
    ) -> Any:
        cache_params = {k: v for k, v in params.items() if k != "apiKey"}
        if use_cache:
            cached = self.cache.get(endpoint, cache_params, ignore_ttl=True)
            if cached is not None:
                return cached
        url = f"{self.config.oddspapi_base_url}/{endpoint.lstrip('/')}"
        for intento in range(self.config.rate_limit_max_reintentos + 1):
            resp = self.session.get(url, params={**params, "apiKey": self.config.odds_api_key}, timeout=40)
            if resp.status_code == 429:
                time.sleep(self.config.rate_limit_backoff_base_seg * (2 ** intento))
                continue
            # Partido sin cuotas todavía: lo cacheamos como "no hay" para no
            # volver a pedirlo (ahorro de cuota) y devolvemos un centinela.
            if resp.status_code == 404 and allow_404:
                sentinel = {"__not_found__": True}
                if use_cache:
                    self.cache.set(endpoint, cache_params, sentinel)
                return sentinel
            if resp.status_code >= 400:
                raise RuntimeError(f"OddsPapi {endpoint} -> HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            if use_cache:
                self.cache.set(endpoint, cache_params, data)
            return data
        raise RuntimeError(f"OddsPapi {endpoint} -> 429 tras reintentos.")

    def list_fixtures(
        self, tournament_id: Optional[int] = None, *, status_id: int = 2
    ) -> list[dict[str, Any]]:
        """Fixtures de un torneo filtrados por estado (1 petición).

        statusId: 0=no empezados, 1=en vivo, 2=finalizados, 3=cancelados.
        """
        tid = tournament_id or self.config.oddspapi_tournament_id
        data = self._get("v4/fixtures", {
            "sportId": self.config.oddspapi_sport_id, "tournamentId": tid, "statusId": status_id,
        })
        items = data if isinstance(data, list) else (data.get("data") or data.get("fixtures") or [])
        return list(items.values()) if isinstance(items, dict) else list(items)

    def list_finished_fixtures(self, tournament_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Fixtures FINALIZADOS (statusId=2) de un torneo (1 petición)."""
        return self.list_fixtures(tournament_id, status_id=2)

    def list_upcoming_fixtures(self, tournament_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Fixtures NO EMPEZADOS (statusId=0) de un torneo (1 petición)."""
        return self.list_fixtures(tournament_id, status_id=0)

    def historical_1x2(self, fixture_id: str, *, bookmakers: Optional[str] = None) -> Optional[OddsTriple]:
        """Cuotas 1X2 de cierre de un fixture (1 petición, cacheada).

        Devuelve None si el partido aún no tiene cuotas (404) o no trae 1X2.
        """
        bms = bookmakers or self.config.oddspapi_bookmakers
        data = self._get(
            "v4/historical-odds", {"fixtureId": fixture_id, "bookmakers": bms}, allow_404=True
        )
        if not isinstance(data, dict) or data.get("__not_found__"):
            return None
        return parse_oddspapi_1x2(data, bookmakers=[b.strip() for b in bms.split(",")])

    def build_book(  # noqa: D417
        self,
        *,
        desde: Optional[date] = None,
        hasta: Optional[date] = None,
        tournament_id: Optional[int] = None,
        max_fixtures: Optional[int] = None,
        bookmakers: Optional[str] = None,
        on_progress: Any = None,
    ) -> OddsBook:
        """Construye un OddsBook bajando 1X2 de cada fixture finalizado.

        ⚠️ Gasta ~1 petición por fixture: úsalo con cabeza (250 req/mes). Filtra
        con `desde`/`hasta` y `max_fixtures` para acotar el gasto. Cachea cada
        respuesta, así re-ejecutar no vuelve a gastar.
        """
        fixtures = self.list_finished_fixtures(tournament_id)
        entries: list[OddsEntry] = []
        usados = 0
        for fx in fixtures:
            fecha = _parse_date(fx.get("startTime") or fx.get("trueStartTime") or "")
            if fecha is None:
                continue
            if desde and fecha < desde:
                continue
            if hasta and fecha > hasta:
                continue
            if max_fixtures is not None and usados >= max_fixtures:
                break
            fid = fx.get("fixtureId")
            if not fid:
                continue
            triple = self.historical_1x2(fid, bookmakers=bookmakers)
            usados += 1
            if on_progress:
                on_progress(usados, fx, triple)
            if triple is None or not triple.valid():
                continue
            entries.append(OddsEntry(
                fecha=fecha,
                home=normalize_team(fx.get("participant1Name", "")),
                away=normalize_team(fx.get("participant2Name", "")),
                triple=triple,
                home_raw=fx.get("participant1Name", ""),
                away_raw=fx.get("participant2Name", ""),
            ))
        return OddsBook(entries)


# ───────────────────────── selección de fuente ─────────────────────────

def make_provider(config: Config = CONFIG) -> OddsProvider:
    """Instancia el proveedor de cuotas según `config.odds_provider`.

    "auto" -> OddsPapi (el proveedor por defecto del proyecto).
    """
    elegido = (config.odds_provider or "auto").strip().lower()
    if elegido in {"oddspapi", "auto"}:
        return OddsPapiProvider(config)
    if elegido in {"the_odds_api", "theoddsapi", "the-odds-api"}:
        return TheOddsApiProvider(config)
    raise ValueError(f"Proveedor de cuotas desconocido: {config.odds_provider!r}")


def build_odds_book(
    config: Config = CONFIG,
    *,
    csv_path: Optional[str | Path] = None,
    column_map: Optional[dict[str, str]] = None,
) -> Optional[OddsBook]:
    """Devuelve un OddsBook para el backtest, SIN gastar cuota por sorpresa.

    Solo usa el CSV local (`data/odds.csv`). El pull desde una API (que gasta
    peticiones) se hace con el comando explícito `fetch-odds`, que vuelca el CSV.
    Devuelve None si no hay CSV (el backtest seguirá sin ROI real).
    """
    path = Path(csv_path or config.odds_csv)
    if path.exists():
        return load_odds_csv(path, column_map)
    return None
