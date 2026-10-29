from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import zipfile
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from trading.data import DATA, history, load_prices, parse_csv, save_run
from trading.engine import Config, RULES, STRATEGIES, backtest, indicators, monthly_returns
from trading.replay import advance
from trading.code_strategy import TEMPLATE, validate_code

st.set_page_config(page_title="Nexo · Laboratorio de estrategias", page_icon="📈", layout="wide")
st.markdown("""<style>
.block-container {padding-top:3.5rem; max-width:none}
h1 {letter-spacing:-1.4px!important; font-weight:750!important}
[data-testid="stMetric"] {background:#141d2c;border:1px solid #263449;border-radius:12px;padding:16px}
[data-testid="stMetricLabel"] {color:#9baac2}
[data-testid="stSidebar"] {border-right:1px solid #263449}
.eyebrow {color:#38d6b0;letter-spacing:3px;font-size:12px;font-weight:700}
.hero {color:#9baac2;font-size:17px;margin-bottom:1.5rem}
div.stButton>button[kind="primary"] {font-weight:700}
</style>""", unsafe_allow_html=True)


def money(v):
    return f"US$ {v:,.2f}"


def fmt(v, suffix=""):
    return "N/D" if v is None or pd.isna(v) else f"{v:,.2f}{suffix}"


def style_chart(fig, height=400):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=height,
                      margin=dict(l=15,r=15,t=35,b=15), legend=dict(orientation="h",y=1.12),
                      font=dict(family="Arial",color="#bac7db"))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#233047")
    return fig


def candle_chart(frame, trades=None, height=620):
    fig = make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[0.78,0.22],vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(x=frame.index,open=frame.Open,high=frame.High,low=frame.Low,
                                close=frame.Close,name="Precio ajustado",increasing_line_color="#38d6b0",
                                decreasing_line_color="#f3778c"),row=1,col=1)
    for key,name,color in [("EMA_fast","EMA rápida","#65a7ff"),("EMA_slow","EMA lenta","#f5c76d")]:
        fig.add_trace(go.Scatter(x=frame.index,y=frame[key],name=name,line=dict(color=color,width=1.5)),row=1,col=1)
    fig.add_trace(go.Bar(x=frame.index,y=frame.Volume,name="Volumen",marker_color="#314765"),row=2,col=1)
    if trades is not None and not trades.empty:
        for x,y,name,color,symbol in [("Entrada","Precio entrada","Compra","#38d6b0","triangle-up"),
                                      ("Salida","Precio salida","Venta","#f3778c","triangle-down")]:
            subset = trades.loc[(pd.to_datetime(trades[x]) >= frame.index[0]) & (pd.to_datetime(trades[x]) <= frame.index[-1])]
            fig.add_trace(go.Scatter(x=subset[x],y=subset[y],mode="markers",name=name,
                                    marker=dict(color=color,symbol=symbol,size=11)),row=1,col=1)
    fig.update_layout(xaxis_rangeslider_visible=False,xaxis2_rangeslider_visible=False)
    fig.update_layout(dragmode="pan", hovermode="x unified", uirevision="chart")
    fig.update_yaxes(side="right",showspikes=True,spikemode="across",spikesnap="cursor")
    fig.update_xaxes(showspikes=True,spikemode="across",spikesnap="cursor")
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat","mon"])])
    return style_chart(fig,height)


def report_zip(result):
    out = io.BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("configuracion.json",json.dumps(result["config"],ensure_ascii=False,indent=2))
        z.writestr("metricas.json",json.dumps(result["metrics"],ensure_ascii=False,indent=2))
        z.writestr("fuentes.json",json.dumps(result["metadata"],ensure_ascii=False,indent=2))
        z.writestr("operaciones.csv",result["trades"].to_csv(index=False))
        z.writestr("capital.csv",result["equity"].to_csv())
        for s,f in result["datasets"].items():
            z.writestr(f"precios/{s.replace('^','')}.csv",f.to_csv())
        if result.get("benchmark") is not None:
            z.writestr("benchmark.csv",result["benchmark"].to_csv())
        z.writestr("metodologia.txt",METHODOLOGY)
    return out.getvalue()


