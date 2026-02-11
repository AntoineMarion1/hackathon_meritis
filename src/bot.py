import math
from collections import deque
from typing import Any, Dict

import numpy as np
import requests
from constant import ORDER_URL, HEADER


class Bot:
    """
    Momentum / vol-target single-asset (MERI ou TIS) avec :
    - compat WS marketData (list) / backtest (dict)
    - positions nettes via positions + shortPositions
    - warm-up (mom/vol)
    - anti-churn: band 5% + cooldown
    - DD stop "risk_off" : un seul flatten puis stop trading
    """

    def __init__(
        self,
        mom: int = 20,
        vol: int = 40,
        thresh: float = 2.0,
        max_gross: float = 0.4,
        target_vol_annual: float = 0.15,
        dd_stop: float = 0.03,
        min_trade_qty: int = 5,
        short: bool = True,
        rebalance_band: float = 0.05,     # 5% de la cible avant de rebouger
        cooldown_ticks: int = 3,          # attendre N ticks entre 2 envois d'ordres
        debug: bool = True,
    ):
        self.mom = mom
        self.vol = vol
        self.thresh = thresh
        self.max_gross = max_gross
        self.target_vol_annual = target_vol_annual
        self.dd_stop = dd_stop
        self.min_trade_qty = min_trade_qty
        self.short = short
        self.rebalance_band = rebalance_band
        self.cooldown_ticks = cooldown_ticks
        self.debug = debug

        self.prices = {
            "MERI": deque(maxlen=max(mom, vol) + 5),
            "TIS": deque(maxlen=max(mom, vol) + 5),
        }

        self.max_valuation = None
        self.risk_off = False
        self._tick_count = 0
        self._last_order_tick = -10**9

    # ---------------- I/O ----------------
    def post_order(self, symbol: str, action: str, quantity: int):
        if quantity <= 0:
            return

        # cooldown global pour éviter le spam d'ordres
        if self._tick_count - self._last_order_tick < self.cooldown_ticks:
            return

        payload = {"symbol": symbol, "action": action, "quantity": int(quantity)}
        r = requests.post(ORDER_URL, headers=HEADER, json=payload)

        if r.status_code == 200:
            self._last_order_tick = self._tick_count
            if self.debug:
                print(f"✅ ORDER {action} {quantity} {symbol}")
        else:
            if self.debug:
                print(f"❌ ORDER FAIL {action} {quantity} {symbol} | {r.status_code} {r.text}")

    # ---------------- Normalisation tick ----------------
    @staticmethod
    def _normalize_market_data(md: Any) -> Dict[str, Dict[str, Any]]:
        # dict: {"MERI": {...}} ou list: [{"symbol":"MERI", ...}, ...]
        if isinstance(md, dict):
            return md
        if isinstance(md, list):
            out: Dict[str, Dict[str, Any]] = {}
            for item in md:
                if isinstance(item, dict) and "symbol" in item:
                    out[str(item["symbol"])] = item
            return out
        return {}

    @staticmethod
    def _net_positions(portfolio: Dict[str, Any]) -> Dict[str, int]:
        longs = portfolio.get("positions") or {}
        shorts = portfolio.get("shortPositions") or {}
        out: Dict[str, int] = {}
        for sym, q in longs.items():
            out[sym] = out.get(sym, 0) + int(q)
        for sym, q in shorts.items():
            out[sym] = out.get(sym, 0) - int(q)  # short => négatif
        return out

    # ---------------- Indicators ----------------
    @staticmethod
    def _rolling_vol_from_prices(prices: deque, window: int) -> float | None:
        if len(prices) < window + 1:
            return None
        p = np.asarray(prices, dtype=float)
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

    # ---------------- Trading ops ----------------
    def _should_trade(self, delta: int, target: int) -> bool:
        if abs(delta) < self.min_trade_qty:
            return False
        if target == 0:
            return True
        return abs(delta) >= self.rebalance_band * abs(target)

    def _rebalance_to_targets(self, cur_M: int, cur_T: int, tgt_M: int, tgt_T: int):
        dM = tgt_M - cur_M
        dT = tgt_T - cur_T

        if self._should_trade(dM, tgt_M):
            self.post_order("MERI", "BUY" if dM > 0 else "SELL", abs(dM))

        if self._should_trade(dT, tgt_T):
            self.post_order("TIS", "BUY" if dT > 0 else "SELL", abs(dT))

    def _flatten(self, cur_M: int, cur_T: int):
        if cur_M != 0:
            self.post_order("MERI", "SELL" if cur_M > 0 else "BUY", abs(cur_M))
        if cur_T != 0:
            self.post_order("TIS", "SELL" if cur_T > 0 else "BUY", abs(cur_T))

    # ---------------- Main ----------------
    def on_tick(self, tick: dict):
        self._tick_count += 1

        md = self._normalize_market_data(tick.get("marketData"))
        if "MERI" not in md or "TIS" not in md:
            if self.debug:
                print("⚠️ marketData incomplet:", md.keys())
            return

        try:
            pM = float(md["MERI"]["close"])
            pT = float(md["TIS"]["close"])
        except Exception:
            if self.debug:
                print("⚠️ close manquant:", md)
            return

        self.prices["MERI"].append(pM)
        self.prices["TIS"].append(pT)

        pf = tick.get("portfolio") or {}
        pos = self._net_positions(pf)
        cur_M = int(pos.get("MERI", 0))
        cur_T = int(pos.get("TIS", 0))

        valuation = float(tick.get("valuation", pf.get("cash", 0.0)))

        # Log de démarrage
        if self.debug and self._tick_count <= 5:
            print(
                f"tick#{self._tick_count} date={tick.get('date')} "
                f"pM={pM:.2f} pT={pT:.2f} posM={cur_M} posT={cur_T} val={valuation:.2f}"
            )

        # Risk-off une fois déclenché
        if self.risk_off:
            return

        # Drawdown stop (kill switch)
        if self.max_valuation is None:
            self.max_valuation = valuation
        self.max_valuation = max(self.max_valuation, valuation)
        dd = (self.max_valuation - valuation) / self.max_valuation
        if dd >= self.dd_stop:
            if self.debug:
                print(f"🛑 DD stop: {dd:.2%} >= {self.dd_stop:.2%} -> flatten & risk_off")
            self._flatten(cur_M, cur_T)
            self.risk_off = True
            return

        # Warm-up
        mM = self._momentum(self.prices["MERI"], self.mom)
        mT = self._momentum(self.prices["TIS"], self.mom)
        sM = self._rolling_vol_from_prices(self.prices["MERI"], self.vol)
        sT = self._rolling_vol_from_prices(self.prices["TIS"], self.vol)
        if mM is None or mT is None or sM is None or sT is None:
            if self.debug and self._tick_count in (1, 5, 10, 20, 40):
                need = max(self.mom, self.vol) + 1
                print(f"⏳ warm-up: {len(self.prices['MERI'])}/{need} ticks (pas d'ordre encore)")
            return

        aM = mM / sM
        aT = mT / sT

        best_a = max(aM, aT)
        worst_a = min(aM, aT)

        # Signal
        target_sym = None
        target_dir = 0

        if best_a > self.thresh:
            target_dir = +1
            target_sym = "MERI" if aM >= aT else "TIS"
        elif self.short and worst_a < -self.thresh:
            target_dir = -1
            target_sym = "MERI" if aM <= aT else "TIS"

        if self.debug and self._tick_count % 20 == 0:
            print(f"sig: aM={aM:.2f} aT={aT:.2f} best={best_a:.2f} thresh={self.thresh:.2f} -> {target_dir} {target_sym}")

        # Position sizing: vol targeting + cap
        target_daily = self.target_vol_annual / math.sqrt(252)
        gross_cap = self.max_gross * valuation

        tgt_M, tgt_T = 0, 0
        if target_dir != 0 and target_sym is not None:
            if target_sym == "MERI":
                notional = min(gross_cap, (target_daily / sM) * valuation)
                qty = int(notional / pM)
                tgt_M = qty * target_dir
            else:
                notional = min(gross_cap, (target_daily / sT) * valuation)
                qty = int(notional / pT)
                tgt_T = qty * target_dir

        self._rebalance_to_targets(cur_M, cur_T, tgt_M, tgt_T)
