"""Dashboard opcional con Streamlit (fase final).

Ejecutar:  streamlit run dashboard.py

Muestra el bankroll, las apuestas, el resumen de rendimiento y la calibración.
Es de solo lectura sobre la base de datos del paper trading.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import CONFIG
from src.reporting import reports
from src.storage.db import BettingStore

st.set_page_config(page_title="WC2026 Betting Agent (paper)", layout="wide")
st.title("⚽ World Cup 2026 — Agente de apuestas (paper trading)")
st.caption("Herramienta educativa de análisis, NO asesoría financiera. "
           "Modo paper: dinero ficticio.")

store = BettingStore(CONFIG)
summary = reports.build_summary(store, config=CONFIG)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Bankroll actual", f"{summary.bankroll_actual:,.2f}",
          delta=f"{summary.bankroll_actual - summary.bankroll_inicial:,.2f}")
c2.metric("ROI", f"{summary.roi_pct:.2f}%")
c3.metric("% acierto", f"{summary.acierto_pct:.2f}%")
c4.metric("Progreso objetivo", f"{summary.progreso_objetivo_pct:.2f}%")

st.subheader("Evolución del bankroll")
hist = store.bankroll_history()
if hist:
    df_bank = pd.DataFrame([{"timestamp": r["timestamp"], "balance": r["balance"]} for r in hist])
    st.line_chart(df_bank.set_index("timestamp")["balance"])

st.subheader("Apuestas")
rows = []
for b in store.all_bets():
    pnl = b.payout - b.stake if b.estado in {"won", "lost"} else 0.0
    rows.append({
        "id": b.id, "tipo": b.tipo, "estado": b.estado, "stake": b.stake,
        "cuota": b.cuota_combinada, "prob": round(b.prob, 3), "edge": round(b.edge, 3),
        "payout": b.payout, "P&L": round(pnl, 2), "n_legs": len(b.legs),
    })
if rows:
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
else:
    st.info("Aún no hay apuestas registradas.")

st.subheader("Calibración (por leg)")
bins = reports.calibration_table(store)
cal_rows = [
    {"rango": f"[{b.low:.2f}-{b.high:.2f})", "n": b.n,
     "pred_media": round(b.pred_mean, 3), "acierto_real": round(b.hit_rate, 3)}
    for b in bins if b.n > 0
]
if cal_rows:
    st.dataframe(pd.DataFrame(cal_rows), use_container_width=True)
else:
    st.info("Sin legs liquidadas todavía para calibrar.")

store.close()
