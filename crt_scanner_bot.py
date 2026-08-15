"""
=====================================================================
BTC CRT (Candle Range Theory) Scanner - Custom Rules Edition
=====================================================================

WHAT THIS SCRIPT DOES
----------------------
1. Downloads BTC-USD daily historical candles (via yfinance).
2. Builds weekly candles from the daily data.
3. Walks through the daily candles and tracks a "range" (a candle's
   high/low). Any candle that stays fully inside the current range is
   ignored (per your rule #2).
4. When a candle wicks ABOVE the range but CLOSES back inside it, and
   its upper wick is the dominant wick (upper wick >= 50% of the
   combined upper+lower wick size), it is a potential BEARISH CRT.
5. When a candle wicks BELOW the range but CLOSES back inside it, and
   its lower wick is the dominant wick, it is a potential BULLISH CRT.
6. Whenever a candle prints a new high/low wick outside the current
   range (whether it becomes a valid CRT or not), that candle's own
   high/low becomes the NEW range going forward (rule #11 - the old
   range is "used up" / invalidated and replaced).
   - If the candle's CLOSE breaks outside the range entirely (not just
     a wick), that is a straightforward breakout / invalidation, and
     again the range resets to that candle.
7. A potential CRT only becomes a CONFIRMED signal if it lines up with
   the weekly bias (rule #5):
       Bearish daily CRT  -> weekly bias must be Bearish
       Bullish daily CRT  -> weekly bias must be Bullish
8. Weekly bias (rule #6/#7/#8) is computed using ONLY fully closed
   weekly candles (never the current, still-forming week). We take the
   last two closed weekly candles:
       - if last_closed_week.Close > body_top(prev_closed_week)  -> Bullish
       - if last_closed_week.Close < body_bottom(prev_closed_week) -> Bearish
       - otherwise -> no clear bias (no signal can be confirmed)

OUTPUT
------
A CSV file (btc_crt_signals.csv by default) listing every CONFIRMED
CRT signal with full detail: the range that was swept, the candle
that made the signal, the wick ratio, and the weekly candles that
defined the aligning bias.

HOW TO RUN
----------
    pip install yfinance pandas
    python btc_crt_scanner.py

You can tweak the CONFIG block below (date range, wick threshold,
week anchor day, output filename) to suit your needs.
=====================================================================
"""

import pandas as pd
import yfinance as yf


# =====================================================================
# CONFIG - adjust these to taste
# =====================================================================
TICKER = "BTC-USD"
START_DATE = "2015-01-01"     # yfinance BTC-USD history starts ~Sep 2014
END_DATE = None               # None = up to today
WICK_RATIO_THRESHOLD = 0.5    # dominant wick must be >= 50% of (upper+lower) wick
OUTPUT_CSV = "btc_crt_signals.csv"

# Weekly candle anchor. 'W-SUN' = week runs Mon->Sun, candle "closes" Sunday.
# Since BTC trades 24/7 you may prefer 'W-MON' (closes Monday) etc. Change
# freely - the bias logic automatically respects whichever anchor you pick.
WEEK_ANCHOR = "W-SUN"


# =====================================================================
# DATA FETCHING
# =====================================================================
def fetch_daily_data(ticker: str, start: str, end):
    df = yf.download(ticker, start=start, end=end, interval="1d",
                      progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(
            "No data was downloaded. Check your internet connection, "
            "the ticker symbol, or the date range."
        )

    # yfinance sometimes returns MultiIndex columns (e.g. when a single
    # ticker is requested with newer versions) - flatten if necessary.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close"]].copy()
    df.dropna(inplace=True)

    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    return df


def build_weekly_candles(daily_df: pd.DataFrame, anchor: str = WEEK_ANCHOR) -> pd.DataFrame:
    weekly = daily_df.resample(anchor).agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    )
    weekly.dropna(inplace=True)
    # weekly.index label = the closing (last) date of that weekly period
    return weekly


# =====================================================================
# WEEKLY BIAS (rules #6, #7, #8)
# =====================================================================
def get_weekly_bias_for_date(date, weekly_df: pd.DataFrame, week_freq: str = WEEK_ANCHOR):
    """
    Determine the weekly bias applicable to a given daily 'date', using
    ONLY weekly candles that were fully closed BEFORE the week that
    contains 'date' (the current, still-forming week is never used).

    Returns (bias, info_dict). bias is 'Bullish', 'Bearish', or None
    (None means not enough closed weekly history yet, or the last
    closed week's close landed inside the previous week's body - i.e.
    no clear bias).
    """
    period = pd.Timestamp(date).to_period(week_freq)
    week_start = period.start_time

    closed_weeks = weekly_df[weekly_df.index < week_start]
    if len(closed_weeks) < 2:
        return None, {}

    prev2 = closed_weeks.iloc[-2]   # the week before the last closed week
    prev1 = closed_weeks.iloc[-1]   # the most recently closed week
    prev2_date = closed_weeks.index[-2]
    prev1_date = closed_weeks.index[-1]

    body_top = max(prev2["Open"], prev2["Close"])
    body_bottom = min(prev2["Open"], prev2["Close"])

    if prev1["Close"] > body_top:
        bias = "Bullish"
    elif prev1["Close"] < body_bottom:
        bias = "Bearish"
    else:
        bias = None

    info = {
        "Weekly Ref Week Close Date": prev1_date.date(),
        "Weekly Ref Week Close": prev1["Close"],
        "Weekly Prev Week Close Date": prev2_date.date(),
        "Weekly Prev Week Open": prev2["Open"],
        "Weekly Prev Week Close": prev2["Close"],
        "Weekly Prev Week Body Top": body_top,
        "Weekly Prev Week Body Bottom": body_bottom,
    }
    return bias, info


