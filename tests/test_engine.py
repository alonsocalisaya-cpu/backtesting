"""Datos artificiales EXCLUSIVAMENTE para verificar la contabilidad del motor."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from trading.data import parse_csv, validate_prices
from trading.engine import Config, backtest, indicators, monthly_returns
from trading.replay import advance


def fixture(rows, entries=(), exits=()):
    f = pd.DataFrame(rows,columns=["Open","High","Low","Close"],index=pd.date_range("2020-01-01",periods=len(rows)))
    f["Volume"] = 1000
    f["Entry"] = [i in entries for i in range(len(f))]
    f["Exit"] = [i in exits for i in range(len(f))]
    return f


class EngineTests(unittest.TestCase):
    def run_frame(self,f,cfg=None,others=None):
        with patch("trading.engine.indicators",side_effect=lambda f,c:f):
            return backtest({"AAA":f,**(others or {})},cfg or Config(commission_pct=0,slippage_bps=0,stop_pct=10),f.index[0],f.index[-1])

    def test_signal_executes_next_open_and_closes_with_cash_conservation(self):
        f=fixture([(100,101,99,100),(100,102,99,101),(110,113,108,112),(120,121,119,120)],entries=[1])
        r=self.run_frame(f)
        t=r["trades"].iloc[0]
        self.assertEqual(t.Entrada,f.index[2])
        self.assertEqual(t["Precio entrada"],110)
        self.assertEqual(t.Cantidad,9)
        self.assertAlmostEqual(r["metrics"]["capital_final"],10090)
        self.assertGreaterEqual(r["equity"].Efectivo.min(),0)

    def test_both_levels_touch_stop_wins(self):
        f=fixture([(100,101,99,100)]*2+[(100,140,85,100),(100,101,99,100)],entries=[1])
        r=self.run_frame(f)
        self.assertEqual(r["ambiguous"],1)
        self.assertEqual(r["trades"].iloc[0]["Precio salida"],90)
        self.assertEqual(r["metrics"]["capital_final"],9900)

    def test_gap_stop_uses_open_not_stop(self):
        f=fixture([(100,101,99,100)]*3+[(70,75,65,72),(72,73,71,72)],entries=[1])
        r=self.run_frame(f)
        self.assertEqual(r["trades"].iloc[0]["Precio salida"],70)
        self.assertEqual(r["metrics"]["capital_final"],9700)

    def test_fees_and_slippage_reduce_equity(self):
        f=fixture([(100,101,99,100)]*5,entries=[1])
        r=self.run_frame(f,Config(commission_pct=1,slippage_bps=10))
        t=r["trades"].iloc[0]
        self.assertGreater(t.Comisiones,0)
        self.assertLess(r["metrics"]["capital_final"],10000)
        self.assertAlmostEqual(r["metrics"]["capital_final"]-10000,t["Resultado USD"])

    def test_portfolio_capital_is_shared(self):
        f=fixture([(100,101,99,100)]*5,entries=[1])
        r=self.run_frame(f,Config(commission_pct=0,slippage_bps=0,max_positions=1),{"BBB":f.copy()})
        self.assertEqual(len(r["trades"]),1)
        self.assertEqual(r["trades"].iloc[0]["Acción"],"AAA")
        self.assertGreater(r["skipped"],0)

    def test_no_reuse_of_intraday_proceeds_at_open(self):
        a=fixture([(100,101,99,100),(100,101,99,100),(100,101,99,100),(100,140,99,100),(100,101,99,100)],entries=[1])
        b=fixture([(100,101,99,100)]*5,entries=[2])
        c=Config(commission_pct=0,slippage_bps=0,risk_pct=100,max_positions=2,stop_pct=10)
        r=self.run_frame(a,c,{"BBB":b})
        self.assertEqual(r["trades"]["Acción"].tolist(),["AAA"])

    def test_future_prices_do_not_change_past_signals(self):
        c=Config(fast=5,slow=20,rsi_period=5,breakout=5)
        prices=100+np.arange(100)*0.2+np.sin(np.arange(100))*2
        f=pd.DataFrame({"Open":prices,"High":prices+2,"Low":prices-2,"Close":prices,"Volume":1000},index=pd.date_range("2020-01-01",periods=100))
        before=indicators(f,c)
        f.iloc[70:,f.columns.get_indexer(["Open","High","Low","Close"])]+=500
        after=indicators(f,c)
        pd.testing.assert_frame_equal(before.iloc[:70],after.iloc[:70])

    def test_monthly_first_return_uses_initial_capital(self):
        e=pd.DataFrame({"Capital":[9900,11000,12100]},index=pd.to_datetime(["2020-01-02","2020-01-31","2020-02-28"]))
        m=monthly_returns(e,10000)
        self.assertAlmostEqual(m.loc[2020,1],10)
        self.assertAlmostEqual(m.loc[2020,2],10)

    def test_invalid_data_rejected(self):
        f=fixture([(100,90,80,100)]*3)
        with self.assertRaises(ValueError): validate_prices(f)
        f=fixture([(100,101,99,100)]*3)
        f.index=[f.index[0]]*3
        with self.assertRaises(ValueError): validate_prices(f)

    def test_csv_aliases_and_iso_dates(self):
        payload=b"Fecha,Apertura,High,Low,Cierre,Volumen\n2020-01-01,100,101,99,100,1000\n2020-01-02,101,102,100,101,1000"
        self.assertEqual(len(parse_csv(payload)),2)

    def test_replay_only_moves_one_bar_and_uses_next_open(self):
        f=fixture([(100,101,99,100),(110,111,109,110),(120,121,119,120)])
        state=dict(cursor=0,cash=10000,position=None,trades=[],curve=[])
        advance(state,f,Config(commission_pct=0,slippage_bps=0,stop_pct=10),"Comprar")
        self.assertEqual(state["cursor"],1)
        self.assertEqual(state["position"]["price"],110)
        advance(state,f,Config(commission_pct=0,slippage_bps=0,stop_pct=10),"Vender")
        self.assertEqual(len(state["trades"]),1)
        self.assertIsNone(state["position"])

    def test_no_trades_is_valid(self):
        f=fixture([(100,101,99,100)]*5)
        r=self.run_frame(f)
        self.assertEqual(r["metrics"]["capital_final"],10000)
        self.assertEqual(r["metrics"]["operaciones"],0)


if __name__ == "__main__":
    unittest.main()
