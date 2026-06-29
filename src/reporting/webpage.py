"""Generador de una página HTML estática de PREDICCIONES del Mundial 2026.

Toma los partidos próximos desde OddsPapi (los mismos que usa `paper-run`),
calcula con el modelo P(1/X/2), el pick más probable y P(Over/Under 2.5), y
produce un único archivo HTML autocontenido (CSS embebido, sin dependencias) en
`reports/predicciones.html`.

Incluye, por día, una **"combinada del día"**: une el pick (resultado más
probable) de cada partido de ese día. La probabilidad combinada es el PRODUCTO de
las probabilidades de los picks (suposición de INDEPENDENCIA — ver advertencia).
Son los FAVORITOS del modelo, NO apuestas de valor: es información, no una
recomendación. Combinar multiplica el riesgo (3 favoritos al 60% ⇒ ~22%).

Honestidad: la página muestra PROBABILIDADES / análisis, NO apuestas ni tips. No
incluye cuotas como recomendación ni promesas de ganancia. Lleva un aviso visible
al pie (modelo estadístico, informativo, no asesoría, +18).

Reutiliza el modelo y los ratings activos (cargados del histórico); cuida la
cuota de OddsPapi (la lista de próximos es 1 petición cacheada; las cuotas de cada
pick se leen de la caché —no se vuelven a pedir las que ya se tienen).
"""
from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import CONFIG, Config
from src.model import ratings as ratings_mod
from src.model.historical import normalize_team
from src.model.poisson import PoissonModel

_PICK_LABEL = {"HOME": "1 (local)", "DRAW": "X (empate)", "AWAY": "2 (visitante)"}


@dataclass
class PagePrediction:
    fecha: str          # YYYY-MM-DD
    hora: str           # HH:MM UTC
    home: str
    away: str
    p_home: float
    p_draw: float
    p_away: float
    p_over: float
    p_under: float
    pick_1x2: str                      # HOME|DRAW|AWAY
    pick_odds: Optional[float] = None  # cuota real del pick (OddsPapi, si la hay)

    @property
    def pick_prob(self) -> float:
        return {"HOME": self.p_home, "DRAW": self.p_draw, "AWAY": self.p_away}[self.pick_1x2]

    @property
    def pick_team(self) -> str:
        return {"HOME": self.home, "DRAW": "empate", "AWAY": self.away}[self.pick_1x2]

    @property
    def pick_label(self) -> str:
        return f"{_PICK_LABEL[self.pick_1x2]} · {self.pick_team}"


@dataclass
class DayParlay:
    """Combinada del día: el pick de cada partido de una misma fecha."""

    fecha: str
    legs: list[PagePrediction] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.legs)

    @property
    def combined_prob(self) -> float:
        """Producto de las probabilidades de los picks (asume independencia)."""
        return math.prod(leg.pick_prob for leg in self.legs)

    @property
    def combined_odds(self) -> Optional[float]:
        """Producto de las cuotas reales de los picks, o None si falta alguna."""
        if not self.legs or any(leg.pick_odds is None for leg in self.legs):
            return None
        return math.prod(leg.pick_odds for leg in self.legs)  # type: ignore[arg-type]