METHODOLOGY = """Velas diarias. Solo compras, acciones enteras, sin apalancamiento.
Señales al cierre; ejecución en la apertura de la siguiente sesión del activo.
Capital compartido; prioridad alfabética si varias acciones generan señales simultáneas.
El riesgo objetivo incluye costos estimados. Un gap puede superar ese riesgo.
Los tamaños se deciden antes de conocer los máximos, mínimos y cierres del día.
Stops fijos y objetivos R desde el precio ejecutado. Si ambos se tocan se aplica el stop primero.
Gaps: stop al precio de apertura; objetivo al precio de apertura si es superior.
Deslizamiento adverso en compras y ventas; comisión porcentual por cada ejecución.
No se reutiliza en la apertura el efectivo liberado por ventas intradía posteriores.
Cierre forzoso al último cierre disponible de cada activo, aplicando costos.
Yahoo: OHLC ajustados por splits y dividendos, equivalentes de precios ajustados;
no son cotizaciones nominales de ejecución. No se suman dividendos de nuevo como efectivo.
CSV: procedencia declarada por usuario; sin verificación externa ni ajustes automáticos.
Calentamiento de indicadores previo al periodo evaluado; no genera operaciones previas.
Sharpe sobre rendimientos diarios, 252 sesiones/año, tasa libre de riesgo 0.
CAGR sobre días naturales; profit factor N/D si no hay pérdidas.
Exposición: porcentaje de sesiones con posiciones abiertas al cierre.
Benchmark: comprar y mantener fraccionado desde el primer cierre común, sin costos.
Retornos mensuales compuestos; el primer mes puede ser parcial.
Datos faltantes no se inventan. Para valorar posiciones durante huecos se usa el último cierre conocido.
El universo lo elige el usuario; no se corrige sesgo de supervivencia ni se incluyen retiradas automáticamente.
Los resultados históricos no garantizan resultados futuros.
"""


