"""Persistencia de la capa de producto: tablas `users` y `recommendations`.

Comparte el mismo fichero SQLite que el paper trading (`config.db_path`), con su
propia conexión. Mantiene:

  * users            — telegram_id, estado_suscripcion, fecha_expiracion, tier,
                       verificación de edad.
  * recommendations  — combinadas/singles publicadas a los suscriptores, con su
                       estado y resultado (track record público).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import CONFIG, Config
from src.subscriptions.models import PublishedRecommendation, SubStatus, User


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SubscriptionStore:
    """Acceso a las tablas de usuarios y recomendaciones publicadas."""

    def __init__(self, config: Config = CONFIG, db_path: Optional[Path] = None) -> None:
        self.config = config
        self.db_path = Path(db_path or config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id        INTEGER PRIMARY KEY,
                estado             TEXT NOT NULL DEFAULT 'none'
                                   CHECK (estado IN ('none','active','expired','cancelled')),
                tier               TEXT,
                fecha_expiracion   TEXT,
                edad_verificada    INTEGER NOT NULL DEFAULT 0,
                creado             TEXT NOT NULL,
                actualizado        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recommendations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha        TEXT NOT NULL,
                jornada      TEXT NOT NULL,
                tipo         TEXT NOT NULL,
                descripcion  TEXT NOT NULL,
                cuota        REAL NOT NULL,
                prob         REAL NOT NULL,
                edge         REAL NOT NULL,
                stake        REAL NOT NULL,
                estado       TEXT NOT NULL DEFAULT 'published',
                resultado    TEXT,
                bet_id       INTEGER,
                legs_json    TEXT
            );
            """
        )
        self.conn.commit()

    # ───────────────────────────── users ─────────────────────────────

    def get_user(self, telegram_id: int) -> Optional[User]:
        row = self.conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return _row_to_user(row) if row else None

    def upsert_user(self, user: User) -> User:
        """Crea o actualiza un usuario. Devuelve el usuario persistido."""
        now = _now_iso()
        existing = self.get_user(user.telegram_id)
        creado = existing.creado if existing else (user.creado or now)
        self.conn.execute(
            """INSERT INTO users
                   (telegram_id, estado, tier, fecha_expiracion, edad_verificada,
                    creado, actualizado)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                   estado=excluded.estado,
                   tier=excluded.tier,
                   fecha_expiracion=excluded.fecha_expiracion,
                   edad_verificada=excluded.edad_verificada,
                   actualizado=excluded.actualizado""",
            (user.telegram_id, user.estado, user.tier, user.fecha_expiracion,
             int(user.edad_verificada), creado, now),
        )
        self.conn.commit()
        return self.get_user(user.telegram_id)  # type: ignore[return-value]

    def get_or_create_user(self, telegram_id: int) -> User:
        user = self.get_user(telegram_id)
        if user is not None:
            return user
        now = _now_iso()
        return self.upsert_user(User(
            telegram_id=telegram_id, estado=SubStatus.NONE, creado=now, actualizado=now,
        ))

    def active_subscriber_ids(self, *, now: Optional[datetime] = None) -> list[int]:
        """IDs de usuarios con derecho a contenido premium (18+ y activos)."""
        rows = self.conn.execute(
            "SELECT * FROM users WHERE estado = 'active' AND edad_verificada = 1"
        ).fetchall()
        out = []
        for r in rows:
            u = _row_to_user(r)
            if u.is_entitled(now=now):
                out.append(u.telegram_id)
        return out

    # ───────────────────────── recommendations ─────────────────────────

    def add_recommendation(self, rec: PublishedRecommendation) -> int:
        legs_json = rec_legs_to_json(rec)
        cur = self.conn.execute(
            """INSERT INTO recommendations
                   (fecha, jornada, tipo, descripcion, cuota, prob, edge, stake,
                    estado, resultado, bet_id, legs_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rec.fecha or _now_iso(), rec.jornada, rec.tipo, rec.descripcion,
             rec.cuota, rec.prob, rec.edge, rec.stake, rec.estado, rec.resultado,
             rec.bet_id, legs_json),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_recommendation_result(
        self, rec_id: int, estado: str, resultado: Optional[str]
    ) -> None:
        self.conn.execute(
            "UPDATE recommendations SET estado = ?, resultado = ? WHERE id = ?",
            (estado, resultado, rec_id),
        )
        self.conn.commit()

    def recommendations(self, *, limit: Optional[int] = None) -> list[PublishedRecommendation]:
        sql = "SELECT * FROM recommendations ORDER BY id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self.conn.execute(sql).fetchall()
        return [_row_to_rec(r) for r in rows]

    def recommendation_by_bet(self, bet_id: int) -> Optional[PublishedRecommendation]:
        row = self.conn.execute(
            "SELECT * FROM recommendations WHERE bet_id = ? ORDER BY id DESC LIMIT 1",
            (bet_id,),
        ).fetchone()
        return _row_to_rec(row) if row else None

    def close(self) -> None:
        self.conn.close()


# ─────────────────────────── helpers ───────────────────────────

def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        telegram_id=row["telegram_id"],
        estado=row["estado"],
        tier=row["tier"],
        fecha_expiracion=row["fecha_expiracion"],
        edad_verificada=bool(row["edad_verificada"]),
        creado=row["creado"],
        actualizado=row["actualizado"],
    )


def _row_to_rec(row: sqlite3.Row) -> PublishedRecommendation:
    return PublishedRecommendation(
        id=row["id"],
        fecha=row["fecha"],
        jornada=row["jornada"],
        tipo=row["tipo"],
        descripcion=row["descripcion"],
        cuota=row["cuota"],
        prob=row["prob"],
        edge=row["edge"],
        stake=row["stake"],
        estado=row["estado"],
        resultado=row["resultado"],
        bet_id=row["bet_id"],
    )


def rec_legs_to_json(rec: PublishedRecommendation) -> str:
    """Serializa la descripción de legs (mínima) para auditoría futura."""
    return json.dumps({"descripcion": rec.descripcion}, ensure_ascii=False)
