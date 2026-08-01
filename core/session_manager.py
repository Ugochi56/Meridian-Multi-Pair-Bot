"""
Session Manager.
Detects active global Forex trading sessions (Asian, London, New York)
and dynamically scales entry Z-thresholds and regime filter parameters.
"""

from datetime import datetime, timezone
from typing import Dict, Any
from config import BotConfig


class SessionManager:
    """
    Tracks UTC time to identify active Forex market trading session.
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg

    def get_active_session(self) -> Dict[str, Any]:
        """
        Determines current active trading session and associated multiplier.
        Sessions (UTC):
        - Asian (Tokyo/Sydney): 22:00 – 07:00 UTC (Ranging, mean-reversion favored)
        - London: 07:00 – 12:00 UTC (Liquidity buildup, trends emerge)
        - London / NY Overlap: 12:00 – 16:00 UTC (Peak volatility & volume)
        - New York: 16:00 – 21:00 UTC (US session trend continuation)
        """
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour

        if not self.cfg.SESSION_ADAPTATION_ENABLED:
            return {"session_name": "STANDARD", "z_multiplier": 1.0, "description": "Default configuration"}

        if 22 <= hour or hour < 7:
            session_name = "ASIAN"
            z_mult = self.cfg.ASIAN_ENTRY_Z_MULTIPLIER
            desc = "Low volatility / Range-bound — Aggressive mean reversion"
        elif 7 <= hour < 12:
            session_name = "LONDON"
            z_mult = self.cfg.LONDON_NY_ENTRY_Z_MULTIPLIER
            desc = "High liquidity / Trend breakouts — Conservative thresholds"
        elif 12 <= hour < 16:
            session_name = "LONDON_NY_OVERLAP"
            z_mult = self.cfg.LONDON_NY_ENTRY_Z_MULTIPLIER
            desc = "Peak global volume & volatility — Strict entry standards"
        else:
            session_name = "NEW_YORK"
            z_mult = self.cfg.LONDON_NY_ENTRY_Z_MULTIPLIER
            desc = "US session trend continuation — Standard thresholds"

        return {
            "session_name": session_name,
            "z_multiplier": z_mult,
            "description": desc,
            "utc_hour": hour
        }

    def get_adjusted_entry_zscore(self) -> float:
        """Returns session-adjusted Entry Z-score threshold."""
        info = self.get_active_session()
        return round(self.cfg.ENTRY_ZSCORE * info["z_multiplier"], 2)