today = datetime.now(ZoneInfo("America/Lima")).date()
last_completed = today-timedelta(days=1)
with st.sidebar:
    st.markdown("### ◈ NEXO")
    st.caption("LABORATORIO DE ESTRATEGIAS")
    st.divider()
    st.markdown("**1 · Datos y periodo**")
    source = st.radio("Fuente",["Yahoo Finance","Archivo CSV"],horizontal=True)
    tickers_text = st.text_input("Acciones / ETF", "AAPL, MSFT, NVDA",help="Símbolos de Yahoo separados por comas. Máximo 20. Cartera con capital compartido.")
    upload = st.file_uploader("CSV: Date, Open, High, Low, Close, Volume",type=["csv"]) if source == "Archivo CSV" else None
    if source == "Archivo CSV":
        st.caption("Un activo por CSV. Usa un solo símbolo. La procedencia y los ajustes son responsabilidad del archivo.")
    start = st.date_input("Desde",date(2016,1,1),max_value=last_completed-timedelta(days=1),min_value=date(1970,1,1))
    end = st.date_input("Hasta (incluido)",last_completed,max_value=last_completed,min_value=date(1970,1,2))
    refresh = st.checkbox("Volver a descargar precios",help="Sin esta opción, se reutiliza la descarga guardada para este mismo periodo.")
    use_benchmark = st.checkbox("Comparar con SPY",value=True)
    st.markdown("**2 · Estrategia**")
    strategy = st.selectbox("Motor de estrategia",STRATEGIES)
    descriptions = {"Retroceso EMA":"Compra cuando EMA rápida > lenta, el mínimo toca la rápida y el cierre queda encima. Sale si cierra debajo de la rápida.",
                    "Cruce de medias":"Compra tras un cruce alcista de EMA rápida/lenta; vende tras el cruce bajista.",
                    "RSI":"Compra con RSI bajo el umbral inferior; vende cuando supera el superior.",
                    "Ruptura de máximos":"Compra al cerrar sobre el máximo de las N sesiones anteriores; vende bajo el mínimo anterior.",
                    "Reglas personalizadas":"Combina condiciones: todas las de entrada deben cumplirse (Y); igual para las de salida."}
    descriptions["Código Python"] = "Escribe tu lógica en el editor principal. Las órdenes se simulan con los costos y límites de esta barra."
    st.caption(descriptions[strategy])
    with st.expander("Indicadores y reglas",expanded=strategy=="Reglas personalizadas"):
        fast = st.number_input("EMA rápida",2,399,20)
        slow = st.number_input("EMA lenta",3,400,200)
        rsi_period = st.number_input("Periodo RSI",2,100,14)
        rsi_low = st.number_input("RSI inferior",1,98,30)
        rsi_high = st.number_input("RSI superior",2,99,70)
        breakout = st.number_input("Ventana de ruptura",2,252,20)
        entry_rules = st.multiselect("Entrada: todas las condiciones",RULES,default=RULES[:1]+RULES[2:3])
        exit_rules = st.multiselect("Salida: todas las condiciones",RULES,default=[RULES[1]])
    st.markdown("**3 · Capital y ejecución**")
    capital = st.number_input("Capital inicial (USD)",100.0,100000000.0,10000.0,1000.0)
    risk = st.number_input("Riesgo objetivo por operación (%)",0.1,100.0,1.0,0.1)
    max_positions = st.number_input("Posiciones simultáneas",1,20,1)
    with st.expander("Stops, objetivos y costos",expanded=True):
        stop_mode = st.selectbox("Stop loss",["Porcentaje","Mínimo de señal"])
        stop_pct = st.number_input("Distancia del stop (%)",0.1,99.0,5.0,0.5,disabled=stop_mode!="Porcentaje")
        reward = st.number_input("Objetivo (múltiplos R)",0.1,20.0,3.0,0.5)
        allocation = st.number_input("Máximo capital por activo (%)",1.0,100.0,100.0,5.0)
        commission = st.number_input("Comisión por ejecución (%)",0.0,5.0,0.1,0.01,format="%.2f")
        slippage = st.number_input("Deslizamiento (puntos básicos)",0.0,500.0,5.0,1.0,help="10 puntos básicos equivalen al 0,1 % del precio.")
    run = st.button("▶ Ejecutar backtesting",type="primary",width="stretch")
    st.caption("LOCAL · PUERTO 8000 · VELAS 1D")

st.markdown('<div class="eyebrow">NEXO / CHART WORKSPACE / 1D</div>',unsafe_allow_html=True)
st.title("Gráfico · Estrategia · Backtest")
code = st.session_state.get("strategy_source",TEMPLATE)
if strategy == "Código Python":
    with st.expander("⌨ Editor de estrategia · Python",expanded=not bool(st.session_state.get("result"))):
        st.caption("Como un editor de estrategias: modifica el código y pulsa Ejecutar backtesting en la barra lateral. Python; no ejecuta Pine Script directamente.")
        code = st.text_area("Código fuente",value=TEMPLATE,key="strategy_source",height=400)
        a,b = st.columns(2)
        if a.button("Comprobar sintaxis"):
            try:
                validate_code(code)
                st.success("Sintaxis válida. La ejecución verificará las señales por vela.")
            except Exception as exc:
                st.error(str(exc))
        b.download_button("Guardar estrategia .py",code,"estrategia.py","text/x-python")
        st.caption("El código se ejecuta localmente con permisos de tu usuario. Usa código propio o de confianza. Recibe solo el histórico hasta la vela actual; no consultes fuentes futuras desde tu código.")
        with st.expander("Contrato y ejemplos de código"):
            st.write("on_bar(data, params) devuelve dos booleanos: comprar y vender. Puedes calcular indicadores con Pandas, importar librerías instaladas y escribir funciones auxiliares. Stops, capital y objetivos se configuran en la barra lateral.")
            st.code('''# Cruce de medias: dentro de on_bar
actual, anterior = data.iloc[-1], data.iloc[-2]
comprar = actual.EMA_fast > actual.EMA_slow and anterior.EMA_fast <= anterior.EMA_slow
vender = actual.EMA_fast < actual.EMA_slow
return bool(comprar), bool(vender)''',language="python")

