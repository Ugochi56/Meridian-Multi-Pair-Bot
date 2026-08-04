"""
MetaTrader 5 (MT5) Native Bridge Connector.
Provides real-time terminal connection, broker tick/candle ingestion, live account syncing,
and automated dual-leg MT5 order execution.
"""

import time
import logging
from typing import Dict, List, Tuple, Optional, Any

try:
    import MetaTrader5 as mt5
    HAS_MT5_LIB = True
except ImportError:
    HAS_MT5_LIB = False

logger = logging.getLogger("MT5Bridge")


class MT5TerminalBridge:
    """
    Direct MetaTrader 5 (MT5) Terminal Connector.
    """
    def __init__(self, magic_number: int = 888999):
        self.magic_number = magic_number
        self.is_connected = False
        self.account_info: Dict[str, Any] = {}
        self.terminal_info: Dict[str, Any] = {}

    def connect(self, login: Optional[int] = None, password: Optional[str] = None, server: Optional[str] = None) -> Tuple[bool, str]:
        """
        Initializes connection to desktop MT5 terminal.
        """
        if not HAS_MT5_LIB:
            self.is_connected = False
            return False, "MetaTrader5 Python library not installed. Operating in Paper Mode."

        # Initialize MT5 terminal
        if login and password and server:
            init_res = mt5.initialize(login=login, password=password, server=server)
        else:
            init_res = mt5.initialize()

        if not init_res:
            err = mt5.last_error()
            self.is_connected = False
            return False, f"MT5 terminal initialization failed: {err}"

        acc = mt5.account_info()
        term = mt5.terminal_info()

        if acc is None:
            self.is_connected = False
            return False, "Failed to retrieve MT5 account info. Please log into MT5 terminal."

        self.account_info = {
            "login": acc.login,
            "trade_mode": acc.trade_mode,
            "lever": acc.leverage,
            "balance": acc.balance,
            "equity": acc.equity,
            "profit": acc.profit,
            "margin": acc.margin,
            "margin_free": acc.margin_free,
            "currency": acc.currency,
            "server": acc.server,
            "company": acc.company
        }
        
        self.terminal_info = {
            "name": term.name if term else "MT5",
            "path": term.path if term else "",
            "connected": term.connected if term else True
        }

        self.is_connected = True
        logger.info(f"MT5 Connected successfully: Account #{acc.login} on {acc.server}")
        return True, f"Connected to MT5 Account #{acc.login} ({acc.company})"

    def get_status(self) -> Dict[str, Any]:
        """Returns current MT5 connection status and account data."""
        if not self.is_connected or not HAS_MT5_LIB:
            return {
                "connected": False,
                "has_library": HAS_MT5_LIB,
                "account": None,
                "message": "MT5 Terminal Offline (Paper Trading Active)"
            }
        
        acc = mt5.account_info()
        if acc:
            self.account_info["balance"] = acc.balance
            self.account_info["equity"] = acc.equity
            self.account_info["profit"] = acc.profit
            self.account_info["margin"] = acc.margin
            self.account_info["margin_free"] = acc.margin_free

        return {
            "connected": True,
            "has_library": True,
            "account": self.account_info,
            "terminal": self.terminal_info
        }

    def resolve_symbol(self, symbol: str) -> str:
        """Auto-resolves broker-specific symbol variants (e.g., GBPUSD, GBPUSDm, GBPUSDc)."""
        if not HAS_MT5_LIB or not self.is_connected:
            return symbol

        # Check exact symbol
        if mt5.symbol_info(symbol) is not None:
            return symbol

        # Try common broker suffixes
        suffixes = ["m", "c", ".r", "_i", ".m", ".c", "v", "b", ".ex"]
        for suf in suffixes:
            variant = f"{symbol}{suf}"
            if mt5.symbol_info(variant) is not None:
                return variant

        return symbol

    def get_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetches real-time bid/ask tick for a Forex symbol from MT5."""
        if not self.is_connected or not HAS_MT5_LIB:
            return None

        sym = self.resolve_symbol(symbol)

        # Ensure symbol is selected in Market Watch
        mt5.symbol_select(sym, True)
        tick = mt5.symbol_info_tick(sym)
        info = mt5.symbol_info(sym)

        if tick is None or info is None:
            return None

        spread_pips = round((tick.ask - tick.bid) / (info.point * 10 if info.digits == 5 or info.digits == 3 else info.point), 1)
        return {
            "symbol": symbol,
            "resolved_symbol": sym,
            "price": round(tick.bid, info.digits),
            "bid": round(tick.bid, info.digits),
            "ask": round(tick.ask, info.digits),
            "spread_pips": spread_pips,
            "time": tick.time
        }

    def get_filling_mode(self, symbol: str) -> int:
        """Auto-detect supported order filling mode for broker symbol."""
        if not HAS_MT5_LIB:
            return 0
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5.ORDER_FILLING_IOC if hasattr(mt5, "ORDER_FILLING_IOC") else 0

        mode = info.filling_mode
        if hasattr(mt5, "ORDER_FILLING_IOC") and (mode & mt5.ORDER_FILLING_IOC or mode == 0):
            return mt5.ORDER_FILLING_IOC
        if hasattr(mt5, "ORDER_FILLING_FOK") and (mode & mt5.ORDER_FILLING_FOK):
            return mt5.ORDER_FILLING_FOK
        if hasattr(mt5, "ORDER_FILLING_RETURN") and (mode & mt5.ORDER_FILLING_RETURN):
            return mt5.ORDER_FILLING_RETURN
        return mt5.ORDER_FILLING_IOC if hasattr(mt5, "ORDER_FILLING_IOC") else 0

    def send_pair_order(self,
                        leg_a: str,
                        leg_b: str,
                        pos_type: str,
                        lots_a: float,
                        lots_b: float) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Submits paired dual-leg market orders to MT5 terminal.
        If LONG_SPREAD: Buy Leg A, Sell Leg B.
        If SHORT_SPREAD: Sell Leg A, Buy Leg B.
        """
        if not self.is_connected or not HAS_MT5_LIB:
            return False, "MT5 Terminal Offline. Cannot submit live orders.", {}

        sym_a = self.resolve_symbol(leg_a)
        sym_b = self.resolve_symbol(leg_b)

        mt5.symbol_select(sym_a, True)
        mt5.symbol_select(sym_b, True)

        tick_a = mt5.symbol_info_tick(sym_a)
        tick_b = mt5.symbol_info_tick(sym_b)

        if not tick_a or not tick_b:
            return False, f"Failed to fetch market ticks for {sym_a} or {sym_b}.", {}

        type_a = mt5.ORDER_TYPE_BUY if pos_type == "LONG_SPREAD" else mt5.ORDER_TYPE_SELL
        type_b = mt5.ORDER_TYPE_SELL if pos_type == "LONG_SPREAD" else mt5.ORDER_TYPE_BUY

        price_a = tick_a.ask if type_a == mt5.ORDER_TYPE_BUY else tick_a.bid
        price_b = tick_b.ask if type_b == mt5.ORDER_TYPE_BUY else tick_b.bid

        filling_a = self.get_filling_mode(sym_a)
        filling_b = self.get_filling_mode(sym_b)

        info_a = mt5.symbol_info(sym_a)
        info_b = mt5.symbol_info(sym_b)
        digits_a = info_a.digits if info_a else 5
        digits_b = info_b.digits if info_b else 5

        # Calculate hard broker Stop Loss and Take Profit (30 pips SL / 45 pips TP)
        sl_distance_a = price_a * 0.0030
        tp_distance_a = price_a * 0.0045
        sl_a = round(price_a - sl_distance_a if type_a == mt5.ORDER_TYPE_BUY else price_a + sl_distance_a, digits_a)
        tp_a = round(price_a + tp_distance_a if type_a == mt5.ORDER_TYPE_BUY else price_a - tp_distance_a, digits_a)

        sl_distance_b = price_b * 0.0030
        tp_distance_b = price_b * 0.0045
        sl_b = round(price_b - sl_distance_b if type_b == mt5.ORDER_TYPE_BUY else price_b + sl_distance_b, digits_b)
        tp_b = round(price_b + tp_distance_b if type_b == mt5.ORDER_TYPE_BUY else price_b - tp_distance_b, digits_b)

        # Leg A Request
        req_a = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym_a,
            "volume": float(lots_a),
            "type": type_a,
            "price": price_a,
            "sl": sl_a,
            "tp": tp_a,
            "deviation": 20,
            "magic": self.magic_number,
            "comment": f"Meridian_Pair_A_{pos_type}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_a,
        }

        res_a = mt5.order_send(req_a)
        if res_a is None or res_a.retcode != mt5.TRADE_RETCODE_DONE:
            err_msg = res_a.comment if res_a else "Order A execution error"
            return False, f"Leg A ({leg_a}) order failed: {err_msg}", {}

        # Leg B Request
        req_b = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym_b,
            "volume": float(lots_b),
            "type": type_b,
            "price": price_b,
            "sl": sl_b,
            "tp": tp_b,
            "deviation": 20,
            "magic": self.magic_number,
            "comment": f"Meridian_Pair_B_{pos_type}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_b,
        }

        res_b = mt5.order_send(req_b)
        if res_b is None or res_b.retcode != mt5.TRADE_RETCODE_DONE:
            # Revert Leg A if Leg B fails
            revert_type = mt5.ORDER_TYPE_SELL if type_a == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            revert_price = tick_a.bid if revert_type == mt5.ORDER_TYPE_SELL else tick_a.ask
            mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym_a,
                "volume": float(lots_a),
                "type": revert_type,
                "price": revert_price,
                "deviation": 20,
                "magic": self.magic_number,
                "comment": "Meridian_Pair_Revert",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_a,
            })
            return False, f"Leg B ({sym_b}) order failed. Reverted Leg A.", {}

        return True, "Paired dual-leg orders executed successfully on MT5.", {
            "ticket_a": res_a.order,
            "ticket_b": res_b.order,
            "price_a": res_a.price,
            "price_b": res_b.price,
            "volume_a": res_a.volume,
            "volume_b": res_b.volume
        }

    def close_all_positions(self) -> int:
        """Closes all active open positions placed by this bot on MT5."""
        if not self.is_connected or not HAS_MT5_LIB:
            return 0

        positions = mt5.positions_get(magic=self.magic_number)
        if not positions:
            return 0

        closed_count = 0
        for pos in positions:
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                continue

            order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask

            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": order_type,
                "price": price,
                "deviation": 20,
                "magic": self.magic_number,
                "comment": "Meridian_Close"
            }
            res = mt5.order_send(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                closed_count += 1

        return closed_count
