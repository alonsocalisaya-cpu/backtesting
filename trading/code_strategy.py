"""Estrategias Python por vela, ejecutadas en un proceso con tiempo limitado."""
import pickle
import subprocess
import sys
from pathlib import Path

TEMPLATE = '''def on_bar(data, params):
    # data contiene SOLO velas hasta el cierre actual.
    # Columnas: Open, High, Low, Close, Volume,
    # EMA_fast, EMA_slow, RSI, Prev_high, Prev_low.
    # Devuelve (comprar, vender). Se ejecuta en la próxima apertura.
    if len(data) < params["slow"]:
        return False, False

    vela = data.iloc[-1]
    comprar = (
        vela.EMA_fast > vela.EMA_slow
        and vela.Low <= vela.EMA_fast
        and vela.Close > vela.EMA_fast
    )
    vender = vela.Close < vela.EMA_fast
    return bool(comprar), bool(vender)
'''


def validate_code(source):
    import ast
    tree = ast.parse(source, filename="estrategia.py")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "on_bar" for n in tree.body):
        raise ValueError("Define la función on_bar(data, params).")
    compile(tree,"estrategia.py","exec")


def code_signals(frame, config):
    validate_code(config.code)
    from dataclasses import asdict
    worker = Path(__file__).with_name("code_worker.py")
    try:
        completed = subprocess.run([sys.executable,str(worker)],
                                   input=pickle.dumps((frame,asdict(config))),capture_output=True,timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("La estrategia superó 45 segundos por activo. Simplifica el cálculo por vela.") from exc
    if completed.returncode:
        raise ValueError(completed.stderr.decode("utf-8",errors="replace")[-3000:])
    return pickle.loads(completed.stdout)