cfg = Config(strategy=strategy,code=code,capital=capital,risk_pct=risk,max_positions=max_positions,
             allocation_pct=allocation,commission_pct=commission,slippage_bps=slippage,
             stop_pct=stop_pct,reward_r=reward,stop_mode=stop_mode,fast=fast,slow=slow,
             rsi_period=rsi_period,rsi_low=rsi_low,rsi_high=rsi_high,breakout=breakout,
             entry_rules=tuple(entry_rules),exit_rules=tuple(exit_rules))
fingerprint = hashlib.sha256(json.dumps([asdict(cfg),str(start),str(end),source,tickers_text,use_benchmark,
                                       hashlib.sha256(upload.getvalue()).hexdigest() if upload else None],sort_keys=True).encode()).hexdigest()

st.caption("Configura la prueba en la barra lateral. Si está oculta, ábrela con la flecha de la esquina superior izquierda.")

if run:
    try:
        cfg.validate()
        if start >= end:
            raise ValueError("Selecciona una fecha inicial anterior a la final.")
        symbols = list(dict.fromkeys(s.strip().upper() for s in tickers_text.split(",") if s.strip()))
        if not 1 <= len(symbols) <= 20:
            raise ValueError("Introduce entre 1 y 20 símbolos.")
        datasets, metadata = {}, []
        with st.status("Preparando datos históricos…",expanded=True) as status:
            if source == "Archivo CSV":
                if not upload or len(symbols)!=1:
                    raise ValueError("Adjunta el CSV e introduce un único símbolo para identificarlo.")
                import re
                if not re.fullmatch(r"[A-Z0-9.^=\-]{1,24}",symbols[0]):
                    raise ValueError("Símbolo inválido.")
                f = parse_csv(upload.getvalue())
                visible = f.loc[str(start):str(end)]
                if len(visible)<2:
                    raise ValueError("El CSV no cubre dos sesiones del periodo.")
                # Nunca utilizar fechas posteriores al intervalo evaluado.
                datasets[symbols[0]] = f.loc[:str(end)]
                metadata.append(dict(symbol=symbols[0],source="CSV del usuario (sin verificación externa)",
                                     first=str(visible.index[0].date()),last=str(visible.index[-1].date()),rows=len(visible),
                                     downloaded_at="No aplica",adjustment="Según archivo, sin ajuste automático",
                                     sha256=hashlib.sha256(upload.getvalue()).hexdigest(),warmup=len(f.loc[f.index<pd.Timestamp(start)])))
            else:
                for s in symbols:
                    st.write(f"Descargando / validando {s}…")
                    datasets[s], meta = load_prices(s,start,end,refresh)
                    metadata.append(meta)
            benchmark = None
            if use_benchmark:
                st.write("Preparando referencia SPY…")
                bf,bm = load_prices("SPY",start,end,refresh)
                benchmark = bf.loc[str(start):str(end),"Close"]
                metadata.append(dict(bm,role="benchmark"))
            result = backtest(datasets,cfg,start,end)
            result.update(datasets=datasets,metadata=metadata,benchmark=benchmark,start=str(start),end=str(end),fingerprint=fingerprint)
            saved_config = dict(result["config"],start=str(start),end=str(end),symbols=symbols,source=source)
            result["id"] = save_run(strategy,saved_config,result["metrics"],metadata,result["trades"],result["equity"])
            result["config"] = saved_config
            st.session_state.result = result
            for key in ("optimization","replay_state","replay_identity"):
                st.session_state.pop(key,None)
            status.update(label="Datos validados · simulación guardada",state="complete",expanded=False)
    except Exception as exc:
        st.error(f"No se pudo ejecutar: {exc}")
        st.info("No se generaron precios de ejemplo. Si Yahoo no responde, reintenta o carga tu CSV histórico.")

