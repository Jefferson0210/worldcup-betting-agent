"""Atajo para el backtest: equivale a `python main.py backtest [opciones]`.

Ejemplos
--------
  python backtest.py
  python backtest.py --tournament "FIFA World Cup" --desde 1990
  python backtest.py --csv data/results.csv --min-train 300

Valida el modelo Poisson (ratings internacionales con decaimiento temporal y
cancha neutral) sobre torneos pasados, reportando accuracy, Brier, log-loss,
calibración y un ROI proxy. Es offline: solo necesita el CSV histórico.
"""
from __future__ import annotations

import sys

from main import main

if __name__ == "__main__":
    raise SystemExit(main(["backtest", *sys.argv[1:]]))