def _parse_dt(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _pick(p_home: float, p_draw: float, p_away: float) -> str:
    return max({"HOME": p_home, "DRAW": p_draw, "AWAY": p_away}.items(), key=lambda kv: kv[1])[0]


def _pick_odds_from_triple(triple: Any, pick: str) -> Optional[float]:
    if triple is None:
        return None
    val = {"HOME": getattr(triple, "home", None),
           "DRAW": getattr(triple, "draw", None),
           "AWAY": getattr(triple, "away", None)}.get(pick)
    try:
        val = float(val)
    except (TypeError, ValueError):
        return None
    return val if val > 1.0 else None


def collect_predictions(
    provider: Any,
    model: PoissonModel,
    *,
    config: Config = CONFIG,
    with_odds: bool = True,
) -> list[PagePrediction]:
    """Predicciones del modelo para los partidos próximos (con equipos reales).

    Filtra los placeholders del cuadro (p.ej. "W75", "RU101"): solo incluye
    partidos cuyos dos equipos existen en los ratings activos. Las cuotas del pick
    se leen de la caché de OddsPapi (no se piden de nuevo las que ya se tienen).
    """
    fixtures = provider.list_upcoming_fixtures()  # 1 petición (cacheada)
    active = ratings_mod.get_active()
    odds_getter = getattr(provider, "historical_1x2", None) if with_odds else None
    preds: list[PagePrediction] = []

    for fx in fixtures:
        home = normalize_team(fx.get("participant1Name", ""))
        away = normalize_team(fx.get("participant2Name", ""))
        # Solo selecciones reales conocidas (descarta placeholders del bracket).
        if not home or not away:
            continue
        if active.get(home) is None or active.get(away) is None:
            continue
        dt = _parse_dt(fx.get("startTime") or fx.get("trueStartTime") or "")
        if dt is None:
            continue
        fid = abs(hash((home, away, dt.isoformat()))) % (10 ** 9)
        probs = model.probabilities(fid, home, away, neutral=config.mundial_es_neutral)
        pick = _pick(probs.p_home, probs.p_draw, probs.p_away)

        pick_odds: Optional[float] = None
        if odds_getter is not None and fx.get("fixtureId"):
            try:
                triple = odds_getter(fx.get("fixtureId"))  # cacheada
            except Exception:  # noqa: BLE001 — sin cuota no rompe la página
                triple = None
            pick_odds = _pick_odds_from_triple(triple, pick)

        preds.append(PagePrediction(
            fecha=dt.date().isoformat(), hora=dt.strftime("%H:%M") + " UTC",
            home=home, away=away,
            p_home=probs.p_home, p_draw=probs.p_draw, p_away=probs.p_away,
            p_over=probs.p_over_25, p_under=probs.p_under_25,
            pick_1x2=pick, pick_odds=pick_odds,
        ))

    preds.sort(key=lambda p: (p.fecha, p.hora, p.home))
    return preds


def build_day_parlays(preds: list[PagePrediction]) -> list[DayParlay]:
    """Agrupa las predicciones por fecha en una combinada del día por fecha."""
    by_date: dict[str, DayParlay] = {}
    for p in preds:
        by_date.setdefault(p.fecha, DayParlay(fecha=p.fecha)).legs.append(p)
    return [by_date[f] for f in sorted(by_date)]


# ─────────────────────────── HTML (autocontenido) ───────────────────────────

_CSS = """
:root{--bg:#0f1623;--card:#182233;--ink:#e8eef7;--muted:#9fb0c7;
--home:#2f80ed;--draw:#7a8aa3;--away:#eb5757;--accent:#27ae60;--warn:#f2c94c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.4}
.wrap{max-width:760px;margin:0 auto;padding:18px}
header{padding:8px 0 4px}
h1{font-size:1.4rem;margin:0 0 2px}
.sub{color:var(--muted);font-size:.85rem;margin:0 0 14px}
.day{font-size:.95rem;font-weight:700;margin:18px 2px 8px;color:var(--ink)}
.match{background:var(--card);border-radius:14px;padding:14px 16px;margin:0 0 12px;
box-shadow:0 1px 0 rgba(255,255,255,.03)}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.date{color:var(--muted);font-size:.78rem;white-space:nowrap}
.teams{font-weight:600;font-size:1.05rem;margin:2px 0 10px}
.teams .vs{color:var(--muted);font-weight:400;margin:0 6px}
.bar{display:flex;height:16px;border-radius:8px;overflow:hidden;background:#0c1320}
.bar span{display:block;height:100%}
.bar .h{background:var(--home)}.bar .d{background:var(--draw)}.bar .a{background:var(--away)}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;margin:8px 0 2px;font-size:.82rem;color:var(--muted)}
.legend b{color:var(--ink)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.dot.h{background:var(--home)}.dot.d{background:var(--draw)}.dot.a{background:var(--away)}
.meta{margin-top:8px;font-size:.86rem;color:var(--muted)}
.meta b{color:var(--ink)}
.ou{margin-top:6px;font-size:.82rem;color:var(--muted)}
.ou .val{color:var(--accent);font-weight:600}
.parlay{background:#13203a;border:1px solid #24395f;border-radius:14px;padding:14px 16px;margin:0 0 14px}
.parlay .ptitle{font-weight:700;margin-bottom:8px}
.parlay ul{margin:0 0 8px;padding-left:18px}
.parlay li{font-size:.9rem;margin:2px 0}
.parlay li b{color:var(--ink)}
.pprob{font-size:.92rem}
.pprob .val{color:var(--accent);font-weight:700}
.pprob .odds{color:var(--ink);font-weight:700}
.pwarn{margin-top:8px;font-size:.78rem;color:var(--warn);border-left:3px solid var(--warn);padding-left:8px}
footer{margin:20px 0 8px;padding:14px;border-radius:12px;background:#141d2b;
color:var(--muted);font-size:.78rem}
footer b{color:var(--ink)}
.empty{background:var(--card);border-radius:14px;padding:24px;text-align:center;color:var(--muted)}
@media(max-width:480px){.teams{font-size:.98rem}h1{font-size:1.2rem}}
"""

_DISCLAIMER = (
    "<b>Aviso:</b> predicciones de un modelo estadístico (Poisson sobre ratings "
    "internacionales), con fines <b>informativos</b>. No son apuestas, ni tips, ni "
    "asesoría financiera, ni garantía de resultado. El fútbol es incierto. Solo +18. "
    "Juego responsable."
)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}"