result = st.session_state.get("result")
if result:
    left,right = st.columns([3,1])
    symbol = left.selectbox("Símbolo del gráfico",list(result["frames"]),key="workspace_symbol")
    logarithmic = right.checkbox("Escala logarítmica")
    frame = result["frames"][symbol].loc[result["start"]:result["end"]]
    trades = result["trades"].loc[result["trades"]["Acción"]==symbol]
    chart = candle_chart(frame,trades)
    if logarithmic:
        chart.update_yaxes(type="log",row=1,col=1)
    st.plotly_chart(chart,width="stretch",key="workspace_chart",config={"scrollZoom":True,"displaylogo":False,"modeBarButtonsToAdd":["drawline","drawrect","eraseshape"]})
    st.caption("Rueda: zoom · arrastrar: desplazar · doble clic: restablecer · barra del gráfico: líneas y rectángulos.")
tabs = st.tabs(["Probador de estrategias","Operaciones","Datos","Validación","Replay","Historial","Guía"])
if result and fingerprint != result["fingerprint"]:
    st.warning("Cambiaste la configuración. Los resultados visibles corresponden a la última prueba guardada; pulsa Ejecutar para actualizar.")

with tabs[0]:
    if not result:
        st.info("Empieza en el panel izquierdo: elige tus acciones, configura la estrategia y pulsa Ejecutar backtesting.")
        a,b,c = st.columns(3)
        for col,title,body in [(a,"01 · Precios de mercado","Descarga históricos diarios o importa tu archivo. Consulta fuente, cobertura y fecha de descarga."),
                                (b,"02 · Reglas claras","Prueba EMA, RSI, rupturas o combina tus propias condiciones. El capital y los costos se simulan juntos."),
                                (c,"03 · Resultados auditables","Revisa cada entrada y salida, compara con SPY y exporta la prueba completa.")]:
            with col:
                with st.container(border=True):
                    st.subheader(title)
                    st.write(body)
        st.caption("No hay resultados precargados ni precios simulados. Solo se mostrará una prueba después de cargar y validar datos.")
    else:
        m = result["metrics"]
        st.caption(f"PRUEBA #{result['id']} · {result['config']['strategy']} · {result['start']} → {result['end']} · {', '.join(result['datasets'])}")
        cols = st.columns(4)
        for col,label,value in zip(cols,["Capital final","Rentabilidad neta","Caída máxima","Operaciones"],
                                   [money(m["capital_final"]),fmt(m["retorno_pct"]," %"),fmt(m["drawdown_pct"]," %"),str(m["operaciones"])]):
            col.metric(label,value)
        cols = st.columns(4)
        for col,label,value in zip(cols,["CAGR · anualizado","Operaciones ganadoras","Profit factor","Sharpe · rf = 0"],
                                   [fmt(m["cagr_pct"]," %"),fmt(m["aciertos_pct"]," %"),fmt(m["profit_factor"]),fmt(m["sharpe"])]):
            col.metric(label,value)
        eq = result["equity"]
        fig = go.Figure(go.Scatter(x=eq.index,y=eq.Capital,name="Estrategia",line=dict(color="#38d6b0",width=2.5),fill="tozeroy",fillcolor="rgba(56,214,176,0.06)"))
        if result["benchmark"] is not None:
            bm = result["benchmark"].reindex(eq.index).ffill()
            if not bm.dropna().empty:
                fig.add_trace(go.Scatter(x=bm.index,y=bm/bm.dropna().iloc[0]*result["config"]["capital"],name="SPY · comprar y mantener",line=dict(color="#7385a4",dash="dot")))
        st.plotly_chart(style_chart(fig),width="stretch",key="equity")
        left,right = st.columns([2,1])
        with left:
            st.subheader("Rentabilidad mensual")
            table = monthly_returns(eq,result["config"]["capital"])
            table.columns = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
            heat = px.imshow(table,text_auto=".1f",color_continuous_scale=["#bd536c","#141d2c","#38d6b0"],color_continuous_midpoint=0,aspect="auto",labels=dict(color="Retorno %"))
            st.plotly_chart(style_chart(heat,max(260,len(table)*32+100)),width="stretch")
        with right:
            st.subheader("Detalle de ejecución")
            st.write(f"Comisiones totales: **{money(m['comisiones'])}**")
            st.write(f"Resultado medio por operación: **{money(m['expectativa_usd'])}**")
            st.write(f"Sesiones invertidas al cierre: **{m['exposicion_pct']:.1f} %**")
            st.write(f"Señales omitidas por límites: **{result['skipped']}**")
            st.write(f"Velas con stop y objetivo tocados: **{result['ambiguous']}**")
            st.download_button("↓ Descargar informe completo (.zip)",report_zip(result),file_name=f"backtest_{result['id']}.zip",mime="application/zip",width="stretch")
            st.caption("Precios Yahoo ajustados por dividendos y splits. El benchmark no incluye costos; la estrategia sí.")

