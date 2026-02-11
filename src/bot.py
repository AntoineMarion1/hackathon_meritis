import math
from collections import deque

import numpy as np
import requests
from constant import ORDER_URL, HEADER

class Bot:
    def __init__(self,
                 mom=20,
                 vol=20,
                 thresh=2.0,
                 max_gross=0.6,
                 target_vol_annual=0.12,
                 dd_stop=0.04,
                 min_trade_qty=5,
                 short=True):
        self.mom = mom
        self.vol = vol
        self.thresh = thresh
        self.max_gross = max_gross
        self.target_vol_annual = target_vol_annual
        self.dd_stop = dd_stop
        self.min_trade_qty = min_trade_qty
        self.short = short

        self.prices = {
            "MERI": deque(maxlen=max(mom, vol) + 2),
            "TIS":  deque(maxlen=max(mom, vol) + 2),
        }
        self.max_valuation = None

    def post_order(self, symbol: str, action: str, quantity: int):
        if quantity <= 0:
            return
        r = requests.post(
            ORDER_URL,
            headers=HEADER,
            json={"symbol": symbol, "action": action, "quantity": int(quantity)}
        )
        if r.status_code != 200:
            print("Erreur ordre:", r.status_code, r.text)

    @staticmethod
    def _rolling_vol_from_prices(prices: deque, window: int) -> float | None:
        if len(prices) < window + 1:
            return None
        p = np.array(prices, dtype=float)
        r = p[1:] / p[:-1] - 1.0
        r_w = r[-window:]
        s = float(np.std(r_w, ddof=1))
        if not np.isfinite(s) or s < 1e-8:
            return None
        return s

    @staticmethod
    def _momentum(prices: deque, window: int) -> float | None:
        if len(prices) < window + 1:
            return None
        p = list(prices)
        return float(p[-1] / p[-1 - window] - 1.0)

    def _rebalance_to_targets(self, cur_M, cur_T, tgt_M, tgt_T):
        dM = tgt_M - cur_M
        dT = tgt_T - cur_T

        if abs(dM) >= self.min_trade_qty:
            if dM > 0:
                self.post_order("MERI", "BUY", dM)
            else:
                self.post_order("MERI", "SELL", -dM)

        if abs(dT) >= self.min_trade_qty:
            if dT > 0:
                self.post_order("TIS", "BUY", dT)
            else:
                self.post_order("TIS", "SELL", -dT)

    def _flatten(self, cur_M, cur_T):
        if cur_M > 0:
            self.post_order("MERI", "SELL", cur_M)
        elif cur_M < 0:
            self.post_order("MERI", "BUY", -cur_M)

        if cur_T > 0:
            self.post_order("TIS", "SELL", cur_T)
        elif cur_T < 0:
            self.post_order("TIS", "BUY", -cur_T)

    def on_tick(self, tick: dict):
        md = tick["marketData"]
        pM = float(md["MERI"]["close"])
        pT = float(md["TIS"]["close"])

        self.prices["MERI"].append(pM)
        self.prices["TIS"].append(pT)

        pos = tick["portfolio"]["positions"]
        cur_M = int(pos.get("MERI", 0))
        cur_T = int(pos.get("TIS", 0))
        valuation = float(tick["valuation"])

        # Drawdown stop
        if self.max_valuation is None:
            self.max_valuation = valuation
        self.max_valuation = max(self.max_valuation, valuation)
        dd = (self.max_valuation - valuation) / self.max_valuation
        if dd >= self.dd_stop:
            self._flatten(cur_M, cur_T)
            return

        # Need enough history
        mM = self._momentum(self.prices["MERI"], self.mom)
        mT = self._momentum(self.prices["TIS"], self.mom)
        sM = self._rolling_vol_from_prices(self.prices["MERI"], self.vol)
        sT = self._rolling_vol_from_prices(self.prices["TIS"], self.vol)
        if mM is None or mT is None or sM is None or sT is None:
            return

        aM = mM / sM
        aT = mT / sT

        # Choose target state
        target_sym = None  # "MERI" or "TIS" or None
        target_dir = 0     # +1 long, -1 short, 0 cash

        best_a = max(aM, aT)
        worst_a = min(aM, aT)

        if best_a > self.thresh:
            target_dir = +1
            target_sym = "MERI" if aM >= aT else "TIS"
        elif self.short and worst_a < -self.thresh:
            target_dir = -1
            target_sym = "MERI" if aM <= aT else "TIS"
        else:
            target_dir = 0
            target_sym = None

        # Compute target notional via vol targeting
        target_daily = self.target_vol_annual / math.sqrt(252)
        gross_cap = self.max_gross * valuation

        tgt_M, tgt_T = 0, 0
        if target_dir != 0 and target_sym is not None:
            if target_sym == "MERI":
                notional = min(gross_cap, (target_daily / sM) * valuation)
                qty = int(notional / pM)
                tgt_M = qty * target_dir
                tgt_T = 0
            else:
                notional = min(gross_cap, (target_daily / sT) * valuation)
                qty = int(notional / pT)
                tgt_T = qty * target_dir
                tgt_M = 0

        self._rebalance_to_targets(cur_M, cur_T, tgt_M, tgt_T)