# =====================================================================
# CRT SCANNER (rules #1-#4, #11)
# =====================================================================
def scan_crt(daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
             wick_threshold: float = WICK_RATIO_THRESHOLD) -> pd.DataFrame:
    signals = []
    dates = daily_df.index
    n = len(daily_df)
    if n < 2:
        return pd.DataFrame()

    # Rule #1: the first candle defines the initial range (high/low of wicks)
    range_high = float(daily_df["High"].iloc[0])
    range_low = float(daily_df["Low"].iloc[0])
    range_candle_date = dates[0]

    for j in range(1, n):
        row = daily_df.iloc[j]
        date = dates[j]
        o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])

        wicks_above = h > range_high
        wicks_below = l < range_low

        # Rule #2: fully inside candle -> ignore completely, range unchanged
        if not wicks_above and not wicks_below:
            continue

        # Candle wicks on BOTH sides at once (outside range up AND down) ->
        # ambiguous / range fully broken either way. Treat as invalidation
        # and rebase the range on this candle.
        if wicks_above and wicks_below:
            range_high, range_low = h, l
            range_candle_date = date
            continue

        upper_wick = max(h - max(o, c), 0.0)
        lower_wick = max(min(o, c) - l, 0.0)
        total_wick = upper_wick + lower_wick

        if wicks_above:
            closed_inside = (c <= range_high) and (c >= range_low)

            if closed_inside:
                # Rule #3: potential bearish CRT
                ratio = (upper_wick / total_wick) if total_wick > 0 else 0.0
                if ratio >= wick_threshold:
                    bias, w_info = get_weekly_bias_for_date(date, weekly_df)
                    # Rule #5: must align with a Bearish weekly bias
                    if bias == "Bearish":
                        signals.append({
                            "Date": date.date(),
                            "Signal": "Bearish CRT",
                            "Range High (swept)": range_high,
                            "Range Low": range_low,
                            "Range Set On": range_candle_date.date(),
                            "CRT Candle Open": o,
                            "CRT Candle High": h,
                            "CRT Candle Low": l,
                            "CRT Candle Close": c,
                            "Upper Wick": round(upper_wick, 2),
                            "Lower Wick": round(lower_wick, 2),
                            "Dominant Wick Ratio": round(ratio, 3),
                            "Weekly Bias": bias,
                            **w_info,
                        })
            # Rule #11: this candle's own high/low becomes the new range,
            # whether or not it turned into a confirmed signal.
            range_high, range_low = h, l
            range_candle_date = date

        elif wicks_below:
            closed_inside = (c >= range_low) and (c <= range_high)

            if closed_inside:
                # Rule #4: potential bullish CRT
                ratio = (lower_wick / total_wick) if total_wick > 0 else 0.0
                if ratio >= wick_threshold:
                    bias, w_info = get_weekly_bias_for_date(date, weekly_df)
                    # Rule #5: must align with a Bullish weekly bias
                    if bias == "Bullish":
                        signals.append({
                            "Date": date.date(),
                            "Signal": "Bullish CRT",
                            "Range High": range_high,
                            "Range Low (swept)": range_low,
                            "Range Set On": range_candle_date.date(),
                            "CRT Candle Open": o,
                            "CRT Candle High": h,
                            "CRT Candle Low": l,
                            "CRT Candle Close": c,
                            "Upper Wick": round(upper_wick, 2),
                            "Lower Wick": round(lower_wick, 2),
                            "Dominant Wick Ratio": round(ratio, 3),
                            "Weekly Bias": bias,
                            **w_info,
                        })
            range_high, range_low = h, l
            range_candle_date = date

    return pd.DataFrame(signals)


# =====================================================================
# MAIN
# =====================================================================
def main():
    print(f"Fetching {TICKER} daily data from {START_DATE} to {END_DATE or 'today'} ...")
    daily = fetch_daily_data(TICKER, START_DATE, END_DATE)
    print(f"  -> {len(daily)} daily candles downloaded "
          f"({daily.index[0].date()} to {daily.index[-1].date()})")

    weekly = build_weekly_candles(daily, anchor=WEEK_ANCHOR)
    print(f"  -> {len(weekly)} weekly candles built (anchor={WEEK_ANCHOR})")

    print("Scanning daily candles for CRT signals aligned with weekly bias ...")
    signals_df = scan_crt(daily, weekly, wick_threshold=WICK_RATIO_THRESHOLD)

    if signals_df.empty:
        print("No valid CRT signals found matching all rules.")
    else:
        signals_df.sort_values("Date", inplace=True)
        signals_df.to_csv(OUTPUT_CSV, index=False)
        bulls = (signals_df["Signal"] == "Bullish CRT").sum()
        bears = (signals_df["Signal"] == "Bearish CRT").sum()
        print(f"Found {len(signals_df)} valid CRT signal(s) "
              f"({bulls} bullish, {bears} bearish).")
        print(f"Saved to: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
