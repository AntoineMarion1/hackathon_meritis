import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import math
import numpy as np
import pandas as pd

from bot import Bot
from process_data import load_data

INITIAL_CAPITAL = 100_000

# Cash earns interest (~€STR 1.93% annual)
RF_ANNUAL = 0.0193
RF_DAILY = RF_ANNUAL / 252


def _load_aligned_prices() -> pd.DataFrame:
    """Load MERI & TIS CSVs and align on common dates. Returns df with date, close_MERI, close_TIS."""
    df_meri = load_data("MERI")
    df_tis = load_data("TIS")

    df_meri = df_meri.rename(columns={"XSdate": "date"})
    df_meri["date"] = pd.to_datetime(df_meri["date"])
    df_tis["date"] = pd.to_datetime(df_tis["date"])

    df = (
        pd.merge(
            df_meri[["date", "close"]].rename(columns={"close": "close_MERI"}),
            df_tis[["date", "close"]].rename(columns={"close": "close_TIS"}),
            on="date",
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    return df


def run_backtest(
    initial_capital: float = INITIAL_CAPITAL,
    verbose: bool = True,
    data_df: pd.DataFrame | None = None,
    **bot_kwargs
) -> dict:
    """
    Offline backtest:
    - build a tick per day with close prices
    - call bot.on_tick(tick)
    - capture orders via monkey-patch and execute them at close
    - cash earns daily interest

    Returns a dict of metrics (incl. hackathon score).
    """
    bot = Bot(**bot_kwargs)
    df = data_df if data_df is not None else _load_aligned_prices()

    cash = float(initial_capital)
    positions = {"MERI": 0, "TIS": 0}
    nb_trades = 0
    valuations: list[float] = []

    pending_orders: list[tuple[str, str, int]] = []

    def fake_post_order(symbol: str, action: str, quantity: int):
        if quantity <= 0:
            return
        pending_orders.append((symbol, action, int(quantity)))

    # Monkey patch: no API call in offline
    bot.post_order = fake_post_order  # type: ignore

    for _, row in df.iterrows():
        # Interest on idle cash
        cash *= (1.0 + RF_DAILY)

        price_meri = float(row["close_MERI"])
        price_tis = float(row["close_TIS"])

        valuation = cash + positions["MERI"] * price_meri + positions["TIS"] * price_tis

        tick = {
            "type": "TICK",
            "date": row["date"].strftime("%Y-%m-%d"),
            "marketData": {
                "MERI": {"close": price_meri},
                "TIS": {"close": price_tis},
            },
            "portfolio": {
                "cash": cash,
                "positions": dict(positions),
            },
            "valuation": valuation,
        }

        pending_orders.clear()
        bot.on_tick(tick)

        # Execute orders at close
        prices = {"MERI": price_meri, "TIS": price_tis}
        for symbol, action, qty in pending_orders:
            p = prices[symbol]
            if action == "BUY":
                cash -= qty * p
                positions[symbol] += qty
            else:  # SELL
                cash += qty * p
                positions[symbol] -= qty
            nb_trades += 1

        valuation_post = cash + positions["MERI"] * price_meri + positions["TIS"] * price_tis
        valuations.append(valuation_post)

    if not valuations:
        raise RuntimeError("No valuations produced (empty dataframe?)")

    vals = np.array(valuations, dtype=float)
    final_val = float(vals[-1])
    pnl = final_val - initial_capital
    total_return = final_val / initial_capital - 1.0

    # Daily returns
    rets = vals[1:] / vals[:-1] - 1.0
    rets = rets[np.isfinite(rets)]

    # Downside deviation annualized
    downside = rets[rets < 0]
    downside_dev = 0.0 if downside.size == 0 else float(math.sqrt(np.mean(downside**2)) * math.sqrt(252))

    # Vol annualized & Sharpe approx
    vol_annual = float(np.std(rets, ddof=1) * math.sqrt(252)) if rets.size > 2 else 0.0
    sharpe = (
        float(np.mean(rets)) / float(np.std(rets, ddof=1)) * math.sqrt(252)
        if rets.size > 2 and np.std(rets, ddof=1) > 1e-12
        else 0.0
    )

    # Hackathon score
    score = total_return - 0.5 * downside_dev

    # Max drawdown
    peak = vals[0]
    max_dd = 0.0
    for v in vals:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd

    metrics = {
        "final_val": final_val,
        "pnl": pnl,
        "return": total_return,
        "max_dd": max_dd,
        "trades": nb_trades,
        "downside_dev": downside_dev,
        "vol_annual": vol_annual,
        "sharpe": sharpe,
        "score": score,
        "final_pos_MERI": positions["MERI"],
        "final_pos_TIS": positions["TIS"],
        "start_date": df["date"].iloc[0].date(),
        "end_date": df["date"].iloc[-1].date(),
        "days": len(df),
        "bot_kwargs": bot_kwargs,
    }

    if verbose:
        print("=== Resultats du Backtest ===")
        print(f"Periode         : {metrics['start_date']} -> {metrics['end_date']} ({metrics['days']} jours)")
        print(f"Capital initial : {initial_capital:,.0f}")
        print(f"Capital final   : {metrics['final_val']:,.2f}")
        print(f"PnL             : {metrics['pnl']:+,.2f}")
        print(f"Rendement       : {metrics['return']:+.2%}")
        print(f"Max Drawdown    : {metrics['max_dd']:.2%}")
        print(f"Nb trades       : {metrics['trades']}")
        print(f"Position finale : MERI={metrics['final_pos_MERI']}  TIS={metrics['final_pos_TIS']}")
        print("")
        print("--- Metriques 'score hackathon' ---")
        print(f"Downside dev (ann.) : {metrics['downside_dev']:.2%}")
        print(f"Vol (ann.)          : {metrics['vol_annual']:.2%}")
        print(f"Sharpe (approx)     : {metrics['sharpe']:.2f}")
        print(f"Score               : {metrics['score']:+.2%}")

    return metrics


def grid_search_fast(
    initial_capital: float = INITIAL_CAPITAL,
    top_k: int = 10,
    top_n_refit: int = 30
) -> tuple[pd.DataFrame, dict]:
    """
    Two-pass grid search:
    - Pass 1: evaluate a grid, store metrics
    - Pass 2: refit only top_n_refit configs and rank by score_full

    Returns (df_ranked, best_config_dict).
    """
    df_full = _load_aligned_prices()

    # PASS 1 GRID (tweak if needed)
    moms = [10, 20, 40, 60]
    vols = [10, 20, 40]
    threshs = [1.5, 2.0, 2.5, 3.0]
    max_gross_vals = [0.4, 0.6, 0.8]
    target_vols = [0.10, 0.12, 0.15]
    dd_stops = [0.03, 0.04, 0.05]
    min_trade_qtys = [5, 10]
    shorts = [False, True]

    rows: list[dict] = []

    for mom in moms:
        for vol in vols:
            for thresh in threshs:
                for max_gross in max_gross_vals:
                    for target_vol_annual in target_vols:
                        for dd_stop in dd_stops:
                            for min_trade_qty in min_trade_qtys:
                                for short in shorts:
                                    m = run_backtest(
                                        initial_capital=initial_capital,
                                        verbose=False,
                                        data_df=df_full,
                                        mom=mom,
                                        vol=vol,
                                        thresh=thresh,
                                        max_gross=max_gross,
                                        target_vol_annual=target_vol_annual,
                                        dd_stop=dd_stop,
                                        min_trade_qty=min_trade_qty,
                                        short=short,
                                    )
                                    rows.append({
                                        "score": m["score"],
                                        "return": m["return"],
                                        "downside_dev": m["downside_dev"],
                                        "max_dd": m["max_dd"],
                                        "trades": m["trades"],
                                        "mom": mom,
                                        "vol": vol,
                                        "thresh": thresh,
                                        "max_gross": max_gross,
                                        "target_vol_annual": target_vol_annual,
                                        "dd_stop": dd_stop,
                                        "min_trade_qty": min_trade_qty,
                                        "short": short,
                                    })

    res = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)

    # PASS 2: refit only top configs
    N = min(top_n_refit, len(res))
    top = res.head(N)

    rows2: list[dict] = []
    for _, r in top.iterrows():
        m = run_backtest(
            initial_capital=initial_capital,
            verbose=False,
            data_df=df_full,
            mom=int(r["mom"]),
            vol=int(r["vol"]),
            thresh=float(r["thresh"]),
            max_gross=float(r["max_gross"]),
            target_vol_annual=float(r["target_vol_annual"]),
            dd_stop=float(r["dd_stop"]),
            min_trade_qty=int(r["min_trade_qty"]),
            short=bool(r["short"]),
        )
        rr = dict(r)
        rr.update({
            "score_full": m["score"],
            "return_full": m["return"],
            "downside_dev_full": m["downside_dev"],
            "max_dd_full": m["max_dd"],
            "trades_full": m["trades"],
        })
        rows2.append(rr)

    res2 = pd.DataFrame(rows2).sort_values("score_full", ascending=False).reset_index(drop=True)

    print("\n=== TOP CONFIGS (score_full) ===")
    cols = [
        "score_full", "return_full", "downside_dev_full", "max_dd_full", "trades_full",
        "mom", "vol", "thresh", "max_gross", "target_vol_annual", "dd_stop", "min_trade_qty", "short"
    ]
    print(res2[cols].head(top_k).to_string(index=False))

    best_config = res2.iloc[0].to_dict()
    print("\n=== BEST CONFIG ===")
    print(best_config)

    return res2, best_config


if __name__ == "__main__":
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))

    # 1) Backtest simple (params Bot par défaut)
    run_backtest()

    # 2) Optimisation 
    # grid_search_fast(top_k=10, top_n_refit=30)
