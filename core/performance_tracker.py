"""
Historical Performance Tracker.
Ported from Nexus (Project): Tracks trade win rates and PnL per pair combination
to provide empirical quality feedback to the Signal Aggregator.
"""

from typing import Dict, Any, List
from config import BotConfig


class PerformanceTracker:
    """
    Maintains empirical performance metrics per pair combination (e.g. AUDUSD_USDCAD).
    Calculates rolling win-rates and profit factors.
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.pair_stats: Dict[str, Dict[str, Any]] = {}

    def record_trade(self, pair_key: str, pnl: float, exit_reason: str):
        """
        Record completed trade result.
        """
        if pair_key not in self.pair_stats:
            self.pair_stats[pair_key] = {
                "wins": 0,
                "losses": 0,
                "total_trades": 0,
                "net_pnl": 0.0,
                "win_rate": 50.0,
                "history": []  # List of PnL values
            }

        stats = self.pair_stats[pair_key]
        stats["total_trades"] += 1
        stats["net_pnl"] += pnl
        stats["history"].append(pnl)

        # Enforce lookback window
        if len(stats["history"]) > self.cfg.PERFORMANCE_LOOKBACK_TRADES:
            stats["history"].pop(0)

        if pnl > 0:
            stats["wins"] += 1
        elif pnl < 0:
            stats["losses"] += 1

        # Calculate rolling win rate
        wins_in_window = sum(1 for x in stats["history"] if x > 0)
        total_in_window = len(stats["history"])
        stats["win_rate"] = round((wins_in_window / max(1, total_in_window)) * 100.0, 1)

    def get_win_rate(self, pair_key: str) -> float:
        """Returns empirical win rate percentage for a pair combination (default 50.0%)."""
        if pair_key in self.pair_stats and self.pair_stats[pair_key]["total_trades"] >= 3:
            return self.pair_stats[pair_key]["win_rate"]
        return 50.0

    def get_summary(self) -> Dict[str, Any]:
        """Returns overall performance summary table."""
        total_trades = sum(s["total_trades"] for s in self.pair_stats.values())
        total_wins = sum(s["wins"] for s in self.pair_stats.values())
        total_pnl = sum(s["net_pnl"] for s in self.pair_stats.values())
        overall_win_rate = round((total_wins / max(1, total_trades)) * 100.0, 1)

        return {
            "total_trades": total_trades,
            "overall_win_rate": overall_win_rate,
            "total_pnl": total_pnl,
            "pair_breakdown": self.pair_stats
        }
