"""Capa de servicio: funciones deterministas de alto nivel que orquestan
datos → modelo → valor → combinadas → staking → almacenamiento → liquidación.

TODA la matemática vive aquí (Python puro). El LLM (src/agent) solo invoca
estas funciones vía tool use y explica los resultados; nunca calcula él mismo.

La CLI (main.py) usa exactamente las mismas funciones.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from config import CONFIG, Config
from src.data.api_client import ApiFootballClient
from src.data.models import Fixture, MarketOdds
from src.model import ratings as ratings_mod
from src.model.poisson import MarketProbabilities, PoissonModel
from src.reporting import reports
from src.settlement.settle import settle_pending_bets
from src.storage.db import Bet, BetLeg, BettingStore
from src.value.parlays import Parlay, build_parlays
from src.value.staking import stake_for_bet
from src.value.value_engine import ValueBet, find_value_bets


@dataclass
class FixtureAnalysis:
    """Análisis completo de un partido: probabilidades + selecciones de valor."""

    fixture: Fixture
    odds: Optional[MarketOdds]
    probs: MarketProbabilities
    value_bets: list[ValueBet]


@dataclass
class RecommendedBet:
    """Una apuesta recomendada (single o parlay) con su staking calculado."""

    tipo: str                 # "single" | "parlay"
    legs: list[ValueBet]
    combined_odds: float
    combined_prob: float
    edge: float
    stake: float
    capped: bool

    def to_bet(self) -> Bet:
        bet_legs = [
            BetLeg(fixture_id=vb.fixture_id, market=vb.market, selection=vb.selection,
                   odds=vb.odds, model_prob=vb.model_prob)
            for vb in self.legs
        ]
        return Bet(
            tipo=self.tipo, stake=self.stake, cuota_combinada=self.combined_odds,
            prob=self.combined_prob, edge=self.edge, legs=bet_legs,
        )


class BettingService:
    """Fachada que reúne cliente API, modelo y almacenamiento."""

    def __init__(
        self,
        config: Config = CONFIG,
        client: Optional[ApiFootballClient] = None,
        store: Optional[BettingStore] = None,
        model: Optional[PoissonModel] = None,
    ) -> None:
        self.config = config
        self.client = client or ApiFootballClient(config)
        self.store = store or BettingStore(config)
        self.model = model or PoissonModel(config)
        # Si hay histórico internacional, deriva ratings reales y reemplaza el
        # prior de relleno. Sin CSV, el modelo sigue con el prior Elo por defecto.
        if config.cargar_ratings_historicos:
            self.load_historical_ratings()

    def load_historical_ratings(self) -> bool:
        """Carga el histórico (si existe) y activa los ratings derivados.

        Devuelve True si se cargaron ratings de datos; False si se usa el prior.
        No lanza si falta el CSV: el sistema degrada con elegancia al prior.
        """
        from pathlib import Path as _Path

        path = _Path(self.config.historical_csv)
        if not path.exists():
            return False
        from src.model.historical import load_matches

        matches = load_matches(path)
        if not matches:
            return False
        derived = ratings_mod.compute_ratings(
            matches,
            half_life_days=self.config.ratings_half_life_dias,
            iterations=self.config.ratings_iteraciones,
            home_advantage=self.config.ventaja_local,
        )
        ratings_mod.set_active(derived)
        return True

    # ───────────────────────── datos crudos ─────────────────────────

    def get_fixtures(self, round_name: Optional[str] = None, date_iso: Optional[str] = None) -> list[Fixture]:
        if date_iso:
            return self.client.get_fixtures_by_date(date_iso)
        return self.client.get_fixtures(round_name)

    def get_odds(self, fixture_id: int) -> Optional[MarketOdds]:
        return self.client.get_odds(fixture_id)

    # ───────────────────── análisis de un partido ─────────────────────

    def analyze_fixture(self, fixture: Fixture) -> FixtureAnalysis:
        """Probabilidades del modelo + selecciones de valor de un partido."""
        home_stats = self.client.get_team_stats(fixture.home_team_id)
        away_stats = self.client.get_team_stats(fixture.away_team_id)
        probs = self.model.probabilities(
            fixture.fixture_id, fixture.home_team, fixture.away_team,
            home_stats, away_stats,
            neutral=self.config.mundial_es_neutral,
        )
        odds = self.client.get_odds(fixture.fixture_id)
        value_bets: list[ValueBet] = []
        if odds is not None:
            value_bets = find_value_bets(
                odds, probs, fixture.home_team, fixture.away_team, config=self.config
            )
        return FixtureAnalysis(fixture=fixture, odds=odds, probs=probs, value_bets=value_bets)

    def analyze_round(
        self,
        round_name: Optional[str] = None,
        date_iso: Optional[str] = None,
    ) -> list[FixtureAnalysis]:
        fixtures = self.get_fixtures(round_name, date_iso)
        # Solo partidos no jugados aún tienen sentido para apostar.
        upcoming = [f for f in fixtures if not f.is_finished and not f.is_void]
        return [self.analyze_fixture(f) for f in upcoming]

    # ─────────────── valor, combinadas y recomendación ───────────────

    def collect_value_bets(self, analyses: list[FixtureAnalysis]) -> list[ValueBet]:
        out: list[ValueBet] = []
        for a in analyses:
            out.extend(a.value_bets)
        out.sort(key=lambda vb: vb.edge, reverse=True)
        return out

    def build_recommendations(
        self,
        analyses: list[FixtureAnalysis],
        *,
        include_singles: bool = True,
        top_parlays: int = 5,
    ) -> list[RecommendedBet]:
        """Genera recomendaciones con staking Kelly fraccionado.

        Incluye los singles de valor y las mejores combinadas (de legs que ya
        son de valor individual). El stake siempre es Kelly disciplinado.
        """
        bankroll = self.store.current_bankroll()
        value_bets = self.collect_value_bets(analyses)
        recs: list[RecommendedBet] = []

        if include_singles:
            for vb in value_bets:
                dec = stake_for_bet(vb.model_prob, vb.odds, bankroll, config=self.config)
                if dec.stake > 0:
                    recs.append(RecommendedBet(
                        tipo="single", legs=[vb], combined_odds=vb.odds,
                        combined_prob=vb.model_prob, edge=vb.edge,
                        stake=dec.stake, capped=dec.capped,
                    ))

        parlays = build_parlays(value_bets, config=self.config)
        for p in parlays[:top_parlays]:
            dec = stake_for_bet(p.combined_prob, p.combined_odds, bankroll, config=self.config)
            if dec.stake > 0:
                recs.append(RecommendedBet(
                    tipo="parlay", legs=list(p.legs), combined_odds=p.combined_odds,
                    combined_prob=p.combined_prob, edge=p.edge,
                    stake=dec.stake, capped=dec.capped,
                ))

        # Ordena por edge descendente (mejor valor primero).
        recs.sort(key=lambda r: r.edge, reverse=True)
        return recs

    # ──────────────────── registro y liquidación ────────────────────

    def place_paper_bet(self, rec: RecommendedBet) -> int:
        """Registra una recomendación como apuesta paper. Devuelve bet_id."""
        return self.store.place_paper_bet(rec.to_bet())

    def settle(self) -> list[tuple[int, str, float]]:
        """Liquida apuestas pendientes con resultados reales."""
        return settle_pending_bets(self.store, self.client, config=self.config)

    # ─────────────────────────── reportes ───────────────────────────

    def report_console(self) -> str:
        return reports.render_console(self.store, config=self.config)

    def report_markdown(self) -> str:
        return reports.render_markdown(self.store, config=self.config)

    def export_reports(self) -> dict[str, str]:
        """Escribe markdown y CSV en config.reports_dir. Devuelve rutas."""
        out_dir = Path(self.config.reports_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / "reporte.md"
        csv_path = out_dir / "apuestas.csv"
        md_path.write_text(self.report_markdown(), encoding="utf-8")
        reports.write_csv(self.store, csv_path)
        return {"markdown": str(md_path), "csv": str(csv_path)}

    def summary_dict(self) -> dict[str, Any]:
        return asdict(reports.build_summary(self.store, config=self.config))

    def close(self) -> None:
        self.store.close()
