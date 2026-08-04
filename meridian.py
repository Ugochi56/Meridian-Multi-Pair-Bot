"""
╔══════════════════════════════════════════════════════════════════╗
║  MERIDIAN FX — Multi-Currency Statistical Arbitrage Bot         ║
║  Automated Headless Pair Trading Engine                         ║
╚══════════════════════════════════════════════════════════════════╝

Entry point: python meridian.py

Runs a fully automated scan → signal → rank → risk → execute loop.
Integrates:
- Signal Aggregator & Quality Ranker
- Realtime News & Sentiment Engine
- Session Manager (Asian, London, NY thresholds)
- Performance Tracker
- Technical RSI/VWAP confirmation
- Partial Exits & MT5 Execution Bridge
"""

import sys
import time
import datetime
import logging
from typing import Dict, List, Any, Optional

from config import BotConfig
from core.forex_pairs import MAJOR_FOREX_PAIRS, PAIR_SYMBOLS
from core.math_utils import calculate_rolling_zscore, calculate_rsi, calculate_vwap
from core.aggregator import SignalAggregator, TradeCandidate
from core.realtime_news import RealtimeNewsEngine
from core.session_manager import SessionManager
from core.performance_tracker import PerformanceTracker
from core.state_manager import meridian_state
from core.trade_logger import export_trade_ledger
from engine.data_feed import ForexDataEngine
from engine.strategy import PairTradingStrategy, TriangularArbitrageScanner
from engine.execution import OrderManagementSystem
from engine.risk_manager import ForexRiskManager
from connectors.mt5_bridge import MT5TerminalBridge


# ─── Logging Setup ─────────────────────────────────────────────────────────────

def setup_logger() -> logging.Logger:
    """Configure structured console logger."""
    # Force UTF-8 on Windows terminals
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    logger = logging.getLogger("MERIDIAN")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "\033[90m[%(asctime)s]\033[0m %(message)s",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)
    return logger


log = setup_logger()


# --- Console Formatting Helpers ---------------------------------------------------

# ANSI color codes
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_MAGENTA = "\033[95m"
C_BLUE = "\033[94m"
C_WHITE = "\033[97m"

SEPARATOR = f"{C_DIM}{'~' * 72}{C_RESET}"
DOUBLE_SEP = f"{C_DIM}{'=' * 72}{C_RESET}"


def fmt_usd(val: float) -> str:
    """Format a USD value with color."""
    if val >= 0:
        return f"{C_GREEN}+${val:,.2f}{C_RESET}"
    return f"{C_RED}-${abs(val):,.2f}{C_RESET}"


def fmt_pct(val: float) -> str:
    """Format a percentage with color."""
    if val >= 0:
        return f"{C_GREEN}+{val:.2f}%{C_RESET}"
    return f"{C_RED}{val:.2f}%{C_RESET}"


def fmt_signal(signal: str) -> str:
    """Color-code a trading signal."""
    colors = {
        "LONG_SPREAD": C_GREEN,
        "SHORT_SPREAD": C_RED,
        "STOP_LOSS": f"{C_RED}{C_BOLD}",
        "NEUTRAL": C_DIM,
    }
    c = colors.get(signal, C_WHITE)
    return f"{c}{signal}{C_RESET}"


# --- Banner -----------------------------------------------------------------------

