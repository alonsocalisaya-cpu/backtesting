from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd


STRATEGIES = ["Código Python", "Retroceso EMA", "Cruce de medias", "RSI", "Ruptura de máximos", "Reglas personalizadas"]
RULES = ["Cierre > EMA rápida", "Cierre < EMA rápida", "EMA rápida > EMA lenta",
         "EMA rápida < EMA lenta", "RSI < umbral bajo", "RSI > umbral alto",
         "Cruce alcista EMA", "Cruce bajista EMA", "Cierre > máximo anterior",
         "Cierre < mínimo anterior", "Volumen > media de volumen"]


@dataclass
class Config:
    strategy: str = "Retroceso EMA"
    capital: float = 10000
    risk_pct: float = 1.0
    max_positions: int = 1
    allocation_pct: float = 100
    commission_pct: float = 0.1
    slippage_bps: float = 5
    stop_pct: float = 5
    reward_r: float = 3
    stop_mode: str = "Porcentaje"
    fast: int = 20
    slow: int = 200
    rsi_period: int = 14
    rsi_low: float = 30
    rsi_high: float = 70
    breakout: int = 20
    entry_rules: tuple = ("EMA rápida > EMA lenta", "Cierre > EMA rápida")
    exit_rules: tuple = ("Cierre < EMA rápida",)
    code: str = ""

    def validate(self):
        if self.strategy == "Código Python":
            from trading.code_strategy import validate_code
            validate_code(self.code)
        if self.strategy not in STRATEGIES:
            raise ValueError("Estrategia desconocida.")
        if not 2 <= self.fast < self.slow <= 400:
            raise ValueError("La EMA rápida debe ser menor que la lenta (máximo 400).")
        if self.capital <= 0 or not 0 < self.risk_pct <= 100 or not 0 < self.allocation_pct <= 100:
            raise ValueError("Capital, riesgo o asignación inválidos.")
        if not 1 <= self.max_positions <= 50 or not 0 < self.stop_pct < 100 or self.reward_r <= 0:
            raise ValueError("Posiciones, stop o beneficio/riesgo inválidos.")
        if not 0 <= self.commission_pct <= 5 or not 0 <= self.slippage_bps <= 500:
            raise ValueError("Costos fuera de rango.")
        if not 2 <= self.rsi_period <= 100 or not 0 < self.rsi_low < self.rsi_high < 100 or not 2 <= self.breakout <= 252:
            raise ValueError("Parámetros de indicadores inválidos.")
        if self.stop_mode not in ("Porcentaje", "Mínimo de señal"):
            raise ValueError("Modo de stop inválido.")
        if self.strategy == "Reglas personalizadas" and (not self.entry_rules or not self.exit_rules):
            raise ValueError("Selecciona al menos una regla de entrada y de salida.")
        if any(r not in RULES for r in (*self.entry_rules, *self.exit_rules)):
            raise ValueError("Regla desconocida.")


