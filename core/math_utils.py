"""
Quantitative Math & Statistical Arbitrage Utilities
Includes Cointegration testing (Engle-Granger), Kalman Filter dynamic hedge ratio estimation,
Ornstein-Uhlenbeck Half-Life calculation, Hurst Exponent, and Z-Score transformations.
"""

import numpy as np
import pandas as pd
from scipy import stats
from typing import Dict, Tuple, Optional, Any

try:
    from statsmodels.tsa.stattools import adfuller
    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False


class KalmanFilterHedgeRatio:
    """
    Online 2-state Kalman Filter for dynamically tracking time-varying hedge ratio (beta)
    and spread intercept (alpha) between two price series Y (dependent) and X (independent).
    
    Measurement equation: Y_t = beta_t * X_t + alpha_t + v_t
    State transition: [beta_t, alpha_t]^T = [beta_{t-1}, alpha_{t-1}]^T + w_t
    """
    def __init__(self, delta: float = 1e-4, R: float = 1e-3):
        """
        :param delta: Transition covariance scale factor (process noise)
        :param R: Measurement variance (measurement noise)
        """
        self.delta = delta
        self.R = R
        # State vector [beta, alpha]^T
        self.state = np.zeros(2)
        # State covariance matrix P
        self.P = np.eye(2) * 1.0
        # Process covariance matrix W
        self.W = self.delta / (1.0 - self.delta) * np.eye(2)
        self.initialized = False

    def initialize(self, x_init: np.ndarray, y_init: np.ndarray):
        """Initialize state using initial OLS regression on training chunk."""
        if len(x_init) < 5:
            self.state = np.array([1.0, 0.0])
            self.initialized = True
            return
        
        # OLS fit: y = beta * x + alpha
        slope, intercept, r_value, p_value, std_err = stats.linregress(x_init, y_init)
        self.state = np.array([slope, intercept])
        self.P = np.eye(2) * 1.0
        self.initialized = True

    def update(self, x_t: float, y_t: float) -> Tuple[float, float, float, float]:
        """
        Update state with new price observation (x_t, y_t).
        :return: (beta_t, alpha_t, spread_t, zscore_t)
        """
        if not self.initialized:
            self.state = np.array([1.0, 0.0])
            self.initialized = True

        # Observation matrix H_t = [x_t, 1.0]
        H = np.array([[x_t, 1.0]])

        # 1. State Prediction (Assume random walk for states)
        # state_pred = state_{t-1}
        P_pred = self.P + self.W

        # 2. Measurement Innovation
        y_hat = float(H @ self.state)
        error = y_t - y_hat

        # Innovation covariance
        S = float(H @ P_pred @ H.T) + self.R

        # Kalman Gain
        K = (P_pred @ H.T) / S

        # 3. State Update
        self.state = self.state + (K.flatten() * error)
        self.P = (np.eye(2) - K @ H) @ P_pred

        beta_t = float(self.state[0])
        alpha_t = float(self.state[1])
        # Use post-update state for spread (not pre-update innovation)
        spread_t = y_t - (beta_t * x_t + alpha_t)

        return beta_t, alpha_t, spread_t, S


