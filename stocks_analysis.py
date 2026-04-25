import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt

tickers = ["TSLA", "MSFT", "GOOGL", "AMZN", "NVDA"]
results = []

plt.style.use('seaborn-v0_8-whitegrid')
fig, axes = plt.subplots(5, 3, figsize=(18, 20))

for i, t in enumerate(tickers):
    hist = yf.Ticker(t).history(period="2y")
    if hist.empty:
        continue
    
    close = hist['Close']
    ma250 = close.rolling(window=250).mean()
    
    # RSI (14-period)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal

    results.append({
        "Ticker": t, 
        "Close": close.iloc[-1], 
        "MA250": ma250.iloc[-1], 
        "RSI": rsi.iloc[-1], 
        "MACD": macd.iloc[-1], 
        "Signal": signal.iloc[-1]
    })

    # Plot Price & MA250
    axes[i, 0].plot(close.index, close, label='Price', color='blue')
    axes[i, 0].plot(ma250.index, ma250, label='MA250', color='red', linestyle='--')
    axes[i, 0].set_title(f'{t} Price & MA250')
    axes[i, 0].legend()
    axes[i, 0].set_ylabel('$')
    
    # Plot RSI
    axes[i, 1].plot(rsi.index, rsi, label='RSI(14)', color='purple')
    axes[i, 1].axhline(70, color='r', linestyle=':', alpha=0.5)
    axes[i, 1].axhline(30, color='g', linestyle=':', alpha=0.5)
    axes[i, 1].set_title(f'{t} RSI (14)')
    axes[i, 1].set_ylim(0, 100)
    
    # Plot MACD
    axes[i, 2].plot(macd.index, macd, label='MACD', color='blue')
    axes[i, 2].plot(signal.index, signal, label='Signal', color='orange')
    axes[i, 2].bar(histogram.index, histogram, label='Histogram', color='gray', alpha=0.5)
    axes[i, 2].set_title(f'{t} MACD (12, 26, 9)')
    axes[i, 2].legend()

plt.tight_layout()
plt.savefig('/Users/rsong/DevSpace/vibe-agent/stocks_analysis.png', dpi=150)
plt.close()

# Print summary table
print(f"{'Ticker':<6} | {'Close':>10} | {'MA250':>10} | {'RSI':>6} | {'MACD':>10} | {'Signal':>10}")
print("-" * 65)
for r in results:
    close_str = f"${r['Close']:,.2f}"
    ma_str = f"${r['MA250']:,.2f}" if pd.notna(r['MA250']) else "N/A"
    rsi_str = f"{r['RSI']:.2f}" if pd.notna(r['RSI']) else "N/A"
    macd_str = f"{r['MACD']:.2f}" if pd.notna(r['MACD']) else "N/A"
    sig_str = f"{r['Signal']:.2f}" if pd.notna(r['Signal']) else "N/A"
    print(f"{r['Ticker']:<6} | {close_str:>10} | {ma_str:>10} | {rsi_str:>6} | {macd_str:>10} | {sig_str:>10}")
