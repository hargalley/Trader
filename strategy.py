# strategy.py
from datetime import datetime, timezone

# Constants (defaults can be overridden from main if you want)
VOLUME_MULTIPLIER_DEFAULT = 15  # changed from 18
C1_DOLLAR_MIN = 5555.0  # your new rule

def evaluate_symbol_for_signal(klines, volume_multiplier=VOLUME_MULTIPLIER_DEFAULT, c1_dollar_min=C1_DOLLAR_MIN):
    """
    klines: list of 3 kline dicts oldest->newest: C1, C2, C3
    returns None or dict with keys:
      direction: "LONG"/"SHORT"
      entry_price_est: float (C3.open)
      entry_timestamp: ISO string of C2.open_time (signal time)
    """

    if len(klines) < 3:
        return None

    C1, C2, C3 = klines

    # --- C1 dollar-volume minimum check ---
    try:
        c1_dollar = float(C1["volume"]) * float(C1["open"])
    except Exception:
        return None

    if c1_dollar < float(c1_dollar_min):
        return None

    # --- Volume explosion check ---
    if C1["volume"] <= 0 or C2["volume"] < C1["volume"] * volume_multiplier:
        return None

    if C1["open"] <= 0:
        return None

    # --- Price move logic ---
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
        "c1_dollar": c1_dollar,
        "up_move": up_move,
        "down_move": down_move,
        "c1_volume": C1["volume"],
        "c2_volume": C2["volume"]
    }
