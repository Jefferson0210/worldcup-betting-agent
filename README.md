#  World Cup Betting Agent — predice el Mundial 2026 (y mide la verdad)

> Un sistema que pronostica partidos del **Mundial 2026** con un **modelo
> estadístico** entrenado sobre 150 años de fútbol internacional, lo explica con
> un **agente de IA (Gemini)**, y —lo más importante— se **mide con rigor** contra
> cuotas reales en vez de venderte humo.

La pregunta que casi nadie responde con honestidad es: *“vale, tu modelo predice…
¿pero de verdad le gana a las casas de apuestas?”*. Este proyecto la responde con
números. Spoiler: **el modelo predice muy bien… y aun así no le gana al mercado.**
Y esa, contada con datos, es la parte más interesante.

>  **Herramienta de análisis / educativa.** Solo modo *paper* (dinero ficticio).
> No es asesoría financiera ni recomendación de apuestas. Más abajo, el aviso completo.

---

##  Qué hace de verdad

No es una demo de juguete: es un motor modular con varias capas, todas funcionando
y con tests.

- **Modelo Poisson sobre ratings internacionales reales.** Aprende la fuerza
  **ofensiva y defensiva** y un **Elo** de cada selección a partir de **~49.000
  partidos internacionales (1872–hoy)**, con **decaimiento temporal** (lo reciente
  pesa más) y **cancha neutral** (como en un Mundial). De ahí salen
  `P(1/X/2)` y `P(Over/Under 2.5)`.
- **Motor de valor + staking disciplinado.** Detecta *value bets*
  (`edge = prob·cuota − 1`), arma combinadas solo con selecciones de valor y
  calcula el stake con **Kelly fraccionado con tope** (nunca persigue un objetivo).
- **Backtest contra cuotas REALES.** Reproduce torneos pasados sin fuga de
  información y reporta **accuracy, Brier, log-loss, calibración** y **ROI real**
  contra las cuotas de cierre de casas *sharp* (Pinnacle/Bet365).
- 🤖 **Agente con Google Gemini (function calling).** El LLM **orquesta y redacta**;
  **toda la matemática vive en Python**. Pide datos, llama a las herramientas
  deterministas y explica la recomendación.
- 📲 **Bot de Telegram** de suscripción (verificación +18, gating, disclaimers) y
  **modo paper en vivo** sobre el Mundial 2026 vía OddsPapi.
- **Página web de predicciones** autocontenida, con barra 1/X/2 por partido y
  una **“combinada del día”**.

---

## El hallazgo honesto (lo que lo hace creíble)

Cualquiera puede decir “mi modelo acierta”. Lo difícil es **demostrar qué tan
bien** y **ser honesto cuando no alcanza**. Esto es lo que salió:

### 1) El modelo predice bien y está *muy* bien calibrado

Backtest sobre **Mundiales + eliminatorias desde 1990** (36 torneos, **7.601
partidos**), entrenando solo con partidos anteriores a cada torneo:

| Métrica | Valor |
|---|---|
| **Accuracy 1X2** | **62.3 %** |
| **Brier (multiclase)** | 0.488 |
| **Log-loss** | 0.834 |

Y la **calibración** —lo que de verdad importa— es casi perfecta: cuando el modelo
dice “60 %”, ocurre ~60 % de las veces.

```
prob. predicha → frecuencia real
  ~ 5%  →   4.1%      ~55%  →  55.8%
  ~15%  →  14.8%      ~65%  →  64.3%
  ~25%  →  25.5%      ~75%  →  73.9%
  ~35%  →  35.0%      ~85%  →  87.1%
  ~45%  →  43.7%      ~95%  →  95.8%
```

### 2) …pero NO le gana al mercado

Aquí está el momento de la verdad. Probamos el modelo en el **Mundial 2026 en curso**
contra **cuotas de cierre reales** (OddsPapi), apostando en *paper* donde el modelo
veía valor:

```
┌─ ROI REAL vs cuotas de cierre (Pinnacle/Bet365) ───────────┐
│  Partidos con cuota real:  72                              │
│  Apuestas de valor:        135                             │
│  % acierto de las value bets: 5.2%                         │
│  ROI REAL (yield):         −57 %   (bankroll 1000 → 42)    │
└────────────────────────────────────────────────────────────┘
```

¿Por qué? El modelo marcaba **demasiadas “value bets”** (underdogs a cuota alta
donde *creía* ver valor). Contra una línea de cierre eficiente, ese valor casi
siempre era un espejismo. Un **ROI proxy** contra un mercado sintético daba +24 %…
y eso era justo la ilusión que las cuotas reales destaparon.

> **La moraleja:** un modelo puede ser un excelente *predictor* y un pésimo
> *apostador*. Vencer la línea de cierre de una casa *sharp* es de lo más difícil
> que hay. Medirlo con cuotas reales —en vez de con un proxy optimista— es lo que
> separa un proyecto serio de una promesa vacía.

---

## Cómo está hecho

