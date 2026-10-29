"""API y plataforma React servidas en un solo puerto local."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import threading
import uuid
import zipfile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from trading.code_strategy import TEMPLATE, validate_code
from trading.data import DATA, ROOT, history, load_prices, parse_csv, save_run
from trading.engine import Config, backtest, indicators, monthly_returns
from trading.replay import advance

app = FastAPI(title="Nexo Local", docs_url=None, redoc_url=None)
executor = ThreadPoolExecutor(max_workers=2)
jobs, replays = {}, {}
lock = threading.Lock()


@app.middleware("http")
async def local_write_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin not in ("http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173", "http://127.0.0.1:5173"):
            return JSONResponse({"detail":"Origen no permitido"},status_code=403)
    return await call_next(request)


def clean(value):
    return json.loads(json.dumps(value, default=lambda v: v.isoformat() if isinstance(v,(datetime,pd.Timestamp)) else v.item() if isinstance(v,np.generic) else str(v)))


def records(frame):
    return json.loads(frame.to_json(orient="records",date_format="iso"))


def get_run(run_id):
    runs = history()
    rows = runs.loc[runs.id==run_id]
    if rows.empty: raise HTTPException(404,"Prueba no encontrada")
    row = rows.iloc[0]
    config, metrics, metadata = (json.loads(row[k]) for k in ("config","metrics","metadata"))
    folder = DATA/"runs"/str(run_id)
    eq = pd.read_csv(folder/"capital.csv",index_col="Date",parse_dates=True)
    trades = pd.read_csv(folder/"operaciones.csv")
    monthly = monthly_returns(eq,config["capital"])
    return dict(id=int(run_id),name=row["name"],created=row.created,config=config,metrics=metrics,metadata=metadata,
                equity=records(eq.reset_index()),trades=records(trades),monthly=records(monthly.reset_index()),
                positions=[],note="Posiciones cerradas al final del backtest; cartera histórica simulada.")


@app.get("/api/health")
def health(): return {"status":"ok","app":"nexo","version":2}


@app.get("/api/bootstrap")
def bootstrap():
    runs = list_runs()
    return {"template":TEMPLATE,"default_config":asdict(Config(strategy="Código Python",code=TEMPLATE)),
            "runs":runs,"latest":get_run(runs[0]["id"]) if runs else None,
            "strategies":list_strategies(),"last_completed":(datetime.now(ZoneInfo("America/Lima")).date()-timedelta(days=1)).isoformat()}


@app.get("/api/runs")
def list_runs():
    return [dict(id=int(r.id),name=r["name"],created=r.created,config=json.loads(r.config),metrics=json.loads(r.metrics)) for _,r in history().iterrows()]


@app.get("/api/runs/{run_id}")
def run_detail(run_id:int): return get_run(run_id)


def symbol_data(symbol,start,end,run_id=None,refresh=False):
    if run_id:
        imported = DATA/"runs"/str(run_id)/"prices"/f"{hashlib.sha256(symbol.encode()).hexdigest()}.csv"
        if imported.exists():
            return pd.read_csv(imported,index_col="Date",parse_dates=True),{"source":"Precios conservados con la prueba","symbol":symbol}
    return load_prices(symbol,start,end,refresh)


@app.get("/api/chart")
def chart(symbol:str="AAPL",start:str="2020-01-01",end:str="2024-12-31",run_id:int|None=None,fast:int=20,slow:int=200):
    try:
        config=Config(fast=fast,slow=slow)
        config.validate()
        frame,meta=symbol_data(symbol,start,end,run_id)
        f=indicators(frame,config).loc[start:end].copy()
        if f.empty: raise ValueError("No hay sesiones para este intervalo")
        f.index.name="Date"
        return {"symbol":symbol,"bars":records(f.reset_index()),"metadata":meta}
    except HTTPException: raise
    except Exception as exc: raise HTTPException(422,str(exc))


class RunRequest(BaseModel):
    name:str=Field(default="Estrategia Python",min_length=1,max_length=100)
    symbols:list[str]=Field(min_length=1,max_length=20)
    start:str
    end:str
    config:dict
    refresh:bool=False
    csv:str|None=Field(default=None,max_length=20_000_000)


def execute_job(job_id,request):
    try:
        cfg=Config(**request.config)
        cfg.validate()
        start,end=pd.Timestamp(request.start),pd.Timestamp(request.end)
        if start>=end or end.date()>=datetime.now(ZoneInfo("America/Lima")).date():
            raise ValueError("Selecciona fechas válidas y excluye el día en curso")
        symbols=list(dict.fromkeys(s.strip().upper() for s in request.symbols if s.strip()))
        if not symbols: raise ValueError("Introduce al menos un símbolo")
        datasets,metadata={},[]
        if request.csv:
            if len(symbols)!=1: raise ValueError("El CSV corresponde a un solo símbolo")
            f=parse_csv(request.csv.encode())
            datasets[symbols[0]]=f.loc[:request.end]
            metadata=[dict(symbol=symbols[0],source="CSV del usuario · sin verificación externa",rows=len(f),first=str(f.index[0].date()),last=str(f.index[-1].date()))]
        else:
            for i,s in enumerate(symbols):
                jobs[job_id].update(message=f"Descargando y validando {s}",progress=10+int(i/len(symbols)*55))
                datasets[s],meta=load_prices(s,request.start,request.end,request.refresh)
                metadata.append(meta)
        jobs[job_id].update(message="Simulando estrategia y cartera…",progress=70)
        result=backtest(datasets,cfg,request.start,request.end)
        config=dict(asdict(cfg),strategy_name=request.name,symbols=symbols,start=request.start,end=request.end,source="CSV" if request.csv else "Yahoo Finance")
        rid=save_run(request.name,config,result["metrics"],metadata,result["trades"],result["equity"])
        folder=DATA/"runs"/str(rid)/"prices"
        folder.mkdir(exist_ok=True)
        for s,f in datasets.items(): f.to_csv(folder/f"{hashlib.sha256(s.encode()).hexdigest()}.csv")
        jobs[job_id].update(status="done",message="Prueba completada",progress=100,run_id=rid)
    except Exception as exc:
        jobs[job_id].update(status="error",message=str(exc),progress=0)


@app.post("/api/jobs")
def submit(request:RunRequest):
    with lock:
        if sum(j["status"]=="running" for j in jobs.values())>=2: raise HTTPException(409,"Ya hay dos pruebas en ejecución")
        jid=uuid.uuid4().hex
        jobs[jid]=dict(status="running",message="Preparando prueba…",progress=5)
    executor.submit(execute_job,jid,request)
    return {"id":jid}


@app.get("/api/jobs/{job_id}")
def job_status(job_id:str):
    if job_id not in jobs: raise HTTPException(404,"Trabajo no encontrado")
    return jobs[job_id]


@app.get("/api/strategies")
def list_strategies():
    folder=DATA/"strategies"
    folder.mkdir(parents=True,exist_ok=True)
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)]


class StrategyRequest(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    code:str=Field(max_length=100_000)
    id:str|None=None


@app.post("/api/strategies")
def save_strategy(request:StrategyRequest):
    try: validate_code(request.code)
    except Exception as exc: raise HTTPException(422,str(exc))
    sid=request.id or uuid.uuid4().hex
    if not sid.isalnum() or len(sid)>64: raise HTTPException(422,"ID inválido")
    folder=DATA/"strategies"
    folder.mkdir(parents=True,exist_ok=True)
    result=dict(id=sid,name=request.name,code=request.code,updated=datetime.now().isoformat())
    (folder/f"{sid}.json").write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")
    return result


@app.post("/api/syntax")
def syntax(request:StrategyRequest):
    try: validate_code(request.code)
    except Exception as exc: raise HTTPException(422,str(exc))
    return {"message":"Sintaxis válida. La ejecución comprobará el contrato de señales."}


@app.get("/api/runs/{run_id}/export")
def export(run_id:int):
    r=get_run(run_id)
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("prueba.json",json.dumps(r,ensure_ascii=False,indent=2))
        z.writestr("metodologia.md",(ROOT/"README.md").read_text(encoding="utf-8"))
        for p in (DATA/"runs"/str(run_id)).rglob("*.csv"): z.write(p,str(p.relative_to(DATA/"runs"/str(run_id))))
    return Response(out.getvalue(),media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="nexo-prueba-{run_id}.zip"'})


@app.post("/api/validation/{run_id}")
def validation(run_id:int):
    r=get_run(run_id)
    cfg=Config(**{k:v for k,v in r["config"].items() if k in Config.__dataclass_fields__})
    datasets={s:symbol_data(s,r["config"]["start"],r["config"]["end"],run_id)[0] for s in r["config"]["symbols"]}
    cal=pd.DatetimeIndex([x["Date"] for x in r["equity"]])
    if len(cal)<60: raise HTTPException(422,"Se necesitan 60 sesiones")
    split=int(len(cal)*.7)
    ranking=[]
    try:
        for target in [1.,2.,3.,4.,5.]:
            result=backtest(datasets,replace(cfg,reward_r=target),r["config"]["start"],cal[split-1].date())
            ranking.append({"R":target,**result["metrics"]})
        ranking.sort(key=lambda x:x["retorno_pct"],reverse=True)
        test=backtest(datasets,replace(cfg,reward_r=ranking[0]["R"]),cal[split].date(),r["config"]["end"])
        return dict(ranking=ranking,best=ranking[0]["R"],split=str(cal[split].date()),test=test["metrics"])
    except Exception as exc: raise HTTPException(422,str(exc))


class ReplayRequest(BaseModel):
    run_id:int
    symbol:str


def replay_payload(rid):
    r=replays[rid]
    frame=r["frame"].iloc[:r["state"]["cursor"]+1].copy()
    frame.index.name="Date"
    return dict(id=rid,state=clean(r["state"]),bars=records(frame.reset_index()),total=len(r["frame"]))


@app.post("/api/replay")
def start_replay(request:ReplayRequest):
    r=get_run(request.run_id)
    if request.symbol not in r["config"]["symbols"]: raise HTTPException(422,"Símbolo fuera de la prueba")
    config=Config(**{k:v for k,v in r["config"].items() if k in Config.__dataclass_fields__})
    f,_=symbol_data(request.symbol,r["config"]["start"],r["config"]["end"],request.run_id)
    frame=indicators(f,replace(config,strategy="Retroceso EMA")).loc[r["config"]["start"]:r["config"]["end"]]
    rid=uuid.uuid4().hex
    replays[rid]=dict(frame=frame,config=config,state=dict(cursor=0,cash=config.capital,position=None,trades=[],curve=[dict(Fecha=frame.index[0],Capital=config.capital)]))
    return replay_payload(rid)


@app.post("/api/replay/{replay_id}/{action}")
def replay_step(replay_id:str,action:str):
    if replay_id not in replays: raise HTTPException(404,"Reinicia la práctica")
    if action not in ("Avanzar","Comprar","Vender"): raise HTTPException(422,"Acción desconocida")
    with lock:
        r=replays[replay_id]
        advance(r["state"],r["frame"],r["config"],action)
        return replay_payload(replay_id)


dist=ROOT/"frontend"/"dist"
if dist.exists(): app.mount("/",StaticFiles(directory=dist,html=True),name="frontend")