def engle_granger_test(y: np.ndarray, x: np.ndarray) -> Dict[str, Any]:
    """
    Engle-Granger 2-step Cointegration Test.
    Step 1: OLS regression Y = beta * X + alpha
    Step 2: ADF test on regression residuals.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    
    # OLS Regression
    slope, intercept, r_value, p_value_ols, std_err = stats.linregress(x, y)
    residuals = y - (slope * x + intercept)
    
    if HAS_STATSMODELS:
        # ADF test on residuals — use regression='n' since EG residuals are demeaned
        adf_res = adfuller(residuals, autolag='AIC', regression='n')
        adf_stat = float(adf_res[0])
        p_value = float(adf_res[1])
        crit_vals = {k: float(v) for k, v in adf_res[4].items()}
    else:
        # Fallback manual ADF approximation
        diff_res = np.diff(residuals)
        lag_res = residuals[:-1]
        slope_adf, _, r_val, p_val_adf, _ = stats.linregress(lag_res, diff_res)
        adf_stat = float(slope_adf / (1e-6 + np.std(diff_res) / np.sqrt(len(diff_res))))
        p_value = float(p_val_adf)
        crit_vals = {'1%': -3.43, '5%': -2.86, '10%': -2.57}

    half_life = calculate_half_life(residuals)
    hurst = calculate_hurst_exponent(residuals)
    correlation = float(r_value)

    return {
        "beta": float(slope),
        "alpha": float(intercept),
        "r_squared": float(r_value ** 2),
        "correlation": correlation,
        "adf_stat": adf_stat,
        "p_value": p_value,
        "critical_values": crit_vals,
        "is_cointegrated": bool(p_value < 0.05),
        "half_life": half_life,
        "hurst_exponent": hurst,
        "residual_std": float(np.std(residuals))
    }


def calculate_half_life(spread: np.ndarray) -> float:
    """
    Calculate the Ornstein-Uhlenbeck (OU) half-life of mean reversion for a spread.
    dS_t = lambda * S_{t-1} + mu + e_t
    Half-life = -ln(2) / lambda
    """
    spread = np.asarray(spread, dtype=float)
    if len(spread) < 10:
        return 999.0

    spread_lag = spread[:-1]
    spread_diff = np.diff(spread)
    
    # Linear regression: diff = lambda * lag + intercept
    res = stats.linregress(spread_lag, spread_diff)
    lam = res.slope
    
    # If lambda is positive or very close to zero, not mean-reverting
    if lam >= 0 or np.isnan(lam):
        return 999.0

    half_life = -np.log(2.0) / lam
    return max(0.5, float(half_life))


def calculate_hurst_exponent(time_series: np.ndarray, max_lag: int = 20) -> float:
    """
    Calculate the Hurst Exponent of a time series.
    H < 0.5: Mean-reverting (Stationary spread)
    H = 0.5: Random walk (Geometric Brownian Motion)
    H > 0.5: Trending (Persistent)
    """
    ts = np.asarray(time_series, dtype=float)
    if len(ts) < max_lag * 2:
        return 0.5

    lags = range(2, min(max_lag, len(ts) // 4))
    tau = []
    
    for lag in lags:
        # Calculate standard deviation of lagged differences
        diff = ts[lag:] - ts[:-lag]
        std_val = np.std(diff)
        if std_val > 1e-12:
            tau.append(std_val)
        else:
            tau.append(1e-12)

    if len(tau) < 2:
        return 0.5

    # Log-log linear regression log(tau) = H * log(lags) + C
    log_lags = np.log(list(lags))
    log_tau = np.log(tau)
    
    res = stats.linregress(log_lags, log_tau)
    hurst = float(res.slope)
    return float(np.clip(hurst, 0.0, 1.0))


def calculate_rolling_zscore(spread: np.ndarray, window: int = 30) -> np.ndarray:
    """Calculate rolling Z-Score of a 1D spread numpy array."""
    s = pd.Series(spread)
    mean = s.rolling(window=window, min_periods=max(5, window // 3)).mean()
    std = s.rolling(window=window, min_periods=max(5, window // 3)).std()
    zscore = (s - mean) / (std.replace(0, 1e-6))
    return zscore.fillna(0.0).to_numpy()


# --- Technical Indicators -----------------------------------------------------

def calculate_atr(df: pd.DataFrame, window: int = 14) -> float:
    """
    Calculate Average True Range (ATR) from OHLC DataFrame.
    """
    if len(df) < window + 1:
        return float(df["high"].iloc[-1] - df["low"].iloc[-1]) if len(df) > 0 else 0.0010

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()

    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])

    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    atr = pd.Series(tr).rolling(window=window).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) else 0.0010


def calculate_rsi(prices: np.ndarray, window: int = 14) -> float:
    """
    Calculate Relative Strength Index (RSI) for a price series.
    """
    if len(prices) < window + 1:
        return 50.0

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[-window:])
    avg_loss = np.mean(losses[-window:])

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi)


def calculate_vwap(df: pd.DataFrame) -> float:
    """
    Calculate Volume Weighted Average Price (VWAP) for an OHLCV DataFrame.
    """
    if len(df) == 0:
        return 0.0

    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, 1)

    vwap = (typical_price * vol).sum() / vol.sum()
    return float(vwap)

