"""
Multi-Currency Pair Trading & Statistical Arbitrage Strategy Engine.
Includes:
1. Cointegration & Correlation Pair Scanner
2. Kalman Filter & OLS Pair Trading Strategy (Z-Score Signals)
3. Triangular Arbitrage Scanner for Forex Triangles
"""

import itertools
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from core.math_utils import engle_granger_test, KalmanFilterHedgeRatio, calculate_rolling_zscore, calculate_half_life, calculate_hurst_exponent
from core.forex_pairs import MAJOR_FOREX_PAIRS, PAIR_SYMBOLS


class PairTradingStrategy:
    """
    Manages statistical arbitrage analysis and signal generation across all Forex pairs.
    """
    def __init__(self,
                 entry_zscore: float = 2.0,
                 exit_zscore: float = 0.0,
                 stop_zscore: float = 3.5,
                 lookback_window: int = 60,
                 use_kalman: bool = True):
        self.entry_zscore = entry_zscore
        self.exit_zscore = exit_zscore
        self.stop_zscore = stop_zscore
        self.lookback_window = lookback_window
        self.use_kalman = use_kalman
        self.kalman_filters: Dict[str, KalmanFilterHedgeRatio] = {}

    def analyze_pair_matrix(self, price_data: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Scans all pair combinations (N * (N-1) / 2) across available Forex pairs.
        Computes cointegration p-value, correlation, half-life, beta, and current Z-score.
        """
        results = []
        symbols = list(price_data.keys())

        for sym_a, sym_b in itertools.combinations(symbols, 2):
            df_a = price_data[sym_a]
            df_b = price_data[sym_b]

            # Use common closing prices
            p_a = df_a["close"].to_numpy()
            p_b = df_b["close"].to_numpy()
            min_len = min(len(p_a), len(p_b))
            
            p_a = p_a[-min_len:]
            p_b = p_b[-min_len:]

            if min_len < 30:
                continue

            # Truncate to recent evaluation window (max 500 bars) for instant matrix scan
            eval_len = min(500, min_len)
            p_a_recent = p_a[-eval_len:]
            p_b_recent = p_b[-eval_len:]

            # Engle-Granger Cointegration test on recent window
            eg_res = engle_granger_test(p_a_recent, p_b_recent)
            beta = eg_res["beta"]
            alpha = eg_res["alpha"]

            # Calculate spread series S_t = P_A - beta * P_B - alpha
            spread = p_a_recent - (beta * p_b_recent + alpha)
            zscores = calculate_rolling_zscore(spread, window=self.lookback_window)
            curr_zscore = float(zscores[-1])

            # Determine trading signal status (check stop BEFORE entry thresholds)
            signal = "NEUTRAL"
            if abs(curr_zscore) >= self.stop_zscore:
                signal = "STOP_LOSS"
            elif curr_zscore <= -self.entry_zscore:
                signal = "LONG_SPREAD"  # Buy Sym A, Sell Sym B
            elif curr_zscore >= self.entry_zscore:
                signal = "SHORT_SPREAD"  # Sell Sym A, Buy Sym B

            results.append({
                "pair_key": f"{sym_a}_{sym_b}",
                "leg_a": sym_a,
                "leg_b": sym_b,
                "beta": round(beta, 4),
                "alpha": round(alpha, 4),
                "correlation": round(eg_res["correlation"], 4),
                "p_value": round(eg_res["p_value"], 4),
                "is_cointegrated": eg_res["is_cointegrated"],
                "half_life": round(eg_res["half_life"], 1),
                "hurst": round(eg_res["hurst_exponent"], 3),
                "current_zscore": round(curr_zscore, 2),
                "current_spread": round(float(spread[-1]), 5),
                "signal": signal,
                "price_a": float(p_a[-1]),
                "price_b": float(p_b[-1])
            })

        # Sort results by cointegration p-value (best pairs first)
        results.sort(key=lambda x: x["p_value"])
        return results

    def generate_single_pair_series(self,
                                  df_a: pd.DataFrame,
                                  df_b: pd.DataFrame,
                                  leg_a: str,
                                  leg_b: str) -> Dict[str, Any]:
        """
        Generates detailed time-series of prices, dynamic spread, Kalman/OLS beta,
        and rolling Z-score for visualization in the UI charts.
        """
        p_a = df_a["close"].to_numpy()
        p_b = df_b["close"].to_numpy()
        timestamps = df_a["timestamp"].dt.strftime('%Y-%m-%d %H:%M').tolist()
        min_len = min(len(p_a), len(p_b))

        p_a = p_a[-min_len:]
        p_b = p_b[-min_len:]
        timestamps = timestamps[-min_len:]

        pair_key = f"{leg_a}_{leg_b}"
        betas = []
        spreads = []

        if self.use_kalman:
            kf = KalmanFilterHedgeRatio()
            kf.initialize(p_b[:30], p_a[:30])
            for i in range(min_len):
                beta_t, alpha_t, spread_t, _ = kf.update(p_b[i], p_a[i])
                betas.append(beta_t)
                spreads.append(spread_t)
            betas = np.array(betas)
            spreads = np.array(spreads)
        else:
            eg_res = engle_granger_test(p_a, p_b)
            beta_const = eg_res["beta"]
            alpha_const = eg_res["alpha"]
            betas = np.full(min_len, beta_const)
            spreads = p_a - (beta_const * p_b + alpha_const)

        zscores = calculate_rolling_zscore(spreads, window=self.lookback_window)

        # Generate signal points
        signals = []
        for i in range(min_len):
            z = zscores[i]
            sig = 0 # 0 neutral, 1 long spread, -1 short spread, 2 exit
            if z <= -self.entry_zscore:
                sig = 1
            elif z >= self.entry_zscore:
                sig = -1
            elif abs(z) <= self.exit_zscore:
                sig = 2
            signals.append(sig)

        return {
            "pair_key": pair_key,
            "leg_a": leg_a,
            "leg_b": leg_b,
            "timestamps": timestamps,
            "price_a": p_a.tolist(),
            "price_b": p_b.tolist(),
            "beta": np.round(betas, 4).tolist(),
            "spread": np.round(spreads, 5).tolist(),
            "zscore": np.round(zscores, 2).tolist(),
            "signals": signals,
            "entry_threshold": self.entry_zscore,
            "exit_threshold": self.exit_zscore,
            "stop_threshold": self.stop_zscore
        }


class TriangularArbitrageScanner:
    """
    Scans Forex cross-rate synthetic triangles for mispricings.
    Triangles scanned:
    1. EUR/USD, GBP/USD -> EUR/GBP
    2. EUR/USD, USD/JPY -> EUR/JPY
    3. GBP/USD, USD/JPY -> GBP/JPY
    4. AUD/USD, NZD/USD -> AUD/NZD (synthetic)
    """
    TRIANGLES = [
        {"name": "EUR-GBP-USD", "leg1": "EURUSD", "leg2": "GBPUSD", "cross": "EURGBP", "type": "DIVIDE"},
        {"name": "EUR-JPY-USD", "leg1": "EURUSD", "leg2": "USDJPY", "cross": "EURJPY", "type": "MULTIPLY"},
        {"name": "GBP-JPY-USD", "leg1": "GBPUSD", "leg2": "USDJPY", "cross": "GBPJPY", "type": "MULTIPLY"},
    ]

    @staticmethod
    def scan_triangles(current_prices: Dict[str, float]) -> List[Dict[str, Any]]:
        results = []

        for tri in TriangularArbitrageScanner.TRIANGLES:
            p1 = current_prices.get(tri["leg1"])
            p2 = current_prices.get(tri["leg2"])
            p_cross = current_prices.get(tri["cross"])

            if not p1 or not p2 or not p_cross:
                continue

            if tri["type"] == "DIVIDE":
                # e.g., Synthetic EURGBP = EURUSD / GBPUSD
                synthetic = p1 / p2
            else:
                # e.g., Synthetic EURJPY = EURUSD * USDJPY
                synthetic = p1 * p2

            diff = p_cross - synthetic
            diff_pct = (diff / p_cross) * 100.0
            
            opportunity = abs(diff_pct) > 0.03 # > 3 pips equivalent deviation

            results.append({
                "triangle": tri["name"],
                "leg1": tri["leg1"],
                "leg2": tri["leg2"],
                "actual_cross_symbol": tri["cross"],
                "actual_cross_price": round(p_cross, 4),
                "synthetic_price": round(synthetic, 4),
                "discrepancy_pips": round(diff * 10000.0, 2),
                "discrepancy_pct": round(diff_pct, 4),
                "opportunity_detected": opportunity,
                "action": "BUY CROSS / SELL LEGS" if diff < 0 else "SELL CROSS / BUY LEGS"
            })

        return results
