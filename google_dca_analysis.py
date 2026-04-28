import yfinance as yf
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

def analyze_google_dca(ticker="GOOGL"):
    print(f"📡 Fetching 6-month daily history for {ticker}...")
    hist = yf.Ticker(ticker).history(period="6mo")
    
    if hist.empty:
        print("❌ No data fetched.")
        return

    # --- Technical Indicators ---
    # Moving Averages
    hist['SMA_50'] = hist['Close'].rolling(window=50).mean()
    hist['SMA_200'] = hist['Close'].rolling(window=200).mean()
    
    # RSI (14-period)
    delta = hist['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    hist['RSI'] = 100 - (100 / (1 + rs))
    
    # Annualized 20-day Volatility
    hist['Daily_Return'] = hist['Close'].pct_change()
    hist['Vol_20d'] = hist['Daily_Return'].rolling(20).std() * np.sqrt(252)
    
    # --- Latest Values ---
    latest = hist.iloc[-1]
    price = latest['Close']
    sma50 = latest['SMA_50']
    sma200 = latest['SMA_200']
    rsi = latest['RSI']
    vol = latest['Vol_20d']
    
    # 1-Month Return (~21 trading days)
    ret_1m = (price / hist['Close'].iloc[-22]) - 1 if len(hist) > 21 else None

    # 6-Month Return
    ret_6m = (price / hist['Close'].iloc[0]) - 1 if len(hist) > 0 else None
    
    # --- Print Trending Data (Last 6 Months) ---
    print(f"\n{'='*60}")
    print(f"📈 {ticker} Trending Data (Last 6 Months)")
    print(f"{'='*60}")
    print(f"Start Date: {hist.index[0].date()} | End Date: {hist.index[-1].date()}")
    print(f"Start Price: ${hist['Close'].iloc[0]:,.2f} | End Price: ${price:,.2f}")
    print(f"6-Month Return: {ret_6m:+.2%}")
    print("-" * 60)
    
    # Print last 10 trading days as a sample of the trend
    print("\n📅 Recent Daily Trend (Last 10 Days):")
    recent_data = hist[['Close', 'SMA_50', 'RSI']].tail(10)
    print(recent_data.to_string())
    
    # --- Print Latest Snapshot ---
    print(f"\n{'='*60}")
    print(f"📊 {ticker} Latest Technical Snapshot")
    print(f"{'='*60}")
    print(f"💰 Current Price:      ${price:,.2f}")
    print(f"📈 SMA 50:             ${sma50:,.2f}")
    print(f"📉 SMA 200:            ${sma200:,.2f}")
    print(f"⚡ RSI (14):           {rsi:.2f}")
    print(f"🌪️ 20d Annualized Vol: {vol:.2%}")
    if ret_1m:
        print(f"📅 1M Return:          {ret_1m:+.2%}")
    print(f"{'='*60}")
    
    # --- DCA Suggestions ---
    print("\n🎯 DCA Strategy Suggestions (Based on Latest Data)")
    print("-" * 60)
    
    # Trend Context
    if price > sma200:
        print("✅ Uptrend confirmed (Price > SMA200). DCA aligns well with long-term momentum.")
    else:
        print("⚠️ Price below SMA200. Consider smaller DCA amounts or wait for trend reversal confirmation.")
        
    # Momentum / Overbought-Oversold
    if rsi > 70:
        print("🔴 RSI > 70 (Overbought). Consider pausing DCA or reducing contribution until a pullback.")
    elif rsi < 30:
        print("🟢 RSI < 30 (Oversold). Historically favorable DCA zones. Maintain or slightly increase amount.")
    else:
        print("🟡 RSI neutral (30–70). Continue standard DCA schedule without adjustment.")
        
    # Volatility Context
    if vol > 0.30:
        print("📉 High volatility detected. DCA naturally smooths entries, but monitor for sharp swings.")
    else:
        print("📊 Volatility moderate. Standard weekly/monthly DCA frequency works well.")
        
    # Recent Performance
    if ret_1m and ret_1m > 0.10:
        print("🚀 Strong 1M rally. Avoid chasing; stick to fixed schedule to prevent buying tops.")
    elif ret_1m and ret_1m < -0.10:
        print("📉 Recent pullback. DCA is working as intended; consider this a discount phase.")
        
    print("-" * 60)

if __name__ == "__main__":
    analyze_google_dca("GOOGL")
