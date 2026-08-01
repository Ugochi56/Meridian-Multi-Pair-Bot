"""
Meridian Trade Logger — Quantitative Ledger & Monthly Reporting Suite.
Ported from Nexus core. Automatically queries MT5 deal history by magic number,
calculates net performance metrics, and exports monthly CSV ledgers and text reports.
"""

import os
import sys
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")


def calculate_analytics(df: pd.DataFrame) -> Dict[str, Any]:
    """Calculates win rate, profit factor, drawdown, and net PnL from trades DataFrame."""
    if df.empty:
        return {"total_trades": 0, "win_rate": 0.0, "net_profit": 0.0, "max_drawdown": 0.0, "profit_factor": 0.0}

    total_trades = len(df)
    wins = len(df[df['Outcome'] == 'WIN'])
    losses = len(df[df['Outcome'] == 'LOSS'])
    bes = len(df[df['Outcome'] == 'BE'])

    total_resolved = wins + losses
    win_rate = (wins / total_resolved * 100.0) if total_resolved > 0 else 0.0
    net_profit = float(df['Net Profit'].sum())

    gross_profit = float(df[df['Net Profit'] > 0]['Net Profit'].sum())
    gross_loss = abs(float(df[df['Net Profit'] < 0]['Net Profit'].sum()))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

    # Calculate Drawdown
    df_sorted = df.sort_values('Close Time')
    cumulative_profit = df_sorted['Net Profit'].cumsum()
    running_max = cumulative_profit.cummax()
    drawdown = running_max - cumulative_profit
    max_drawdown = float(drawdown.max())

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "bes": bes,
        "win_rate": round(win_rate, 2),
        "net_profit": round(net_profit, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_drawdown, 2)
    }


def export_trade_ledger(magic_number: int = 888999) -> bool:
    """
    Exports monthly CSV trade ledger and text summary reports for the specified magic number.
    Returns True if ledger was successfully created/updated.
    """
    if not MT5_AVAILABLE or not mt5.terminal_info():
        return False

    time_from = datetime(2020, 1, 1)
    time_to = datetime.now() + timedelta(days=1)
    deals = mt5.history_deals_get(time_from, time_to)
    
    if deals is None or len(deals) == 0:
        return False

    positions: Dict[int, Dict[str, Any]] = {}
    for d in deals:
        pid = d.position_id
        if pid == 0:
            continue  # Deposit/withdrawal

        if pid not in positions:
            positions[pid] = {
                'magic': d.magic, 
                'symbol': d.symbol, 
                'profit': 0.0, 
                'commission': 0.0, 
                'swap': 0.0, 
                'fee': 0.0,
                'volume': 0.0,
                'reason': ''
            }
            
        positions[pid]['profit'] += d.profit
        positions[pid]['commission'] += d.commission
        positions[pid]['swap'] += d.swap
        positions[pid]['fee'] += d.fee
        
        if d.entry == mt5.DEAL_ENTRY_IN:
            positions[pid]['open_time'] = datetime.fromtimestamp(d.time)
            positions[pid]['type'] = 'BUY' if d.type == mt5.DEAL_TYPE_BUY else 'SELL'
            positions[pid]['entry_price'] = d.price
            positions[pid]['volume'] = d.volume
            positions[pid]['magic'] = d.magic 
            positions[pid]['reason'] = d.comment if d.comment else "Meridian Stat-Arb"
            
        elif d.entry == mt5.DEAL_ENTRY_OUT:
            positions[pid]['close_time'] = datetime.fromtimestamp(d.time)
            positions[pid]['exit_price'] = d.price

    records = []
    for pid, p in positions.items():
        if p.get('magic') != magic_number:
            continue
        if 'close_time' not in p:
            continue  # Still open

        net_profit = p['profit'] + p['commission'] + p['swap'] + p['fee']
        
        outcome = "BE"
        if net_profit > 0.50:
            outcome = "WIN"
        elif net_profit < -0.50:
            outcome = "LOSS"
        
        records.append({
            'Ticket': pid,
            'Symbol': p['symbol'],
            'Type': p.get('type', 'UNKNOWN'),
            'Volume': p.get('volume', 0.0),
            'Open Time': p.get('open_time'),
            'Close Time': p.get('close_time'),
            'Entry Price': round(p.get('entry_price', 0.0), 5),
            'Exit Price': round(p.get('exit_price', 0.0), 5),
            'Gross Profit': round(p['profit'], 2),
            'Fees': round(p['commission'] + p['swap'] + p['fee'], 2),
            'Net Profit': round(net_profit, 2),
            'Outcome': outcome,
            'Reason': p.get('reason', 'Meridian Stat-Arb')
        })

    if not records:
        return False

    df_all = pd.DataFrame(records)
    df_all['Close Time'] = pd.to_datetime(df_all['Close Time'])
    df_all['MonthKey'] = df_all['Close Time'].dt.strftime('%b_%Y')

    os.makedirs(REPORTS_DIR, exist_ok=True)
    
    account_info = mt5.account_info()
    acc_num = account_info.login if account_info else "000000"

    for month_key, df_month in df_all.groupby('MonthKey'):
        # Export CSV Ledger
        csv_path = os.path.join(REPORTS_DIR, f"meridian_ledger_{acc_num}_{month_key}.csv")
        df_export = df_month.drop(columns=['MonthKey']).sort_values('Close Time', ascending=False)
        try:
            df_export.to_csv(csv_path, index=False)
        except PermissionError:
            pass

        # Export Analytics Summary Text Report
        stats = calculate_analytics(df_month)
        report_path = os.path.join(REPORTS_DIR, f"meridian_report_{acc_num}_{month_key}.txt")
        
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write("=========================================\n")
                f.write(f"MERIDIAN STAT-ARB QUANT LEDGER : {month_key}\n")
                f.write("=========================================\n\n")
                f.write(f"Account ID          : #{acc_num}\n")
                f.write(f"Total Trades Taken  : {stats['total_trades']}\n")
                f.write(f"Winning Trades      : {stats['wins']}\n")
                f.write(f"Losing Trades       : {stats['losses']}\n")
                f.write(f"Break-Even/Scratch  : {stats['bes']}\n")
                f.write("-----------------------------------------\n")
                f.write(f"WIN RATE            : {stats['win_rate']}%\n")
                f.write(f"PROFIT FACTOR       : {stats['profit_factor']}\n")
                f.write(f"MAXIMUM DRAWDOWN    : ${stats['max_drawdown']:,.2f}\n")
                f.write(f"NET PROFIT          : ${stats['net_profit']:,.2f}\n")
                f.write("=========================================\n")
        except PermissionError:
            pass

    return True