with tabs[1]:
    if result:
        symbol = st.selectbox("Ver activo",list(result["frames"]),key="chart_symbol")
        frame = result["frames"][symbol].loc[result["start"]:result["end"]]
        tr = result["trades"]
        tr = tr.loc[tr["Acción"]==symbol]
        st.subheader("Registro de operaciones")
        st.dataframe(result["trades"],hide_index=True,width="stretch")
        st.download_button("↓ Operaciones CSV",result["trades"].to_csv(index=False).encode("utf-8-sig"),"operaciones.csv","text/csv")
        dd = go.Figure(go.Scatter(x=result["equity"].index,y=result["equity"]["Drawdown %"],fill="tozeroy",line_color="#f3778c",name="Drawdown %"))
        st.plotly_chart(style_chart(dd,250),width="stretch")
    else:
        st.info("Ejecuta una prueba para inspeccionar sus operaciones.")

with tabs[2]:
    st.subheader("Conoce los datos que estás probando")
    if result:
        st.dataframe(pd.DataFrame(result["metadata"]),hide_index=True,width="stretch")
        for meta in result["metadata"]:
            if meta.get("role")=="benchmark":
                continue
            if pd.Timestamp(meta["first"]) > pd.Timestamp(result["start"])+pd.Timedelta(days=7):
                st.warning(f"{meta['symbol']}: empieza en {meta['first']}; no cubre todo el periodo solicitado.")
            if pd.Timestamp(meta["last"]) < pd.Timestamp(result["end"])-pd.Timedelta(days=7):
                st.warning(f"{meta['symbol']}: termina en {meta['last']}; revisa la cobertura final.")
            if meta["warmup"] < result["config"]["slow"]:
                st.warning(f"{meta['symbol']}: calentamiento insuficiente antes del inicio. No operará hasta completar los indicadores.")
        for s,f in result["datasets"].items():
            gaps = f.loc[result["start"]:result["end"]].index.to_series().diff().dt.days
            if (gaps>7).any():
                st.warning(f"{s}: hay saltos de más de 7 días naturales. Comprueba suspensiones, festivos o datos ausentes.")
        s = st.selectbox("Exportar serie",list(result["datasets"]),key="export_symbol")
        st.download_button("↓ Precios utilizados CSV",result["datasets"][s].to_csv().encode("utf-8-sig"),f"{s}_diario.csv","text/csv")
    st.write("Se rechazan fechas duplicadas, precios imposibles, valores vacíos y velas incoherentes. Esta validación comprueba estructura y coherencia; no certifica la exactitud del proveedor.")
    st.caption("Las fechas sin sesión no se rellenan con velas inventadas. Un CSV se identifica siempre como fuente del usuario.")

