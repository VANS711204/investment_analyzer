# Investment Analyzer

This Streamlit app reads broker transaction records from a published Google Sheets CSV URL. It does not read the local `portfolio.xlsx` file.

## Data Source

```python
HOLDINGS_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTuANiIaqPgsy_-PF16-evKIwSsKluYMacgQG9zVtQ4hlxRrl3_s_6SWzSOkD4pOtA4GD3sb9af9TAn/pub?gid=0&single=true&output=csv"
```

The app loads transactions from `HOLDINGS_CSV_URL` with a 15-second timeout, then parses the CSV with pandas:

```python
urlopen(HOLDINGS_CSV_URL, timeout=15)
pandas.read_csv(...)
```

If Google CSV loading fails after a previous successful load in the same Streamlit session, the app keeps using the last successful data and shows a warning. Successful CSV loads are also saved locally to `last_successful_transactions.csv`, so the app can still use the latest local cache after a restart if Google CSV is temporarily unreachable.

The app tries multiple connection methods (`urlopen`, `requests`, `curl_cffi`, and Windows `curl.exe`) because some Windows, firewall, antivirus, or proxy setups may block one Python networking method while allowing another.

If you see `WinError 10061`, first open the CSV URL in your browser to confirm it is accessible. Also check your network connection, firewall, antivirus software, or company security restrictions, because they may block Python from connecting to Google Sheets.

## Required Transaction Columns

The Google Sheet CSV must include these columns:

- 交易別
- 代碼
- 商品名稱
- 成交股數
- 成交單價
- 成交價金
- 手續費
- 交易稅
- 淨收付
- 交易日期

The app classifies each row by `交易別`:

- Rows containing `現買` or `買` are treated as buys.
- Rows containing `現賣` or `賣` are treated as sells.
- Rows such as `現買股息`, `股息`, `配息`, and `配股` are excluded from holdings calculations and shown in a separate review table.

Sell cost is estimated with FIFO (first in, first out). The app keeps buy lots for each `代碼`, removes the oldest lots first when a sell row appears, and calculates the remaining positions:

- 股票代號
- 股票名稱
- 持有股數
- 總成交價金
- 成交均價
- 總投入成本
- 平均成本

Cash is still entered manually with a Streamlit `number_input`. Use the available cash shown in your broker app as the source of truth; the app does not calculate cash automatically from Google Sheets.

The entered cash can be saved locally with the `保存現金` button. The value is stored in `settings.json` and used as the default next time the app loads.

`成交均價` is based on remaining-lot trade amount only, so it is closer to the average price shown by broker apps. `平均成本` includes fees and taxes from the remaining lots, so it reflects estimated invested cost.

## Price and Profit Analysis

The app uses `yfinance` to estimate current prices. For each Taiwan stock or ETF code, it tries:

- `代碼 + ".TW"`
- `代碼 + ".TWO"`

If neither symbol returns a price, the holdings table shows `無法取得` and the app continues running.

The holdings table also calculates:

- 現價
- 目前市值
- 預估賣出手續費
- 預估賣出交易稅
- 預估賣出費用
- 未實現損益
- 預估損益
- 報酬率
- 預估報酬率
- 持股佔比

`未實現損益` is the gross mark-to-market profit/loss. `預估損益` subtracts estimated selling fees and transaction tax. Brokerage fee is estimated at 0.1425%; ETF transaction tax is estimated at 0.1%; regular stock transaction tax is estimated at 0.3%.

The top summary shows stock market value, total invested cost, gross unrealized profit/loss, estimated profit/loss after selling costs, returns, cash, and total assets.

`總資產` is calculated as stock market value plus the manually entered cash. `持股佔比` is calculated against stock market value, and the holdings table also includes `佔總資產比例`.

## Buy / Sell Recommendations

The app adds a rule-based recommendation for each holding. This is only an auxiliary analysis, not a guaranteed-profit strategy or formal investment advice.

Asset type is inferred from `股票名稱` and `股票代號`:

- If the name contains `ETF`, `台灣50`, `高息`, `高股息`, `美債`, `債`, or `公司債`, or the code is one of `0050`, `00679B`, `00687B`, `00919`, `00929`, `00878`, `00885`, it is treated as `ETF / 債券 ETF`.
- Otherwise, it is treated as `個股`.

ETF / bond ETF recommendation rules:

- Return <= -5% and holding ratio < 25%: `可分批買進`
- -5% < return < 15%: `續抱觀察`
- Return >= 15% and holding ratio < 30%: `續抱，可視資金需求小幅調節`
- Holding ratio >= 30%: remind that the ETF position is getting high
- Holding ratio >= 35%: `暫不加碼，持股比重偏高`

Individual stock recommendation rules:

- Return <= -20%: `風險偏高，先檢查基本面，不建議盲目攤平`
- -20% < return <= -10%: `虧損中，暫不急著加碼`
- -10% < return < 15%: `續抱觀察`
- Return >= 15% and holding ratio < 15%: `可續抱，留意是否分批停利`
- Holding ratio >= 15%: remind that single-stock risk is concentrated
- Holding ratio >= 20%: `單一個股比重偏高，不建議加碼`

If current price is unavailable, the recommendation is shown as `資料不足`.

Manual cash is included in the recommendation context. If cash is below 20% of total assets, the app warns `現金水位偏低`. If cash is below 10% of total assets, the app strongly warns you not to actively add positions. Buy-like recommendations also remind you to buy in batches instead of investing all cash at once.

## Trade Simulation

The app includes a simulation area where you can manually enter either a currently held or unheld stock code. For Taiwan symbols, it tries `代碼 + ".TW"` first, then `代碼 + ".TWO"`.

For currently held stocks, the app compares cost and current price, shows the existing recommendation, and lets you simulate buying or selling. It estimates:

- Simulated trade amount
- Estimated fee and transaction tax
- Cash after the simulated trade
- Total assets after the simulated trade
- Holding ratios after the simulated trade

Buy/sell quantity is entered in lots (`張`), where 1 lot equals 1,000 shares.

Concentration reminders use fixed thresholds: individual stocks trigger a concentration reminder at 15% of assets and a no-add reminder at 20%; ETFs trigger a high-weight reminder at 30% and a no-add reminder at 35%.

For unheld stocks, the app shows status as `未持有`, uses current price if available, and only performs a preliminary buy-risk analysis based on current price, manually entered cash, and allocation ratio. It estimates:

- Planned buy lots
- Estimated shares purchasable from planned buy amount
- Estimated lots purchasable from planned buy amount
- Market value after buying
- Cash after buying
- Total assets after buying
- The stock's share of total assets after buying

If current price is unavailable, it shows `無法取得此股票現價，請確認代號是否正確`. Unheld-stock analysis does not evaluate company fundamentals or price attractiveness; you still need to verify fundamentals and price level yourself.

## Run

```bash
streamlit run app.py
```
