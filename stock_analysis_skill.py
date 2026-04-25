"""
StockAnalysisSkill: A reusable Python module for fetching stock data,
calculating technical indicators (MA250, RSI, MACD), generating charts,
and producing summary reports.

Usage:
    1. Import & use as a class
    2. Run via CLI: python stock_analysis_skill.py --tickers TSLA MSFT GOOGL AMZN NVDA
"""

import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import os
import argparse
from typing import List, Dict, Optional
import warnings

warnings.filterwarnings('ignore')
plt.style.use('seaborn-v0_8-whitegrid')


class StockAnalysisSkill:
    """Reusable skill for multi-ticker technical analysis."""

    def __init__(self, tickers: List[str], period: str = "2y", output_dir: str = "."):
        self.tickers = [t.upper() for t in tickers]
        self.period = period
        self.output_dir = output_dir
        self.data: Dict[str, pd.DataFrame] = {}
        self.results: List[Dict] = []

    def fetch_data(self) -> Dict[str, pd.DataFrame]:
        """Fetch historical price data for all tickers."""
        print(f"📥 Fetching {self.period} history for {self.tickers}...")
        for t in self.tickers:
            hist = yf.Ticker(t).history(period=self.period)
            if hist.empty:
                print(f"⚠️ No data for {t}. Skipping.")
                continue
            self.data[t] = hist
        return self.data

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add MA250, RSI(14), and MACD(12,26,9) to a DataFrame."""
        close = df['Close']

        # MA250
        df['MA250'] = close.rolling(window=250).mean()

        # RSI(14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)  # avoid div by zero
        df['RSI'] = 100 - (100 / (1 + rs))

        # MACD(12,26,9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Histogram'] = df['MACD'] - df['Signal']

        return df

    def generate_charts(self, output_path: str) -> str:
        """Generate 3-panel technical charts for all tickers."""
        n = len(self.data)
        if n == 0:
            print("⚠️ No data to plot.")
            return ""

        fig, axes = plt.subplots(n, 3, figsize=(18, 4 * n))
        if n == 1:
            axes = axes.reshape(1, 3)

        for i, (ticker, df) in enumerate(self.data.items()):
            # Price & MA250
            axes[i, 0].plot(df.index, df['Close'], label='Price', color='blue')
            axes[i, 0].plot(df.index, df['MA250'], label='MA250', color='red', linestyle='--')
            axes[i, 0].set_title(f'{ticker} Price & MA250')
            axes[i, 0].legend()
            axes[i, 0].set_ylabel('$')

            # RSI
            axes[i, 1].plot(df.index, df['RSI'], label='RSI(14)', color='purple')
            axes[i, 1].axhline(70, color='r', linestyle=':', alpha=0.5)
            axes[i, 1].axhline(30, color='g', linestyle=':', alpha=0.5)
            axes[i, 1].set_title(f'{ticker} RSI (14)')
            axes[i, 1].set_ylim(0, 100)

            # MACD
            axes[i, 2].plot(df.index, df['MACD'], label='MACD', color='blue')
            axes[i, 2].plot(df.index, df['Signal'], label='Signal', color='orange')
            axes[i, 2].bar(df.index, df['Histogram'], label='Histogram', color='gray', alpha=0.5)
            axes[i, 2].set_title(f'{ticker} MACD (12, 26, 9)')
            axes[i, 2].legend()

        plt.tight_layout()
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        fig.savefig(output_path, dpi=150)
        plt.close()
        print(f"📊 Chart saved to: {os.path.abspath(output_path)}")
        return output_path

    def get_summary_df(self) -> pd.DataFrame:
        """Extract latest indicator values into a summary DataFrame."""
        self.results = []
        for ticker, df in self.data.items():
            if df.empty:
                continue
            self.results.append({
                "Ticker": ticker,
                "Close": df['Close'].iloc[-1],
                "MA250": df['MA250'].iloc[-1],
                "RSI": df['RSI'].iloc[-1],
                "MACD": df['MACD'].iloc[-1],
                "Signal": df['Signal'].iloc[-1]
            })
        return pd.DataFrame(self.results)

    def print_summary(self) -> None:
        """Print formatted summary table."""
        df = self.get_summary_df()
        if df.empty:
            print("⚠️ No summary data available.")
            return

        print(f"\n{'Ticker':<6} | {'Close':>10} | {'MA250':>10} | {'RSI':>6} | {'MACD':>10} | {'Signal':>10}")
        print("-" * 65)
        for _, r in df.iterrows():
            close_str = f"${r['Close']:,.2f}"
            ma_str = f"${r['MA250']:,.2f}" if pd.notna(r['MA250']) else "N/A"
            rsi_str = f"{r['RSI']:.2f}" if pd.notna(r['RSI']) else "N/A"
            macd_str = f"{r['MACD']:.2f}" if pd.notna(r['MACD']) else "N/A"
            sig_str = f"{r['Signal']:.2f}" if pd.notna(r['Signal']) else "N/A"
            print(f"{r['Ticker']:<6} | {close_str:>10} | {ma_str:>10} | {rsi_str:>6} | {macd_str:>10} | {sig_str:>10}")

    def run(self, output_path: Optional[str] = None) -> Dict:
        """Execute full pipeline: fetch → calculate → chart → summary."""
        if output_path is None:
            output_path = os.path.join(self.output_dir, "stock_analysis.png")

        self.fetch_data()
        for t, df in self.data.items():
            self.calculate_indicators(df)

        self.generate_charts(output_path)
        self.print_summary()

        return {
            "chart_path": output_path,
            "summary_df": self.get_summary_df(),
            "data": self.data
        }


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Stock Analysis Skill")
    parser.add_argument("--tickers", nargs="+", required=True, help="List of ticker symbols")
    parser.add_argument("--period", default="2y", help="Historical period (default: 2y)")
    parser.add_argument("--output", default="stock_analysis.png", help="Output chart path")
    args = parser.parse_args()

    skill = StockAnalysisSkill(tickers=args.tickers, period=args.period)
    skill.run(output_path=args.output)


if __name__ == "__main__":
    main()
