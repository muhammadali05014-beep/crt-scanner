"""
CRT Scanner — GitHub Actions Version
=====================================

- Checks 4H and 1D candles
- 4H scan only during first 20 minutes after UTC 4H boundary
- Forex: Monday-Friday
- BTC/USDT: Monday-Sunday
- Twelve Data explicitly requested in UTC
- One-time candle processing using crt_state.json
- Sends one startup notification per UTC day
- Sends CRT alerts to Telegram
"""

import sys
import time
import json
import os
import requests

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional


# ============================================================
# === CONFIGURATION ==========================================
# ============================================================

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "crt_state.json"

TIMEFRAMES = ["4h", "1day"]

FOREX_SYMBOLS = [
    "GBP/USD",
    "EUR/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
]

CRYPTO_SYMBOLS = [
    "BTC/USD",
]


# ============================================================
# === DATA STRUCTURE =========================================
# ============================================================

@dataclass
class Candle:
    datetime: str
    open: float
    high: float
    low: float
    close: float


# ============================================================
# === STATE ==================================================
# ============================================================

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}

    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================
# === TIME GATEKEEPER ========================================
# ============================================================

def is_within_candle_window(window_minutes: int = 20) -> bool:
    """
    Checks if current UTC time is within the first 20 minutes
    after a 4H candle boundary.

    4H boundaries:
    00:00 UTC
    04:00 UTC
    08:00 UTC
    12:00 UTC
    16:00 UTC
    20:00 UTC
    """

    now = datetime.now(timezone.utc)

    minutes_since_boundary = (
        (now.hour % 4) * 60
        + now.minute
    )

    return minutes_since_boundary < window_minutes


# ============================================================
# === TELEGRAM ===============================================
# ============================================================

def send_telegram_message(text: str):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    response = requests.post(
        url,
        data=payload,
        timeout=10
    )

    print(f"[TELEGRAM] Status: {response.status_code}")
    print(f"[TELEGRAM] Response: {response.text}")

    return response.ok


# ============================================================
# === STARTUP NOTIFICATION ===================================
# ============================================================

def send_startup_notification(state: dict):
    """
    Sends one startup notification per UTC day.
    """

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if state.get("last_startup_notification") == today:
        print("[INFO] Startup notification already sent today.")
        return

    text = (
        "🟢 *CRT Scanner Bot STARTED*\n\n"
        f"UTC Date: {today}\n"
        "GitHub Actions has started the scanner successfully."
    )

    if send_telegram_message(text):
        state["last_startup_notification"] = today
        print("[INFO] Startup notification sent successfully.")
    else:
        print("[ERROR] Startup notification failed.")


# ============================================================
# === TWELVE DATA ============================================
# ============================================================

def get_candles(
    symbol: str,
    interval: str,
    outputsize: int = 10
) -> list[Candle]:

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "timezone": "UTC",
        "order": "ASC",
    }

    print(
        f"[API] Requesting {symbol} "
        f"{interval} candles in UTC..."
    )

    response = requests.get(
        url,
        params=params,
        timeout=20
    )

    data = response.json()

    if "values" not in data:
        print(
            f"[ERROR] Twelve Data response for "
            f"{symbol} {interval}: {data}"
        )
        return []

    candles = []

    for item in data["values"]:
        candles.append(
            Candle(
                datetime=item["datetime"],
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
            )
        )

    return candles


# ============================================================
# === CRT DETECTION ==========================================
# ============================================================

def detect_crt(
    candles: list[Candle]
) -> Optional[str]:

    if len(candles) < 3:
        return None

    first = candles[-3]
    second = candles[-2]

    # Bullish CRT
    if (
        second.low < first.low
        and second.close > first.low
        and second.close < first.high
    ):
        return "BULLISH"

    # Bearish CRT
    if (
        second.high > first.high
        and second.close < first.high
        and second.close > first.low
    ):
        return "BEARISH"

    return None


# ============================================================
# === SYMBOL FILTER ==========================================
# ============================================================

def get_symbols_for_today() -> list[str]:
    """
    Forex only Monday-Friday.
    BTC/USD every day.
    """

    weekday = datetime.now(timezone.utc).weekday()

    symbols = list(CRYPTO_SYMBOLS)

    # Monday = 0
    # Friday = 4
    if weekday <= 4:
        symbols.extend(FOREX_SYMBOLS)

    return symbols


# ============================================================
# === SCAN ===================================================
# ============================================================

def scan_symbol(
    symbol: str,
    timeframe: str,
    state: dict
):

    candles = get_candles(
        symbol=symbol,
        interval=timeframe,
        outputsize=10
    )

    if not candles:
        return

    signal = detect_crt(candles)

    if signal is None:
        print(
            f"[INFO] No CRT detected: "
            f"{symbol} {timeframe}"
        )
        return

    signal_candle = candles[-2]

    state_key = (
        f"{symbol}_{timeframe}_"
        f"{signal_candle.datetime}"
    )

    if state.get("processed", {}).get(state_key):
        print(
            f"[INFO] Already processed: "
            f"{symbol} {timeframe} "
            f"{signal_candle.datetime}"
        )
        return

    text = (
        f"🚨 *CRT SIGNAL*\n\n"
        f"*Symbol:* {symbol}\n"
        f"*Timeframe:* {timeframe}\n"
        f"*Signal:* {signal}\n"
        f"*Candle:* {signal_candle.datetime} UTC\n"
        f"*Open:* {signal_candle.open}\n"
        f"*High:* {signal_candle.high}\n"
        f"*Low:* {signal_candle.low}\n"
        f"*Close:* {signal_candle.close}"
    )

    if send_telegram_message(text):

        if "processed" not in state:
            state["processed"] = {}

        state["processed"][state_key] = True

        print(
            f"[ALERT] {signal} CRT sent for "
            f"{symbol} {timeframe}"
        )


# ============================================================
# === MAIN ===================================================
# ============================================================

def main():

    print("=" * 60)
    print("CRT SCANNER STARTING")
    print("=" * 60)

    print(
        f"[INFO] Current UTC time: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    state = load_state()

    # --------------------------------------------------------
    # STARTUP NOTIFICATION
    # --------------------------------------------------------

    send_startup_notification(state)

    # Save immediately so the notification state survives
    # even if the scanner exits afterward.
    save_state(state)

    # --------------------------------------------------------
    # TIME WINDOW
    # --------------------------------------------------------

    if not is_within_candle_window(20):

        print(
            "[INFO] Outside 20-minute "
            "post-candle window."
        )

        print(
            "[INFO] No Twelve Data API calls will be made."
        )

        save_state(state)

        return

    print(
        "[INFO] Inside 20-minute "
        "post-candle window."
    )

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    symbols = get_symbols_for_today()

    print(
        f"[INFO] Symbols to scan: {', '.join(symbols)}"
    )

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    for timeframe in TIMEFRAMES:

        print(
            f"\n[INFO] Scanning timeframe: {timeframe}"
        )

        for symbol in symbols:

            print(
                f"[INFO] Scanning {symbol} "
                f"{timeframe}"
            )

            scan_symbol(
                symbol=symbol,
                timeframe=timeframe,
                state=state
            )

            save_state(state)

    print("\n" + "=" * 60)
    print("CRT SCANNER FINISHED")
    print("=" * 60)


# ============================================================
# === ENTRY POINT ============================================
# ============================================================

if __name__ == "__main__":
    main()
