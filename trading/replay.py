"""Replay manual: cada acción consume exclusivamente la siguiente vela."""
import math


def advance(state, frame, cfg, action="Avanzar"):
    if state["cursor"] >= len(frame)-1:
        return state
    state["cursor"] += 1
    day = frame.index[state["cursor"]]
    row = frame.iloc[state["cursor"]]
    fee, slip = cfg.commission_pct/100, cfg.slippage_bps/10000

    def sell(reference, reason):
        p = state["position"]
        price = reference*(1-slip)
        exit_fee = p["qty"]*price*fee
        state["cash"] += p["qty"]*price-exit_fee
        state["trades"].append({"Entrada": p["day"], "Salida": day, "Cantidad": p["qty"],
                                "Resultado USD": (price-p["price"])*p["qty"]-exit_fee-p["fee"], "Motivo": reason})
        state["position"] = None

    p = state["position"]
    if p:
        if row.Open <= p["stop"]:
            sell(row.Open, "Gap bajo stop")
        elif row.Open >= p["target"]:
            sell(row.Open, "Gap sobre objetivo")
        elif action == "Vender":
            sell(row.Open, "Venta manual")
    elif action == "Comprar":
        price = row.Open*(1+slip)
        stop = price*(1-cfg.stop_pct/100) if cfg.stop_mode == "Porcentaje" else frame.iloc[state["cursor"]-1].Low
        distance = price-stop
        if distance > 0:
            unit_risk = distance+stop*slip+price*fee+stop*(1-slip)*fee
            qty = math.floor(min(state["cash"]*cfg.risk_pct/100/unit_risk,
                                 state["cash"]*cfg.allocation_pct/100/(price*(1+fee))))
            if qty > 0:
                state["cash"] -= qty*price*(1+fee)
                state["position"] = {"qty": qty, "price": price, "stop": stop,
                                     "target": price+distance*cfg.reward_r, "day": day, "fee": qty*price*fee}
    p = state["position"]
    if p:
        if row.Low <= p["stop"]:
            sell(p["stop"], "Stop (ambos tocados)" if row.High >= p["target"] else "Stop loss")
        elif row.High >= p["target"]:
            sell(p["target"], "Take profit")
        elif state["cursor"] == len(frame)-1:
            sell(row.Close, "Fin de datos")
    value = state["cash"] + (state["position"]["qty"]*row.Close if state["position"] else 0)
    state["curve"].append({"Fecha": day, "Capital": value})
    return state