def print_banner(cfg: BotConfig):
    """Print startup banner with configuration summary."""
    banner = f"""
{C_CYAN}{C_BOLD}
    __  ___          _     ___            
   /  |/  /__  _____(_)___/ (_)___ _____  
  / /|_/ / _ \\/ ___/ / __  / / __ `/ __ \\ 
 / /  / /  __/ /  / / /_/ / / /_/ / / / / 
/_/  /_/\\___/_/  /_/\\__,_/_/\\__,_/_/ /_/  

       Multi-Currency Statistical Arbitrage Engine
{C_RESET}"""
    print(banner)

    log.info(DOUBLE_SEP)
    log.info(f"{C_BOLD}CONFIGURATION{C_RESET}")
    log.info(SEPARATOR)
    log.info(f"  Mode          : {C_CYAN}{cfg.EXECUTION_MODE.upper()}{C_RESET}")
    log.info(f"  Auto-Trade    : {C_GREEN if cfg.AUTO_TRADE else C_RED}{'ON' if cfg.AUTO_TRADE else 'OFF'}{C_RESET}")
    log.info(f"  Tick Interval : {cfg.TICK_INTERVAL_SECONDS}s")
    log.info(f"  Entry Z       : ±{cfg.ENTRY_ZSCORE}")
    log.info(f"  Exit Z        : ±{cfg.EXIT_ZSCORE}")
    log.info(f"  Stop Z        : ±{cfg.STOP_ZSCORE}")
    log.info(f"  Lookback      : {cfg.LOOKBACK_WINDOW} bars")
    log.info(f"  Kalman Filter : {'ON' if cfg.USE_KALMAN else 'OFF (OLS)'}")
    log.info(f"  Aggregator    : Top-{cfg.AGGREGATOR_TOP_N_SELECTION} rank selection (Max {cfg.MAX_SAME_CURRENCY_PAIRS} same currency)")
    log.info(f"  Realtime News : {'ON' if cfg.ENABLE_NEWS_SENTIMENT_BOOST else 'OFF'}")
    log.info(f"  Max Positions : {cfg.MAX_OPEN_PAIRS}")
    log.info(f"  Daily Loss Cap: {cfg.MAX_DAILY_LOSS_PCT}%")
    log.info(f"  Leverage      : {cfg.LEVERAGE}:1")
    log.info(f"  Pairs         : {len(PAIR_SYMBOLS)} symbols → {len(PAIR_SYMBOLS) * (len(PAIR_SYMBOLS) - 1) // 2} combinations")
    log.info(DOUBLE_SEP)


# ─── Core Bot Class ───────────────────────────────────────────────────────────