def indicators(frame, cfg):
    f = frame.copy()
    c = f.Close
    f["EMA_fast"] = c.ewm(span=cfg.fast, adjust=False, min_periods=cfg.fast).mean()
    f["EMA_slow"] = c.ewm(span=cfg.slow, adjust=False, min_periods=cfg.slow).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/cfg.rsi_period, adjust=False, min_periods=cfg.rsi_period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/cfg.rsi_period, adjust=False, min_periods=cfg.rsi_period).mean()
    f["RSI"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    f.loc[(loss == 0) & (gain > 0), "RSI"] = 100
    f.loc[(loss == 0) & (gain == 0), "RSI"] = 50
    f["Prev_high"] = f.High.shift(1).rolling(cfg.breakout).max()
    f["Prev_low"] = f.Low.shift(1).rolling(cfg.breakout).min()
    f["Volume_mean"] = f.Volume.shift(1).rolling(20).mean()
    cross_up = (f.EMA_fast > f.EMA_slow) & (f.EMA_fast.shift(1) <= f.EMA_slow.shift(1))
    cross_down = (f.EMA_fast < f.EMA_slow) & (f.EMA_fast.shift(1) >= f.EMA_slow.shift(1))
    rules = {RULES[0]: c > f.EMA_fast, RULES[1]: c < f.EMA_fast,
             RULES[2]: f.EMA_fast > f.EMA_slow, RULES[3]: f.EMA_fast < f.EMA_slow,
             RULES[4]: f.RSI < cfg.rsi_low, RULES[5]: f.RSI > cfg.rsi_high,
             RULES[6]: cross_up, RULES[7]: cross_down, RULES[8]: c > f.Prev_high,
             RULES[9]: c < f.Prev_low, RULES[10]: f.Volume > f.Volume_mean}
    if cfg.strategy == "Código Python":
        from trading.code_strategy import code_signals
        entries, exits = code_signals(f, cfg)
        f["Entry"] = entries
        f["Exit"] = exits
        return f
    if cfg.strategy == "Retroceso EMA":
        entry = (f.EMA_fast > f.EMA_slow) & (f.Low <= f.EMA_fast) & (c > f.EMA_fast)
        exit_ = c < f.EMA_fast
    elif cfg.strategy == "Cruce de medias":
        entry, exit_ = cross_up, cross_down
    elif cfg.strategy == "RSI":
        entry, exit_ = rules[RULES[4]], rules[RULES[5]]
    elif cfg.strategy == "Ruptura de máximos":
        entry, exit_ = rules[RULES[8]], rules[RULES[9]]
    else:
        entry = pd.concat([rules[r] for r in cfg.entry_rules], axis=1).all(axis=1)
        exit_ = pd.concat([rules[r] for r in cfg.exit_rules], axis=1).all(axis=1)
    # Preparación uniforme: ningún indicador opera antes de completar su calentamiento.
    ready = f[["EMA_slow", "RSI", "Prev_high", "Volume_mean"]].notna().all(axis=1)
    f["Entry"] = entry & ready
    f["Exit"] = exit_ & ready
    return f


def backtest(datasets: dict, cfg: Config, start, end):
    cfg.validate()
    if pd.Timestamp(start) >= pd.Timestamp(end):
        raise ValueError("La fecha inicial debe ser anterior a la final.")
    frames = {s: indicators(f, cfg) for s, f in sorted(datasets.items())}
    frames = {s: f for s, f in frames.items() if len(f.loc[str(start):str(end)]) >= 2}
    if not frames:
        raise ValueError("No hay sesiones suficientes para simular.")
    calendar = sorted(set().union(*(f.loc[str(start):str(end)].index for f in frames.values())))
    indexed = {s: {d: i for i, d in enumerate(f.index)} for s, f in frames.items()}
    last_dates = {s: f.loc[str(start):str(end)].index[-1] for s, f in frames.items()}
    fee, slip = cfg.commission_pct / 100, cfg.slippage_bps / 10000
    cash = cfg.capital
    positions, marks = {}, {}
    trades, curve = [], []
    skipped, ambiguous = 0, 0

    def close(symbol, day, reference, reason):
        nonlocal cash
        p = positions.pop(symbol)
        price = reference * (1 - slip)
        exit_fee = p["qty"] * price * fee
        pnl = p["qty"] * (price - p["entry"]) - exit_fee - p["entry_fee"]
        cash += p["qty"] * price - exit_fee
        trades.append({"Acción": symbol, "Señal": p["signal"], "Entrada": p["date"], "Salida": day,
                       "Cantidad": p["qty"], "Precio entrada": p["entry"], "Precio salida": price,
                       "Stop inicial": p["stop"], "Objetivo": p["target"],
                       "Comisiones": p["entry_fee"] + exit_fee, "Resultado USD": pnl,
                       "Resultado %": pnl / (p["qty"] * p["entry"]) * 100,
                       "R neto": pnl / p["risk"], "Días": (day-p["date"]).days, "Motivo": reason})

    for day in calendar:
        rows = {s: f.loc[day] for s, f in frames.items() if day in indexed[s]}
        previous = {s: frames[s].iloc[indexed[s][day]-1] for s in rows if indexed[s][day] > 0}
        # Salidas al abrir: señales conocidas del cierre anterior o gaps de stop/objetivo.
        exited = set()
        for s in list(positions):
            if s not in rows:
                continue
            r, p = rows[s], positions[s]
            if r.Open <= p["stop"]:
                close(s, day, r.Open, "Gap bajo stop")
            elif r.Open >= p["target"]:
                close(s, day, r.Open, "Gap sobre objetivo")
            elif s in previous and previous[s].Exit:
                close(s, day, r.Open, "Salida por señal")
            if s not in positions:
                exited.add(s)
        # La asignación se decide con precios de apertura; jamás con el cierre de hoy.
        equity_open = cash + sum(p["qty"] * (rows[s].Open if s in rows else marks[s]) for s, p in positions.items())
        for s in sorted(rows):
            r = rows[s]
            prev = previous.get(s)
            if prev is None or not prev.Entry or s in positions or s in exited or day == last_dates[s] or day == calendar[0]:
                continue
            signal_day = frames[s].index[indexed[s][day]-1]
            if signal_day < pd.Timestamp(start):
                continue
            if len(positions) >= cfg.max_positions:
                skipped += 1
                continue
            price = r.Open * (1 + slip)
            stop = float(prev.Low) if cfg.stop_mode == "Mínimo de señal" else price * (1-cfg.stop_pct/100)
            distance = price-stop
            if distance <= 0:
                skipped += 1
                continue
            # Incluye comisión de ambas piernas y deslizamiento estimado del stop.
            unit_risk = distance + stop*slip + price*fee + stop*(1-slip)*fee
            qty = math.floor(min(equity_open*cfg.risk_pct/100/unit_risk,
                                 equity_open*cfg.allocation_pct/100/(price*(1+fee)),
                                 cash/(price*(1+fee))))
            if qty < 1:
                skipped += 1
                continue
            entry_fee = qty*price*fee
            cash -= qty*price + entry_fee
            positions[s] = {"qty": qty, "entry": price, "date": day, "signal": signal_day,
                            "stop": stop, "target": price+distance*cfg.reward_r,
                            "entry_fee": entry_fee, "risk": qty*unit_risk}
        # Después de asignar capital se procesa el rango diario. Stop primero si ambos se tocan.
        for s in list(positions):
            if s not in rows:
                continue
            r, p = rows[s], positions[s]
            stop_hit, target_hit = r.Low <= p["stop"], r.High >= p["target"]
            if stop_hit:
                if target_hit:
                    ambiguous += 1
                close(s, day, p["stop"], "Stop (ambos tocados)" if target_hit else "Stop loss")
            elif target_hit:
                close(s, day, p["target"], "Take profit")
            elif day == last_dates[s]:
                close(s, day, r.Close, "Fin de datos")
        marks.update({s: float(r.Close) for s, r in rows.items()})
        value = cash + sum(p["qty"]*marks[s] for s, p in positions.items())
        curve.append({"Date": day, "Capital": value, "Efectivo": cash,
                      "Invertido": value-cash, "Posiciones": len(positions)})
    equity = pd.DataFrame(curve).set_index("Date")
    equity["Drawdown %"] = (equity.Capital/equity.Capital.cummax()-1)*100
    trades = pd.DataFrame(trades, columns=["Acción", "Señal", "Entrada", "Salida", "Cantidad", "Precio entrada",
                                         "Precio salida", "Stop inicial", "Objetivo", "Comisiones", "Resultado USD",
                                         "Resultado %", "R neto", "Días", "Motivo"])
    metrics = summarize(equity, trades, cfg.capital)
    return {"equity": equity, "trades": trades, "metrics": metrics, "frames": frames,
            "config": asdict(cfg), "skipped": skipped, "ambiguous": ambiguous}


def summarize(equity, trades, initial):
    final = float(equity.Capital.iloc[-1])
    years = (equity.index[-1]-equity.index[0]).days/365.25
    daily = equity.Capital.pct_change().dropna()
    net = trades["Resultado USD"]
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    sharpe = float(daily.mean()/daily.std()*np.sqrt(252)) if len(daily)>1 and daily.std()>0 else None
    return {"capital_final": final, "retorno_pct": (final/initial-1)*100,
            "cagr_pct": ((final/initial)**(1/years)-1)*100 if years > 0 and final > 0 else None,
            "drawdown_pct": float(equity["Drawdown %"].min()),
            "operaciones": len(trades), "aciertos_pct": float((net>0).mean()*100) if len(net) else 0,
            "profit_factor": float(wins/losses) if losses > 0 else None,
            "sharpe": sharpe, "comisiones": float(trades.Comisiones.sum()),
            "expectativa_usd": float(net.mean()) if len(net) else 0,
            "exposicion_pct": float((equity.Posiciones>0).mean()*100)}


def monthly_returns(equity, initial):
    closes = equity.Capital.resample("ME").last()
    returns = closes.pct_change()
    returns.iloc[0] = closes.iloc[0]/initial-1
    table = pd.DataFrame({"Año": returns.index.year, "Mes": returns.index.month, "Retorno": returns.values*100})
    return table.pivot(index="Año", columns="Mes", values="Retorno").reindex(columns=range(1,13))
