"""
Event-Driven & Vectorized Backtesting Engine for Forex Pair Trading.
Simulates spread trading executions, transaction spreads, slippage, and performance analytics.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from core.math_utils import KalmanFilterHedgeRatio, calculate_rolling_zscore, engle_granger_test
from core.forex_pairs import MAJOR_FOREX_PAIRS, calculate_pip_value


class PairBacktester:
    """
    Backtests statistical arbitrage pair trading strategy on historical candle data.
    """
    def __init__(self,
                 initial_capital: float = 100000.0,
                 risk_per_trade_pct: float = 2.0,
                 leverage: float = 30.0,
                 entry_zscore: float = 2.0,
                 exit_zscore: float = 0.0,
                 stop_zscore: float = 3.5,
                 lookback_window: int = 60,
                 use_kalman: bool = True):
        self.initial_capital = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.leverage = leverage
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.stop_zscore = stop_zscore
        self.lookback_window = lookback_window
        self.use_kalman = use_kalman

    def run_backtest(self,
                     df_a: pd.DataFrame,
                     df_b: pd.DataFrame,
                     leg_a: str,
                     leg_b: str) -> Dict[str, Any]:
        """
        Executes backtest for pair leg_a vs leg_b over historical candles.
        """
        p_a = df_a["close"].to_numpy()
        p_b = df_b["close"].to_numpy()
        timestamps = df_a["timestamp"].dt.strftime('%Y-%m-%d %H:%M').tolist()
        min_len = min(len(p_a), len(p_b))

        p_a = p_a[-min_len:]
        p_b = p_b[-min_len:]
        timestamps = timestamps[-min_len:]

        # Compute dynamic beta and spread
        if self.use_kalman:
            kf = KalmanFilterHedgeRatio()
            kf.initialize(p_b[:30], p_a[:30])
            betas = []
            spreads = []
            for i in range(min_len):
                b_t, a_t, s_t, _ = kf.update(p_b[i], p_a[i])
                betas.append(b_t)
                spreads.append(s_t)
            betas = np.array(betas)
            spreads = np.array(spreads)
        else:
            eg = engle_granger_test(p_a, p_b)
            b_const = eg["beta"]
            a_const = eg["alpha"]
            betas = np.full(min_len, b_const)
            spreads = p_a - (b_const * p_b + a_const)

        zscores = calculate_rolling_zscore(spreads, window=self.lookback_window)

        # Simulation state
        capital = self.initial_capital
        equity_curve = [capital]
        trade_log = []
        in_position = False
        pos_type = None # "LONG_SPREAD" or "SHORT_SPREAD"
        entry_idx = 0
        entry_price_a = 0.0
        entry_price_b = 0.0
        entry_beta = 1.0
        lots_a = 1.0
        lots_b = 1.0

        info_a = MAJOR_FOREX_PAIRS.get(leg_a, {})
        info_b = MAJOR_FOREX_PAIRS.get(leg_b, {})
        spread_a_cost = info_a.get("typical_spread_pips", 1.0) * info_a.get("pip_size", 0.0001)
        spread_b_cost = info_b.get("typical_spread_pips", 1.0) * info_b.get("pip_size", 0.0001)

        for i in range(self.lookback_window, min_len):
            z = zscores[i]
            curr_p_a = p_a[i]
            curr_p_b = p_b[i]
            curr_beta = betas[i]
            ts = timestamps[i]

            # Mark to market equity if in position
            if in_position:
                if pos_type == "LONG_SPREAD":
                    # Long A, Short B
                    pnl_a = (curr_p_a - entry_price_a) * lots_a * info_a.get("standard_lot", 100000)
                    pnl_b = (entry_price_b - curr_p_b) * lots_b * info_b.get("standard_lot", 100000)
                else:
                    # Short A, Long B
                    pnl_a = (entry_price_a - curr_p_a) * lots_a * info_a.get("standard_lot", 100000)
                    pnl_b = (curr_p_b - entry_price_b) * lots_b * info_b.get("standard_lot", 100000)
                
                # Check exit condition or stop loss
                exit_signal = False
                exit_reason = ""

                if pos_type == "LONG_SPREAD" and z >= self.exit_zscore:
                    exit_signal = True
                    exit_reason = "MEAN_REVERSION"
                elif pos_type == "SHORT_SPREAD" and z <= -self.exit_zscore:
                    exit_signal = True
                    exit_reason = "MEAN_REVERSION"
                elif abs(z) >= self.stop_zscore:
                    exit_signal = True
                    exit_reason = "STOP_LOSS"

                if exit_signal:
                    # Subtract transaction costs (broker spread)
                    t_cost_a = spread_a_cost * lots_a * info_a.get("standard_lot", 100000)
                    t_cost_b = spread_b_cost * lots_b * info_b.get("standard_lot", 100000)
                    total_realized_pnl = pnl_a + pnl_b - (t_cost_a + t_cost_b)

                    capital += total_realized_pnl
                    trade_log.append({
                        "trade_id": len(trade_log) + 1,
                        "pair": f"{leg_a}/{leg_b}",
                        "type": pos_type,
                        "entry_time": timestamps[entry_idx],
                        "exit_time": ts,
                        "entry_zscore": round(float(zscores[entry_idx]), 2),
                        "exit_zscore": round(float(z), 2),
                        "entry_price_a": entry_price_a,
                        "exit_price_a": curr_p_a,
                        "entry_price_b": entry_price_b,
                        "exit_price_b": curr_p_b,
                        "beta": round(float(entry_beta), 4),
                        "lots_a": round(lots_a, 2),
                        "lots_b": round(lots_b, 2),
                        "pnl": round(total_realized_pnl, 2),
                        "return_pct": round((total_realized_pnl / capital) * 100.0, 2),
                        "exit_reason": exit_reason
                    })

                    in_position = False
                    pos_type = None

            # Entry Logic
            if not in_position:
                if z <= -self.entry_zscore: # Undervalued spread -> Long Spread
                    in_position = True
                    pos_type = "LONG_SPREAD"
                    entry_idx = i
                    entry_price_a = curr_p_a
                    entry_price_b = curr_p_b
                    entry_beta = max(0.1, abs(curr_beta))

                    # Position Sizing based on risk %
                    risk_amt = capital * (self.risk_per_trade_pct / 100.0)
                    # Base 1 lot A, beta lots B
                    lots_a = max(0.01, round((risk_amt / 1000.0), 2))
                    lots_b = max(0.01, round(lots_a * entry_beta, 2))

                elif z >= self.entry_zscore: # Overvalued spread -> Short Spread
                    in_position = True
                    pos_type = "SHORT_SPREAD"
                    entry_idx = i
                    entry_price_a = curr_p_a
                    entry_price_b = curr_p_b
                    entry_beta = max(0.1, abs(curr_beta))

                    risk_amt = capital * (self.risk_per_trade_pct / 100.0)
                    lots_a = max(0.01, round((risk_amt / 1000.0), 2))
                    lots_b = max(0.01, round(lots_a * entry_beta, 2))

            equity_curve.append(round(capital, 2))

        # Performance Calculations
        eq_arr = np.array(equity_curve)
        returns = np.diff(eq_arr) / eq_arr[:-1]
        
        total_trades = len(trade_log)
        winning_trades = [t for t in trade_log if t["pnl"] > 0]
        losing_trades = [t for t in trade_log if t["pnl"] <= 0]
        
        win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_profit = sum(t["pnl"] for t in winning_trades)
        gross_loss = abs(sum(t["pnl"] for t in losing_trades))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

        # Max Drawdown
        peak = np.maximum.accumulate(eq_arr)
        drawdowns = (eq_arr - peak) / peak
        max_drawdown_pct = float(np.min(drawdowns)) * 100.0
        max_drawdown_usd = float(np.max(peak - eq_arr))

        # Sharpe & Sortino
        std_ret = np.std(returns)
        mean_ret = np.mean(returns) if len(returns) > 0 else 0.0
        
        # Annualized factor for 15min data (approx 96 bars per day * 252 days)
        ann_factor = np.sqrt(96 * 252)
        sharpe_ratio = float((mean_ret / std_ret) * ann_factor) if std_ret > 0 else 0.0

        downside_returns = returns[returns < 0]
        downside_std = np.std(downside_returns) if len(downside_returns) > 0 else 1e-6
        sortino_ratio = float((mean_ret / downside_std) * ann_factor) if downside_std > 0 else 0.0

        total_return_usd = capital - self.initial_capital
        total_return_pct = (total_return_usd / self.initial_capital) * 100.0

        return {
            "summary": {
                "pair": f"{leg_a}/{leg_b}",
                "initial_capital": self.initial_capital,
                "final_capital": round(capital, 2),
                "total_return_usd": round(total_return_usd, 2),
                "total_return_pct": round(total_return_pct, 2),
                "sharpe_ratio": round(sharpe_ratio, 2),
                "sortino_ratio": round(sortino_ratio, 2),
                "max_drawdown_pct": round(max_drawdown_pct, 2),
                "max_drawdown_usd": round(max_drawdown_usd, 2),
                "win_rate_pct": round(win_rate, 2),
                "total_trades": total_trades,
                "profit_factor": round(profit_factor, 2),
            },
            "equity_curve": equity_curve,
            "timestamps": timestamps[self.lookback_window:],
            "trade_log": trade_log
        }
