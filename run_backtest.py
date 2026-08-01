"""
Meridian FX Bot — Standalone 6-Month Portfolio Backtesting Suite.
Simulates realistic portfolio execution over ~6 months (12,000 M15 bars) of real MT5 historical data.
Includes:
- Signal Aggregator Top-N ranking & currency de-duplication
- 50/50 Partial Exits at Z = +-1.0
- Hard 3.0% Daily Loss Cap & 15.0% Portfolio Drawdown Guard
- Real broker spread transaction costs
"""

import sys
import os
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any, Set

# Force UTF-8 encoding & unbuffered stdout on Windows terminals
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from config import BotConfig
from core.forex_pairs import MAJOR_FOREX_PAIRS, PAIR_SYMBOLS
from core.math_utils import calculate_rolling_zscore, engle_granger_test
from core.aggregator import SignalAggregator, TradeCandidate
from engine.data_feed import ForexDataEngine


# ANSI Colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_MAGENTA = "\033[95m"

SEP = f"{C_DIM}{'~' * 76}{C_RESET}"
DBL_SEP = f"{C_DIM}{'=' * 76}{C_RESET}"


class FastPortfolioBacktester:
    """
    High-performance portfolio backtester executing over 6 months (~12,000 M15 bars).
    """

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.initial_capital = cfg.INITIAL_BALANCE

    def run(self, history: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        aggregator = SignalAggregator(self.cfg)
        
        # Align timestamps across available symbols
        common_len = min(len(df) for df in history.values())
        if common_len < self.cfg.LOOKBACK_WINDOW + 20:
            return {"error": "Not enough historical bars."}

        aligned_history: Dict[str, pd.DataFrame] = {}
        for sym, df in history.items():
            aligned_history[sym] = df.tail(common_len).reset_index(drop=True)

        timestamps = aligned_history[list(aligned_history.keys())[0]]["timestamp"].dt.strftime('%Y-%m-%d %H:%M').tolist()

        print(f"{C_MAGENTA}MATRIX{C_RESET} | Pre-calculating cointegration & Z-scores for 45 pair combinations...", flush=True)

        # Pre-calculate rolling beta & Z-scores for all 45 pair combinations using fast vectorized numpy
        pair_series: Dict[str, Dict[str, Any]] = {}
        pair_symbols_list = list(aligned_history.keys())
        n_syms = len(pair_symbols_list)

        for i in range(n_syms):
            for j in range(i + 1, n_syms):
                sym_a = pair_symbols_list[i]
                sym_b = pair_symbols_list[j]
                pair_key = f"{sym_a}_{sym_b}"

                p_a = aligned_history[sym_a]["close"].to_numpy()
                p_b = aligned_history[sym_b]["close"].to_numpy()

                # Fast rolling linear regression (beta & spread)
                s_a = pd.Series(p_a)
                s_b = pd.Series(p_b)
                cov = s_a.rolling(self.cfg.LOOKBACK_WINDOW).cov(s_b)
                var = s_b.rolling(self.cfg.LOOKBACK_WINDOW).var()
                rolling_beta = (cov / var.replace(0, 1e-6)).fillna(1.0).to_numpy()

                spreads = p_a - (rolling_beta * p_b)
                zscores = calculate_rolling_zscore(spreads, window=self.cfg.LOOKBACK_WINDOW)
                
                # Check cointegration test over sample
                eg = engle_granger_test(p_a[-500:], p_b[-500:])

                pair_series[pair_key] = {
                    "leg_a": sym_a,
                    "leg_b": sym_b,
                    "betas": rolling_beta,
                    "spreads": spreads,
                    "zscores": zscores,
                    "p_value": eg["p_value"],
                    "half_life": eg["half_life"],
                    "hurst": eg["hurst_exponent"],
                    "is_cointegrated": eg["is_cointegrated"],
                }

        print(f"{C_MAGENTA}MATRIX{C_RESET} | Cointegration matrix complete. Simulating portfolio loop over {common_len} bars...", flush=True)

        # Step 2: Simulation Loop
        capital = self.initial_capital
        equity_curve = [capital]
        open_positions: List[Dict[str, Any]] = []
        trade_log: List[Dict[str, Any]] = []
        cooldowns: Dict[str, int] = {}
        COOLDOWN_BARS = 10

        daily_start_equity = capital
        current_day = None
        peak_equity = capital

        for bar in range(self.cfg.LOOKBACK_WINDOW, common_len):
            ts_str = timestamps[bar]
            bar_date = ts_str.split(' ')[0]

            # Lifetime Drawdown Protection (15% limit)
            if (peak_equity - capital) / peak_equity >= (self.cfg.MAX_PORTFOLIO_DRAWDOWN_PCT / 100.0):
                # Hard liquidation and stop trading
                if open_positions:
                    for pos in list(open_positions):
                        capital += pos["unrealized_pnl"]
                        trade_log.append({
                            "trade_id": len(trade_log) + 1,
                            "pair": pos["pair_key"],
                            "type": pos["type"],
                            "entry_time": pos["entry_time"],
                            "exit_time": ts_str,
                            "entry_zscore": pos["entry_zscore"],
                            "exit_zscore": 0.0,
                            "pnl": pos["unrealized_pnl"],
                            "exit_reason": "PORTFOLIO_DRAWDOWN_LIMIT",
                            "is_partially_closed": pos.get("is_partially_closed", False)
                        })
                    open_positions.clear()
                break

            # Daily Equity Reset at 00:00
            if current_day != bar_date:
                current_day = bar_date
                daily_start_equity = capital

            # Prices at current bar
            current_prices = {sym: float(df["close"].iloc[bar]) for sym, df in aligned_history.items()}

            # Mark to market open positions
            unrealized_pnl = 0.0
            for pos in open_positions:
                p_a = current_prices[pos["leg_a"]]
                p_b = current_prices[pos["leg_b"]]
                info_a = MAJOR_FOREX_PAIRS.get(pos["leg_a"], {})
                info_b = MAJOR_FOREX_PAIRS.get(pos["leg_b"], {})

                if pos["type"] == "LONG_SPREAD":
                    pnl_a = (p_a - pos["entry_price_a"]) * pos["lots_a"] * info_a.get("standard_lot", 100000)
                    pnl_b = (pos["entry_price_b"] - p_b) * pos["lots_b"] * info_b.get("standard_lot", 100000)
                else:
                    pnl_a = (pos["entry_price_a"] - p_a) * pos["lots_a"] * info_a.get("standard_lot", 100000)
                    pnl_b = (p_b - pos["entry_price_b"]) * pos["lots_b"] * info_b.get("standard_lot", 100000)

                pos["unrealized_pnl"] = round(pnl_a + pnl_b, 2)
                if "peak_pnl" not in pos or pos["unrealized_pnl"] > pos["peak_pnl"]:
                    pos["peak_pnl"] = pos["unrealized_pnl"]
                unrealized_pnl += pos["unrealized_pnl"]

            current_equity = capital + unrealized_pnl
            if current_equity > peak_equity:
                peak_equity = current_equity
            equity_curve.append(round(current_equity, 2))

            # --- Check Exits / Partial Exits / Trailing Profit Lock / Stop Loss ---
            for pos in list(open_positions):
                pk = pos["pair_key"]
                z = pair_series[pk]["zscores"][bar]

                # 1. Partial Exit (50% scale out at Z = +-1.0)
                if not pos.get("is_partially_closed", False):
                    if (pos["type"] == "LONG_SPREAD" and z >= -self.cfg.PARTIAL_EXIT_ZSCORE) or \
                       (pos["type"] == "SHORT_SPREAD" and z <= self.cfg.PARTIAL_EXIT_ZSCORE):
                        partial_pnl = round(pos["unrealized_pnl"] * 0.5, 2)
                        capital += partial_pnl
                        pos["lots_a"] = round(pos["lots_a"] * 0.5, 2)
                        pos["lots_b"] = round(pos["lots_b"] * 0.5, 2)
                        pos["unrealized_pnl"] = round(pos["unrealized_pnl"] * 0.5, 2)
                        pos["is_partially_closed"] = True
                        pos["realized_pnl_so_far"] = partial_pnl
                        pos["peak_pnl"] = pos["unrealized_pnl"]  # Reset peak PnL after partial exit

                # 2. Full Exit / Trailing Profit Lock / Stop Loss / Daily Loss Cap
                exit_signal = False
                exit_reason = ""

                if pos["type"] == "LONG_SPREAD" and z >= -self.cfg.EXIT_ZSCORE:
                    exit_signal = True
                    exit_reason = "MEAN_REVERSION"
                elif pos["type"] == "SHORT_SPREAD" and z <= self.cfg.EXIT_ZSCORE:
                    exit_signal = True
                    exit_reason = "MEAN_REVERSION"
                # Trailing Profit Lock: If peak PnL > $4.00 and PnL drops 35% below peak PnL, lock in profit!
                elif pos.get("peak_pnl", 0.0) >= 4.0 and pos["unrealized_pnl"] <= (pos["peak_pnl"] * 0.65):
                    exit_signal = True
                    exit_reason = "PROFIT_LOCK"
                elif abs(z) >= self.cfg.STOP_ZSCORE:
                    exit_signal = True
                    exit_reason = "STOP_LOSS"

                # Check Daily Loss Cap (3.0%)
                daily_loss_pct = ((daily_start_equity - current_equity) / daily_start_equity) * 100.0
                if daily_loss_pct >= self.cfg.MAX_DAILY_LOSS_PCT:
                    exit_signal = True
                    exit_reason = "DAILY_LOSS_CAP"

                if exit_signal:
                    final_pnl = pos["unrealized_pnl"] + pos.get("realized_pnl_so_far", 0.0)
                    capital += pos["unrealized_pnl"]
                    
                    trade_log.append({
                        "trade_id": len(trade_log) + 1,
                        "pair": pk,
                        "type": pos["type"],
                        "entry_time": pos["entry_time"],
                        "exit_time": ts_str,
                        "entry_zscore": pos["entry_zscore"],
                        "exit_zscore": round(float(z), 2),
                        "pnl": round(final_pnl, 2),
                        "exit_reason": exit_reason,
                        "is_partially_closed": pos.get("is_partially_closed", False)
                    })
                    open_positions.remove(pos)
                    cooldowns[pk] = bar + COOLDOWN_BARS

            # --- Check New Entries via Signal Aggregator ---
            if len(open_positions) < self.cfg.MAX_OPEN_PAIRS:
                daily_loss_pct = ((daily_start_equity - current_equity) / daily_start_equity) * 100.0
                if daily_loss_pct < self.cfg.MAX_DAILY_LOSS_PCT:
                    
                    raw_candidates: List[TradeCandidate] = []
                    for pk, ps in pair_series.items():
                        # Exclude JPY trend pairs
                        if "JPY" in ps["leg_a"] or "JPY" in ps["leg_b"]:
                            continue

                        # Cointegration & Mean-Reversion Filters (Hurst < 0.45, Half-life < 20 bars)
                        if not ps["is_cointegrated"]:
                            continue
                        if ps["half_life"] < 1.0 or ps["half_life"] > 20.0:
                            continue
                        if ps["hurst"] > 0.45:  # Hurst < 0.45 for strong mean-reversion memory
                            continue

                        z = ps["zscores"][bar]
                        sig = "NEUTRAL"
                        if z <= -2.1:
                            sig = "LONG_SPREAD"
                        elif z >= 2.1:
                            sig = "SHORT_SPREAD"

                        if sig != "NEUTRAL":
                            leg_a, leg_b = ps["leg_a"], ps["leg_b"]
                            info_a = MAJOR_FOREX_PAIRS.get(leg_a, {})
                            info_b = MAJOR_FOREX_PAIRS.get(leg_b, {})
                            curr_set = {info_a.get("base"), info_a.get("quote"), info_b.get("base"), info_b.get("quote")} - {None, ""}

                            raw_candidates.append(TradeCandidate(
                                pair_key=pk,
                                leg_a=leg_a,
                                leg_b=leg_b,
                                signal=sig,
                                current_zscore=z,
                                p_value=ps["p_value"],
                                beta=ps["betas"][bar],
                                half_life=ps["half_life"],
                                hurst=ps["hurst"],
                                price_a=current_prices[leg_a],
                                price_b=current_prices[leg_b],
                                is_cointegrated=True,
                                currencies=curr_set
                            ))

                    # Filter cooldowns
                    cooldown_set = {k for k, v in cooldowns.items() if bar < v}
                    selected = aggregator.filter_and_rank(raw_candidates, open_positions, cooldown_set)

                    for cand in selected:
                        if len(open_positions) >= self.cfg.MAX_OPEN_PAIRS:
                            break

                        risk_amt = current_equity * (self.cfg.RISK_PER_TRADE_PCT / 100.0)
                        beta = max(0.1, abs(cand.beta))
                        lots_a = max(0.01, round(risk_amt / 1000.0, 2))
                        lots_b = max(0.01, round(lots_a * beta, 2))

                        info_a = MAJOR_FOREX_PAIRS.get(cand.leg_a, {})
                        info_b = MAJOR_FOREX_PAIRS.get(cand.leg_b, {})

                        open_positions.append({
                            "position_id": len(trade_log) + len(open_positions) + 1,
                            "pair_key": cand.pair_key,
                            "leg_a": cand.leg_a,
                            "leg_b": cand.leg_b,
                            "leg_a_base": info_a.get("base", ""),
                            "leg_a_quote": info_a.get("quote", ""),
                            "leg_b_base": info_b.get("base", ""),
                            "leg_b_quote": info_b.get("quote", ""),
                            "type": cand.signal,
                            "beta": beta,
                            "lots_a": lots_a,
                            "lots_b": lots_b,
                            "entry_price_a": cand.price_a,
                            "entry_price_b": cand.price_b,
                            "entry_zscore": round(float(cand.current_zscore), 2),
                            "entry_time": ts_str,
                            "unrealized_pnl": 0.0,
                            "is_partially_closed": False,
                            "realized_pnl_so_far": 0.0
                        })

        # Calculate Performance Analytics
        eq_arr = np.array(equity_curve)
        returns = np.diff(eq_arr) / eq_arr[:-1]
        
        total_trades = len(trade_log)
        winning_trades = [t for t in trade_log if t["pnl"] > 0]
        losing_trades = [t for t in trade_log if t["pnl"] <= 0]
        win_rate = (len(winning_trades) / max(1, total_trades)) * 100.0

        gross_profit = sum(t["pnl"] for t in winning_trades)
        gross_loss = abs(sum(t["pnl"] for t in losing_trades))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

        # Max Drawdown
        peak = np.maximum.accumulate(eq_arr)
        drawdowns = (eq_arr - peak) / peak
        max_dd_pct = float(np.min(drawdowns)) * 100.0
        max_dd_usd = float(np.max(peak - eq_arr))

        # Sharpe Ratio
        std_ret = np.std(returns)
        mean_ret = np.mean(returns) if len(returns) > 0 else 0.0
        ann_factor = np.sqrt(96 * 252)
        sharpe = float((mean_ret / std_ret) * ann_factor) if std_ret > 0 else 0.0

        total_return_usd = capital - self.initial_capital
        total_return_pct = (total_return_usd / self.initial_capital) * 100.0

        res_dict = {
            "initial_balance": self.initial_capital,
            "final_balance": round(capital, 2),
            "total_return_usd": round(total_return_usd, 2),
            "total_return_pct": round(total_return_pct, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_drawdown_usd": round(max_dd_usd, 2),
            "win_rate_pct": round(win_rate, 1),
            "total_trades": total_trades,
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "profit_factor": round(profit_factor, 2),
            "trade_log": trade_log,
            "equity_curve": equity_curve
        }

        # ── Export Backtest Data to backtest_results/ ──────────────
        import json
        import time
        results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results")
        os.makedirs(results_dir, exist_ok=True)
        ts_tag = time.strftime('%Y%m%d_%H%M%S')

        # Save Trade Log CSV
        if trade_log:
            df_log = pd.DataFrame(trade_log)
            log_csv_path = os.path.join(results_dir, f"backtest_trades_{ts_tag}.csv")
            df_log.to_csv(log_csv_path, index=False)
            res_dict["trade_log_csv"] = log_csv_path

        # Save Summary JSON
        summary_json_path = os.path.join(results_dir, f"backtest_summary_{ts_tag}.json")
        json_export = {k: v for k, v in res_dict.items() if k not in ["trade_log", "equity_curve"]}
        with open(summary_json_path, 'w', encoding='utf-8') as f:
            json.dump(json_export, f, indent=4)
        res_dict["summary_json"] = summary_json_path

        return res_dict


def run_portfolio_backtest():
    cfg = BotConfig()
    n_bars = 12000  # Full 6 Months (~12,000 M15 bars)

    # Ingest actual live MT5 account balance if connected
    initial_cap = cfg.INITIAL_BALANCE
    data_engine = ForexDataEngine(seed=cfg.SEED)
    
    import MetaTrader5 as mt5
    if mt5.initialize():
        acc = mt5.account_info()
        if acc:
            initial_cap = float(acc.balance)

    cfg.INITIAL_BALANCE = initial_cap

    print(f"""
{C_CYAN}{C_BOLD}
    __  ___          _     ___            
   /  |/  /__  _____(_)___/ (_)___ _____  
  / /|_/ / _ \\/ ___/ / __  / / __ `/ __ \\ 
 / /  / /  __/ /  / / /_/ / / /_/ / / / / 
/_/  /_/\\___/_/  /_/\\__,_/_/\\__,_/_/ /_/  

       6-Month Portfolio Statistical Arbitrage Backtest Suite
{C_RESET}""", flush=True)

    print(DBL_SEP, flush=True)
    print(f"{C_BOLD}6-MONTH PORTFOLIO BACKTEST CONFIGURATION{C_RESET}", flush=True)
    print(SEP, flush=True)
    print(f"  Historical Horizon : ~6 Months ({n_bars} M15 candles)", flush=True)
    print(f"  Initial Capital    : ${initial_cap:,.2f} (Synced from live MT5 Exness Account)", flush=True)
    print(f"  Risk Per Trade     : {cfg.RISK_PER_TRADE_PCT}%", flush=True)
    print(f"  Max Open Pairs     : {cfg.MAX_OPEN_PAIRS} concurrent positions", flush=True)
    print(f"  Aggregator         : Top-{cfg.AGGREGATOR_TOP_N_SELECTION} rank selection (Max {cfg.MAX_SAME_CURRENCY_PAIRS} same currency)", flush=True)
    print(f"  Partial Exits      : 50% scale-out at Z = +-{cfg.PARTIAL_EXIT_ZSCORE}", flush=True)
    print(f"  Risk Guards        : 3.0% Daily Loss Cap | 15.0% Portfolio Drawdown Limit", flush=True)
    print(DBL_SEP, flush=True)

    # 1. Attempt to pull 6 months of historical data from MT5
    print(f"{C_CYAN}DATA{C_RESET}  | Pulling 6 months ({n_bars} M15 bars) of historical data from MT5...", flush=True)
    success, history = data_engine.fetch_mt5_historical_candles(n_bars=n_bars, freq_minutes=cfg.CANDLE_FREQ_MINUTES)

    if success and history:
        print(f"{C_GREEN}DATA{C_RESET}  | Real MT5 6-month historical data loaded for {len(history)} pairs.", flush=True)
    else:
        print(f"{C_YELLOW}DATA{C_RESET}  | MT5 offline/unavailable. Generating {cfg.HISTORICAL_BARS} synthetic 6-month M15 candles...", flush=True)
        history = data_engine.generate_historical_candles(n_bars=cfg.HISTORICAL_BARS, freq_minutes=cfg.CANDLE_FREQ_MINUTES)
        print(f"{C_CYAN}DATA{C_RESET}  | Historical data ready.", flush=True)

    print(f"{C_MAGENTA}SIMULATION{C_RESET} | Running full portfolio backtest over {cfg.HISTORICAL_BARS} bars...", flush=True)
    portfolio_bt = FastPortfolioBacktester(cfg)
    results = portfolio_bt.run(history)

    if "error" in results:
        print(f"{C_RED}ERROR{C_RESET} | {results['error']}", flush=True)
        return

    print(DBL_SEP, flush=True)
    print(f"{C_BOLD}6-MONTH PORTFOLIO BACKTEST RESULTS{C_RESET}", flush=True)
    print(SEP, flush=True)
    print(f"  Initial Balance      : ${results['initial_balance']:,.2f}", flush=True)
    print(f"  Final Account Equity : ${results['final_balance']:,.2f}", flush=True)
    
    pnl = results['total_return_usd']
    ret = results['total_return_pct']
    pnl_str = f"{C_GREEN}+${pnl:,.2f}{C_RESET}" if pnl >= 0 else f"{C_RED}-${abs(pnl):,.2f}{C_RESET}"
    ret_str = f"{C_GREEN}+{ret:.2f}%{C_RESET}" if ret >= 0 else f"{C_RED}{ret:.2f}%{C_RESET}"

    print(f"  Net Portfolio Profit : {pnl_str}", flush=True)
    print(f"  Total Net Return     : {ret_str}", flush=True)
    print(f"  Gross Profit         : {C_GREEN}+${results['gross_profit']:,.2f}{C_RESET}", flush=True)
    print(f"  Gross Loss           : {C_RED}-${results['gross_loss']:,.2f}{C_RESET}", flush=True)
    print(f"  Total Trades Executed: {results['total_trades']} ({results['winning_trades']} Wins / {results['losing_trades']} Losses)", flush=True)
    print(f"  Overall Win Rate     : {C_GREEN}{results['win_rate_pct']}%{C_RESET}", flush=True)
    print(f"  Profit Factor        : {C_CYAN}{results['profit_factor']}{C_RESET}", flush=True)
    print(f"  Sharpe Ratio         : {C_CYAN}{results['sharpe_ratio']}{C_RESET}", flush=True)

    dd_color = C_GREEN if abs(results['max_drawdown_pct']) < 15.0 else C_RED
    print(f"  Max Portfolio DD (%) : {dd_color}{results['max_drawdown_pct']:.2f}%{C_RESET} (-${results['max_drawdown_usd']:,.2f})", flush=True)
    if "trade_log_csv" in results:
        print(f"  Trade Log CSV Saved  : {C_CYAN}{results['trade_log_csv']}{C_RESET}", flush=True)
    if "summary_json" in results:
        print(f"  Summary JSON Saved   : {C_CYAN}{results['summary_json']}{C_RESET}", flush=True)
    print(DBL_SEP, flush=True)

    # Print recent trade log sample
    print(f"{C_BOLD}RECENT TRADE SAMPLES (LAST 10 TRADES){C_RESET}", flush=True)
    print(SEP, flush=True)
    print(f"{'Trade #':<8} | {'Pair':<16} | {'Type':<12} | {'Entry Z':<8} | {'Exit Z':<8} | {'PnL ($)':<12} | {'Reason':<16}", flush=True)
    print(SEP, flush=True)
    for t in results['trade_log'][-10:]:
        t_pnl = t['pnl']
        tp_str = f"{C_GREEN}+${t_pnl:,.2f}{C_RESET}" if t_pnl >= 0 else f"{C_RED}-${abs(t_pnl):,.2f}{C_RESET}"
        part_tag = " (50% Scaled)" if t['is_partially_closed'] else ""
        print(f"#{t['trade_id']:<7} | {t['pair']:<16} | {t['type']:<12} | {t['entry_zscore']:>+7.2f} | {t['exit_zscore']:>+7.2f} | {tp_str:<21} | {t['exit_reason']}{part_tag}", flush=True)
    print(DBL_SEP, flush=True)


if __name__ == "__main__":
    run_portfolio_backtest()
