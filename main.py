"""CLI del agente de pronóstico y apuestas paper para el Mundial 2026.

Modo recomendación / paper trading: registra apuestas con dinero ficticio y las
liquida con resultados reales. NUNCA coloca apuestas reales.

Ejemplos
--------
  python main.py fixtures --round "Group Stage - 1"
  python main.py analyze --date 2026-06-28
  python main.py recommend --date 2026-06-28
  python main.py place --date 2026-06-28 --index 0
  python main.py settle
  python main.py report
  python main.py agent --ask "Analiza los partidos del 2026-06-28 y recomienda qué jugar"
"""
from __future__ import annotations

import argparse
import sys

from config import CONFIG
from src.service import BettingService

DISCLAIMER = (
    "[modo PAPER — dinero ficticio. Herramienta educativa de análisis, NO asesoría "
    "financiera. Apostar implica riesgo de pérdida.]"
)


def _print_recs(recs) -> None:
    if not recs:
        print("No hay recomendaciones de valor con stake > 0 para este conjunto.")
        return
    print(f"\nRecomendaciones (ordenadas por edge) — bankroll y staking Kelly fraccionado:")
    for i, r in enumerate(recs):
        legs = " + ".join(
            f"{vb.home_team} vs {vb.away_team} [{vb.market}:{vb.selection}@{vb.odds:.2f}]"
            for vb in r.legs
        )
        cap = "  (stake topado)" if r.capped else ""
        print(f"  [{i}] {r.tipo.upper():7} edge={r.edge*100:5.2f}%  "
              f"cuota={r.combined_odds:.2f}  prob={r.combined_prob*100:5.2f}%  "
              f"stake={r.stake:.2f}{cap}")
        print(f"       {legs}")
    print("\nSugerencia: 'python main.py place --index N ...' para registrar la paper bet.")


def cmd_fixtures(service: BettingService, args) -> None:
    fixtures = service.get_fixtures(args.round, args.date)
    print(f"Partidos encontrados: {len(fixtures)}")
    for f in fixtures:
        score = ""
        if f.home_goals is not None and f.away_goals is not None:
            score = f"  {f.home_goals}-{f.away_goals}"
        print(f"  #{f.fixture_id}  {f.date_utc:%Y-%m-%d %H:%M}  [{f.status_short}]  "
              f"{f.home_team} vs {f.away_team}{score}")


def cmd_analyze(service: BettingService, args) -> None:
    analyses = service.analyze_round(args.round, args.date)
    print(f"Partidos analizados: {len(analyses)}")
    for a in analyses:
        f = a.fixture
        print(f"\n  {f.home_team} vs {f.away_team}  ({f.date_utc:%Y-%m-%d %H:%M})")
        print(f"    P(1)={a.probs.p_home*100:5.1f}%  P(X)={a.probs.p_draw*100:5.1f}%  "
              f"P(2)={a.probs.p_away*100:5.1f}%  P(O2.5)={a.probs.p_over_25*100:5.1f}%")
        if not a.odds:
            print("    (sin cuotas disponibles)")
        for vb in a.value_bets:
            print(f"    VALOR  {vb.market}:{vb.selection} @ {vb.odds:.2f}  edge={vb.edge*100:.2f}%")


def cmd_recommend(service: BettingService, args) -> None:
    analyses = service.analyze_round(args.round, args.date)
    recs = service.build_recommendations(
        analyses, include_singles=not args.only_parlays, top_parlays=args.top_parlays
    )
    _print_recs(recs)


def cmd_place(service: BettingService, args) -> None:
    analyses = service.analyze_round(args.round, args.date)
    recs = service.build_recommendations(
        analyses, include_singles=not args.only_parlays, top_parlays=args.top_parlays
    )
    if not (0 <= args.index < len(recs)):
        print(f"Índice {args.index} fuera de rango (hay {len(recs)} recomendaciones).")
        _print_recs(recs)
        return
    bet_id = service.place_paper_bet(recs[args.index])
    print(f"✓ Apuesta paper registrada: #{bet_id}  (stake {recs[args.index].stake:.2f})")
    print(f"  Bankroll actual: {service.store.current_bankroll():.2f}")


def cmd_settle(service: BettingService, args) -> None:
    settled = service.settle()
    if not settled:
        print("No hay apuestas liquidables todavía (faltan resultados FT).")
        return
    for bet_id, estado, payout in settled:
        print(f"  Apuesta #{bet_id}: {estado.upper()}  payout={payout:.2f}")
    print(f"Bankroll actual: {service.store.current_bankroll():.2f}")


