"""Prueba de interfaz con los históricos reales almacenados por smoke_real.py."""
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from streamlit.testing.v1 import AppTest


def check(at):
    if at.exception:
        raise AssertionError([e.message for e in at.exception])


def element(elements,label):
    return next(e for e in elements if e.label==label)


at=AppTest.from_file(str(Path(__file__).resolve().parent.parent/"app.py"),default_timeout=60).run()
check(at)
print("Inicio: sin excepciones",flush=True)
element(at.text_input,"Acciones / ETF").set_value("AAPL, MSFT")
element(at.date_input,"Desde").set_value(date(2020,1,1))
element(at.date_input,"Hasta (incluido)").set_value(date(2024,12,31))
element(at.button,"▶ Ejecutar backtesting").click().run()
check(at)
if at.error:
    raise AssertionError([e.value for e in at.error])
assert at.session_state["result"]["metrics"]["operaciones"]>0
print("Backtesting real: correcto",flush=True)
element(at.button,"Evaluar separación temporal").click().run()
check(at)
assert at.session_state["optimization"]["metrics"]["operaciones"]>0
print("Validación temporal: correcta",flush=True)
element(at.button,"Comprar y avanzar").click().run()
check(at)
assert at.session_state["replay_state"]["cursor"]==1
print("Replay manual: correcto",flush=True)