with tabs[3]:
    st.subheader("Ajusta en el pasado. Evalúa después.")
    st.write("Separa cronológicamente los datos. Se elige el mejor objetivo R en entrenamiento y se prueba una sola vez en el tramo posterior, sin elegir parámetros con sus resultados.")
    train_pct = st.slider("Porcentaje de sesiones para entrenamiento",50,85,70,5)
    st.caption("Se comparan objetivos 1R, 2R, 3R, 4R y 5R. Los demás parámetros corresponden a la prueba guardada. Cada tramo empieza con capital nuevo y posiciones cerradas.")
    if result and st.button("Evaluar separación temporal"):
        try:
            saved = {k:v for k,v in result["config"].items() if k in Config.__dataclass_fields__}
            rcfg = Config(**saved)
            cal = result["equity"].index
            if len(cal)<60:
                raise ValueError("Se necesitan al menos 60 sesiones para separar los tramos.")
            split = int(len(cal)*train_pct/100)
            rows = []
            with st.spinner("Evaluando entrenamiento y periodo posterior…"):
                for r in [1.,2.,3.,4.,5.]:
                    trial = backtest(result["datasets"],replace(rcfg,reward_r=r),result["start"],cal[split-1].date())
                    rows.append({"Objetivo R":r,"Retorno entrenamiento %":trial["metrics"]["retorno_pct"],"Operaciones":trial["metrics"]["operaciones"]})
                ranking = pd.DataFrame(rows).sort_values("Retorno entrenamiento %",ascending=False)
                best = float(ranking.iloc[0]["Objetivo R"])
                test = backtest(result["datasets"],replace(rcfg,reward_r=best),cal[split].date(),result["end"])
                st.session_state.optimization = dict(ranking=ranking,best=best,metrics=test["metrics"],split=str(cal[split].date()),train_pct=train_pct)
        except Exception as exc:
            st.error(str(exc))
    if "optimization" in st.session_state:
        o = st.session_state.optimization
        st.caption(f"Separación evaluada: {o['train_pct']} % entrenamiento. Fuera de muestra desde {o['split']}.")
        st.dataframe(o["ranking"],hide_index=True,width="stretch")
        st.success(f"Elegido en entrenamiento: {o['best']:g}R · Retorno posterior: {o['metrics']['retorno_pct']:.2f} % · Operaciones posteriores: {o['metrics']['operaciones']}")
        st.warning("Si cambias parámetros después de mirar el tramo posterior, ese tramo deja de ser una evaluación independiente. Reserva otro periodo para confirmar.")

with tabs[4]:
    st.subheader("Practica sin ver la siguiente vela")
    st.write("Cada botón avanza una sesión. Comprar y vender ejecutan en la próxima apertura; el stop y objetivo se comprueban dentro de esa nueva vela.")
    if result:
        rs = st.selectbox("Activo para practicar",list(result["frames"]),key="replay_symbol")
        rf = result["frames"][rs].loc[result["start"]:result["end"]]
        identity = f"{result['id']}:{rs}"
        if st.session_state.get("replay_identity") != identity:
            st.session_state.replay_identity = identity
            st.session_state.replay_state = dict(cursor=0,cash=result["config"]["capital"],position=None,trades=[],curve=[{"Fecha":rf.index[0],"Capital":result["config"]["capital"]}])
        state = st.session_state.replay_state
        saved = {k:v for k,v in result["config"].items() if k in Config.__dataclass_fields__}
        rcfg = Config(**saved)
        a,b,c,d = st.columns(4)
        action = None
        finished = state["cursor"]>=len(rf)-1
        if a.button("⏭ Siguiente vela",disabled=finished,width="stretch"):
            action="Avanzar"
        if b.button("Comprar y avanzar",disabled=finished or state["position"] is not None,width="stretch"):
            action="Comprar"
        if c.button("Vender y avanzar",disabled=finished or state["position"] is None,width="stretch"):
            action="Vender"
        if d.button("Reiniciar práctica",width="stretch"):
            st.session_state.pop("replay_identity",None)
            st.rerun()
        if action:
            advance(state,rf,rcfg,action)
            st.rerun()
        visible = rf.iloc[:state["cursor"]+1]
        st.caption(f"Fecha visible: {visible.index[-1].date()} · Sesión {state['cursor']+1} / {len(rf)} · Riesgo {rcfg.risk_pct:g} % · Objetivo {rcfg.reward_r:g}R")
        st.metric("Capital de la práctica",money(state["curve"][-1]["Capital"]))
        st.plotly_chart(candle_chart(visible.iloc[-150:],height=450),width="stretch",key="replay_chart")
        if state["position"]:
            p = state["position"]
            st.info(f"Posición: {p['qty']} acciones · Entrada {p['price']:.2f} · Stop {p['stop']:.2f} · Objetivo {p['target']:.2f}")
        st.dataframe(pd.DataFrame(state["trades"]),hide_index=True,width="stretch")
        st.download_button("↓ Diario de práctica",pd.DataFrame(state["trades"]).to_csv(index=False).encode("utf-8-sig"),"replay.csv","text/csv")
        st.caption("Para practicar a ciegas, usa esta pestaña sin consultar antes el gráfico completo ni las operaciones automáticas.")
    else:
        st.info("Carga una prueba para disponer de un histórico para practicar.")

