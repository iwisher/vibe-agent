+++
vibe_skill_version = "2.0.0"
id = "stock-analysis"
name = "Stock Analysis"
description = "Analyze stock prices from a local CSV or stooq.com and compute technical indicators"
category = "finance"
tags = ["stocks", "analysis", "finance"]

[trigger]
patterns = ["analyze stock", "check price of"]
required_tools = ["bash"]

[[variables]]
name = "ticker"
type = "string"
required = true
pattern = "^[A-Za-z0-9.-]{1,10}$"
description = "Stock ticker symbol, e.g. QQQ"

[[variables]]
name = "days"
type = "integer"
required = false
default = 30
minimum = 5
maximum = 3650
description = "Lookback window in trading days"

[[variables]]
name = "csv"
type = "string"
required = false
default = ""
description = "Optional path to a local CSV with Date,Close columns (skips the network fetch)"

[[steps]]
id = "analyze"
description = "Run the deterministic analysis script and emit JSON indicators"
tool = "bash"
script = "scripts/analyze.py"
command = "{{ ticker }} --days {{ days }} --csv {{ csv }}"

[steps.verification]
exit_code = 0
json_has_keys = ["ticker", "sma_20"]
+++

# Stock Analysis Skill

## Overview
Fetches daily closing prices (from stooq.com, or a local CSV via the `csv`
variable) and computes basic technical indicators, printed as a single JSON
object by the deterministic `scripts/analyze.py` script.

## Steps

### Step 1: Analyze

**Script:** `scripts/analyze.py`
**Tool:** bash
**Command:** `{{ ticker }} --days {{ days }} --csv {{ csv }}`

**Verification:** exit_code == 0 and JSON output contains `ticker` and `sma_20`.

## Pitfalls

- stooq symbols are lowercased and bare US tickers get a `.us` suffix automatically
- `sma_20` is null when fewer than 20 data points are available

## Examples

### Example 1: Offline analysis from a local CSV

**Input:** ticker="TEST" csv="prices.csv"
**Expected:** JSON object with ticker, period_return_pct, sma_20