Arquitectura modular con una regla de oro: **la decisión es de Python; el LLM solo
orquesta y explica.**

| Capa | Qué hace |
|---|---|
| `src/model/` | Carga el histórico, aprende ratings (ataque/defensa + Elo), modelo Poisson, backtest y cuotas reales (OddsPapi / The Odds API). |
| `src/value/` | Valor (`edge`), combinadas y staking Kelly fraccionado con tope. |
| `src/service.py` | Fachada determinista que comparten CLI, agente, bot y paper. |
| `src/agent/` | Orquestador con **Gemini** vía *function calling*. |
| `src/bot/` · `src/subscriptions/` · `src/scheduler/` | Bot de Telegram, entitlement de suscripción y jobs programados. |
| `src/paper/` | Modo paper en vivo del Mundial 2026 (partidos + cuotas de OddsPapi). |
| `src/reporting/` | Reportes, **predicciones partido a partido** y la **página web**. |
| `src/storage/` · `src/settlement/` | SQLite del paper trading y liquidación con resultados reales. |

 **~100 tests con pytest**, todos **mockeados (sin red)**: modelo, ratings,
backtest, ROI con cuotas, entitlement, scheduler, el bucle de Gemini y la página.

---

## Cómo usarlo

```bash
git clone <tu-repo>  &&  cd worldcup-betting-agent
python -m venv .venv
# Windows:  .venv\Scripts\activate   |   macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # y edita .env con TUS claves
```

En `.env` (nunca se sube; está en `.gitignore`) pon tus claves —todas opcionales
según lo que quieras usar:

```
APISPORTS_KEY=...        # datos de API-Football (opcional)
GEMINI_API_KEY=...       # agente de IA (https://aistudio.google.com/apikey)
ODDS_API_KEY=...         # cuotas reales (OddsPapi) para el backtest/paper
TELEGRAM_BOT_TOKEN=...   # bot de Telegram (BotFather)
```

Para los ratings reales, descarga el dataset **“International football results
1872–present”** (Kaggle) y guárdalo en `data/results.csv`.

### Comandos principales

```bash
python main.py ratings --top 20                       # ranking de selecciones (datos)
python main.py backtest --tournament "FIFA World Cup" # valida el modelo (incl. ROI real)
python main.py paper-run                              # registra/liquida apuestas paper (Mundial 2026)
python main.py predicciones                           # predicción vs resultado, partido a partido
python main.py pagina                                 # genera reports/predicciones.html
python main.py report                                 # bankroll, ROI, % acierto, calibración
python main.py agent --ask "Analiza los partidos de mañana y explícame qué ve el modelo"
python main.py bot --with-scheduler                  # bot de Telegram + jobs diarios
```

###  La página de predicciones

`python main.py pagina` genera un **único HTML autocontenido**
(`reports/predicciones.html`, responsive, sin dependencias) con la predicción del
modelo por partido y, por día, una **“combinada del día”** (los favoritos del
modelo, con su probabilidad combinada). Es **información, no una recomendación**:
combinar multiplica el riesgo (3 favoritos al 60 % ⇒ ~22 %). Vuelve a correr el
comando para actualizarla — ideal junto a tu `paper-run` diario.

---

##  Qué aprendí

- **Calibrar > acertar el favorito.** Un modelo útil no solo dice quién gana: dice
  *con qué probabilidad*, y esa probabilidad tiene que ser fiable. La tabla de
  calibración fue la métrica que más me enseñó.
- **El proxy miente; las cuotas reales no.** Mi primer ROI (contra un mercado
  sintético) daba positivo y era emocionante… hasta que lo medí contra cuotas de
  cierre reales y dio **−57 %**. Esa diferencia es exactamente la lección.
- **Ganarle al mercado es brutalmente difícil.** Las casas *sharp* incorporan
  información que un Poisson sobre resultados no tiene. Reconocerlo no es un
  fracaso: es el resultado honesto, y es lo que hace que el proyecto valga.
- **Disciplina de ingeniería.** Matemática en Python, LLM solo como orquestador,
  secretos fuera del repo, y ~100 tests sin red. Aburrido, pero es lo que hace que
  se pueda confiar en los números.

---

##  Aviso responsable

Esta es una **herramienta de análisis con fines informativos y educativos**.
**No** es asesoría financiera ni una recomendación de apuestas. Funciona solo en
modo **paper** (dinero ficticio): **no coloca apuestas reales ni automatiza casas**.
Las combinadas acumulan el margen de la casa y tienden a **valor esperado negativo**.
Apostar implica **riesgo de pérdida**; **los resultados pasados no garantizan los
futuros**. Solo para mayores de **18 años**. Si el juego es un problema para ti,
busca ayuda. **Juega con responsabilidad.**

---

<sub>Hecho con Python · modelo Poisson + ratings internacionales · agente Gemini ·
pytest. Predicciones de un modelo estadístico: el fútbol, por suerte, sigue siendo
incierto.</sub>
