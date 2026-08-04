"""
ADX & Volatility Market Regime Classifier.
Detects whether current market conditions are RANGING (ideal for pair trading)
or TRENDING (suppresses counter-trend statistical arbitrage).
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any


class MarketRegimeDetector:
    """
    Calculates ADX and Volatility Ratio across major Forex pairs to determine active market regime.
    """
    def __init__(self, adx_trend_threshold: float = 45.0, adx_range_threshold: float = 25.0):
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_range_threshold = adx_range_threshold

    @staticmethod
    def calculate_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
        """Calculates 14-period Average Directional Index (ADX)."""
        if len(close) < period * 2:
            return np.full(len(close), 15.0)

        n = len(close)
        tr = np.zeros(n)
        p_dm = np.zeros(n)
        m_dm = np.zeros(n)

        for i in range(1, n):
            h_l = high[i] - low[i]
            h_c = abs(high[i] - close[i-1])
            l_c = abs(low[i] - close[i-1])
            tr[i] = max(h_l, h_c, l_c)

            up_move = high[i] - high[i-1]
            down_move = low[i-1] - low[i]

            if up_move > down_move and up_move > 0:
                p_dm[i] = up_move
            if down_move > up_move and down_move > 0:
                m_dm[i] = down_move

        tr_s = pd.Series(tr).rolling(period).sum().to_numpy()
        p_dm_s = pd.Series(p_dm).rolling(period).sum().to_numpy()
        m_dm_s = pd.Series(m_dm).rolling(period).sum().to_numpy()

        p_di = 100.0 * (p_dm_s / np.maximum(1e-6, tr_s))
        m_di = 100.0 * (m_dm_s / np.maximum(1e-6, tr_s))

        dx = 100.0 * np.abs(p_di - m_di) / np.maximum(1e-6, (p_di + m_di))
        adx = pd.Series(dx).rolling(period).mean().fillna(15.0).to_numpy()

        return adx

    def evaluate_regime(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Evaluates market regime for a single Forex pair OHLC DataFrame.
        Stat-Arb trades spread mean-reversion, so trades are allowed up to extreme ADX > 45.0.
        """
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        close = df["close"].to_numpy()

        adx_series = self.calculate_adx(high, low, close)
        curr_adx = float(adx_series[-1])

        # Determine regime
        if curr_adx > self.adx_trend_threshold:
            regime = "PARABOLIC_TREND"
            trade_allowed = False
            status = f"EXTREME PARABOLIC TREND DETECTED (ADX {curr_adx:.1f} > 45.0): Trades suppressed for safety."
        elif curr_adx < self.adx_range_threshold:
            regime = "RANGE"
            trade_allowed = True
            status = f"CHOPPY / RANGING (ADX {curr_adx:.1f}): Optimal conditions for Statistical Arbitrage."
        else:
            regime = "MODERATE_TREND"
            trade_allowed = True
            status = f"MODERATE TREND (ADX {curr_adx:.1f}): Cointegrated pair trading active."

        return {
            "adx": round(curr_adx, 1),
            "regime": regime,
            "trade_allowed": trade_allowed,
            "status_message": status
        }