def _match_html(p: PagePrediction) -> str:
    h, d, a = p.p_home * 100, p.p_draw * 100, p.p_away * 100
    home, away = html.escape(p.home), html.escape(p.away)
    return (
        '<article class="match">'
        f'<div class="top"><span class="date">{p.fecha} · {p.hora}</span></div>'
        f'<div class="teams">{home}<span class="vs">vs</span>{away}</div>'
        '<div class="bar">'
        f'<span class="h" style="width:{h:.2f}%"></span>'
        f'<span class="d" style="width:{d:.2f}%"></span>'
        f'<span class="a" style="width:{a:.2f}%"></span>'
        '</div>'
        '<div class="legend">'
        f'<span><i class="dot h"></i>1 {home}: <b>{_pct(p.p_home)}%</b></span>'
        f'<span><i class="dot d"></i>X (empate): <b>{_pct(p.p_draw)}%</b></span>'
        f'<span><i class="dot a"></i>2 {away}: <b>{_pct(p.p_away)}%</b></span>'
        '</div>'
        f'<div class="meta">Resultado más probable del modelo: <b>{html.escape(p.pick_label)}</b></div>'
        f'<div class="ou">Probabilidad de <b>más de 2,5 goles</b>: '
        f'<span class="val">{_pct(p.p_over)}%</span> '
        f'(menos de 2,5: {_pct(p.p_under)}%)</div>'
        '</article>'
    )


def _parlay_html(day: DayParlay) -> str:
    legs = "".join(
        f'<li>{html.escape(leg.home)} vs {html.escape(leg.away)} → '
        f'<b>{html.escape(_PICK_LABEL[leg.pick_1x2])}</b> '
        f'({html.escape(leg.pick_team)}, {_pct(leg.pick_prob)}%)</li>'
        for leg in day.legs
    )
    prob = f'<span class="val">{_pct(day.combined_prob)}%</span>'
    odds_part = ""
    if day.combined_odds is not None:
        odds_part = (f' · Cuota combinada (informativa): '
                     f'<span class="odds">{day.combined_odds:.2f}</span>')
    return (
        '<div class="parlay">'
        f'<div class="ptitle">🎯 Combinada del día — {day.n} partidos (favoritos del modelo)</div>'
        f'<ul>{legs}</ul>'
        f'<div class="pprob">Probabilidad de que acierten <b>los {day.n}</b>: {prob}{odds_part}</div>'
        '<div class="pwarn">Son los <b>favoritos</b> del modelo (el resultado más probable '
        'de cada partido), NO apuestas de valor: es información, no una recomendación. '
        'Combinar varios partidos <b>multiplica el riesgo</b>: la probabilidad de acertar '
        'TODOS cae rápido (p.ej. 3 favoritos al 60% ⇒ ~22%). Solo análisis.</div>'
        '</div>'
    )


def render_html(days: list[DayParlay], *, generated_at: str) -> str:
    """Devuelve el HTML completo (un solo archivo, sin dependencias externas)."""
    n_matches = sum(d.n for d in days)
    if days:
        bloques: list[str] = []
        for day in days:
            bloques.append(f'<div class="day">📅 {html.escape(day.fecha)}</div>')
            for leg in day.legs:
                bloques.append(_match_html(leg))
            if day.n >= 2:  # una "combinada" necesita ≥2 partidos
                bloques.append(_parlay_html(day))
        cuerpo = "\n".join(bloques)
    else:
        cuerpo = ('<div class="empty">No hay partidos próximos con predicción ahora '
                  'mismo. Vuelve a generar la página cuando haya nuevos cruces.</div>')
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Predicciones · Mundial 2026</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
<h1>⚽ Predicciones · Mundial 2026</h1>
<p class="sub">Modelo estadístico (Poisson sobre ratings internacionales) · {n_matches} partido(s) · Actualizado el {html.escape(generated_at)}</p>
</header>
<main>
{cuerpo}
</main>
<footer>{_DISCLAIMER}</footer>
</div>
</body>
</html>
"""


def generate_page(
    provider: Any,
    model: PoissonModel,
    *,
    config: Config = CONFIG,
    out_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> tuple[Path, int]:
    """Genera/sobrescribe el HTML. Devuelve (ruta, nº de partidos)."""
    preds = collect_predictions(provider, model, config=config)
    days = build_day_parlays(preds)
    now = now or datetime.now(timezone.utc)
    generated_at = now.strftime("%Y-%m-%d %H:%M UTC")
    out = Path(out_path or (Path(config.reports_dir) / "predicciones.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(days, generated_at=generated_at), encoding="utf-8")
    return out, len(preds)
