# executor.py
import os
import math
import time
from decimal import Decimal, ROUND_DOWN
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException

class Executor:
    def __init__(self, api_key, api_secret, testnet=True, tp_pct=0.03, max_slices=10):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.tp_pct = float(tp_pct)
        self.max_slices = int(max_slices)
        self.leverage = 5  # enforced by strategy

        # Create client (api_key may be empty for read-only)
        self.client = Client(self.api_key, self.api_secret)
        if self.testnet:
            # python-binance uses this override for futures testnet
            # Note: Testnet base URL sometimes differs; this is standard.
            self.client.API_URL = "https://testnet.binancefuture.com"

    # --- Helpers ---
    def _get_usdt_balance(self):
        """Return available USDT balance in futures wallet. Fallback to 1000 if fail."""
        try:
            bal = self.client.futures_account_balance()
            for a in bal:
                if a.get("asset") == "USDT":
                    # prefer 'availableBalance' if present, else 'balance'
                    available = a.get("availableBalance") or a.get("balance") or "0"
                    return float(available)
        except Exception as e:
            print("Warning: failed to read futures balance:", e)
        return 1000.0

    def _get_symbol_info(self, symbol):
        info = self.client.futures_exchange_info()
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                return s
        return None

    def _qty_from_usdt(self, symbol, usdt_amount, leverage, price):
        """
        Convert usdt_amount margin -> contracts qty.
        We'll assume desired notional = usdt_amount * leverage
        qty = notional / price
        Then round down to symbol's stepSize.
        """
        if price <= 0:
            return 0
        notional = float(usdt_amount) * float(leverage)
        raw_qty = notional / float(price)

        # Fetch stepSize filter
        info = self._get_symbol_info(symbol)
        qty_step = 1.0
        if info:
            for f in info.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    step = float(f.get("stepSize", 1.0))
                    qty_step = step
                    break

        # Round down to nearest step
        precision = int(round(-math.log10(qty_step))) if qty_step < 1 else 0
        # Use Decimal quantize for safe rounding down
        q = Decimal(str(raw_qty)).quantize(Decimal(str(qty_step)), rounding=ROUND_DOWN)
        # If quantize with step like 0.001, ensure format
        try:
            qty_out = float(q)
        except Exception:
            qty_out = float(math.floor(raw_qty / qty_step) * qty_step)
        if qty_out <= 0:
            return 0
        return qty_out

    # --- Public API ---
    def open_trade(self, symbol, direction, entry_price_est, current_open_count=0):
        """
        Open a trade on the futures testnet:
        - set margin type to ISOLATED
        - set leverage to self.leverage
        - compute slice using available USDT / remaining slots
        - place MARKET order for qty derived from slice_amount * leverage / price
        - create a reduceOnly LIMIT TP order at tp_price
        Returns dict with ok, fill_price, qty, tp_price, usdt_slice
        """
        # 1) Set margin type to ISOLATED (best-effort)
        try:
            try:
                self.client.futures_change_margin_type(symbol=symbol, marginType="ISOLATED")
            except Exception as e:
                # not fatal - some symbols may already be isolated or restricted
                print("Note: margin type change:", e)

            # 2) Set leverage
            self.client.futures_change_leverage(symbol=symbol, leverage=self.leverage)
        except Exception as e:
            print("Failed to set leverage/margin:", e)
            return {"ok": False, "error": str(e)}

        # 3) Compute slice amount
        usdt_balance = self._get_usdt_balance()
        remaining_slots = max(1, self.max_slices - int(current_open_count))
        slice_amount = float(usdt_balance) / float(remaining_slots)

        # Safety: cap slice to a max (so one trade doesn't use entire wallet in odd cases)
        max_slice_allowed = float(usdt_balance) * 0.5  # don't use more than 50% per slice by default
        if slice_amount > max_slice_allowed:
            slice_amount = max_slice_allowed

        # 4) Compute qty using entry_price_est (but we'll fetch current price to be safer)
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker.get("price", entry_price_est))
        except Exception:
            current_price = float(entry_price_est)

        qty = self._qty_from_usdt(symbol, slice_amount, self.leverage, current_price)
        if qty <= 0:
            return {"ok": False, "error": "computed qty <= 0"}

        side = "BUY" if direction == "LONG" else "SELL"

        # 5) Place MARKET order
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty
            )
            # extract fill price from fills if present
            fill_price = None
            if "avgPrice" in order and float(order["avgPrice"])>0:
                fill_price = float(order["avgPrice"])
            else:
                # fallback to current_price
                fill_price = current_price
        except BinanceAPIException as e:
            print("BinanceAPIException placing market order:", e)
            return {"ok": False, "error": str(e)}
        except Exception as e:
            print("Error placing market order:", e)
            return {"ok": False, "error": str(e)}

        # 6) Place TP limit reduceOnly order
        try:
            if side == "BUY":
                tp_price = round(fill_price * (1 + self.tp_pct), 8)
                tp_side = "SELL"
            else:
                tp_price = round(fill_price * (1 - self.tp_pct), 8)
                tp_side = "BUY"

            # create reduceOnly limit order (GTC)
            tp_order = self.client.futures_create_order(
                symbol=symbol,
                side=tp_side,
                type="LIMIT",
                timeInForce="GTC",
                price=str(tp_price),
                quantity=qty,
                reduceOnly=True
            )
        except Exception as e:
            print("Warning: failed to place TP order:", e)
            # still return success for the market fill but note missing TP
            return {"ok": True, "fill_price": fill_price, "qty": qty, "tp_price": None, "slice_amount_usdt": slice_amount}

        return {"ok": True, "fill_price": fill_price, "qty": qty, "tp_price": float(tp_price), "slice_amount_usdt": slice_amount}