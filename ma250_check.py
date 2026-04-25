import yfinance as yf
import pandas as pd

# Map common names to correct ticker symbols
tickers = ["TSLA", "MSFT", "GOOGL", "AMZN", "NVDA"]
results = []

for t in tickers:
    # Fetch ~1 year of daily data (covers ~250 trading days needed for MA250)
    hist = yf.Ticker(t).history(period="1y")
    if hist.empty:
        continue

    latest_close = hist["Close"].iloc[-1]
    # Calculate 250-day Simple Moving Average
    ma250 = hist["Close"].rolling(window=250).mean().iloc[-1]

    results.append({
        "Ticker": t,
        "Close": latest_close,
        "MA250": ma250
    })

# Print formatted table
print(f"{'Ticker':<6} | {'Latest Close':>12} | {'MA250':>12}")
print("-" * 35)
for r in results:
    close_str = f"${r['Close']:,.2f}"
    ma_str = f"${r['MA250']:,.2f}" if pd.notna(r['MA250']) else "N/A"
    print(f"{r['Ticker']:<6} | {close_str:>12} | {ma_str:>12}")
