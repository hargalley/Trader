# strategy.py
from datetime import datetime, timezone

# Constants (defaults can be overridden from main if you want)
VOLUME_MULTIPLIER_DEFAULT = 18
C1_DOLLAR_MIN = 5555.0  # your new rule

def evaluate_symbol_for_signal(klines, volume_multiplier=VOLUME_MULTIPLIER_DEFAULT, c1_dollar_min=C1_DOLLAR_MIN):
    """
    klines: list of 3 kline dicts oldest->newest:
      [ {open_time, open, high, low, close, volume}, ... ]
    returns None or dict with keys:
      direction: "LONG"/"SHORT"
      entry_price_est: float (C3.open)
      entry_timestamp: ISO string of C2.open_time (signal time)
    """

    if len(klines) < 3:
        return None

    C1 = klines[0]
    C2 = klines[1]
    C3 = klines[2]

    # --- NEW: C1 dollar-volume minimum check ---
    # Calculate C1.volume * C1.open and require >= threshold
    # Example: if C1.open = 0.042 and C1.volume = 180000 => 0.042 * 180000 = 7560
    try:
        c1_dollar = float(C1["volume"]) * float(C1["open"])
    except Exception:
        return None

    if c1_dollar < float(c1_dollar_min):
        # Not enough dollar-volume in C1
        return None

    # volume and data sanity
    if C1["volume"] <= 0 or C2["volume"] < C1["volume"] * volume_multiplier:
        return None

    if C1["open"] <= 0:
        return None

    up_move = (C2["high"] - C1["open"]) / C1["open"]
    down_move = (C1["open"] - C2["low"]) / C1["open"]

    direction = None
    if up_move >= 0.15:
        direction = "LONG"
    elif down_move >= 0.15:
        direction = "SHORT"

    if not direction:
        return None

    return {
        "direction": direction,
        "entry_price_est": float(C3["open"]),
        "entry_timestamp": datetime.fromtimestamp(C2["open_time"]/1000.0, tz=timezone.utc).isoformat(),
        "c1_dollar": c1_dollar
    }