import yfinance as yf

def fetch_qqq():
    try:
        ticker = yf.Ticker("QQQ")
        # Fetch data for the last trading day
        hist = ticker.history(period="1d")
        
        if hist.empty:
            print("No data returned. The market may be closed.")
            return

        latest = hist.iloc[-1]
        
        print(f"📊 QQQ Latest Data:")
        print(f"   Date: {latest.name}")
        print(f"   Open:   ${latest['Open']:.2f}")
        print(f"   High:   ${latest['High']:.2f}")
        print(f"   Low:    ${latest['Low']:.2f}")
        print(f"   Close:  ${latest['Close']:.2f}")
        print(f"   Volume: {latest['Volume']:,.0f}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_qqq()