with tabs[5]:
    st.subheader("Tu cuaderno de investigación")
    runs = history()
    if runs.empty:
        st.info("Tus pruebas se guardarán aquí automáticamente.")
    else:
        records=[]
        for _,r in runs.iterrows():
            m=json.loads(r.metrics)
            conf=json.loads(r.config)
            records.append({"ID":r.id,"Fecha UTC":r.created,"Estrategia":r["name"],"Activos":", ".join(conf.get("symbols",[])),"Periodo":f"{conf.get('start','')} → {conf.get('end','')}","Retorno %":m["retorno_pct"],"Drawdown %":m["drawdown_pct"],"Operaciones":m["operaciones"]})
        st.dataframe(pd.DataFrame(records),hide_index=True,width="stretch")
        choices=st.multiselect("Comparar curvas guardadas (máximo 4)",[r["ID"] for r in records],max_selections=4)
        if choices:
            fig=go.Figure()
            for rid in choices:
                path=DATA/"runs"/str(rid)/"capital.csv"
                if path.exists():
                    e=pd.read_csv(path,index_col="Date",parse_dates=True)
                    conf=json.loads(runs.loc[runs.id==rid,"config"].iloc[0])
                    fig.add_trace(go.Scatter(x=e.index,y=(e.Capital/conf["capital"]-1)*100,name=f"Prueba #{rid}"))
            st.plotly_chart(style_chart(fig),width="stretch")
            st.caption("Curvas en rentabilidad porcentual. Compara pruebas con periodos, universos y costos equivalentes.")
        rid=st.selectbox("Inspeccionar configuración guardada",runs.id.tolist())
        st.json(json.loads(runs.loc[runs.id==rid,"config"].iloc[0]))

with tabs[6]:
    st.subheader("Cómo hacer tu primera prueba")
    st.markdown("""1. Introduce símbolos separados por comas, por ejemplo **AAPL, MSFT, NVDA**.
2. Elige las fechas y una estrategia. En **Reglas personalizadas** puedes combinar condiciones sin escribir código.
3. Define capital, riesgo, stop, objetivo y costos. **1R** es la distancia entre tu entrada y tu stop inicial.
4. Ejecuta y revisa el capital, las caídas y el registro de operaciones.
5. Comprueba la cobertura en **Calidad de datos** y utiliza **Validación** para separar periodos.
6. Exporta el informe ZIP para conservar precios, reglas, fuentes y resultados juntos.""")
    st.subheader("Supuestos de la simulación")
    st.text(METHODOLOGY)
    st.caption("Fuente de precios Yahoo: uso personal e investigación según las condiciones de su proveedor. No se envían órdenes a ningún bróker.")
