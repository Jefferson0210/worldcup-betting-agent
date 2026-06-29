"""Tabla `paper_fixtures`: mapea el fixture_id (entero) de una apuesta paper a su
fecha y equipos, para poder liquidar luego contra `results.csv` (que casa por
fecha + equipos, no por id).

Comparte el mismo fichero SQLite del paper trading (`config.db_path`).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import CONFIG, Config


@dataclass(frozen=True)
class PaperFixture:
    fixture_id: int
    fecha: str       # ISO YYYY-MM-DD
    home: str        # normalizado
    away: str        # normalizado


class PaperFixtureStore:
    """Persistencia de la metadata de fixtures apostados en paper."""

    def __init__(self, config: Config = CONFIG, db_path: Optional[Path] = None) -> None:
        self.config = config
        self.db_path = Path(db_path or config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_fixtures (
                fixture_id INTEGER PRIMARY KEY,
                fecha      TEXT NOT NULL,
                home       TEXT NOT NULL,
                away       TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def upsert(self, fixture_id: int, fecha: str, home: str, away: str) -> None:
        self.conn.execute(
            """INSERT INTO paper_fixtures (fixture_id, fecha, home, away)
               VALUES (?,?,?,?)
               ON CONFLICT(fixture_id) DO UPDATE SET
                   fecha=excluded.fecha, home=excluded.home, away=excluded.away""",
            (fixture_id, fecha, home, away),
        )
        self.conn.commit()

    def get(self, fixture_id: int) -> Optional[PaperFixture]:
        row = self.conn.execute(
            "SELECT * FROM paper_fixtures WHERE fixture_id = ?", (fixture_id,)
        ).fetchone()
        if not row:
            return None
        return PaperFixture(row["fixture_id"], row["fecha"], row["home"], row["away"])

    def all(self) -> list[PaperFixture]:
        rows = self.conn.execute("SELECT * FROM paper_fixtures ORDER BY fecha").fetchall()
        return [PaperFixture(r["fixture_id"], r["fecha"], r["home"], r["away"]) for r in rows]

    def close(self) -> None:
        self.conn.close()
