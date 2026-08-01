"""
Signal Aggregator & Composite Ranker.
Aggregates candidate trade signals across the 45-pair matrix, calculates a composite Quality Score,
prevents currency over-exposure, and selects the top N highest-quality trades per cycle.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Set
from config import BotConfig


@dataclass
class TradeCandidate:
    """Dataclass representing a candidate pair trade opportunity."""
    pair_key: str
    leg_a: str
    leg_b: str
    signal: str  # "LONG_SPREAD" or "SHORT_SPREAD"
    current_zscore: float
    p_value: float
    beta: float
    half_life: float
    hurst: float
    price_a: float
    price_b: float
    is_cointegrated: bool
    news_sentiment_score: float = 0.0  # Range -1.0 to +1.0
    historical_win_rate: float = 50.0  # Historical win percentage
    quality_score: float = 0.0         # Calculated composite score
    currencies: Set[str] = field(default_factory=set)


class SignalAggregator:
    """
    Evaluates candidate trade signals, ranks them by statistical & fundamental quality,
    and filters out currency overlap to select the optimal portfolio of trades.
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg

    def compute_quality_score(self, candidate: TradeCandidate) -> float:
        """
        Calculates a normalized composite Quality Score [0.0, 100.0] for a candidate setup.
        Components:
        1. Z-Score magnitude (higher Z = stronger mispricing)
        2. Cointegration p-value (lower p = higher confidence)
        3. Hurst Exponent (lower H = stronger mean reversion)
        4. News Sentiment Alignment
        5. Historical Pair Win Rate
        """
        # 1. Z-Score Score (0 to 100) — capped at Z = 4.0
        z_abs = min(4.0, abs(candidate.current_zscore))
        z_score_val = (z_abs / 4.0) * 100.0

        # 2. P-Value Score (0 to 100) — p < 0.05 is required
        p_val_score = max(0.0, (1.0 - (candidate.p_value / 0.05))) * 100.0

        # 3. Hurst Score (0 to 100) — H < 0.5 mean-reverting
        h_val = max(0.0, min(0.5, candidate.hurst))
        hurst_score = (1.0 - (h_val / 0.5)) * 100.0

        # 4. News Sentiment Score (0 to 100)
        # Check if trade direction aligns with news sentiment
        sentiment_score = 50.0  # Neutral baseline
        s = candidate.news_sentiment_score
        if candidate.signal == "LONG_SPREAD" and s > 0:
            sentiment_score = 50.0 + (s * 50.0)
        elif candidate.signal == "SHORT_SPREAD" and s < 0:
            sentiment_score = 50.0 + (abs(s) * 50.0)
        elif candidate.signal == "LONG_SPREAD" and s < 0:
            sentiment_score = max(0.0, 50.0 - (abs(s) * 50.0))
        elif candidate.signal == "SHORT_SPREAD" and s > 0:
            sentiment_score = max(0.0, 50.0 - (s * 50.0))

        # 5. Historical Win Rate Score (0 to 100)
        win_rate_score = min(100.0, max(0.0, candidate.historical_win_rate))

        # Weighted Sum
        composite = (
            self.cfg.WEIGHT_ZSCORE * z_score_val +
            self.cfg.WEIGHT_PVALUE * p_val_score +
            self.cfg.WEIGHT_HURST * hurst_score +
            self.cfg.WEIGHT_NEWS_SENTIMENT * sentiment_score +
            self.cfg.WEIGHT_WIN_RATE * win_rate_score
        )
        return float(round(composite, 2))

    def filter_and_rank(
        self,
        candidates: List[TradeCandidate],
        open_positions: List[Dict[str, Any]],
        cooldown_pairs: Set[str]
    ) -> List[TradeCandidate]:
        """
        Scores all candidate setups, eliminates cooldown/open pairs, enforces max currency
        exposure overlap, and returns top N highest-quality trades.
        """
        # 1. Filter out already open pairs or pairs on cooldown
        open_keys = {f"{p['leg_a']}_{p['leg_b']}" for p in open_positions}
        valid_candidates = [
            c for c in candidates
            if c.pair_key not in open_keys and c.pair_key not in cooldown_pairs
        ]

        # 2. Score valid candidates
        for c in valid_candidates:
            c.quality_score = self.compute_quality_score(c)

        # 3. Sort by Quality Score descending
        valid_candidates.sort(key=lambda x: x.quality_score, reverse=True)

        # 4. Count existing currency usage in open positions
        currency_counts: Dict[str, int] = {}
        for pos in open_positions:
            for ccy in (pos.get("leg_a_base"), pos.get("leg_a_quote"), pos.get("leg_b_base"), pos.get("leg_b_quote")):
                if ccy:
                    currency_counts[ccy] = currency_counts.get(ccy, 0) + 1

        selected_candidates: List[TradeCandidate] = []

        # 5. Enforce currency overlap limits (max N trades per single currency)
        for cand in valid_candidates:
            if len(selected_candidates) >= self.cfg.AGGREGATOR_TOP_N_SELECTION:
                break

            # Check currency overlap
            overlap_exceeded = False
            for ccy in cand.currencies:
                if currency_counts.get(ccy, 0) >= self.cfg.MAX_SAME_CURRENCY_PAIRS:
                    overlap_exceeded = True
                    break

            if overlap_exceeded:
                continue

            # Accept candidate and update currency counts
            selected_candidates.append(cand)
            for ccy in cand.currencies:
                currency_counts[ccy] = currency_counts.get(ccy, 0) + 1

        return selected_candidates
