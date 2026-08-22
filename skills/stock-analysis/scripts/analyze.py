#!/usr/bin/env python3
"""Deterministic stock analysis for the stock-analysis skill.

Reads Date,Close rows from a local CSV (--csv) or fetches daily prices from
stooq.com when no CSV is given, computes basic technical indicators, and
prints a single JSON object to stdout. On failure, prints an error JSON
object to stderr and exits non-zero.

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
FETCH_TIMEOUT = 10  # seconds
SMA_WINDOW = 20


class AnalysisError(Exception):
    """Raised for expected failures (bad input, fetch error, bad data)."""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute stock indicators as JSON.")
    parser.add_argument("ticker", help="Stock ticker symbol, e.g. QQQ")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in trading days")
    parser.add_argument(
        "--csv",
        default="",
        help="Read Date,Close rows from this local CSV instead of fetching",
    )
    return parser.parse_args(argv)


def parse_close_rows(text: str) -> list[tuple[str, float]]:
    """Parse CSV text with Date and Close columns into (date, close) rows."""
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []
    if "Date" not in fieldnames or "Close" not in fieldnames:
        raise AnalysisError("CSV must have Date and Close columns")
    rows = []
    for row in reader:
        try:
            rows.append((row["Date"], float(row["Close"])))
        except (TypeError, ValueError):
            continue  # skip malformed rows
    return rows


def load_rows(ticker: str, csv_path: str) -> list[tuple[str, float]]:
    """Load (date, close) rows from a local CSV or from stooq.com."""
    if csv_path:
        path = Path(csv_path)
        if not path.is_file():
            raise AnalysisError(f"CSV file not found: {csv_path}")
        return parse_close_rows(path.read_text(encoding="utf-8"))

    # stooq uses lowercase symbols; bare US tickers take a ".us" suffix
    symbol = ticker.lower()
    if "." not in symbol:
        symbol += ".us"
    url = STOOQ_URL.format(symbol=urllib.parse.quote(symbol))
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
            text = resp.read().decode("utf-8")
    except Exception as e:
        raise AnalysisError(f"failed to fetch data for '{ticker}': {e}") from e
    return parse_close_rows(text)


def compute_indicators(ticker: str, days: int, rows: list[tuple[str, float]]) -> dict:
    """Compute indicators over the most recent `days` rows."""
    if days < 1:
        raise AnalysisError("days must be >= 1")
    if not rows:
        raise AnalysisError("no price data available")

    window = rows[-days:] if len(rows) > days else rows
    closes = [close for _, close in window]
    first_close, last_close = closes[0], closes[-1]

    period_return_pct = None
    if first_close:
        period_return_pct = round((last_close - first_close) / first_close * 100, 2)

    sma_20 = None
    if len(closes) >= SMA_WINDOW:
        sma_20 = round(sum(closes[-SMA_WINDOW:]) / SMA_WINDOW, 4)

    return {
        "ticker": ticker,
        "days": days,
        "data_points": len(window),
        "start_date": window[0][0],
        "end_date": window[-1][0],
        "first_close": first_close,
        "last_close": last_close,
        "min_close": min(closes),
        "max_close": max(closes),
        "period_return_pct": period_return_pct,
        "sma_20": sma_20,
    }


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        rows = load_rows(args.ticker, args.csv)
        result = compute_indicators(args.ticker, args.days, rows)
    except AnalysisError as e:
        print(json.dumps({"ticker": args.ticker, "error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
