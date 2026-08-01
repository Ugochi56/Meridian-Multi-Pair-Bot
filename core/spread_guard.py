"""
Meridian Spread Guard — Dynamic Broker Spread & Rollover Protection Engine.
Monitors broker spreads across all Forex pair legs.
Blocks trade entries during:
1. Rollover liquidity gaps (21:00 - 22:00 UTC daily)
2. Sudden spread spikes (> 2.5x historical average spread)
"""

import datetime
from typing import Dict, Tuple, Any

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False


class SpreadGuard:
    """
    Evaluates dynamic broker spreads and prevents trade execution during illiquid market spikes.
    """
    def __init__(self, max_spread_multiplier: float = 2.5):
        self.max_spread_multiplier = max_spread_multiplier
        self.normal_spreads: Dict[str, float] = {
            "EURUSD": 0.00015,
            "GBPUSD": 0.00020,
            "USDJPY": 0.020,
            "USDCHF": 0.00020,
            "USDCAD": 0.00020,
            "AUDUSD": 0.00020,
            "NZDUSD": 0.00025,
            "EURGBP": 0.00020,
            "EURJPY": 0.025,
            "GBPJPY": 0.030,
        }

    @staticmethod
    def is_rollover_period() -> Tuple[bool, str]:
        """Checks if current time is within daily Forex rollover window (21:00 - 22:00 UTC)."""
        dt_now = datetime.datetime.now(datetime.timezone.utc)
        
        # 1. Active Rollover Hour (21:00 - 22:00 UTC)
        if dt_now.hour == 21:
            mins_left = 60 - dt_now.minute
            return True, f"ROLLOVER ACTIVE: Daily 21:00-22:00 UTC broker rollover gap in progress ({mins_left}m remaining). Spreads wide."

        # 2. Approaching Rollover (20:45 - 21:00 UTC)
        if dt_now.hour == 20 and dt_now.minute >= 45:
            mins_until = 60 - dt_now.minute
            return True, f"ROLLOVER APPROACHING: Daily rollover starts in {mins_until}m (21:00 UTC). Suppressing new entries to avoid spread/swap spikes."

        return False, "Normal liquidity hours."

    @staticmethod
    def get_time_until_rollover() -> str:
        """Returns human-readable time remaining until next daily rollover (21:00 UTC)."""
        dt_now = datetime.datetime.now(datetime.timezone.utc)
        target = dt_now.replace(hour=21, minute=0, second=0, microsecond=0)
        if dt_now >= target:
            target += datetime.timedelta(days=1)
        diff = target - dt_now
        hours, remainder = divmod(int(diff.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours}h {minutes}m"

    def check_spread_spike(self, symbol_a: str, symbol_b: str) -> Tuple[bool, str]:
        """
        Checks if current broker spread for symbol_a or symbol_b exceeds normal parameters.
        Returns (is_blocked, reason_string).
        """
        # 1. Check Rollover
        in_rollover, r_reason = self.is_rollover_period()
        if in_rollover:
            return True, r_reason

        if not MT5_AVAILABLE or not mt5.terminal_info():
            return False, "MT5 offline — spread guard bypassed."

        for sym in [symbol_a, symbol_b]:
            info = mt5.symbol_info(sym)
            if info is None:
                continue

            current_spread = info.ask - info.bid
            base_sym = sym.rstrip("mc.r_i.exv b")  # Trim broker suffix for baseline lookup
            normal = self.normal_spreads.get(base_sym, 0.00030)

            if current_spread > (normal * self.max_spread_multiplier):
                return True, f"SPREAD SPIKE GUARD: {sym} spread {current_spread:.5f} exceeds {self.max_spread_multiplier}x normal ({normal:.5f})."

        return False, "Spreads normal."
