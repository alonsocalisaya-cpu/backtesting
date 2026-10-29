"""Verificación HTTP de la plataforma con precios históricos reales ya descargados."""
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE="http://127.0.0.1:8000/api"


def call(path,body=None,origin=None):
    headers={"Content-Type":"application/json"}
    if origin: headers["Origin"]=origin
    req=Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
    with urlopen(req,timeout=90) as response:
        return json.load(response)


if __name__=="__main__":
    assert call('/health')['app']=='nexo'
    boot=call('/bootstrap')
    assert boot['runs'] and boot['latest']
    print('Historial y resultados conservados: OK',flush=True)
    chart=call('/chart?symbol=AAPL&start=2020-01-01&end=2024-12-31')
    assert len(chart['bars'])==1258
    assert chart['metadata']['source']=='Yahoo Finance / yfinance'
    print('Gráfico con 1258 velas reales: OK',flush=True)
    source=boot['template']
    assert call('/syntax',dict(name='EMA',code=source))
    saved=call('/strategies',dict(id='ema20ema200',name='Retroceso EMA 20 / 200',code=source))
    assert any(s['id']==saved['id'] for s in call('/strategies'))
    print('Editor: comprobación, guardado y reapertura: OK',flush=True)
    job=call('/jobs',dict(symbols=['AAPL','MSFT'],start='2020-01-01',end='2024-12-31',config={**boot['default_config'],'code':source}))
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        status=call('/jobs/'+job['id'])
        if status['status']!='running': break
        time.sleep(.5)
    assert status['status']=='done',status
    run=call('/runs/'+str(status['run_id']))
    assert run['metrics']['operaciones']>0
    assert abs(sum(t['Resultado USD'] for t in run['trades'])-(run['metrics']['capital_final']-run['config']['capital']))<.0001
    print('Ejecución asíncrona y cartera compartida: OK',flush=True)
    replay=call('/replay',dict(run_id=run['id'],symbol='AAPL'))
    assert len(replay['bars'])==1
    step=call('/replay/'+replay['id']+'/Comprar',{})
    assert step['state']['cursor']==1 and len(step['bars'])==2
    print('Replay: avanza una vela y oculta el futuro: OK',flush=True)
    with urlopen(BASE+'/runs/'+str(run['id'])+'/export') as response:
        assert response.read(2)==b'PK'
    print('Exportación ZIP: OK',flush=True)
    try: call('/syntax',dict(name='EMA',code=source),origin='https://example.com')
    except HTTPError as e: assert e.code==403
    else: raise AssertionError('Se aceptó un origen externo')
    print('Protección de escrituras desde origen externo: OK',flush=True)
