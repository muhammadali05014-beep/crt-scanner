"""
CRT Scanner — GitHub Actions Version
=====================================
Runs automatically through GitHub Actions.

- Checks 4H and 1D candles
- Only scans during the first 20 minutes after a 4H candle close
- Forex pairs: Monday-Friday
- BTC/USDT: Monday-Sunday
- Sends CRT alerts to Telegram
- Sends one "BOT IS ACTIVE" message per UTC day
- Stores state in crt_state.json
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
# === TIME GATEKEEPER ===
# ============================================================

def is_within_candle_window(window_minutes: int = 20) -> bool:
    """
    Checks if current UTC time is within the first 20 minutes
    after a 4H candle close.

    4H candle closes:
    00:00 UTC
    04:00 UTC
    08:00 UTC
    12:00 UTC
    16:00 UTC
    20:00 UTC
    """

    now = datetime.now(timezone.utc)

    is_candle_hour = (now.hour % 4 == 0)
    is_in_window = is_candle_hour and (now.minute < window_minutes)

    return is_in_window


# ============================================================
# === CONFIG ===
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

TIMEFRAMES = [
    "4h",
    "1day",
]

MARGIN_RATIO = 0.5

STATE_FILE = "crt_state.json"


# ============================================================
# === CANDLE DATA STRUCTURE ===
# ============================================================

@dataclass
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float


# ============================================================
# === CRT SIGNAL LOGIC ===
# ============================================================

def crt_signal(
    prev: Candle,
    curr: Candle,
    margin_ratio: float = 0.5
) -> Optional[str]:

    ref_high = prev.high
    ref_low = prev.low

    threshold = (
        ref_low
        + (ref_high - ref_low) * margin_ratio
    )

    # Both sides swept
    dual_wick = (
        curr.high > ref_high
        and curr.low < ref_low
    )

    if dual_wick:
        return None

    # Bearish CRT
    if (
        curr.high > ref_high
        and curr.close <= ref_high
        and curr.close > threshold
    ):
        return "bearish"

    # Bullish CRT
    if (
        curr.low < ref_low
        and curr.close >= ref_low
        and curr.close < threshold
    ):
        return "bullish"

    return None


# ============================================================
# === DATA FETCHING — TWELVE DATA ===
# ============================================================

def fetch_last_two_candles(
    symbol: str,
    interval: str
) -> Optional[tuple[Candle, Candle]]:

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": 2,
        "apikey": TWELVE_DATA_API_KEY,
        "order": "ASC",
        "timezone": "UTC",
    }

    try:

        resp = requests.get(
            url,
            params=params,
            timeout=15
        )

        data = resp.json()

        if "values" not in data or len(data["values"]) < 2:

            print(
                f"[WARN] Not enough data for "
                f"{symbol} ({interval}): {data}"
            )

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

        print(
            f"[ERROR] Fetching "
            f"{symbol} ({interval}): {e}"
        )

        return None


# ============================================================
# === TELEGRAM CRT ALERT ===
# ============================================================

def send_telegram_alert(
    pair: str,
    timeframe: str,
    signal: str,
    curr: Candle
):

    emoji = "🔴" if signal == "bearish" else "🟢"

    text = (
        f"{emoji} *{signal.upper()} CRT* — "
        f"{pair} ({timeframe})\n"
        f"Candle time: {curr.time}\n"
        f"Close: {curr.close}\n"
        f"High: {curr.high}  "
        f"Low: {curr.low}"
    )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:

        response = requests.post(
            url,
            data=payload,
            timeout=10
        )

        print(
            f"[TELEGRAM ALERT] "
            f"{response.status_code}"
        )

        print(
            f"[TELEGRAM RESPONSE] "
            f"{response.text}"
        )

        if response.ok:
            print(
                f"[ALERT SENT] "
                f"{pair} {timeframe} -> {signal}"
            )
        else:
            print(
                "[ERROR] Telegram rejected alert."
            )

    except Exception as e:

        print(
            f"[ERROR] Sending Telegram alert: {e}"
        )


# ============================================================
# === DAILY BOT HEARTBEAT ===
# ============================================================

def send_daily_heartbeat(state: dict):

    today = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")

    # Already sent today
    if state.get("last_heartbeat") == today:

        print(
            "[INFO] Daily heartbeat already sent today."
        )

        return

    text = (
        "✅ *CRT Scanner Bot is ACTIVE*\n"
        f"Date: {today}\n"
        "The scanner is running normally."
    )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:

        response = requests.post(
            url,
            data=payload,
            timeout=10
        )

        print(
            f"[HEARTBEAT] "
            f"{response.status_code}"
        )

        print(
            f"[HEARTBEAT RESPONSE] "
            f"{response.text}"
        )

        if response.ok:

            state["last_heartbeat"] = today

            print(
                "✅ Daily heartbeat sent successfully."
            )

        else:

            print(
                "❌ Daily heartbeat failed."
            )

    except Exception as e:

        print(
            f"[ERROR] Sending daily heartbeat: {e}"
        )


# ============================================================
# === STATE MANAGEMENT ===
# ============================================================

def load_state() -> dict:

    if os.path.exists(STATE_FILE):

        try:

            with open(
                STATE_FILE,
                "r"
            ) as f:

                return json.load(f)

        except Exception as e:

            print(
                f"[WARN] Could not read state file: {e}"
            )

            return {}

    return {}


def save_state(state: dict):

    with open(
        STATE_FILE,
        "w"
    ) as f:

        json.dump(
            state,
            f,
            indent=2
        )


# ============================================================
# === RUN ONE FULL SCAN ===
# ============================================================

def run_scan():

    state = load_state()

    now = datetime.now(timezone.utc)

    # Weekend:
    # Scan BTC only.
    if now.weekday() >= 5:

        pairs_to_scan = [
            "BTC/USDT"
        ]

        print(
            "[INFO] Weekend detected. "
            "Scanning BTC/USDT only."
        )

    # Weekday:
    # Scan Forex + BTC.
    else:

        pairs_to_scan = PAIRS

        print(
            "[INFO] Weekday detected. "
            "Scanning Forex + BTC."
        )

    for pair in pairs_to_scan:

        for tf in TIMEFRAMES:

            print(
                f"[SCAN] {pair} — {tf}"
            )

            result = fetch_last_two_candles(
                pair,
                tf
            )

            if result is not None:

                prev, curr = result

                key = f"{pair}_{tf}"

                last_seen_time = state.get(
                    key
                )

                print(
                    f"[CANDLE] {pair} {tf} "
                    f"Current: {curr.time}"
                )

                # Only process a candle once
                if curr.time != last_seen_time:

                    signal = crt_signal(
                        prev,
                        curr,
                        MARGIN_RATIO
                    )

                    if signal:

                        print(
                            f"[CRT FOUND] "
                            f"{pair} {tf} -> {signal}"
                        )

                        send_telegram_alert(
                            pair,
                            tf,
                            signal,
                            curr
                        )

                    else:

                        print(
                            f"[NO CRT] "
                            f"{pair} {tf}"
                        )

                    state[key] = curr.time

                else:

                    print(
                        f"[SKIP] "
                        f"{pair} {tf} already processed."
                    )

            # Keep Twelve Data requests spaced out
            time.sleep(8)

    save_state(state)

    print("================================")
    print("Scan complete.")
    print("================================")


# ============================================================
# === MAIN ===
# ============================================================

if __name__ == "__main__":

    print("================================")
    print("CRT SCANNER STARTING")
    print("================================")

    # Load state
    state = load_state()

    # --------------------------------------------------------
    # DAILY HEARTBEAT
    # --------------------------------------------------------

    send_daily_heartbeat(state)

    # Save heartbeat state immediately
    save_state(state)

    # --------------------------------------------------------
    # TIME GATEKEEPER
    # --------------------------------------------------------

    if not is_within_candle_window(
        window_minutes=20
    ):

        print(
            "[INFO] Outside 20-minute "
            "post-candle window."
        )

        print(
            "[INFO] No Twelve Data API calls "
            "will be made."
        )

        sys.exit(0)

    # --------------------------------------------------------
    # RUN SCAN
    # --------------------------------------------------------

    print(
        "[INFO] Inside 20-minute window!"
    )

    print(
        "[INFO] Proceeding with API scan..."
    )

    run_scan()
