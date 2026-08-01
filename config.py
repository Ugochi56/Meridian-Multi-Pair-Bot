"""
Meridian FX Bot — Centralized Configuration.
All tuneable parameters in one place.
"""


class BotConfig:
    """Global bot configuration."""

    # ─── Bot Control ───────────────────────────────────────────
    TICK_INTERVAL_SECONDS: float = 15.0       # Seconds between scan cycles
    AUTO_TRADE: bool = True                   # Auto-execute signals (False = scan-only / dry-run)
    EXECUTION_MODE: str = "mt5"               # "mt5" (connects to active terminal) or "paper"

    # ─── Strategy Parameters ───────────────────────────────────
    ENTRY_ZSCORE: float = 2.0                 # Z-score threshold to open a spread trade
    EXIT_ZSCORE: float = 0.5                  # Z-score threshold to close (mean reversion target)
    PARTIAL_EXIT_ZSCORE: float = 1.0          # Z-score threshold for 50% partial exit
    STOP_ZSCORE: float = 3.5                  # Z-score threshold to stop-loss
    LOOKBACK_WINDOW: int = 60                 # Rolling window for Z-score calculation (bars)
    USE_KALMAN: bool = True                   # True = Kalman Filter, False = static OLS beta
    MIN_HALF_LIFE: float = 1.0               # Minimum OU half-life (bars) to consider a pair tradable
    MAX_HALF_LIFE: float = 20.0              # Maximum OU half-life — fast reverting pairs only
    MAX_HURST: float = 0.45                  # Maximum Hurst exponent (< 0.45 = strong mean-reverting memory)

    # ─── Signal Aggregator & Ranking ───────────────────────────
    AGGREGATOR_TOP_N_SELECTION: int = 3       # Max new trades selected per scan cycle
    MAX_SAME_CURRENCY_PAIRS: int = 2          # Max open trades sharing any single currency
    WEIGHT_ZSCORE: float = 0.35               # Quality score weight for Z-score magnitude
    WEIGHT_PVALUE: float = 0.25               # Quality score weight for ADF p-value
    WEIGHT_HURST: float = 0.20                # Quality score weight for low Hurst exponent
    WEIGHT_NEWS_SENTIMENT: float = 0.10       # Quality score weight for news alignment
    WEIGHT_WIN_RATE: float = 0.10             # Quality score weight for historical win rate

    # ─── Real-Time News & Sentiment Engine ─────────────────────
    ENABLE_NEWS_SENTIMENT_BOOST: bool = True  # Boost signals aligned with live news sentiment
    NEWS_POLL_INTERVAL_SECONDS: float = 30.0  # Seconds between live news RSS/calendar updates

    # ─── Session Manager ───────────────────────────────────────
    SESSION_ADAPTATION_ENABLED: bool = True   # Adjust thresholds dynamically by trading session
    ASIAN_ENTRY_Z_MULTIPLIER: float = 0.9     # Asian session multiplier (lower Z = more entries)
    LONDON_NY_ENTRY_Z_MULTIPLIER: float = 1.15 # London/NY multiplier (higher Z = safer entries)

    # ─── Technical Filters ─────────────────────────────────────
    ENABLE_VWAP_RSI_FILTER: bool = True       # Enforce VWAP / RSI confirmation
    RSI_OVERSOLD: float = 35.0                # RSI threshold for oversold confirmation
    RSI_OVERBOUGHT: float = 65.0              # RSI threshold for overbought confirmation
    USE_ATR_SL: bool = True                   # Hard ATR-based broker Stop Loss
    ATR_SL_MULTIPLIER: float = 2.5            # Multiplier for ATR Stop Loss

    # ─── Performance Tracker ───────────────────────────────────
    ENABLE_PERFORMANCE_FEEDBACK: bool = True  # Track win-rates to adjust candidate scores
    PERFORMANCE_LOOKBACK_TRADES: int = 50     # Trade history window for win rate calculation

    # ─── Risk Management ───────────────────────────────────────
    INITIAL_BALANCE: float = 100_000.0        # Starting paper balance (USD)
    LEVERAGE: float = 30.0                    # Account leverage
    RISK_PER_TRADE_PCT: float = 3.0           # % of equity risked per trade (Optimized to 3.0%)
    MAX_DAILY_LOSS_PCT: float = 3.0           # Hard daily equity drawdown limit
    MAX_PORTFOLIO_DRAWDOWN_PCT: float = 15.0  # Lifetime max drawdown limit
    MAX_OPEN_PAIRS: int = 5                   # Maximum concurrent open pair positions
    MAX_CURRENCY_EXPOSURE_PCT: float = 30.0   # Max net exposure to any single currency (% of balance)
    FRIDAY_CUTOFF_HOUR_UTC: int = 21          # Close all positions after this hour on Friday

    # ─── MT5 Connection ────────────────────────────────────────
    MT5_LOGIN: int = 0                        # MT5 account login (0 = use default terminal)
    MT5_PASSWORD: str = ""                    # MT5 account password
    MT5_SERVER: str = ""                      # MT5 broker server name
    MT5_MAGIC_NUMBER: int = 888999            # Magic number for identifying bot orders

    # ─── Display & Verbosity ───────────────────────────────────
    MINIMAL_LOGGING: bool = True              # Quiet mode: 1-line clean ticker, suppresses wall of text
    SHOW_MATRIX_EVERY_TICK: bool = False      # Only log pair matrix on trade signal or every 20 ticks
    LOG_RISK_CHECKS: bool = False             # Suppress repetitive risk block messages during closed hours

    # ─── Execution ──────────────────────────────────────────────────
    HISTORICAL_BARS: int = 500                # Number of historical candles for live bot startup (500 M15 bars)
    CANDLE_FREQ_MINUTES: int = 15             # Candle timeframe (M15)
    SEED: int = 42                            # Random seed for reproducible synthetic data

    # ─── Logging ───────────────────────────────────────────────
    LOG_TICKS: bool = False                   # Log every tick update (verbose)
    LOG_SCANS: bool = True                    # Log pair scan results each cycle
    LOG_RISK_CHECKS: bool = True              # Log risk guard evaluations
    LOG_POSITIONS: bool = True                # Log open position mark-to-market
    MATRIX_RESCAN_INTERVAL: int = 20          # Re-scan full cointegration matrix every N ticks
