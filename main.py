# main.py (edited for 3-min loop + debug analysis)
import os
import json
import time
from datetime import datetime, timezone
from binance.client import Client
from strategy import evaluate_symbol_for_signal
from executor import Executor

# --- CONFIG from env ---
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_SECRET_KEY", "")
TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() in ("1", "true", "yes")
MAX_SLICES = int(os.getenv("MAX_SLICES", "10"))
TP_PCT = float(os.getenv("TP_PCT", "0.03"))
STATE_FILE = "state.json"

# create client for server-time and public calls
client = Client(API_KEY, API_SECRET)
if TESTNET:
    client.API_URL = "https://testnet.binancefuture.com"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {"open_trades": []}
    return {"open_trades": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, default=str)

def server_time_ok_for_run():
    """Return True if server minute % 3 == 0 and server second <= 5"""
    try:
        t = client.futures_time()
        server_ms = int(t.get("serverTime"))
        dt = datetime.fromtimestamp(server_ms/1000.0, tz=timezone.utc)
        minute = dt.minute
        second = dt.second
        print(f"Server time: {dt.isoformat()} (m={minute}, s={second})")
        return (minute % 3 == 0) and (second <= 5)
    except Exception as e:
        print("Failed to get server time:", e)
        return False

def get_usdtm_symbols():
    info = client.futures_exchange_info()
    syms = []
    for s in info.get("symbols", []):
        if s.get("contractType") == "PERPETUAL" and s.get("symbol", "").endswith("USDT"):
            syms.append(s["symbol"])
    return syms

def fetch_last_3_klines(symbol):
    try:
        k = client.futures_klines(symbol=symbol, interval="3m", limit=3)
        if not k or len(k) < 3:
            return None
        out = []
        for row in k:
            out.append({
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5])
            })
        return out
    except Exception as e:
        print(f"Failed to fetch klines for {symbol}:", e)
        return None

def show_account_activity():
    """Print balances and open orders"""
    print("\n=== ACCOUNT BALANCES ===")
    try:
        account_info = client.get_account()
        for balance in account_info['balances']:
            free = float(balance['free'])
            locked = float(balance['locked'])
            if free > 0 or locked > 0:
                print(f"{balance['asset']}: Free={free}, Locked={locked}")
    except Exception as e:
        print("Failed to fetch balances:", e)

    print("\n=== OPEN ORDERS ===")
    try:
        open_orders = client.get_open_orders()
        if open_orders:
            for order in open_orders:
                print(order)
        else:
            print("No open orders.")
    except Exception as e:
        print("Failed to fetch open orders:", e)

def run_bot_iteration():
    """Single bot run iteration (logic from your original main)"""
    print("Starting bot iteration. TESTNET =", TESTNET)
    if not server_time_ok_for_run():
        print("Not the candle boundary. Skipping this run.")
        return

    state = load_state()
    open_trades = state.get("open_trades", [])

    execer = Executor(api_key=API_KEY, api_secret=API_SECRET, testnet=TESTNET, tp_pct=TP_PCT, max_slices=MAX_SLICES)

    symbols = get_usdtm_symbols()
    print(f"Found {len(symbols)} USDT-M perpetual symbols. Scanning...")

    new_opened = []
    for sym in symbols:
        try:
            klines = fetch_last_3_klines(sym)
            if not klines:
                continue

            # --- DEBUG ANALYSIS: print candle properties ---
            c1, c2, c3 = klines
            print("\n------------------------------")
            print(f"DEBUG {sym}")
            print("------------------------------")
            print(f"C1 -> time:{c1['open_time']} O:{c1['open']} H:{c1['high']} L:{c1['low']} C:{c1['close']} V:{c1['volume']}")
            print(f"C2 -> time:{c2['open_time']} O:{c2['open']} H:{c2['high']} L:{c2['low']} C:{c2['close']} V:{c2['volume']}")
            print(f"C3 -> time:{c3['open_time']} O:{c3['open']} (in-progress)")

            # --- send to strategy (volume_multiplier x15 and price-change logic will be handled in strategy.py) ---
            signal = evaluate_symbol_for_signal(klines, volume_multiplier=15)
            if not signal:
                continue

            # all-in logic: no slices
            result = execer.open_trade(symbol=sym,
                                       direction=signal["direction"],
                                       entry_price_est=signal["entry_price_est"],
                                       current_open_count=0)
            if result.get("ok"):
                entry = {
                    "symbol": sym,
                    "direction": signal["direction"],
                    "entry_time": signal["entry_timestamp"],
                    "entry_price": result.get("fill_price", signal["entry_price_est"]),
                    "qty": result.get("qty"),
                    "tp_price": result.get("tp_price"),
                    "slice_amount_usdt": result.get("slice_amount_usdt")
                }
                new_opened.append(entry)
                print(f"Opened {sym} {signal['direction']} fill={entry['entry_price']} qty={entry['qty']} tp={entry['tp_price']}")
        except Exception as e:
            print("Error scanning", sym, e)

    if new_opened:
        open_trades.extend(new_opened)
        state["open_trades"] = open_trades
        save_state(state)
        print(f"Saved {len(open_trades)} open trades.")
    else:
        print("No new trades this iteration.")

if __name__ == "__main__":
    while True:
        run_bot_iteration()
        show_account_activity()
        print("\n--- Waiting 3 minutes before next iteration ---\n")
        time.sleep(180)  # 3 minutes
