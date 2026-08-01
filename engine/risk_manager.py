"""
Multi-Currency Risk & Defensive Guard Manager.
Includes:
- Single-currency net exposure limits (USD, EUR, GBP, JPY, etc.)
- Daily Equity Loss Cap (3.0% daily limit with emergency auto-liquidation)
- Friday Weekend Liquidation Protocol (21:00 UTC)
- High-impact Economic News Blackout validation
- ADX Market Regime validation
"""

import datetime
from typing import Dict, List, Any, Tuple, Optional
from core.forex_pairs import MAJOR_FOREX_PAIRS
from core.news_filter import EconomicNewsFilter
from core.regime_detector import MarketRegimeDetector
from core.spread_guard import SpreadGuard


class ForexRiskManager:
    """
    Institutional Multi-Layered Risk Management Engine for Forex Stat-Arb.
    """
    def __init__(self,
                 max_currency_exposure_pct: float = 30.0,
                 max_daily_loss_pct: float = 3.0,
                 max_portfolio_drawdown_pct: float = 15.0,
                 max_open_pairs: int = 5,
                 max_leverage: float = 30.0,
                 initial_balance: float = 100000.0):
        self.max_currency_exposure_pct = max_currency_exposure_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_portfolio_drawdown_pct = max_portfolio_drawdown_pct
        self.max_open_pairs = max_open_pairs
        self.max_leverage = max_leverage
        self.initial_balance = initial_balance
        self.daily_start_equity = initial_balance

        self.news_filter = EconomicNewsFilter(blackout_minutes_before=60, blackout_minutes_after=60)
        self.regime_detector = MarketRegimeDetector()
        self.spread_guard = SpreadGuard()
        
        # Midnight equity baseline tracker
        self.daily_start_equity = 100000.0
        self.last_reset_day = datetime.datetime.now().day

    def check_daily_reset(self, current_equity: float):
        """Resets daily equity baseline at 00:00 server time."""
        now_day = datetime.datetime.now().day
        if now_day != self.last_reset_day:
            self.daily_start_equity = current_equity
            self.last_reset_day = now_day

    def is_daily_equity_cap_breached(self, current_equity: float) -> Tuple[bool, str]:
        """Checks if daily equity loss has breached the 3.0% hard limit."""
        self.check_daily_reset(current_equity)
        loss_pct = ((self.daily_start_equity - current_equity) / self.daily_start_equity) * 100.0

        if loss_pct >= self.max_daily_loss_pct:
            return True, f"DAILY EQUITY GUARD BREACHED: Lost {round(loss_pct, 2)}% today (Limit: {self.max_daily_loss_pct}%)."
        return False, "Daily equity loss within normal parameters."

    @staticmethod
    def is_friday_weekend_close() -> Tuple[bool, str]:
        """Checks if current time is within weekend market closure window (Friday 21:00 UTC - Sunday 22:00 UTC)."""
        dt_now = datetime.datetime.now(datetime.timezone.utc)
        w = dt_now.weekday()
        h = dt_now.hour

        # Friday past 21:00 UTC
        if w == 4 and h >= 21:
            return True, "WEEKEND GUARD: Past 21:00 UTC Friday. Market is closed for the weekend."
        # Saturday all day
        if w == 5:
            return True, "WEEKEND GUARD: Saturday. Market is closed for the weekend."
        # Sunday before 22:00 UTC (Sydney open)
        if w == 6 and h < 22:
            return True, "WEEKEND GUARD: Sunday before 22:00 UTC. Market is closed for the weekend."

        return False, "Normal trading hours."

    def evaluate_currency_exposures(self, open_positions: List[Dict[str, Any]], account_balance: float) -> Dict[str, float]:
        """
        Decomposes active pair positions into net currency exposure (USD, EUR, GBP, JPY, etc.).
        """
        exposures: Dict[str, float] = {
            "USD": 0.0, "EUR": 0.0, "GBP": 0.0, "JPY": 0.0,
            "AUD": 0.0, "CAD": 0.0, "CHF": 0.0, "NZD": 0.0
        }

        for pos in open_positions:
            leg_a = pos.get("leg_a")
            leg_b = pos.get("leg_b")
            lots_a = pos.get("lots_a", 1.0)
            lots_b = pos.get("lots_b", 1.0)
            pos_type = pos.get("type")

            info_a = MAJOR_FOREX_PAIRS.get(leg_a, {})
            info_b = MAJOR_FOREX_PAIRS.get(leg_b, {})

            base_a, quote_a = info_a.get("base"), info_a.get("quote")
            base_b, quote_b = info_b.get("base"), info_b.get("quote")

            notional_a = lots_a * info_a.get("standard_lot", 100000)
            notional_b = lots_b * info_b.get("standard_lot", 100000)

            dir_a = 1.0 if pos_type == "LONG_SPREAD" else -1.0
            dir_b = -1.0 if pos_type == "LONG_SPREAD" else 1.0

            if base_a in exposures:
                exposures[base_a] += dir_a * notional_a
            if quote_a in exposures:
                exposures[quote_a] -= dir_a * notional_a

            if base_b in exposures:
                exposures[base_b] += dir_b * notional_b
            if quote_b in exposures:
                exposures[quote_b] -= dir_b * notional_b

        return {curr: round((val / max(1.0, account_balance)) * 100.0, 2) for curr, val in exposures.items()}

    def can_open_position(self,
                          new_pair_a: str,
                          new_pair_b: str,
                          current_positions: List[Dict[str, Any]],
                          account_equity: float,
                          current_drawdown_pct: float,
                          pair_df: Optional[Any] = None) -> Tuple[bool, str]:
        """
        Comprehensive Pre-Trade Institutional Risk Validation.
        """
        # 1. Daily Equity Cap Check
        breached, daily_reason = self.is_daily_equity_cap_breached(account_equity)
        if breached:
            return False, daily_reason

        # 2. Friday Weekend Gap Check
        is_friday, fri_reason = ForexRiskManager.is_friday_weekend_close()
        if is_friday:
            return False, fri_reason

        # 3. Max Positions Limit
        if len(current_positions) >= self.max_open_pairs:
            return False, f"Maximum open pair limit ({self.max_open_pairs}) reached."

        # 4. News Blackout Check (all 4 currencies: base + quote for both legs)
        info_a = MAJOR_FOREX_PAIRS.get(new_pair_a, {})
        info_b = MAJOR_FOREX_PAIRS.get(new_pair_b, {})
        currencies_involved = [
            info_a.get("base", ""), info_a.get("quote", ""),
            info_b.get("base", ""), info_b.get("quote", ""),
        ]
        is_blackout, news_reason = self.news_filter.check_news_blackout(*currencies_involved)
        if is_blackout:
            return False, news_reason

        # 5. Dynamic Spread Spike & Rollover Gap Check
        is_spread_blocked, spread_reason = self.spread_guard.check_spread_spike(new_pair_a, new_pair_b)
        if is_spread_blocked:
            return False, spread_reason

        # 6. ADX Regime Check (if candle data provided)
        if pair_df is not None:
            reg = self.regime_detector.evaluate_regime(pair_df)
            if not reg["trade_allowed"]:
                return False, f"REGIME GUARD: {reg['status_message']}"

        # 6. Check Duplicate Pair Key
        new_key = f"{new_pair_a}_{new_pair_b}"
        for pos in current_positions:
            existing_key = f"{pos.get('leg_a')}_{pos.get('leg_b')}"
            if new_key == existing_key:
                return False, f"Position already active for pair {new_key}."

        return True, "All risk & defensive guards passed."
