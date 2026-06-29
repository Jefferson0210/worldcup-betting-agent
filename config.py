"""Configuración central del agente de pronóstico y apuestas paper.

Todos los parámetros son configurables. Las claves y algunos valores numéricos
se leen de variables de entorno (.env); el resto usa defaults sensatos
documentados aquí. Si algo es ambiguo, el default conservador manda.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Carga .env desde la raíz del proyecto (si existe).
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    """Parámetros del sistema.

    Atributos económicos
    ---------------------
    bankroll_inicial : capital ficticio de partida (paper trading).
    objetivo_ganancia : meta de beneficio. SOLO informativa: nunca altera el
        staking. Sirve para medir progreso, no para perseguir resultados.

    Atributos de valor / staking
    ----------------------------
    umbral_valor : edge mínimo para considerar una selección "de valor".
    kelly_fraccion : fracción de Kelly aplicada (Kelly fraccionado, p.ej. 0.25).
    stake_max_pct : tope de stake por apuesta como % del bankroll actual.
    max_legs : nº máximo de selecciones por combinada.

    Mercados / modelo
    -----------------
    mercados : mercados considerados por el motor de valor.
    liga_nombre : nombre a buscar dinámicamente en /leagues (no hardcodear id).
    """

    # ── Claves (desde .env) ─────────────────────────────────────────────
    apisports_key: str = field(default_factory=lambda: os.getenv("APISPORTS_KEY", ""))
    # Clave de Google Gemini (capa de agente). Antes era Anthropic; ahora Gemini.
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    # Token del bot de Telegram (BotFather). Necesario solo para la capa de bot.
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    # ID de Telegram del admin (para confirmar pagos manuales / comandos admin).
    admin_telegram_id: int = field(default_factory=lambda: _env_int("ADMIN_TELEGRAM_ID", 0))

    # ── Economía ────────────────────────────────────────────────────────
    bankroll_inicial: float = field(default_factory=lambda: _env_float("BANKROLL_INICIAL", 1000.0))
    objetivo_ganancia: float = field(default_factory=lambda: _env_float("OBJETIVO_GANANCIA", 500.0))

    # ── Valor / staking ─────────────────────────────────────────────────
    umbral_valor: float = field(default_factory=lambda: _env_float("UMBRAL_VALOR", 0.05))
    kelly_fraccion: float = field(default_factory=lambda: _env_float("KELLY_FRACCION", 0.25))
    stake_max_pct: float = field(default_factory=lambda: _env_float("STAKE_MAX_PCT", 0.05))
    max_legs: int = field(default_factory=lambda: _env_int("MAX_LEGS", 3))

    # ── Mercados considerados ───────────────────────────────────────────
    # Claves internas normalizadas que entiende el motor de valor.
    mercados: tuple[str, ...] = ("1X2", "OU2.5")

    # ── Modelo de probabilidad ──────────────────────────────────────────
    # Goles esperados medios por equipo y partido (prior de Mundial).
    media_goles_liga: float = 1.35
    # Factor multiplicativo de ventaja de localía sobre los goles esperados.
    # Solo aplica en partidos con sede real; los del Mundial se modelan como
    # cancha neutral (neutral=True) y NO reciben este factor.
    ventaja_local: float = 1.10
    # Peso del prior de ratings (Elo/FIFA) al regularizar fuerzas de equipo.
    # 0 = solo datos; 1 = solo prior. Útil porque hay pocos datos de selecciones.
    peso_prior_ratings: float = 0.35
    # Goles máximos por equipo al construir la matriz de marcadores.
    max_goles_matriz: int = 10

    # ── Ratings internacionales derivados de datos ──────────────────────
    # CSV histórico de resultados internacionales (formato del dataset público
    # "International football results 1872-present" de Kaggle: columnas
    # date,home_team,away_team,home_score,away_score,tournament,city,country,neutral).
    # Coloca el CSV en esta ruta para que el modelo use ratings reales en vez del
    # prior Elo de relleno. Si no existe, se usa el prior por defecto.
    historical_csv: Path = PROJECT_ROOT / "data" / "results.csv"
    # Vida media (en días) del decaimiento temporal: a esta antigüedad un partido
    # pesa la mitad. ~4 años por defecto (relevante para ciclos de selecciones).
    ratings_half_life_dias: float = field(
        default_factory=lambda: _env_float("RATINGS_HALF_LIFE_DIAS", 1460.0)
    )
    # Iteraciones del ajuste iterativo ataque/defensa (converge rápido).
    ratings_iteraciones: int = field(
        default_factory=lambda: _env_int("RATINGS_ITERACIONES", 50)
    )
    # Cargar ratings desde historical_csv automáticamente al crear el servicio.
    cargar_ratings_historicos: bool = True
    # Los partidos del Mundial se tratan como cancha neutral por defecto.
    mundial_es_neutral: bool = True

    # ── Cuotas históricas para el ROI REAL del backtest ─────────────────
    # CSV local con cuotas 1X2 reales (cierre o pre-partido). Columnas mínimas:
    #   date, home_team, away_team, odds_home, odds_draw, odds_away
    # (los nombres de columna se autodetectan; ver src/model/odds_history.py).
    odds_csv: Path = PROJECT_ROOT / "data" / "odds.csv"
    # Clave de la API de cuotas (sirve para OddsPapi o The Odds API). Vacío = no
    # se usa la API (el backtest usará data/odds.csv si existe).
    odds_api_key: str = field(default_factory=lambda: os.getenv("ODDS_API_KEY", ""))
    # Proveedor de cuotas: "oddspapi" | "the_odds_api" | "auto" (auto = oddspapi).
    odds_provider: str = field(default_factory=lambda: os.getenv("ODDS_PROVIDER", "auto"))

    # — The Odds API (https://the-odds-api.com) —
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    odds_api_regions: str = "eu"
    odds_api_sport: str = "soccer_fifa_world_cup"

    # — OddsPapi (https://oddspapi.io) —
    # Histórico de cuotas desde ~ene-2026; cubre internacionales (World Cup id 16,
    # eliminatorias, Euro, Copa América...). 1X2 = mercado "101" (Home/Draw/Away
    # = outcomes 101/102/103). Tier gratuito: 250 req/mes -> usar con cabeza.
    oddspapi_base_url: str = "https://api.oddspapi.io"
    oddspapi_sport_id: int = 10                 # fútbol
    oddspapi_tournament_id: int = 16            # World Cup
    oddspapi_bookmakers: str = "pinnacle,bet365"  # máx 3 casas por petición

    # ── Fuente de datos ─────────────────────────────────────────────────
    api_base_url: str = "https://v3.football.api-sports.io"
    liga_nombre: str = "World Cup"
    # Si el plan no permite resolver temporada vigente, fallback:
    temporada_fallback: int = 2026
    # Override de temporada (env TEMPORADA). Útil con el plan GRATUITO de
    # API-Football, que solo cubre 2022–2024: pon TEMPORADA=2022 para probar el
    # producto con el Mundial 2022. 0 = sin override (resolución dinámica).
    temporada_forzada: int = field(default_factory=lambda: _env_int("TEMPORADA", 0))
    # Caché y rate-limit
    cache_dir: Path = PROJECT_ROOT / ".cache"
    cache_ttl_horas: int = 24
    rate_limit_max_reintentos: int = 4
    rate_limit_backoff_base_seg: float = 2.0

    # ── Persistencia ────────────────────────────────────────────────────
    db_path: Path = PROJECT_ROOT / "data" / "betting.db"
    reports_dir: Path = PROJECT_ROOT / "reports"

    # ── Modelos LLM (capa agente) — Google Gemini ───────────────────────
    # Se usa un modelo *Flash* porque el tier gratuito de Gemini cubre Flash y
    # Flash-Lite (los Pro son de pago). Alternativa más nueva: "gemini-3-flash".
    modelo_orquestador: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    )
    modelo_barato: str = "gemini-2.5-flash-lite"

    # ── Bot de Telegram / suscripciones ─────────────────────────────────
    # Duración (días) que otorga una activación de suscripción.
    suscripcion_dias: int = field(default_factory=lambda: _env_int("SUSCRIPCION_DIAS", 30))
    # Tier por defecto al activar.
    suscripcion_tier_default: str = "pro"
    # Edad mínima legal para usar el producto (verificación 18+).
    edad_minima: int = 18
    # Fecha/hora a la que el scheduler publica recomendaciones (hora local del server).
    publicar_hora: int = field(default_factory=lambda: _env_int("PUBLICAR_HORA", 9))
    publicar_minuto: int = field(default_factory=lambda: _env_int("PUBLICAR_MINUTO", 0))
    # Cada cuántos minutos el scheduler intenta liquidar apuestas pendientes.
    liquidar_cada_min: int = field(default_factory=lambda: _env_int("LIQUIDAR_CADA_MIN", 60))
    # Nº máximo de recomendaciones premium publicadas por jornada.
    max_recs_publicadas: int = field(default_factory=lambda: _env_int("MAX_RECS_PUBLICADAS", 5)
    )

    def validar_claves(self, *, requiere_gemini: bool = False) -> None:
        """Lanza si faltan claves necesarias. Mensajes claros para el usuario."""
        if not self.apisports_key:
            raise RuntimeError(
                "Falta APISPORTS_KEY. Copia .env.example a .env y pon tu clave de API-Football."
            )
        if requiere_gemini and not self.gemini_api_key:
            raise RuntimeError(
                "Falta GEMINI_API_KEY. Necesaria para la capa de agente (src/agent)."
            )


# Instancia única reutilizable.
CONFIG = Config()
