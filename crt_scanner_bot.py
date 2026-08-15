"""
CRT Scanner — GitHub Actions Version (runs once per invocation)
==================================================================
Same CRT logic as before. This version is designed to be triggered on a
schedule by GitHub Actions (cron), rather than running a persistent loop
on your own machine.

Reads API keys / tokens from environment variables (set as GitHub Secrets),
not hardcoded in this file.
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
# === TIME GATEKEEPER — Prevents unnecessary API calls ===
# ============================================================
def is_within_candle_window(window_minutes: int = 20) -> bool:
    """
    Checks if current UTC time is within `window_minutes` 
    after a 4H or 1D candle close (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC).
    """
    now = datetime.now(timezone.utc)
    
    # 4H boundaries occur at hours 0, 4, 8, 12, 16, 20 UTC
    is_candle_hour = (now.hour % 4 == 0)
    is_in_window = is_candle_hour and (now.minute < window_minutes)
    
    return is_in_window


# ============================================================
# === CONFIG — pulled from environment variables (GitHub Secrets) ===
# ============================================================
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "BTC/USDT",
]

TIMEFRAMES = ["4h", "1day"]
MARGIN_RATIO = 0.5

STATE_FILE = "crt_state.json"

# ============================================================
# === CRT Signal Logic (unchanged) ===
# ============================================================
@dataclass
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float


def crt_signal(prev: Candle, curr: Candle, margin_ratio: float = 0.5) -> Optional[str]:
    ref_high = prev.high
    ref_low = prev.low
    threshold = ref_low + (ref_high - ref_low) * margin_ratio

    dual_wick = curr.high > ref_high and curr.low < ref_low
    if dual_wick:
        return None

    if curr.high > ref_high and curr.close <= ref_high and curr.close > threshold:
        return "bearish"

    if curr.low < ref_low and curr.close >= ref_low and curr.close < threshold:
        return "bullish"

    return None


# ============================================================
# === Data Fetching (Twelve Data) ===
# ============================================================
def fetch_last_two_candles(symbol: str, interval: str) -> Optional[tuple[Candle, Candle]]:
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": 2,
        "apikey": TWELVE_DATA_API_KEY,
        "order": "ASC",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if "values" not in data or len(data["values"]) < 2:
            print(f"[WARN] Not enough data for {symbol} ({interval}): {data}")
            return None

        candles = [
            Candle(
                time=v["datetime"],
                open=float(v["open"]),
                high=float(v["high"]),
                low=float(v["low"]),
                close=float(v["close"]),
            )
            for v in data["values"]
        ]
        return candles[0], candles[1]
    except Exception as e:
        print(f"[ERROR] Fetching {symbol} ({interval}): {e}")
        return None


# ============================================================
# === Telegram Alert ===
# ============================================================
def send_telegram_alert(pair: str, timeframe: str, signal: str, curr: Candle):
    emoji = "🔴" if signal == "bearish" else "🟢"
    text = (
        f"{emoji} *{signal.upper()} CRT* — {pair} ({timeframe})\n"
        f"Candle time: {curr.time}\n"
        f"Close: {curr.close}\n"
        f"High: {curr.high}  Low: {curr.low}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
        print(f"[ALERT SENT] {pair} {timeframe} -> {signal}")
    except Exception as e:
        print(f"[ERROR] Sending Telegram alert: {e}")


# ============================================================
# === State Persistence ===
# ============================================================
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================
# === Run one full scan (all pairs, both timeframes) ===
# ============================================================
def run_scan():
    state = load_state()

    for pair in PAIRS:
        for tf in TIMEFRAMES:
            result = fetch_last_two_candles(pair, tf)
            if result is not None:
                prev, curr = result
                key = f"{pair}_{tf}"
                last_seen_time = state.get(key)

                if curr.time != last_seen_time:
                    signal = crt_signal(prev, curr, MARGIN_RATIO)
                    if signal:
                        send_telegram_alert(pair, tf, signal, curr)
                    state[key] = curr.time

            time.sleep(8)  # stay under Twelve Data's free-tier rate limit (8 req/min)

    save_state(state)
    print("Scan complete.")


if __name__ == "__main__":
    if not is_within_candle_window(window_minutes=20):
        print("[INFO] Outside 20-minute post-candle window. Exiting to save API calls.")
        sys.exit(0)

    print("[INFO] Inside window! Proceeding with API scan...")
    run_scan()
