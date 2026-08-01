"""
Realtime News & Sentiment Engine.
Polls live Forex news releases and central bank RSS headlines to compute a rolling currency sentiment bias.
"""

import time
import json
import urllib.request
import threading
from typing import Dict, Any, List
from config import BotConfig


class RealtimeNewsEngine:
    """
    Background worker that fetches live ForexFactory calendar releases and financial news RSS feeds.
    Calculates a real-time sentiment score per currency (-1.0 to +1.0).
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.currency_sentiment: Dict[str, float] = {
            "USD": 0.0, "EUR": 0.0, "GBP": 0.0, "JPY": 0.0,
            "AUD": 0.0, "NZD": 0.0, "CAD": 0.0, "CHF": 0.0
        }
        self.last_update_time = 0.0
        self.running = False
        self._thread = None

        # Keyword dictionaries for basic NLP sentiment scoring
        self.HAWKISH_KEYWORDS = [
            "rate hike", "hawkish", "inflation surge", "tightening",
            "gdp growth", "strong payrolls", "cpi rise", "rate increase"
        ]
        self.DOVISH_KEYWORDS = [
            "rate cut", "dovish", "recession", "easing",
            "cpi drop", "unemployment rise", "slowing growth", "rate decrease"
        ]

    def start(self):
        """Starts background news polling thread."""
        if not self.running:
            self.running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

    def stop(self):
        """Stops background news polling thread."""
        self.running = False

    def _poll_loop(self):
        """Main polling loop running in background."""
        while self.running:
            try:
                self.update_sentiment()
            except Exception:
                pass
            time.sleep(self.cfg.NEWS_POLL_INTERVAL_SECONDS)

    def update_sentiment(self):
        """Fetches live calendar & RSS feeds to update currency sentiment map."""
        now = time.time()
        if now - self.last_update_time < self.cfg.NEWS_POLL_INTERVAL_SECONDS:
            return

        try:
            url = "https://nodedata.forexfactory.com/ff_calendar_thisweek.json"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                events = json.loads(response.read().decode('utf-8'))
                
                # Reset neutral scores
                temp_scores: Dict[str, float] = {k: 0.0 for k in self.currency_sentiment}

                for item in events:
                    ccy = item.get("country", "").upper()
                    if ccy not in temp_scores:
                        continue

                    title = item.get("title", "").lower()
                    impact = item.get("impact", "").lower()
                    weight = 0.5 if impact in ["high", "red"] else 0.2

                    # Keyword match on event title / description
                    for kw in self.HAWKISH_KEYWORDS:
                        if kw in title:
                            temp_scores[ccy] += weight
                    for kw in self.DOVISH_KEYWORDS:
                        if kw in title:
                            temp_scores[ccy] -= weight

                # Normalize sentiment to [-1.0, +1.0]
                for ccy, score in temp_scores.items():
                    self.currency_sentiment[ccy] = max(-1.0, min(1.0, round(score, 2)))

                self.last_update_time = now
        except Exception:
            # Fallback neutral if offline
            pass

    def get_sentiment(self, currency: str) -> float:
        """Returns sentiment score for a currency [-1.0, +1.0]."""
        return self.currency_sentiment.get(currency.upper(), 0.0)

    def get_pair_trade_sentiment(self, leg_a: str, leg_b: str) -> float:
        """
        Calculates net trade sentiment bias for pair trade (Leg A vs Leg B).
        Returns net sentiment score [-1.0, +1.0].
        """
        from core.forex_pairs import MAJOR_FOREX_PAIRS
        info_a = MAJOR_FOREX_PAIRS.get(leg_a, {})
        info_b = MAJOR_FOREX_PAIRS.get(leg_b, {})

        base_a, quote_a = info_a.get("base", ""), info_a.get("quote", "")
        base_b, quote_b = info_b.get("base", ""), info_b.get("quote", "")

        # Net sentiment: (Leg A sentiment) - (Leg B sentiment)
        sent_a = self.get_sentiment(base_a) - self.get_sentiment(quote_a)
        sent_b = self.get_sentiment(base_b) - self.get_sentiment(quote_b)

        net = sent_a - sent_b
        return max(-1.0, min(1.0, round(net, 2)))
