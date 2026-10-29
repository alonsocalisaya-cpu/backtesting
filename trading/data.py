from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def validate_prices(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    missing = set(PRICE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Faltan columnas de precios: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("La fuente no devolvió precios para este periodo.")
    index = pd.to_datetime(frame.index, errors="coerce")
    if index.isna().any():
        raise ValueError("Hay fechas inválidas.")
    if index.tz is not None:
        index = index.tz_localize(None)
    frame.index = index.normalize()
    frame.index.name = "Date"
    if frame.index.duplicated().any():
        raise ValueError("Hay fechas duplicadas; corrige la fuente antes de simular.")
    frame = frame.sort_index()[PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("Hay valores vacíos o no numéricos. No se rellenarán precios artificialmente.")
    if (frame[["Open", "High", "Low", "Close"]] <= 0).any().any() or (frame.Volume < 0).any():
        raise ValueError("Precios o volúmenes fuera de rango.")
    if ((frame.High < frame[["Open", "Close", "Low"]].max(axis=1)) |
            (frame.Low > frame[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError("Las aperturas/cierres no están dentro del máximo y mínimo de la vela.")
    return frame


def parse_csv(payload: bytes) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(payload), sep=None, engine="python")
    aliases = {"fecha": "Date", "date": "Date", "apertura": "Open", "open": "Open",
               "maximo": "High", "máximo": "High", "high": "High", "minimo": "Low",
               "mínimo": "Low", "low": "Low", "cierre": "Close", "close": "Close",
               "volumen": "Volume", "volume": "Volume"}
    frame = frame.rename(columns={c: aliases.get(str(c).strip().lower(), c) for c in frame})
    if "Date" not in frame:
        raise ValueError("El CSV debe contener Date o Fecha, con fechas ISO: AAAA-MM-DD.")
    frame["Date"] = pd.to_datetime(frame.Date, format="ISO8601", errors="raise")
    return validate_prices(frame.set_index("Date"))


def load_prices(symbol: str, start, end, refresh=False):
    import yfinance as yf

    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.^=\-]{1,24}", symbol):
        raise ValueError("Símbolo inválido.")
    DATA.mkdir(exist_ok=True)
    cache = DATA / "prices"
    cache.mkdir(exist_ok=True)
    # Calentamiento separado del periodo evaluado para EMA/RSI y reglas largas.
    download_start = pd.Timestamp(start) - timedelta(days=1500)
    download_end = pd.Timestamp(end) + timedelta(days=1)
    identity = f"{symbol}|{download_start.date()}|{download_end.date()}|adjusted-v1"
    key = hashlib.sha256(identity.encode()).hexdigest()
    csv = cache / f"{key}.csv"
    meta_path = cache / f"{key}.json"
    if csv.exists() and meta_path.exists() and not refresh:
        frame = validate_prices(pd.read_csv(csv, index_col="Date", parse_dates=True))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["cache"] = True
    else:
        yf.set_tz_cache_location(str(DATA / "yfinance"))
        frame = yf.download(symbol, start=download_start.date().isoformat(),
                            end=download_end.date().isoformat(), interval="1d",
                            auto_adjust=True, actions=False, progress=False,
                            threads=False, timeout=25, multi_level_index=False)
        frame = validate_prices(frame)
        meta = {"symbol": symbol, "source": "Yahoo Finance / yfinance",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "adjustment": "OHLC ajustados por dividendos y splits (auto_adjust=True)",
                "sha256": hashlib.sha256(frame.to_csv().encode()).hexdigest(),
                "cache": False}
        csv.write_text(frame.to_csv(), encoding="utf-8")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    usable = frame.loc[str(start):str(end)]
    if len(usable) < 2:
        raise ValueError(f"{symbol}: menos de dos sesiones dentro del periodo solicitado.")
    meta = dict(meta, first=str(usable.index[0].date()), last=str(usable.index[-1].date()),
                rows=len(usable), warmup=len(frame.loc[frame.index < pd.Timestamp(start)]))
    return frame, meta


def connect():
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DATA / "research.sqlite")
    con.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, created TEXT, name TEXT, config TEXT, metrics TEXT, metadata TEXT)")
    return con


def save_run(name, config, metrics, metadata, trades, equity):
    with connect() as con:
        cur = con.execute("INSERT INTO runs (created,name,config,metrics,metadata) VALUES (?,?,?,?,?)",
                          (datetime.now(timezone.utc).isoformat(), name,
                           json.dumps(config, ensure_ascii=False), json.dumps(metrics, ensure_ascii=False),
                           json.dumps(metadata, ensure_ascii=False)))
        run_id = cur.lastrowid
    folder = DATA / "runs" / str(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    trades.to_csv(folder / "operaciones.csv", index=False)
    equity.to_csv(folder / "capital.csv")
    return run_id


def history():
    with connect() as con:
        return pd.read_sql_query("SELECT * FROM runs ORDER BY id DESC LIMIT 200", con)
