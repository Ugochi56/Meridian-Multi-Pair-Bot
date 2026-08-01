"""
Forex Major Pairs Specifications & Definitions
"""

from typing import Dict, Any, List

MAJOR_FOREX_PAIRS: Dict[str, Dict[str, Any]] = {
    "EURUSD": {
        "symbol": "EURUSD",
        "name": "Euro / US Dollar",
        "base": "EUR",
        "quote": "USD",
        "pip_size": 0.0001,
        "pip_decimal_places": 4,
        "standard_lot": 100000,
        "typical_spread_pips": 0.8,
        "base_price": 1.0850,
        "volatility_daily_pct": 0.0055, # 0.55% daily std
    },
    "GBPUSD": {
        "symbol": "GBPUSD",
        "name": "British Pound / US Dollar",
        "base": "GBP",
        "quote": "USD",
        "pip_size": 0.0001,
        "pip_decimal_places": 4,
        "standard_lot": 100000,
        "typical_spread_pips": 1.2,
        "base_price": 1.2720,
        "volatility_daily_pct": 0.0068,
    },
    "USDJPY": {
        "symbol": "USDJPY",
        "name": "US Dollar / Japanese Yen",
        "base": "USD",
        "quote": "JPY",
        "pip_size": 0.01,
        "pip_decimal_places": 2,
        "standard_lot": 100000,
        "typical_spread_pips": 1.0,
        "base_price": 154.50,
        "volatility_daily_pct": 0.0075,
    },
    "AUDUSD": {
        "symbol": "AUDUSD",
        "name": "Australian Dollar / US Dollar",
        "base": "AUD",
        "quote": "USD",
        "pip_size": 0.0001,
        "pip_decimal_places": 4,
        "standard_lot": 100000,
        "typical_spread_pips": 1.1,
        "base_price": 0.6580,
        "volatility_daily_pct": 0.0072,
    },
    "USDCAD": {
        "symbol": "USDCAD",
        "name": "US Dollar / Canadian Dollar",
        "base": "USD",
        "quote": "CAD",
        "pip_size": 0.0001,
        "pip_decimal_places": 4,
        "standard_lot": 100000,
        "typical_spread_pips": 1.3,
        "base_price": 1.3650,
        "volatility_daily_pct": 0.0058,
    },
    "USDCHF": {
        "symbol": "USDCHF",
        "name": "US Dollar / Swiss Franc",
        "base": "USD",
        "quote": "CHF",
        "pip_size": 0.0001,
        "pip_decimal_places": 4,
        "standard_lot": 100000,
        "typical_spread_pips": 1.4,
        "base_price": 0.8980,
        "volatility_daily_pct": 0.0062,
    },
    "NZDUSD": {
        "symbol": "NZDUSD",
        "name": "New Zealand Dollar / US Dollar",
        "base": "NZD",
        "quote": "USD",
        "pip_size": 0.0001,
        "pip_decimal_places": 4,
        "standard_lot": 100000,
        "typical_spread_pips": 1.5,
        "base_price": 0.6120,
        "volatility_daily_pct": 0.0078,
    },
    "EURGBP": {
        "symbol": "EURGBP",
        "name": "Euro / British Pound",
        "base": "EUR",
        "quote": "GBP",
        "pip_size": 0.0001,
        "pip_decimal_places": 4,
        "standard_lot": 100000,
        "typical_spread_pips": 1.2,
        "base_price": 0.8530,
        "volatility_daily_pct": 0.0042,
    },
    "EURJPY": {
        "symbol": "EURJPY",
        "name": "Euro / Japanese Yen",
        "base": "EUR",
        "quote": "JPY",
        "pip_size": 0.01,
        "pip_decimal_places": 2,
        "standard_lot": 100000,
        "typical_spread_pips": 1.4,
        "base_price": 167.63,
        "volatility_daily_pct": 0.0082,
    },
    "GBPJPY": {
        "symbol": "GBPJPY",
        "name": "British Pound / Japanese Yen",
        "base": "GBP",
        "quote": "JPY",
        "pip_size": 0.01,
        "pip_decimal_places": 2,
        "standard_lot": 100000,
        "typical_spread_pips": 1.8,
        "base_price": 196.52,
        "volatility_daily_pct": 0.0095,
    }
}

PAIR_SYMBOLS: List[str] = list(MAJOR_FOREX_PAIRS.keys())


def get_pair_info(symbol: str) -> Dict[str, Any]:
    symbol_upper = symbol.upper().replace("/", "")
    if symbol_upper in MAJOR_FOREX_PAIRS:
        return MAJOR_FOREX_PAIRS[symbol_upper]
    raise ValueError(f"Unknown Forex symbol: {symbol}")


def calculate_pip_value(symbol: str, lot_size: float = 1.0, current_price: float = 1.0) -> float:
    """Returns the value of 1 pip in USD for a given lot size."""
    info = get_pair_info(symbol)
    pip_size = info["pip_size"]
    standard_lot = info["standard_lot"]
    quote = info["quote"]

    # If quote currency is USD (e.g. EURUSD, GBPUSD), 1 pip = pip_size * standard_lot * lot_size
    if quote == "USD":
        return pip_size * standard_lot * lot_size
    # If quote currency is JPY (e.g. USDJPY), 1 pip in JPY = pip_size * standard_lot * lot_size -> convert to USD by / price
    elif quote == "JPY":
        return (pip_size * standard_lot * lot_size) / current_price
    elif quote == "CAD":
        return (pip_size * standard_lot * lot_size) / current_price
    elif quote == "CHF":
        return (pip_size * standard_lot * lot_size) / current_price
    elif quote == "GBP":
        # Quote is GBP, so value in GBP convert to USD using GBPUSD price approx 1.27
        return (pip_size * standard_lot * lot_size) * 1.27
    return pip_size * standard_lot * lot_size
