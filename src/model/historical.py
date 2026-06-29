"""Loader del histórico de partidos internacionales de selecciones.

Lee el dataset público "International football results 1872-present" (Kaggle),
un CSV con columnas:

    date,home_team,away_team,home_score,away_score,tournament,city,country,neutral

`neutral` es "True"/"False" (cancha neutral). Este histórico es la base para
aprender la fuerza ofensiva/defensiva y el Elo de cada selección (ver
`src/model/ratings.py`), en vez de depender de los pocos partidos del Mundial.

El parseo usa el módulo `csv` de la stdlib (sin red, robusto y testeable). Los
nombres de selección se normalizan a las grafías que usa el resto del sistema
(p.ej. API-Football) mediante `TEAM_ALIASES`.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

# Alias de nombres de selección: nombre del dataset -> nombre canónico usado en
# el resto del proyecto (consistente con API-Football y ratings.DEFAULT_ELO).
# Solo se listan los que difieren; cualquier otro nombre se usa tal cual.
TEAM_ALIASES: dict[str, str] = {
    "United States": "USA",
    "South Korea": "Korea Republic",
    "North Korea": "Korea DPR",
    "Ivory Coast": "Côte d'Ivoire",
    "IR Iran": "Iran",
    "Cape Verde": "Cape Verde Islands",
    "Czechia": "Czech Republic",
    "China PR": "China",
    "DR Congo": "Congo DR",
    "Republic of Ireland": "Ireland",
    "Chinese Taipei": "Taiwan",
    # Variantes habituales en fuentes de CUOTAS -> grafía del dataset/canónica.
    # (No remapear nombres que el dataset ya usa tal cual, p.ej. "Czech Republic"
    #  o "North Macedonia", para no romper las claves de ratings.)
    "Korea South": "Korea Republic",
    "Korea North": "Korea DPR",
    "Türkiye": "Turkey",
    "Turkiye": "Turkey",
    "Cabo Verde": "Cape Verde Islands",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
}


def normalize_team(name: str) -> str:
    """Normaliza el nombre de una selección a su grafía canónica."""
    name = (name or "").strip()
    return TEAM_ALIASES.get(name, name)


@dataclass(frozen=True)
class HistMatch:
    """Un partido internacional histórico ya normalizado."""

    fecha: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    tournament: str
    neutral: bool

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals


def _parse_bool(raw: str) -> bool:
    return str(raw).strip().lower() in {"true", "1", "yes", "y"}


def _parse_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_rows(rows: Iterable[dict[str, str]]) -> Iterator[HistMatch]:
    """Convierte filas (dicts del CSV) en HistMatch válidos, saltando las rotas."""
    for row in rows:
        fecha = _parse_date(row.get("date", ""))
        if fecha is None:
            continue
        try:
            hg = int(float(row.get("home_score", "")))
            ag = int(float(row.get("away_score", "")))
        except (TypeError, ValueError):
            continue
        home = normalize_team(row.get("home_team", ""))
        away = normalize_team(row.get("away_team", ""))
        if not home or not away:
            continue
        yield HistMatch(
            fecha=fecha,
            home=home,
            away=away,
            home_goals=hg,
            away_goals=ag,
            tournament=(row.get("tournament", "") or "").strip(),
            neutral=_parse_bool(row.get("neutral", "False")),
        )


def load_matches(csv_path: str | Path) -> list[HistMatch]:
    """Carga y normaliza todos los partidos del CSV histórico.

    Lanza FileNotFoundError si el fichero no existe (el caller decide el
    fallback). Devuelve la lista ordenada cronológicamente.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"No se encontró el histórico en {path}. Descarga el dataset "
            "'International football results 1872-present' (Kaggle) y guárdalo ahí, "
            "o ajusta config.historical_csv."
        )
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        matches = list(parse_rows(reader))
    matches.sort(key=lambda m: m.fecha)
    return matches