def cmd_report(service: BettingService, args) -> None:
    print(service.report_console())
    paths = service.export_reports()
    print(f"\nExportado: {paths['markdown']}  |  {paths['csv']}")


def cmd_backtest(service: BettingService, args) -> None:
    from pathlib import Path

    from src.model import historical
    from src.model.backtest import render_backtest, render_roi_markdown, run_backtest
    from src.model.odds_history import build_odds_book

    path = args.csv or CONFIG.historical_csv
    try:
        matches = historical.load_matches(path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return

    # Cuotas reales (opcional): CSV local o The Odds API. Sin fuente -> sin ROI real.
    odds_book = None
    if not args.no_odds:
        odds_book = build_odds_book(CONFIG, csv_path=args.odds_csv)
        if odds_book is not None:
            print(f"Cuotas reales cargadas: {len(odds_book)} partidos con cuota.")
        else:
            print("Sin cuotas reales (ni data/odds.csv ni ODDS_API_KEY): el "
                  "backtest correrá solo con métricas predictivas (sin ROI real).")

    print(f"Histórico cargado: {len(matches)} partidos. Ejecutando backtest "
          f"(filtro torneo: {args.tournament!r})...\n")
    res = run_backtest(
        matches, config=CONFIG, tournament_filter=args.tournament,
        min_train=args.min_train, desde_anio=args.desde, odds_book=odds_book,
    )
    print(render_backtest(res))

    # Guarda el reporte de ROI en markdown.
    out_dir = Path(CONFIG.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "backtest_roi.md"
    titulo = f"Backtest ROI — {args.tournament}"
    report_path.write_text(render_roi_markdown(res, titulo=titulo), encoding="utf-8")
    print(f"\nReporte guardado en: {report_path}")


def cmd_fetch_odds(service: BettingService, args) -> None:
    """Baja cuotas históricas 1X2 de la API (OddsPapi por defecto) a data/odds.csv.

    GASTA cuota: ~1 petición por partido (tier gratuito OddsPapi = 250/mes). Usa
    --max para acotar. Las respuestas se cachean: re-ejecutar no vuelve a gastar.
    """
    from pathlib import Path

    from src.model.odds_history import make_provider, write_odds_csv

    if not CONFIG.odds_api_key:
        print("Falta ODDS_API_KEY en .env.", file=sys.stderr)
        return
    provider = make_provider(CONFIG)
    print(f"Proveedor de cuotas: {provider.name}  |  torneo id: {args.tournament_id}  "
          f"|  máx partidos: {args.max if args.max else 'sin límite'}")
    print("Descargando (1 petición por partido; cacheado)...")

    def _progress(n, fx, triple):
        ok = "ok" if (triple and triple.valid()) else "sin 1X2"
        print(f"  [{n}] {fx.get('participant1Name')} vs {fx.get('participant2Name')} "
              f"({(fx.get('startTime') or '')[:10]})  -> {ok}")

    book = provider.build_book(  # type: ignore[call-arg]
        tournament_id=args.tournament_id, max_fixtures=args.max,
        bookmakers=args.bookmakers, on_progress=_progress,
    )
    out = Path(args.out or CONFIG.odds_csv)
    write_odds_csv(book, out)
    print(f"\nPartidos con 1X2 escritos: {len(book)}  ->  {out}")


def _do_paper_run(service: BettingService, args) -> None:
    from src.paper.runner import PaperRunner

    runner = PaperRunner(CONFIG, service=service)

    def prog(fx, triple):
        ok = "ok" if (triple and triple.valid()) else "sin 1X2"
        print(f"  cuotas {fx.get('participant1Name')} vs {fx.get('participant2Name')} -> {ok}")

    print("Buscando partidos próximos del Mundial 2026 en OddsPapi "
          "(solo los aún no apostados)...")
    result = runner.run(max_fixtures=args.max, on_progress=prog)
    placed, settled = result["registradas"], result["liquidadas"]
    print(f"\nApuestas paper NUEVAS registradas: {len(placed)}  {placed if placed else ''}")
    if settled:
        print("Liquidadas con resultados de results.csv:")
        for bid, est, pay in settled:
            print(f"  #{bid}: {est.upper()}  payout={pay:.2f}")
    else:
        print("Sin liquidaciones nuevas (faltan resultados de los partidos apostados).")
    print(f"Bankroll actual (paper): {service.store.current_bankroll():.2f}")
    print("\n[modo PAPER — dinero ficticio. No es asesoría financiera.]")


def cmd_paper_run(service: BettingService, args) -> None:
    if not CONFIG.odds_api_key:
        print("Falta ODDS_API_KEY en .env (OddsPapi).", file=sys.stderr)
        return
    if not args.schedule:
        _do_paper_run(service, args)
        return
    # Modo programado: corre ahora y luego una vez al día (APScheduler).
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    _do_paper_run(service, args)
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(lambda: _do_paper_run(service, args),
                  CronTrigger(hour=CONFIG.publicar_hora, minute=CONFIG.publicar_minuto),
                  id="paper_run_diario", replace_existing=True)
    print(f"\nProgramado: paper-run diario a las {CONFIG.publicar_hora:02d}:"
          f"{CONFIG.publicar_minuto:02d} UTC. Ctrl+C para parar.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler detenido.")


def cmd_pagina(service: BettingService, args) -> None:
    from src.model.odds_history import OddsPapiProvider
    from src.reporting.webpage import generate_page

    if not CONFIG.odds_api_key:
        print("Falta ODDS_API_KEY en .env (OddsPapi).", file=sys.stderr)
        return
    provider = OddsPapiProvider(CONFIG)
    print("Generando página de predicciones (partidos próximos del Mundial 2026)...")
    out, n = generate_page(provider, service.model, config=CONFIG)
    print(f"✓ Página generada: {out}  ({n} partido(s))")
    print("  Ábrela con doble clic o súbela a cualquier hosting estático.")
    print("  Es estática: vuelve a correr este comando para actualizarla "
          "(ideal: junto a tu paper-run diario).")


def cmd_predicciones(service: BettingService, args) -> None:
    from pathlib import Path

    from src.model.historical import load_matches
    from src.paper.store import PaperFixtureStore
    from src.reporting.predictions import build_predictions, render_predictions

    paper_store = PaperFixtureStore(CONFIG)
    path = Path(CONFIG.historical_csv)
    hist = load_matches(path) if path.exists() else []
    preds = build_predictions(
        service.store, service.model, paper_store, hist,
        config=CONFIG, date_filter=args.date, team_filter=args.team,
    )
    print(render_predictions(preds))
    paper_store.close()


def cmd_ratings(service: BettingService, args) -> None:
    from src.model import ratings as ratings_mod

    loaded = service.load_historical_ratings()
    active = ratings_mod.get_active()
    fuente = "histórico (datos)" if loaded else "prior Elo de relleno (sin CSV)"
    print(f"Fuente de ratings: {fuente}  |  selecciones: {len(active)}")
    teams = sorted(active._teams.values(), key=lambda r: r.elo, reverse=True)  # type: ignore[attr-defined]
    top = teams[: args.top]
    print(f"\nTop {len(top)} por Elo:")
    print(f"  {'selección':<22} {'elo':>7} {'ataque':>8} {'defensa':>8} {'n':>6}")
    for r in top:
        print(f"  {r.team:<22} {r.elo:>7.0f} {r.attack:>8.2f} {r.defense:>8.2f} {r.n_matches:>6}")


def cmd_bot(service: BettingService, args) -> None:
    from src.bot.bot import BettingBot
    from src.subscriptions.service import SubscriptionService

    if not CONFIG.telegram_bot_token:
        print("Falta TELEGRAM_BOT_TOKEN en .env (token de BotFather).", file=sys.stderr)
        return
    bot = BettingBot(
        CONFIG, service=service, subscriptions=SubscriptionService(CONFIG),
    )
    modo = " + scheduler" if args.with_scheduler else ""
    print(f"Iniciando bot de Telegram{modo}... (Ctrl+C para parar)")
    bot.run_polling(with_scheduler=args.with_scheduler)


def cmd_agent(service: BettingService, args) -> None:
    from src.agent.orchestrator import BettingAgent

    agent = BettingAgent(CONFIG, service=service)
    print(f"Agente (Gemini {CONFIG.modelo_orquestador}) procesando: {args.ask!r}\n")
    answer = agent.run(args.ask)
    print("\n" + "=" * 64 + "\nRESPUESTA DEL AGENTE\n" + "=" * 64)
    print(answer)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agente de apuestas paper — Mundial 2026")
    sub = p.add_subparsers(dest="command", required=True)

    def add_round_date(sp):
        sp.add_argument("--round", help="Nombre de la ronda (p.ej. 'Group Stage - 1')")
        sp.add_argument("--date", help="Fecha YYYY-MM-DD")

    sp = sub.add_parser("fixtures", help="Listar partidos")
    add_round_date(sp)

    sp = sub.add_parser("analyze", help="Probabilidades + valor por partido")
    add_round_date(sp)

    sp = sub.add_parser("recommend", help="Recomendaciones con staking")
    add_round_date(sp)
    sp.add_argument("--only-parlays", action="store_true", help="Solo combinadas")
    sp.add_argument("--top-parlays", type=int, default=5)

    sp = sub.add_parser("place", help="Registrar una apuesta paper")
    add_round_date(sp)
    sp.add_argument("--index", type=int, required=True, help="Índice de la recomendación")
    sp.add_argument("--only-parlays", action="store_true")
    sp.add_argument("--top-parlays", type=int, default=5)

    sub.add_parser("settle", help="Liquidar apuestas pendientes")
    sub.add_parser("report", help="Reporte de rendimiento y calibración")

    sp = sub.add_parser("paper-run",
                        help="Modo paper en vivo Mundial 2026 (OddsPapi): registra + liquida")
    sp.add_argument("--max", type=int, default=None,
                    help="Máx. partidos próximos a procesar (acota la cuota OddsPapi)")
    sp.add_argument("--schedule", action="store_true",
                    help="Tras correr ahora, repetir a diario (APScheduler)")

    sp = sub.add_parser("predicciones",
                        help="Predicción del modelo vs resultado, partido por partido")
    sp.add_argument("--date", help="Filtrar por fecha YYYY-MM-DD")
    sp.add_argument("--team", help="Filtrar por equipo (subcadena)")

    sub.add_parser("pagina",
                   help="Genera reports/predicciones.html con las probabilidades actuales")

    sp = sub.add_parser("backtest", help="Backtest del modelo sobre torneos pasados")
    sp.add_argument("--csv", help="Ruta del CSV histórico (por defecto config.historical_csv)")
    sp.add_argument("--tournament", default="FIFA World Cup",
                    help="Subcadena del torneo a backtestear (def: 'FIFA World Cup')")
    sp.add_argument("--min-train", type=int, default=200, dest="min_train",
                    help="Partidos mínimos de entrenamiento previos")
    sp.add_argument("--desde", type=int, default=None, help="Año mínimo de torneo a evaluar")
    sp.add_argument("--odds-csv", default=None, dest="odds_csv",
                    help="CSV de cuotas reales (por defecto config.odds_csv = data/odds.csv)")
    sp.add_argument("--no-odds", action="store_true", dest="no_odds",
                    help="No calcular el ROI real aunque haya cuotas disponibles")

    sp = sub.add_parser("ratings", help="Construye/muestra los ratings internacionales")
    sp.add_argument("--top", type=int, default=20, help="Nº de selecciones a mostrar")

    sp = sub.add_parser("fetch-odds", help="Baja cuotas históricas 1X2 a data/odds.csv (gasta cuota)")
    sp.add_argument("--tournament-id", type=int, default=CONFIG.oddspapi_tournament_id,
                    dest="tournament_id", help="ID de torneo OddsPapi (16 = World Cup)")
    sp.add_argument("--max", type=int, default=None, help="Máx. partidos a bajar (acota el gasto)")
    sp.add_argument("--bookmakers", default=None, help="Casas (máx 3), p.ej. 'pinnacle,bet365'")
    sp.add_argument("--out", default=None, help="Ruta de salida (def: data/odds.csv)")

    sp = sub.add_parser("agent", help="Orquestador con Gemini (function calling)")
    sp.add_argument("--ask", required=True, help="Instrucción en lenguaje natural")

    sp = sub.add_parser("bot", help="Arranca el bot de Telegram (suscripción)")
    sp.add_argument("--with-scheduler", action="store_true", dest="with_scheduler",
                    help="Arranca también los jobs programados (APScheduler)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(DISCLAIMER)
    # backtest/ratings (solo CSV) y fetch-odds (solo ODDS_API_KEY) no requieren
    # la clave de API-Football.
    offline = args.command in {"backtest", "ratings", "fetch-odds", "paper-run",
                               "predicciones", "pagina"}
    if not offline:
        try:
            CONFIG.validar_claves(requiere_gemini=(args.command == "agent"))
        except RuntimeError as exc:
            print(f"\nError de configuración: {exc}", file=sys.stderr)
            return 2

    service = BettingService(CONFIG)
    try:
        handler = {
            "fixtures": cmd_fixtures, "analyze": cmd_analyze,
            "recommend": cmd_recommend, "place": cmd_place,
            "settle": cmd_settle, "report": cmd_report, "agent": cmd_agent,
            "backtest": cmd_backtest, "ratings": cmd_ratings, "bot": cmd_bot,
            "fetch-odds": cmd_fetch_odds, "paper-run": cmd_paper_run,
            "predicciones": cmd_predicciones, "pagina": cmd_pagina,
        }[args.command]
        handler(service, args)
    except Exception as exc:  # noqa: BLE001
        print(f"\nError: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
