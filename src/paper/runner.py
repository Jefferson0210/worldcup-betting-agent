"""Orquestador del modo paper en vivo (idempotente, una vez al día).

Flujo de `run()`:
  1. Trae partidos próximos + cuotas de OddsPapi (solo los aún no apostados).
  2. Modelo → value bets → combinadas → staking (motor existente) y registra
     las recomendaciones como apuestas PAPER, SIN repetir un partido ya apostado.
  3. Liquida las apuestas paper cuyos partidos ya terminaron, usando los
     resultados de `results.csv` (casados por fecha + equipos).

Reutiliza `BettingService`, `OddsPapiProvider`, `src/settlement` y `src/storage`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config import CONFIG, Config
from src.data.models import Fixture
from src.model.odds_history import OddsPapiProvider
from src.paper.source import UpcomingMeta, upcoming_analyses
from src.paper.store import PaperFixtureStore
from src.service import BettingService
from src.settlement.settle import _settle_single_bet


class PaperRunner:
    """Corre el ciclo paper diario sobre el Mundial 2026."""

    def __init__(
        self,
        config: Config = CONFIG,
        *,
        service: Optional[BettingService] = None,
        provider: Optional[OddsPapiProvider] = None,
        paper_store: Optional[PaperFixtureStore] = None,
        hist_matches: Optional[list] = None,
    ) -> None:
        self.config = config
        self.service = service or BettingService(config)
        self.provider = provider or OddsPapiProvider(config)
        self.paper_store = paper_store or PaperFixtureStore(config)
        self._hist_matches = hist_matches  # se carga perezosamente si None

    # ───────────────────────── registro ─────────────────────────

    def committed_fixture_ids(self) -> set[int]:
        """Fixtures que YA tienen una apuesta paper (para no repetir)."""
        ids: set[int] = set()
        for bet in self.service.store.all_bets():
            for leg in bet.legs:
                ids.add(leg.fixture_id)
        return ids

    def collect_and_register(
        self, *, max_fixtures: Optional[int] = None, on_progress=None
    ) -> list[int]:
        """Registra apuestas paper nuevas; devuelve los bet_id creados."""
        committed = self.committed_fixture_ids()
        analyses, meta = upcoming_analyses(
            self.provider, self.service.model, config=self.config,
            skip_fixture_ids=committed, max_fixtures=max_fixtures, on_progress=on_progress,
        )
        meta_by_id: dict[int, UpcomingMeta] = {m.fixture_id: m for m in meta}

        recs = self.service.build_recommendations(analyses)
        usados: set[int] = set(committed)
        placed: list[int] = []

        for rec in recs:
            leg_fids = {vb.fixture_id for vb in rec.legs}
            if leg_fids & usados:
                continue  # algún partido de esta rec ya está apostado -> saltar
            try:
                bet_id = self.service.place_paper_bet(rec)
            except ValueError:
                # Bankroll insuficiente para el stake: dejamos de registrar.
                break
            for fid in leg_fids:
                m = meta_by_id.get(fid)
                if m:
                    self.paper_store.upsert(fid, m.fecha, m.home, m.away)
            usados |= leg_fids
            placed.append(bet_id)
        return placed

    # ───────────────────────── liquidación ─────────────────────────

    def _hist(self) -> list:
        if self._hist_matches is None:
            from src.model.historical import load_matches
            path = Path(self.config.historical_csv)
            self._hist_matches = load_matches(path) if path.exists() else []
        return self._hist_matches

    def settle(self) -> list[tuple[int, str, float]]:
        """Liquida las apuestas paper pendientes con resultados de results.csv."""
        store = self.service.store
        pending = store.pending_bets()
        if not pending:
            return []
        fixture_ids = sorted({leg.fixture_id for b in pending for leg in b.legs})
        results = build_result_fixtures(self.paper_store, self._hist(), fixture_ids)

        settled: list[tuple[int, str, float]] = []
        for bet in pending:
            outcome = _settle_single_bet(bet, results)
            if outcome is None:
                continue
            estado, payout, leg_results = outcome
            store.settle_bet(bet.id, estado, payout, leg_results)  # type: ignore[arg-type]
            settled.append((bet.id, estado, payout))  # type: ignore[arg-type]
        return settled

    def run(self, *, max_fixtures: Optional[int] = None, on_progress=None) -> dict:
        """Ciclo completo: registrar nuevas + liquidar terminadas."""
        placed = self.collect_and_register(max_fixtures=max_fixtures, on_progress=on_progress)
        settled = self.settle()
        return {"registradas": placed, "liquidadas": settled}

    def close(self) -> None:
        self.paper_store.close()


# ─────────────── resultados desde results.csv (por fecha+equipos) ───────────────

def _index_hist(hist_matches: list) -> dict[str, list]:
    idx: dict[str, list] = {}
    for m in hist_matches:
        idx.setdefault(m.fecha.isoformat(), []).append(m)
    return idx


def _find_result(idx: dict[str, list], fecha_iso: str, home: str, away: str):
    """Busca el HistMatch del partido (fecha ±1 día, equipos en cualquier orden).

    Devuelve (home_goals, away_goals) orientado a (home, away), o None.
    """
    try:
        base = date.fromisoformat(fecha_iso)
    except ValueError:
        return None
    for delta in (0, 1, -1):
        d = (base + timedelta(days=delta)).isoformat()
        for m in idx.get(d, ()):  # noqa: B007
            if m.home == home and m.away == away:
                return m.home_goals, m.away_goals
            if m.home == away and m.away == home:  # orientación invertida
                return m.away_goals, m.home_goals
    return None


def build_result_fixtures(
    paper_store: PaperFixtureStore, hist_matches: list, fixture_ids: list[int]
) -> dict[int, Fixture]:
    """Construye `dict[fixture_id -> Fixture]` (status FT con goles) de los que
    ya tienen resultado en `results.csv`. Los que aún no, se omiten (siguen
    pendientes)."""
    idx = _index_hist(hist_matches)
    out: dict[int, Fixture] = {}
    for fid in fixture_ids:
        meta = paper_store.get(fid)
        if meta is None:
            continue
        res = _find_result(idx, meta.fecha, meta.home, meta.away)
        if res is None:
            continue
        hg, ag = res
        out[fid] = Fixture(
            fixture_id=fid,
            date_utc=datetime.fromisoformat(meta.fecha + "T00:00:00+00:00").astimezone(timezone.utc),
            status_short="FT", round_name="FIFA World Cup",
            home_team_id=0, home_team=meta.home, away_team_id=0, away_team=meta.away,
            home_goals=hg, away_goals=ag,
        )
    return out
