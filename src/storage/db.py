"""Almacenamiento SQLite para el paper trading.

Tablas
------
bankroll_history(timestamp, balance, cambio, motivo)
bets(id, fecha, tipo, stake, cuota_combinada, prob, edge, estado, payout, fecha_liquidacion)
bet_legs(id, bet_id, fixture_id, mercado, seleccion, cuota, prob_modelo, resultado)

Estados de apuesta: pending | won | lost | void.
La primera vez se inicializa el bankroll con el valor de config.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import CONFIG, Config


# ───────────────────────────── modelos ─────────────────────────────

@dataclass
class BetLeg:
    fixture_id: int
    market: str
    selection: str
    odds: float
    model_prob: float
    result: Optional[str] = None   # None | "won" | "lost" | "void"
    id: Optional[int] = None
    bet_id: Optional[int] = None


@dataclass
class Bet:
    tipo: str                      # "single" | "parlay"
    stake: float
    cuota_combinada: float
    prob: float
    edge: float
    legs: list[BetLeg]
    estado: str = "pending"        # pending | won | lost | void
    payout: float = 0.0
    fecha: Optional[str] = None
    fecha_liquidacion: Optional[str] = None
    id: Optional[int] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────── store ───────────────────────────────

class BettingStore:
    """Acceso a la base de datos del paper trading."""

    def __init__(self, config: Config = CONFIG, db_path: Optional[Path] = None) -> None:
        self.config = config
        self.db_path = Path(db_path or config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self._ensure_bankroll_initialized()

    # ── esquema ──
    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bankroll_history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                balance   REAL NOT NULL,
                cambio    REAL NOT NULL,
                motivo    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bets (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha             TEXT NOT NULL,
                tipo              TEXT NOT NULL CHECK (tipo IN ('single','parlay')),
                stake             REAL NOT NULL,
                cuota_combinada   REAL NOT NULL,
                prob              REAL NOT NULL,
                edge              REAL NOT NULL,
                estado            TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (estado IN ('pending','won','lost','void')),
                payout            REAL NOT NULL DEFAULT 0,
                fecha_liquidacion TEXT
            );

            CREATE TABLE IF NOT EXISTS bet_legs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id      INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
                fixture_id  INTEGER NOT NULL,
                mercado     TEXT NOT NULL,
                seleccion   TEXT NOT NULL,
                cuota       REAL NOT NULL,
                prob_modelo REAL NOT NULL,
                resultado   TEXT
            );
            """
        )
        self.conn.commit()

    def _ensure_bankroll_initialized(self) -> None:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM bankroll_history").fetchone()
        if row["n"] == 0:
            self.conn.execute(
                "INSERT INTO bankroll_history (timestamp, balance, cambio, motivo) VALUES (?,?,?,?)",
                (_now_iso(), self.config.bankroll_inicial, self.config.bankroll_inicial,
                 "bankroll inicial"),
            )
            self.conn.commit()

    # ── bankroll ──
    def current_bankroll(self) -> float:
        row = self.conn.execute(
            "SELECT balance FROM bankroll_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return float(row["balance"]) if row else self.config.bankroll_inicial

    def _apply_bankroll_change(self, cambio: float, motivo: str) -> float:
        nuevo = self.current_bankroll() + cambio
        self.conn.execute(
            "INSERT INTO bankroll_history (timestamp, balance, cambio, motivo) VALUES (?,?,?,?)",
            (_now_iso(), nuevo, cambio, motivo),
        )
        self.conn.commit()
        return nuevo

    def bankroll_history(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM bankroll_history ORDER BY id ASC"
        ).fetchall())

    # ── registro de apuestas (paper) ──
    def place_paper_bet(self, bet: Bet) -> int:
        """Registra una apuesta paper y descuenta el stake del bankroll.

        Devuelve el id de la apuesta. El stake se descuenta inmediatamente;
        al liquidar se acreditará el payout (0 si pierde).
        """
        if bet.stake <= 0:
            raise ValueError("No se registra una apuesta con stake <= 0.")
        bankroll = self.current_bankroll()
        if bet.stake > bankroll + 1e-9:
            raise ValueError(
                f"Stake {bet.stake:.2f} supera el bankroll disponible {bankroll:.2f}."
            )

        fecha = bet.fecha or _now_iso()
        cur = self.conn.execute(
            """INSERT INTO bets (fecha, tipo, stake, cuota_combinada, prob, edge, estado, payout)
               VALUES (?,?,?,?,?,?, 'pending', 0)""",
            (fecha, bet.tipo, bet.stake, bet.cuota_combinada, bet.prob, bet.edge),
        )
        bet_id = int(cur.lastrowid)
        for leg in bet.legs:
            self.conn.execute(
                """INSERT INTO bet_legs
                   (bet_id, fixture_id, mercado, seleccion, cuota, prob_modelo, resultado)
                   VALUES (?,?,?,?,?,?,?)""",
                (bet_id, leg.fixture_id, leg.market, leg.selection,
                 leg.odds, leg.model_prob, leg.result),
            )
        self.conn.commit()
        self._apply_bankroll_change(-bet.stake, f"stake apuesta #{bet_id}")
        return bet_id

    # ── consultas ──
    def get_bet(self, bet_id: int) -> Optional[Bet]:
        row = self.conn.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
        if not row:
            return None
        legs = self._legs_for(bet_id)
        return _row_to_bet(row, legs)

    def pending_bets(self) -> list[Bet]:
        rows = self.conn.execute(
            "SELECT * FROM bets WHERE estado = 'pending' ORDER BY id ASC"
        ).fetchall()
        return [_row_to_bet(r, self._legs_for(r["id"])) for r in rows]

    def settled_bets(self) -> list[Bet]:
        rows = self.conn.execute(
            "SELECT * FROM bets WHERE estado != 'pending' ORDER BY id ASC"
        ).fetchall()
        return [_row_to_bet(r, self._legs_for(r["id"])) for r in rows]

    def all_bets(self) -> list[Bet]:
        rows = self.conn.execute("SELECT * FROM bets ORDER BY id ASC").fetchall()
        return [_row_to_bet(r, self._legs_for(r["id"])) for r in rows]

    def _legs_for(self, bet_id: int) -> list[BetLeg]:
        rows = self.conn.execute(
            "SELECT * FROM bet_legs WHERE bet_id = ? ORDER BY id ASC", (bet_id,)
        ).fetchall()
        return [
            BetLeg(
                id=r["id"], bet_id=r["bet_id"], fixture_id=r["fixture_id"],
                market=r["mercado"], selection=r["seleccion"], odds=r["cuota"],
                model_prob=r["prob_modelo"], result=r["resultado"],
            )
            for r in rows
        ]

    # ── liquidación (usado por src/settlement) ──
    def settle_bet(
        self,
        bet_id: int,
        estado: str,
        payout: float,
        leg_results: dict[int, str],
    ) -> None:
        """Aplica el resultado de una apuesta y ajusta el bankroll.

        `leg_results` mapea leg.id -> "won"|"lost"|"void".
        El bankroll se acredita con `payout` (el stake ya se descontó al registrar).
        Para apuestas void, payout = stake (devolución).
        """
        if estado not in {"won", "lost", "void"}:
            raise ValueError(f"Estado de liquidación inválido: {estado!r}")

        self.conn.execute(
            "UPDATE bets SET estado = ?, payout = ?, fecha_liquidacion = ? WHERE id = ?",
            (estado, payout, _now_iso(), bet_id),
        )
        for leg_id, resultado in leg_results.items():
            self.conn.execute(
                "UPDATE bet_legs SET resultado = ? WHERE id = ?", (resultado, leg_id)
            )
        self.conn.commit()
        if payout != 0:
            self._apply_bankroll_change(payout, f"payout apuesta #{bet_id} ({estado})")

    def close(self) -> None:
        self.conn.close()


# ─────────────────────────── helpers ───────────────────────────

def _row_to_bet(row: sqlite3.Row, legs: list[BetLeg]) -> Bet:
    return Bet(
        id=row["id"],
        fecha=row["fecha"],
        tipo=row["tipo"],
        stake=row["stake"],
        cuota_combinada=row["cuota_combinada"],
        prob=row["prob"],
        edge=row["edge"],
        estado=row["estado"],
        payout=row["payout"],
        fecha_liquidacion=row["fecha_liquidacion"],
        legs=legs,
    )
