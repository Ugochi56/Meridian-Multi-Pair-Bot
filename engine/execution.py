"""
Live Paper Trading Order Management System (OMS) & Execution Engine.
Manages active pair positions, margin utilization, live unrealized PnL updates, trade history, and partial exits.
"""

import time
from typing import Dict, List, Any, Optional
from core.forex_pairs import MAJOR_FOREX_PAIRS, calculate_pip_value


class OrderManagementSystem:
    """
    Paper Trading OMS tracking active pair positions, account capital, and partial exits.
    """
    def __init__(self, initial_balance: float = 100000.0, leverage: float = 30.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.leverage = leverage
        self.open_positions: List[Dict[str, Any]] = []
        self.closed_trades: List[Dict[str, Any]] = []
        self.position_id_counter = 1

    def get_account_summary(self) -> Dict[str, Any]:
        """Calculates balance, equity, margin, free margin, margin level, total PnL."""
        unrealized_pnl = sum(p.get("unrealized_pnl", 0.0) for p in self.open_positions)
        self.equity = round(self.balance + unrealized_pnl, 2)
        
        # Calculate used margin: Notional / Leverage
        margin_used = 0.0
        for pos in self.open_positions:
            info_a = MAJOR_FOREX_PAIRS.get(pos["leg_a"], {})
            info_b = MAJOR_FOREX_PAIRS.get(pos["leg_b"], {})
            notional_a = pos["lots_a"] * info_a.get("standard_lot", 100000)
            notional_b = pos["lots_b"] * info_b.get("standard_lot", 100000)
            margin_used += (notional_a + notional_b) / self.leverage

        free_margin = max(0.0, self.equity - margin_used)
        margin_level = (self.equity / margin_used * 100.0) if margin_used > 0 else 999.0
        total_pnl = self.equity - self.initial_balance
        total_pnl_pct = (total_pnl / self.initial_balance) * 100.0

        return {
            "initial_balance": self.initial_balance,
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "margin_used": round(margin_used, 2),
            "free_margin": round(free_margin, 2),
            "margin_level_pct": round(margin_level, 1),
            "open_positions_count": len(self.open_positions),
            "closed_trades_count": len(self.closed_trades)
        }

    def update_ticks(self, current_prices: Dict[str, float]):
        """Updates mark-to-market unrealized PnL for all active pair positions."""
        for pos in self.open_positions:
            leg_a = pos["leg_a"]
            leg_b = pos["leg_b"]
            p_a = current_prices.get(leg_a, pos["entry_price_a"])
            p_b = current_prices.get(leg_b, pos["entry_price_b"])

            info_a = MAJOR_FOREX_PAIRS.get(leg_a, {})
            info_b = MAJOR_FOREX_PAIRS.get(leg_b, {})

            pos["current_price_a"] = p_a
            pos["current_price_b"] = p_b

            if pos["type"] == "LONG_SPREAD":
                pnl_a = (p_a - pos["entry_price_a"]) * pos["lots_a"] * info_a.get("standard_lot", 100000)
                pnl_b = (pos["entry_price_b"] - p_b) * pos["lots_b"] * info_b.get("standard_lot", 100000)
            else: # SHORT_SPREAD
                pnl_a = (pos["entry_price_a"] - p_a) * pos["lots_a"] * info_a.get("standard_lot", 100000)
                pnl_b = (p_b - pos["entry_price_b"]) * pos["lots_b"] * info_b.get("standard_lot", 100000)

            pos["unrealized_pnl"] = round(pnl_a + pnl_b, 2)

    def open_pair_position(self,
                           leg_a: str,
                           leg_b: str,
                           pos_type: str,
                           beta: float,
                           lots_a: float,
                           lots_b: float,
                           entry_price_a: float,
                           entry_price_b: float,
                           entry_zscore: float) -> Dict[str, Any]:
        """Opens a new statistical arbitrage pair trade."""
        info_a = MAJOR_FOREX_PAIRS.get(leg_a, {})
        info_b = MAJOR_FOREX_PAIRS.get(leg_b, {})
        pos = {
            "position_id": self.position_id_counter,
            "pair_key": f"{leg_a}_{leg_b}",
            "leg_a": leg_a,
            "leg_b": leg_b,
            "leg_a_base": info_a.get("base", ""),
            "leg_a_quote": info_a.get("quote", ""),
            "leg_b_base": info_b.get("base", ""),
            "leg_b_quote": info_b.get("quote", ""),
            "type": pos_type, # "LONG_SPREAD" or "SHORT_SPREAD"
            "beta": round(beta, 4),
            "lots_a": lots_a,
            "lots_b": lots_b,
            "entry_price_a": entry_price_a,
            "entry_price_b": entry_price_b,
            "current_price_a": entry_price_a,
            "current_price_b": entry_price_b,
            "entry_zscore": entry_zscore,
            "unrealized_pnl": 0.0,
            "is_partially_closed": False,
            "realized_pnl_so_far": 0.0,
            "open_time": time.strftime('%H:%M:%S')
        }
        self.position_id_counter += 1
        self.open_positions.append(pos)
        return pos

    def partial_close_position(self, position_id: int, pct: float = 0.5) -> Optional[Dict[str, Any]]:
        """
        Partially closes a position by scaling out a percentage of volume (e.g. 50%).
        Realizes partial PnL and updates remaining volume.
        """
        pos = next((p for p in self.open_positions if p["position_id"] == position_id), None)
        if not pos or pos.get("is_partially_closed", False):
            return None

        # Calculate realized PnL chunk
        partial_pnl = round(pos["unrealized_pnl"] * pct, 2)
        self.balance += partial_pnl
        pos["realized_pnl_so_far"] += partial_pnl

        # Reduce remaining lot sizes
        pos["lots_a"] = max(0.01, round(pos["lots_a"] * (1.0 - pct), 2))
        pos["lots_b"] = max(0.01, round(pos["lots_b"] * (1.0 - pct), 2))
        pos["unrealized_pnl"] = round(pos["unrealized_pnl"] * (1.0 - pct), 2)
        pos["is_partially_closed"] = True

        return {
            "position_id": position_id,
            "pair_key": pos["pair_key"],
            "partial_pnl": partial_pnl,
            "remaining_lots_a": pos["lots_a"],
            "remaining_lots_b": pos["lots_b"]
        }

    def close_position(self, position_id: int) -> Optional[Dict[str, Any]]:
        """Closes an active pair position by ID and updates account balance."""
        pos_idx = next((i for i, p in enumerate(self.open_positions) if p["position_id"] == position_id), None)
        if pos_idx is None:
            return None

        pos = self.open_positions.pop(pos_idx)
        pnl = pos["unrealized_pnl"]
        self.balance += pnl
        total_realized_pnl = round(pnl + pos.get("realized_pnl_so_far", 0.0), 2)

        closed_record = {
            **pos,
            "close_time": time.strftime('%H:%M:%S'),
            "realized_pnl": total_realized_pnl
        }
        self.closed_trades.append(closed_record)
        return closed_record

    def close_all_positions(self) -> List[Dict[str, Any]]:
        """Panic button: Closes all active open positions immediately."""
        closed = []
        ids = [p["position_id"] for p in self.open_positions]
        for pid in ids:
            res = self.close_position(pid)
            if res:
                closed.append(res)
        return closed