class MeridianBot:
    """
    Automated headless pair trading bot.
    Scans → Signals → Ranks → Risk Checks → Executes → Monitors → Exits.
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.tick_count = 0

        # Engine components
        self.data_engine = ForexDataEngine(seed=cfg.SEED)
        self.strategy = PairTradingStrategy(
            entry_zscore=cfg.ENTRY_ZSCORE,
            exit_zscore=cfg.EXIT_ZSCORE,
            stop_zscore=cfg.STOP_ZSCORE,
            lookback_window=cfg.LOOKBACK_WINDOW,
            use_kalman=cfg.USE_KALMAN,
        )
        self.oms = OrderManagementSystem(
            initial_balance=cfg.INITIAL_BALANCE,
            leverage=cfg.LEVERAGE,
        )
        self.risk_manager = ForexRiskManager(
            max_currency_exposure_pct=cfg.MAX_CURRENCY_EXPOSURE_PCT,
            max_daily_loss_pct=cfg.MAX_DAILY_LOSS_PCT,
            max_portfolio_drawdown_pct=cfg.MAX_PORTFOLIO_DRAWDOWN_PCT,
            max_open_pairs=cfg.MAX_OPEN_PAIRS,
            max_leverage=cfg.LEVERAGE,
        )
        self.mt5 = MT5TerminalBridge(magic_number=cfg.MT5_MAGIC_NUMBER)

        # Advanced Upgrade Components
        self.aggregator = SignalAggregator(cfg)
        self.news_engine = RealtimeNewsEngine(cfg)
        self.session_manager = SessionManager(cfg)
        self.perf_tracker = PerformanceTracker(cfg)

        # Cached matrix results
        self.pair_matrix: List[Dict[str, Any]] = []

        # Cooldown: pair_key -> tick number when cooldown expires
        self.cooldown_pairs: Dict[str, int] = {}
        self.COOLDOWN_TICKS = 5  # Don't re-enter a pair for 5 ticks after closing

    # ── Initialization ─────────────────────────────────────────

    def initialize(self):
        """Run startup sequence."""
        # 1. Connect to MT5 (or fall back to paper)
        self._connect_mt5()

        # 2. Start realtime news background worker
        if self.cfg.ENABLE_NEWS_SENTIMENT_BOOST:
            self.news_engine.start()
            log.info(f"{C_BLUE}NEWS{C_RESET}  │ Realtime News & Sentiment Engine started.")

        # 3. Ingest historical candle data (from MT5 if connected, or synthetic fallback)
        loaded_mt5 = False
        if self.mt5.is_connected:
            log.info(f"{C_BLUE}DATA{C_RESET}  │ Pulling {self.cfg.HISTORICAL_BARS} real M{self.cfg.CANDLE_FREQ_MINUTES} historical candles from MT5...")
            success, history = self.data_engine.fetch_mt5_historical_candles(
                n_bars=self.cfg.HISTORICAL_BARS,
                freq_minutes=self.cfg.CANDLE_FREQ_MINUTES
            )
            if success and history:
                loaded_mt5 = True
                log.info(f"{C_GREEN}DATA{C_RESET}  │ Real MT5 historical candles loaded for {len(history)} pairs.")

        if not loaded_mt5:
            log.info(f"{C_BLUE}DATA{C_RESET}  │ Generating {self.cfg.HISTORICAL_BARS} historical M{self.cfg.CANDLE_FREQ_MINUTES} candles for {len(PAIR_SYMBOLS)} pairs...")
            self.data_engine.generate_historical_candles(
                n_bars=self.cfg.HISTORICAL_BARS,
                freq_minutes=self.cfg.CANDLE_FREQ_MINUTES,
            )
            log.info(f"{C_BLUE}DATA{C_RESET}  │ Historical data ready.")

        # 4. Initial cointegration matrix scan
        self._scan_matrix()

    def _connect_mt5(self):
        """Attempt MT5 terminal connection."""
        if self.cfg.EXECUTION_MODE != "mt5":
            log.info(f"{C_YELLOW}MT5{C_RESET}   │ Paper mode — MT5 connection skipped.")
            return

        login = self.cfg.MT5_LOGIN if self.cfg.MT5_LOGIN > 0 else None
        password = self.cfg.MT5_PASSWORD or None
        server = self.cfg.MT5_SERVER or None

        log.info(f"{C_YELLOW}MT5{C_RESET}   │ Connecting to MT5 terminal...")
        success, msg = self.mt5.connect(login=login, password=password, server=server)

        if success:
            log.info(f"{C_GREEN}MT5{C_RESET}   │ {msg}")
            acc = self.mt5.account_info
            log.info(f"{C_GREEN}MT5{C_RESET}   │ Balance: ${acc.get('balance', 0):,.2f} │ Equity: ${acc.get('equity', 0):,.2f} │ Leverage: {acc.get('lever', 0)}:1")
        else:
            log.warning(f"{C_RED}MT5{C_RESET}   │ {msg}")
            log.warning(f"{C_RED}MT5{C_RESET}   │ Falling back to Paper Trading mode.")
            self.cfg.EXECUTION_MODE = "paper"

    # ── Matrix Scan ────────────────────────────────────────────

    def _scan_matrix(self):
        """Full 45-pair cointegration matrix scan."""
        self.pair_matrix = self.strategy.analyze_pair_matrix(self.data_engine.history)
        cointegrated = [p for p in self.pair_matrix if p["is_cointegrated"]]
        actionable = [p for p in self.pair_matrix if p["signal"] != "NEUTRAL"]

        # Verbose output only on periodic intervals (avoid console spam)
        if self.cfg.SHOW_MATRIX_EVERY_TICK:
            verbose = True
        else:
            verbose = (self.tick_count == 1) or (self.tick_count % 20 == 0)

        if verbose and not self.cfg.MINIMAL_LOGGING:
            log.info(SEPARATOR)
            log.info(f"{C_MAGENTA}MATRIX{C_RESET} | Scanned {len(self.pair_matrix)} pairs | Cointegrated: {C_CYAN}{len(cointegrated)}{C_RESET} | Actionable: {C_CYAN}{len(actionable)}{C_RESET}")

            if self.cfg.LOG_SCANS and cointegrated:
                for p in cointegrated[:5]:
                    z_color = C_GREEN if abs(p["current_zscore"]) >= self.cfg.ENTRY_ZSCORE else C_DIM
                    log.info(
                        f"{C_MAGENTA}MATRIX{C_RESET} |  {p['leg_a']}/{p['leg_b']}"
                        f"  p={p['p_value']:.4f}  B={p['beta']:.4f}"
                        f"  HL={p['half_life']:.1f}  H={p['hurst']:.3f}"
                        f"  {z_color}Z={p['current_zscore']:+.2f}{C_RESET}"
                        f"  {fmt_signal(p['signal'])}"
                    )
            log.info(SEPARATOR)

    # ── Main Loop ──────────────────────────────────────────────

    def run(self):
        """Main automated trading loop."""
        log.info(f"{C_BOLD}{C_CYAN}ENGINE{C_RESET} │ Bot loop started. Ctrl+C to stop.")
        log.info("")

        try:
            while True:
                self.tick_count += 1
                self._tick()
                time.sleep(self.cfg.TICK_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            log.info("")
            log.info(DOUBLE_SEP)
            log.info(f"{C_BOLD}{C_YELLOW}SHUTDOWN{C_RESET} │ Keyboard interrupt received.")
            self._shutdown()

    def _tick(self):
        """Single scan-signal-rank-risk-execute cycle."""

        # 0. Weekend Market Closure Guard (Pause ticks when market is closed)
        is_weekend, weekend_reason = ForexRiskManager.is_friday_weekend_close()
        if is_weekend:
            if not getattr(self, "_weekend_logged", False) or self.tick_count % 40 == 0:
                log.info(f"{C_YELLOW}WEEKEND SLEEP MODE{C_RESET} │ Market closed for the weekend (Friday 21:00 - Sunday 22:00 UTC). Bot idling...")
                self._weekend_logged = True
            time.sleep(15.0)
            return

        self._weekend_logged = False
        tick_data = self.data_engine.simulate_next_tick()
        prices = self.data_engine.get_latest_prices()

        # Update MT5 prices if connected
        if self.mt5.is_connected:
            for sym in PAIR_SYMBOLS:
                t = self.mt5.get_tick(sym)
                if t:
                    prices[sym] = t["price"]

        # 2. Mark-to-market open positions
        self.oms.update_ticks(prices)
        account = self.oms.get_account_summary()

        if self.mt5.is_connected:
            mt5_status = self.mt5.get_status()
            if mt5_status.get("account"):
                mt5_acc = mt5_status["account"]
                account["equity"] = mt5_acc.get("equity", account["equity"])
                account["balance"] = mt5_acc.get("balance", account["balance"])

        # 3. Session info & clean log ticker
        sess = self.session_manager.get_active_session()
        mode_str = f"{C_GREEN}MT5 LIVE{C_RESET}" if self.mt5.is_connected else f"{C_YELLOW}PAPER{C_RESET}"

        if self.cfg.MINIMAL_LOGGING:
            # Clean 1-line ticker
            log.info(
                f"{C_BOLD}MERIDIAN{C_RESET} │ Tick #{self.tick_count}"
                f" │ {mode_str}"
                f" │ Session: {C_CYAN}{sess['session_name']}{C_RESET}"
                f" │ Equity: {C_WHITE}${account['equity']:,.2f}{C_RESET}"
                f" │ Open: {len(self.oms.open_positions)}/{self.cfg.MAX_OPEN_PAIRS}"
                f" │ P&L: {fmt_usd(account['total_pnl'])}"
                f" │ Balance: ${account['balance']:,.2f}"
            )
        else:
            log.info(DOUBLE_SEP)
            log.info(
                f"{C_BOLD}MERIDIAN{C_RESET} │ Tick #{self.tick_count}"
                f" │ {mode_str}"
                f" │ Session: {C_CYAN}{sess['session_name']}{C_RESET}"
                f" │ Equity: {C_WHITE}${account['equity']:,.2f}{C_RESET}"
                f" │ Positions: {len(self.oms.open_positions)}/{self.cfg.MAX_OPEN_PAIRS}"
            )
            log.info(SEPARATOR)

        # 4. Re-scan cointegration matrix
        self._scan_matrix()

        # 5. Check and close positions that hit exit, partial exit, or stop
        self._check_exits(prices)

        # 6. Scan and rank new entry signals via Signal Aggregator
        if self.cfg.AUTO_TRADE:
            self._check_entries(prices)

        # 7. Log open positions (if active)
        if self.cfg.LOG_POSITIONS and self.oms.open_positions and not self.cfg.MINIMAL_LOGGING:
            self._log_positions()

        # 8. Summary footer (only if verbose mode)
        if not self.cfg.MINIMAL_LOGGING:
            unrealized = account["unrealized_pnl"]
            day_pnl = account["total_pnl"]
            log.info(SEPARATOR)
            log.info(
                f"{C_BOLD}SUMMARY{C_RESET} │ Open: {len(self.oms.open_positions)}"
                f" │ Unrealized: {fmt_usd(unrealized)}"
                f" │ Total P&L: {fmt_usd(day_pnl)}"
                f" │ Balance: ${account['balance']:,.2f}"
            )

    # ── Exit Check ─────────────────────────────────────────────

    def _check_exits(self, prices: Dict[str, float]):
        """Check open positions for full exit, partial exit, or stop conditions."""
        positions_to_close: List[Tuple[int, Dict[str, Any], float, str]] = []

        for pos in self.oms.open_positions:
            leg_a, leg_b = pos["leg_a"], pos["leg_b"]

            # Get current pair details for Z-score
            if leg_a in self.data_engine.history and leg_b in self.data_engine.history:
                details = self.strategy.generate_single_pair_series(
                    self.data_engine.history[leg_a],
                    self.data_engine.history[leg_b],
                    leg_a, leg_b,
                )
                curr_z = details["zscore"][-1] if details["zscore"] else 0.0
            else:
                curr_z = 0.0

            # Calculate current unrealized PnL & update peak PnL
            unreal_pnl = pos.get("unrealized_pnl", 0.0)
            if "peak_pnl" not in pos or unreal_pnl > pos["peak_pnl"]:
                pos["peak_pnl"] = unreal_pnl

            ticks_held = self.tick_count - pos.get("open_tick_count", self.tick_count)

            # 1. Partial Exit (50/50 scale out at Z = 1.0, requires at least 4 ticks holding time)
            if not pos.get("is_partially_closed", False) and ticks_held >= 4:
                if (pos["type"] == "LONG_SPREAD" and curr_z >= -self.cfg.PARTIAL_EXIT_ZSCORE) or \
                   (pos["type"] == "SHORT_SPREAD" and curr_z <= self.cfg.PARTIAL_EXIT_ZSCORE):
                    part_res = self.oms.partial_close_position(pos["position_id"], pct=0.5)
                    if part_res:
                        pos["peak_pnl"] = pos.get("unrealized_pnl", 0.0)
                        log.info(
                            f"{C_YELLOW}PARTIAL EXIT{C_RESET} | #{pos['position_id']} {leg_a}/{leg_b}"
                            f" | Scaled 50% at Z={curr_z:+.2f} | PnL: {fmt_usd(part_res['partial_pnl'])}"
                        )

            # 2. Nexus Pre-News Protection Guard (within 15 mins of high-impact news)
            news_filter = self.risk_manager.news_filter
            info_a = MAJOR_FOREX_PAIRS.get(leg_a, {})
            info_b = MAJOR_FOREX_PAIRS.get(leg_b, {})
            trade_currs = [info_a.get("base"), info_a.get("quote"), info_b.get("base"), info_b.get("quote")]
            is_imminent, news_msg = news_filter.is_news_imminent_for_active_trades(*trade_currs, imminent_minutes=15)
            if is_imminent and not pos.get("pre_news_locked", False):
                part_res = self.oms.partial_close_position(pos["position_id"], pct=0.5)
                pos["pre_news_locked"] = True
                log.info(f"{C_YELLOW}NEXUS PRE-NEWS GUARD{C_RESET} | #{pos['position_id']} {leg_a}/{leg_b} | {news_msg} | 50% profit harvested & stop moved to BE.")

            # 3. Full Exit conditions & Trailing Profit Lock
            exit_signal = False
            exit_reason = ""

            if ticks_held >= 4:
                if pos["type"] == "LONG_SPREAD" and curr_z >= -self.cfg.EXIT_ZSCORE:
                    exit_signal = True
                    exit_reason = "MEAN_REVERSION"
                elif pos["type"] == "SHORT_SPREAD" and curr_z <= self.cfg.EXIT_ZSCORE:
                    exit_signal = True
                    exit_reason = "MEAN_REVERSION"

            # Nexus Trailing Profit Lock: If peak PnL >= $4.00 and PnL drops 35% from peak
            if pos.get("peak_pnl", 0.0) >= 4.0 and unreal_pnl <= (pos["peak_pnl"] * 0.65):
                exit_signal = True
                exit_reason = "PROFIT_LOCK"

            if abs(curr_z) >= self.cfg.STOP_ZSCORE:
                exit_signal = True
                exit_reason = "STOP_LOSS"

            # Daily equity guard - force close all
            breached, _ = self.risk_manager.is_daily_equity_cap_breached(self.oms.equity)
            if breached:
                exit_signal = True
                exit_reason = "DAILY_EQUITY_GUARD"

            # Friday weekend guard - force close all
            is_friday, _ = ForexRiskManager.is_friday_weekend_close()
            if is_friday:
                exit_signal = True
                exit_reason = "FRIDAY_GUARD"

            if exit_signal:
                positions_to_close.append((pos["position_id"], pos, curr_z, exit_reason))

        for pos_id, pos, z, reason in positions_to_close:
            closed = self.oms.close_position(pos_id)
            if closed:
                # Close on MT5 too if live
                if self.cfg.EXECUTION_MODE == "mt5" and self.mt5.is_connected:
                    self.mt5.close_all_positions()

                pnl = closed["realized_pnl"]
                reason_color = C_GREEN if reason == "MEAN_REVERSION" else C_RED
                log.info(
                    f"{C_RED}CLOSE{C_RESET} | #{pos_id} {pos['leg_a']}/{pos['leg_b']}"
                    f" | {reason_color}{reason}{C_RESET}"
                    f" | Z={z:+.2f}"
                    f" | PnL: {fmt_usd(pnl)}"
                )

                # Record trade outcome in Performance Tracker
                pair_key = f"{pos['leg_a']}_{pos['leg_b']}"
                self.perf_tracker.record_trade(pair_key, pnl, reason)

                # Sync persistent memory & export monthly ledger
                meridian_state.update_positions(self.oms.open_positions)
                export_trade_ledger(magic_number=self.cfg.MT5_MAGIC_NUMBER)

                # Set cooldown so we don't immediately re-enter
                self.cooldown_pairs[pair_key] = self.tick_count + self.COOLDOWN_TICKS

    # ── Entry Check via Signal Aggregator ──────────────────────

    def _check_entries(self, prices: Dict[str, float]):
        """Collects candidate signals, passes them to Aggregator for ranking, and executes top N."""
        entry_z_thresh = self.session_manager.get_adjusted_entry_zscore()
        raw_candidates: List[TradeCandidate] = []

        # 1. Gather all raw candidate signals across matrix
        for p in self.pair_matrix:
            curr_z = p["current_zscore"]
            signal = "NEUTRAL"
            if curr_z <= -entry_z_thresh:
                signal = "LONG_SPREAD"
            elif curr_z >= entry_z_thresh:
                signal = "SHORT_SPREAD"

            if signal == "NEUTRAL":
                continue

            leg_a, leg_b = p["leg_a"], p["leg_b"]
            info_a = MAJOR_FOREX_PAIRS.get(leg_a, {})
            info_b = MAJOR_FOREX_PAIRS.get(leg_b, {})
            curr_set = {info_a.get("base"), info_a.get("quote"), info_b.get("base"), info_b.get("quote")} - {None, ""}

            # News sentiment score & Win rate
            sent_score = self.news_engine.get_pair_trade_sentiment(leg_a, leg_b) if self.cfg.ENABLE_NEWS_SENTIMENT_BOOST else 0.0
            win_rate = self.perf_tracker.get_win_rate(f"{leg_a}_{leg_b}")

            cand = TradeCandidate(
                pair_key=f"{leg_a}_{leg_b}",
                leg_a=leg_a,
                leg_b=leg_b,
                signal=signal,
                current_zscore=curr_z,
                p_value=p["p_value"],
                beta=p["beta"],
                half_life=p["half_life"],
                hurst=p["hurst"],
                price_a=prices.get(leg_a, p["price_a"]),
                price_b=prices.get(leg_b, p["price_b"]),
                is_cointegrated=p["is_cointegrated"],
                news_sentiment_score=sent_score,
                historical_win_rate=win_rate,
                currencies=curr_set
            )

            # Basic quality pre-filters
            if cand.half_life >= self.cfg.MIN_HALF_LIFE and cand.half_life <= self.cfg.MAX_HALF_LIFE and \
               cand.hurst <= self.cfg.MAX_HURST and cand.is_cointegrated:
                raw_candidates.append(cand)

        if not raw_candidates:
            return

        # 2. Pass candidate pool to Signal Aggregator for composite scoring & currency ranking
        selected_candidates = self.aggregator.filter_and_rank(
            raw_candidates,
            self.oms.open_positions,
            set(self.cooldown_pairs.keys())
        )

        # 3. Validate & Execute selected candidates
        for cand in selected_candidates:
            leg_a, leg_b = cand.leg_a, cand.leg_b

            # Pre-trade Risk Guard Chain
            can_trade, reason = self.risk_manager.can_open_position(
                leg_a, leg_b,
                self.oms.open_positions,
                self.oms.equity,
                0.0,
                pair_df=self.data_engine.history.get(leg_a),
            )

            if not can_trade:
                if self.cfg.LOG_RISK_CHECKS:
                    log.info(f"{C_YELLOW}RISK{C_RESET}  │ {leg_a}/{leg_b} blocked: {reason}")
                continue

            # Micro-Lot Position Sizing (0.01 lots per ~$250 equity to respect MT5 free margin)
            base_lots = max(0.01, round(self.oms.equity / 25000.0, 2))
            beta = max(0.1, abs(cand.beta))
            lots_a = max(0.01, min(0.10, base_lots))
            lots_b = max(0.01, min(0.10, round(lots_a * beta, 2)))

            p_a, p_b = cand.price_a, cand.price_b

            # Execute on MT5 if live
            if self.cfg.EXECUTION_MODE == "mt5" and self.mt5.is_connected:
                success, msg, details = self.mt5.send_pair_order(
                    leg_a, leg_b, cand.signal, lots_a, lots_b,
                )
                if not success:
                    log.warning(f"{C_RED}MT5{C_RESET}   │ Order rejected: {msg}")
                    continue
                log.info(f"{C_GREEN}MT5{C_RESET}   │ {msg}")

            # Record in paper OMS
            pos = self.oms.open_pair_position(
                leg_a=leg_a,
                leg_b=leg_b,
                pos_type=cand.signal,
                beta=beta,
                lots_a=lots_a,
                lots_b=lots_b,
                entry_price_a=p_a,
                entry_price_b=p_b,
                entry_zscore=cand.current_zscore,
            )
            if pos:
                pos["open_tick_count"] = self.tick_count

            # Sync persistent state memory
            meridian_state.update_positions(self.oms.open_positions)

            log.info(
                f"{C_GREEN}OPEN{C_RESET}  │ #{pos['position_id']} {leg_a}/{leg_b}"
                f" │ {fmt_signal(cand.signal)}"
                f" │ Q-Score={cand.quality_score:.1f}"
                f" │ Z={cand.current_zscore:+.2f}"
                f" │ β={beta:.4f}"
                f" │ Lots: {lots_a}/{lots_b}"
            )

    # ── Position Logger ────────────────────────────────────────

    def _log_positions(self):
        """Log mark-to-market for all open positions."""
        for pos in self.oms.open_positions:
            pnl = pos.get("unrealized_pnl", 0.0)
            part_str = " (50% Scaled)" if pos.get("is_partially_closed") else ""
            log.info(
                f"{C_BLUE}POS{C_RESET}   │ #{pos['position_id']} {pos['leg_a']}/{pos['leg_b']}"
                f" │ {fmt_signal(pos['type'])}"
                f" │ β={pos['beta']:.4f}"
                f" │ PnL: {fmt_usd(pnl)}{part_str}"
            )

    # ── Shutdown ───────────────────────────────────────────────

    def _shutdown(self):
        """Graceful shutdown: stop news worker, close positions, disconnect MT5."""
        if self.cfg.ENABLE_NEWS_SENTIMENT_BOOST:
            self.news_engine.stop()

        if self.oms.open_positions:
            log.info(f"{C_YELLOW}SHUTDOWN{C_RESET} │ Closing {len(self.oms.open_positions)} open positions...")
            closed = self.oms.close_all_positions()
            for c in closed:
                log.info(
                    f"{C_YELLOW}SHUTDOWN{C_RESET} │ Closed #{c['position_id']} {c['leg_a']}/{c['leg_b']}"
                    f" │ PnL: {fmt_usd(c['realized_pnl'])}"
                )

            if self.mt5.is_connected:
                self.mt5.close_all_positions()

        # Final account summary
        export_trade_ledger(magic_number=self.cfg.MT5_MAGIC_NUMBER)
        account = self.oms.get_account_summary()
        if self.mt5.is_connected:
            mt5_status = self.mt5.get_status()
            if mt5_status.get("account"):
                account["balance"] = mt5_status["account"].get("balance", account["balance"])
                account["total_pnl"] = round(account["balance"] - self.mt5.account_info.get("balance", account["balance"]), 2)
                account["total_pnl_pct"] = 0.0

        log.info(SEPARATOR)
        log.info(f"{C_BOLD}FINAL{C_RESET}  │ Balance: ${account['balance']:,.2f} │ Total P&L: {fmt_usd(account['total_pnl'])} ({fmt_pct(account['total_pnl_pct'])})")
        log.info(f"{C_BOLD}FINAL{C_RESET}  │ Trades executed: {account['closed_trades_count']}")
        log.info(DOUBLE_SEP)
        log.info(f"{C_DIM}Meridian terminated.{C_RESET}")


# ─── Entry Point ───────────────────────────────────────────────────────────────

def main():
    cfg = BotConfig()
    print_banner(cfg)

    bot = MeridianBot(cfg)
    bot.initialize()
    bot.run()


if __name__ == "__main__":
    main()
