"""
Forex Data Feed & Synthetic Market Generator Engine.
Provides historical candle data and real-time tick streaming for Major Forex pairs.
Uses a factor-based currency model to ensure authentic multi-currency cointegration,
cross-pair consistency, and realistic Forex volatility.
"""

import time
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from core.forex_pairs import MAJOR_FOREX_PAIRS, PAIR_SYMBOLS


class ForexDataEngine:
    """
    Forex Market Generator & Data Provider.
    Generates multi-currency price series driven by underlying currency factors (USD, EUR, GBP, JPY, etc.)
    guaranteeing real statistical cointegration and realistic arbitrage dynamics.
    """
    def __init__(self, seed: int = 42):
        np.random.seed(seed)
        self.currencies = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]
        # Base currency factor levels (log scale)
        self.factor_levels = {
            "USD": 1.000,
            "EUR": 1.085,
            "GBP": 1.272,
            "JPY": 1.0 / 154.50,
            "AUD": 0.658,
            "CAD": 1.0 / 1.365,
            "CHF": 1.0 / 0.898,
            "NZD": 0.612
        }
        self.history: Dict[str, pd.DataFrame] = {}
        self.current_prices: Dict[str, float] = {}
        self.tick_counter = 0

    def generate_historical_candles(self, n_bars: int = 500, freq_minutes: int = 15) -> Dict[str, pd.DataFrame]:
        """
        Generate `n_bars` of multi-currency M15/H1 historical OHLC data with cointegration.
        """
        dt_end = pd.Timestamp.now().floor('15min')
        dt_start = dt_end - pd.Timedelta(minutes=freq_minutes * n_bars)
        timestamps = pd.date_range(start=dt_start, end=dt_end, periods=n_bars)

        # Factor stochastic drift (Geometric Brownian Motion + Mean-reverting spreads)
        dt = 1.0 / (252.0 * 24.0 * 4.0) # 15-minute timestep in annual fraction
        
        factor_series: Dict[str, np.ndarray] = {}
        for curr in self.currencies:
            init_val = np.log(self.factor_levels[curr])
            # Random walk for currency factor
            vol = 0.08 if curr != "USD" else 0.05
            steps = np.random.normal(loc=0.0, scale=vol * np.sqrt(dt), size=n_bars)
            # Add subtle long-term trend
            steps[0] = 0
            factor_path = init_val + np.cumsum(steps)
            factor_series[curr] = np.exp(factor_path)

        # Pair prices calculated from fundamental factors + stationary noise
        price_dict: Dict[str, Dict[str, np.ndarray]] = {}

        for symbol, info in MAJOR_FOREX_PAIRS.items():
            base = info["base"]
            quote = info["quote"]

            # Fundamental price ratio = Factor_Base / Factor_Quote
            fundamental_ratio = factor_series[base] / factor_series[quote]

            # Add cointegrated mean-reverting OU noise to pair
            ou_noise = np.zeros(n_bars)
            theta = 0.05 # Mean reversion speed
            noise_std = info["pip_size"] * 15.0
            
            for i in range(1, n_bars):
                ou_noise[i] = ou_noise[i-1] - theta * ou_noise[i-1] + np.random.normal(0, noise_std)

            close_prices = fundamental_ratio + ou_noise
            
            # Generate OHLC
            daily_vol = info["volatility_daily_pct"] / np.sqrt(96.0) # 15min vol
            high_prices = close_prices * (1.0 + np.abs(np.random.normal(0, daily_vol, n_bars)))
            low_prices = close_prices * (1.0 - np.abs(np.random.normal(0, daily_vol, n_bars)))
            
            # Ensure high >= max(open, close) and low <= min(open, close)
            open_prices = np.roll(close_prices, 1)
            open_prices[0] = close_prices[0] * (1.0 + np.random.normal(0, 0.0002))
            
            highs = np.maximum(high_prices, np.maximum(open_prices, close_prices))
            lows = np.minimum(low_prices, np.minimum(open_prices, close_prices))
            volumes = np.random.randint(500, 4500, n_bars)

            df = pd.DataFrame({
                "timestamp": timestamps,
                "open": np.round(open_prices, info["pip_decimal_places"]),
                "high": np.round(highs, info["pip_decimal_places"]),
                "low": np.round(lows, info["pip_decimal_places"]),
                "close": np.round(close_prices, info["pip_decimal_places"]),
                "volume": volumes
            })
            self.history[symbol] = df
            self.current_prices[symbol] = float(df["close"].iloc[-1])

        return self.history

    def fetch_mt5_historical_candles(self, n_bars: int = 500, freq_minutes: int = 15) -> Tuple[bool, Dict[str, pd.DataFrame]]:
        """
        Pulls real historical OHLC candles directly from MetaTrader 5 terminal.
        """
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return False, {}

        if not mt5.terminal_info():
            if not mt5.initialize():
                return False, {}

        tf_map = {
            1: mt5.TIMEFRAME_M1,
            5: mt5.TIMEFRAME_M5,
            15: mt5.TIMEFRAME_M15,
            30: mt5.TIMEFRAME_M30,
            60: mt5.TIMEFRAME_H1,
        }
        timeframe = tf_map.get(freq_minutes, mt5.TIMEFRAME_M15)
        suffixes = ["", "m", "c", ".r", "_i", ".m", ".c", "v", "b", ".ex"]

        for symbol, info in MAJOR_FOREX_PAIRS.items():
            sym_name = None
            for suf in suffixes:
                var = f"{symbol}{suf}"
                if mt5.symbol_info(var) is not None:
                    sym_name = var
                    break

            if not sym_name:
                continue

            mt5.symbol_select(sym_name, True)
            rates = mt5.copy_rates_from_pos(sym_name, timeframe, 0, n_bars)

            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df["timestamp"] = pd.to_datetime(df["time"], unit="s")
                df = df[["timestamp", "open", "high", "low", "close", "tick_volume"]]
                df.rename(columns={"tick_volume": "volume"}, inplace=True)
                self.history[symbol] = df
                self.current_prices[symbol] = float(df["close"].iloc[-1])

        if len(self.history) >= 2:
            return True, self.history

        return False, {}

    def get_latest_prices(self) -> Dict[str, float]:
        """Returns map of symbol -> current close price."""
        return self.current_prices

    def simulate_next_tick(self) -> Dict[str, Any]:
        """
        Simulates live tick movement across all Forex pairs.
        Appends a new candle to history so that Z-scores and cointegration stats evolve.
        Returns detailed tick packet with updated prices, bid/ask, and volume.
        """
        self.tick_counter += 1
        ticks = {}
        timestamp = pd.Timestamp.now()

        # Small factor perturbations
        for curr in self.currencies:
            if curr != "USD":
                delta = np.random.normal(0, 0.00015)
                self.factor_levels[curr] *= (1.0 + delta)

        for symbol, info in MAJOR_FOREX_PAIRS.items():
            base = info["base"]
            quote = info["quote"]
            ratio = self.factor_levels[base] / self.factor_levels[quote]

            # Add micro noise
            noise = np.random.normal(0, info["pip_size"] * 0.4)
            new_price = round(ratio + noise, info["pip_decimal_places"])
            self.current_prices[symbol] = new_price

            spread_val = info["typical_spread_pips"] * info["pip_size"]
            bid = round(new_price - spread_val / 2.0, info["pip_decimal_places"])
            ask = round(new_price + spread_val / 2.0, info["pip_decimal_places"])

            ticks[symbol] = {
                "symbol": symbol,
                "price": new_price,
                "bid": bid,
                "ask": ask,
                "spread_pips": info["typical_spread_pips"],
                "timestamp": timestamp.isoformat()
            }

            # Append new candle to history and drop oldest to keep window fixed
            if symbol in self.history:
                daily_vol = info["volatility_daily_pct"] / np.sqrt(96.0)
                prev_close = float(self.history[symbol]["close"].iloc[-1])
                new_open = round(prev_close, info["pip_decimal_places"])
                new_close = round(new_price, info["pip_decimal_places"])
                new_high = round(max(new_open, new_close) * (1.0 + abs(np.random.normal(0, daily_vol))), info["pip_decimal_places"])
                new_low = round(min(new_open, new_close) * (1.0 - abs(np.random.normal(0, daily_vol))), info["pip_decimal_places"])

                new_row = pd.DataFrame({
                    "timestamp": [timestamp],
                    "open": [new_open],
                    "high": [new_high],
                    "low": [new_low],
                    "close": [new_close],
                    "volume": [np.random.randint(500, 4500)],
                })
                self.history[symbol] = pd.concat(
                    [self.history[symbol].iloc[1:], new_row],
                    ignore_index=True,
                )

        return {
            "tick_id": self.tick_counter,
            "timestamp": timestamp.isoformat(),
            "ticks": ticks
        }

