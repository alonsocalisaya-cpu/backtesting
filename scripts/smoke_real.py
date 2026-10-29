"""Verificación end-to-end con precios reales. Necesita acceso a Yahoo Finance."""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from dataclasses import asdict
from trading.data import load_prices, save_run
from trading.engine import Config, backtest

if __name__ == "__main__":
    cfg=Config()
    start,end="2020-01-01","2024-12-31"
    datasets,metadata={},[]
    for symbol in ["AAPL","MSFT","SPY"]:
        frame,meta=load_prices(symbol,start,end)
        print(f"{symbol}: {meta['rows']} sesiones reales ({meta['first']} - {meta['last']})")
        if symbol!="SPY": datasets[symbol]=frame
        metadata.append(meta)
    r=backtest(datasets,cfg,start,end)
    assert len(r["equity"])>1000
    assert len(r["trades"])>0
    assert r["equity"].Efectivo.min()>=-0.00001
    assert abs(r["metrics"]["capital_final"]-cfg.capital-r["trades"]["Resultado USD"].sum())<0.0001
    conf=dict(asdict(cfg),start=start,end=end,symbols=list(datasets),source="Yahoo Finance")
    rid=save_run("Verificación · EMA · datos reales",conf,r["metrics"],metadata,r["trades"],r["equity"])
    print(json.dumps(dict(run_id=rid,**r["metrics"]),indent=2))
