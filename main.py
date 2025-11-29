# main.py
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
VOLUME_MULTIPLIER = int(os.getenv("VOLUME_MULTIPLIER", "18"))
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
        # choose perpetual USDT-margined contracts
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
        # Could be symbol delisted or rate-limited
        # print(f"klines_error {symbol}: {e}")
        return None

def main():
    print("Starting run. TESTNET =", TESTNET)
    if not server_time_ok_for_run():
        print("Not the candle boundary. Exiting.")
        return

    state = load_state()
    open_trades = state.get("open_trades", [])

    execer = Executor(api_key=API_KEY, api_secret=API_SECRET, testnet=TESTNET, tp_pct=TP_PCT, max_slices=MAX_SLICES)

    symbols = get_usdtm_symbols()
    print(f"Found {len(symbols)} USDT-M perpetual symbols. Scanning...")

    new_opened = []
    for sym in symbols:
        # quick guard to reduce API pressure in initial tests:
        try:
            klines = fetch_last_3_klines(sym)
            if not klines:
                continue
            signal = evaluate_symbol_for_signal(klines, volume_multiplier=VOLUME_MULTIPLIER)
            if not signal:
                continue

            # check slots remaining
            if len(open_trades) + len(new_opened) >= MAX_SLICES:
                # no slots left
                continue

            # attempt to open (pass current open count so executor can size)
            result = execer.open_trade(symbol=sym,
                                       direction=signal["direction"],
                                       entry_price_est=signal["entry_price_est"],
                                       current_open_count=len(open_trades) + len(new_opened))
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
        print("No new trades this run.")

if __name__ == "__main__":
    main()