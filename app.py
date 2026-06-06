# -*- coding: utf-8 -*-
import json
import subprocess
from io import StringIO
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import pandas as pd
import requests
import streamlit as st

try:
    import yfinance as yf
except ImportError:
    yf = None


HOLDINGS_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTuANiIaqPgsy_-PF16-evKIwSsKluYMacgQG9zVtQ4hlxRrl3_s_6SWzSOkD4pOtA4GD3sb9af9TAn/pub?gid=0&single=true&output=csv"
REAL_ESTATE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTuANiIaqPgsy_-PF16-evKIwSsKluYMacgQG9zVtQ4hlxRrl3_s_6SWzSOkD4pOtA4GD3sb9af9TAn/pub?gid=1303969979&single=true&output=csv"
SETTINGS_FILE = Path(__file__).with_name("settings.json")
CSV_CACHE_FILE = Path(__file__).with_name("last_successful_transactions.csv")
YFINANCE_CACHE_DIR = Path(__file__).with_name(".yfinance_cache")
CSV_READ_TIMEOUT_SECONDS = 15

if yf is not None:
    YFINANCE_CACHE_DIR.mkdir(exist_ok=True)
    try:
        yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))
    except Exception:
        pass

REQUIRED_COLUMNS = [
    "\u4ea4\u6613\u5225",
    "\u4ee3\u78bc",
    "\u5546\u54c1\u540d\u7a31",
    "\u6210\u4ea4\u80a1\u6578",
    "\u6210\u4ea4\u55ae\u50f9",
    "\u6210\u4ea4\u50f9\u91d1",
    "\u624b\u7e8c\u8cbb",
    "\u4ea4\u6613\u7a05",
    "\u6de8\u6536\u4ed8",
    "\u4ea4\u6613\u65e5\u671f",
]

NUMERIC_COLUMNS = [
    "\u6210\u4ea4\u80a1\u6578",
    "\u6210\u4ea4\u55ae\u50f9",
    "\u6210\u4ea4\u50f9\u91d1",
    "\u624b\u7e8c\u8cbb",
    "\u4ea4\u6613\u7a05",
]

REAL_ESTATE_BASE_REQUIRED_COLUMNS = [
    "\u8cc7\u7522\u540d\u7a31",
    "\u985e\u578b",
    "\u623f\u7522\u73fe\u503c",
    "\u8cb8\u6b3e\u7e3d\u984d",
    "\u6708\u7e73\u91d1\u984d",
    "\u5e74\u5229\u7387",
    "\u5099\u8a3b",
]

REAL_ESTATE_NEW_SCHEDULE_COLUMNS = [
    "\u8cb8\u6b3e\u8d77\u59cb\u65e5\u671f",
    "\u8cb8\u6b3e\u5e74\u9650",
]

REAL_ESTATE_OLD_SCHEDULE_COLUMNS = ["\u5269\u9918\u671f\u6578"]

REAL_ESTATE_NUMERIC_COLUMNS = [
    "\u623f\u7522\u73fe\u503c",
    "\u8cb8\u6b3e\u7e3d\u984d",
    "\u6708\u7e73\u91d1\u984d",
    "\u5e74\u5229\u7387",
    "\u8cb8\u6b3e\u5e74\u9650",
]

EXCLUDED_TRANSACTION_KEYWORDS = [
    "\u73fe\u8cb7\u80a1\u606f",
    "\u80a1\u606f",
    "\u914d\u606f",
    "\u914d\u80a1",
]

BUY_KEYWORDS = ["\u73fe\u8cb7", "\u8cb7"]
SELL_KEYWORDS = ["\u73fe\u8ce3", "\u8ce3"]
BROKERAGE_FEE_RATE = 0.001425
ETF_TRANSACTION_TAX_RATE = 0.001
STOCK_TRANSACTION_TAX_RATE = 0.003
STOCK_CONCENTRATION_WARNING_THRESHOLD = 0.15
STOCK_NO_ADD_THRESHOLD = 0.20
ETF_CONCENTRATION_WARNING_THRESHOLD = 0.30
ETF_NO_ADD_THRESHOLD = 0.35
CASH_LOW_THRESHOLD = 0.20
CASH_CRITICAL_THRESHOLD = 0.10
REAL_ESTATE_LOAN_PRESSURE_THRESHOLD = 0.70
MONTHLY_MORTGAGE_WARNING_AMOUNT = 80000
REAL_ESTATE_CONCENTRATION_THRESHOLD = 0.60
ETF_TYPE_KEYWORDS = ["ETF", "\u53f0\u706350", "\u9ad8\u606f", "\u9ad8\u80a1\u606f", "\u7f8e\u50b5", "\u50b5", "\u516c\u53f8\u50b5"]
ETF_TYPE_CODES = {"0050", "00679B", "00687B", "00919", "00929", "00878", "00885"}
SHARES_PER_LOT = 1000


def parse_number(series):
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip(),
        errors="coerce",
    ).fillna(0)


def load_settings():
    if not SETTINGS_FILE.exists():
        return {}

    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_settings(settings):
    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_holdings_csv(url):
    errors = []

    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(url, timeout=CSV_READ_TIMEOUT_SECONDS) as response:
            csv_text = response.read().decode("utf-8-sig")
        return pd.read_csv(StringIO(csv_text)), None
    except Exception as exc:
        errors.append(f"urlopen: {exc}")

    try:
        import requests

        session = requests.Session()
        session.trust_env = False
        response = session.get(url, timeout=CSV_READ_TIMEOUT_SECONDS)
        response.raise_for_status()
        return pd.read_csv(StringIO(response.text)), None
    except Exception as exc:
        errors.append(f"requests: {exc}")

    try:
        from curl_cffi import requests as curl_requests

        response = curl_requests.get(
            url,
            timeout=CSV_READ_TIMEOUT_SECONDS,
            impersonate="chrome",
        )
        response.raise_for_status()
        return pd.read_csv(StringIO(response.text)), None
    except Exception as exc:
        errors.append(f"curl_cffi: {exc}")

    try:
        completed = subprocess.run(
            [
                "curl.exe",
                "-L",
                "--noproxy",
                "*",
                "--max-time",
                str(CSV_READ_TIMEOUT_SECONDS),
                url,
            ],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            timeout=CSV_READ_TIMEOUT_SECONDS + 5,
        )
        return pd.read_csv(StringIO(completed.stdout)), None
    except Exception as exc:
        errors.append(f"curl.exe: {exc}")

    return None, "；".join(errors)


def read_real_estate_csv():
    try:
        return pd.read_csv(REAL_ESTATE_CSV_URL), None
    except Exception as exc:
        pandas_error = str(exc)

    fallback_df, fallback_error = read_holdings_csv(REAL_ESTATE_CSV_URL)
    if fallback_df is not None:
        return fallback_df, None

    return None, f"pandas.read_csv: {pandas_error}；備援讀取: {fallback_error}"


def calculate_elapsed_months(start_dates, today=None):
    today = today or pd.Timestamp.today().normalize()
    start_dates = pd.to_datetime(start_dates, errors="coerce")
    elapsed = (today.year - start_dates.dt.year) * 12 + (today.month - start_dates.dt.month)
    elapsed = elapsed.where(today.day >= start_dates.dt.day, elapsed - 1)
    return elapsed.clip(lower=0).fillna(0)


def build_real_estate_analysis(real_estate_df):
    real_estate = real_estate_df.copy()
    for column in REAL_ESTATE_NUMERIC_COLUMNS:
        if column in real_estate.columns:
            real_estate[column] = parse_number(real_estate[column])

    if "\u8cb8\u6b3e\u8d77\u59cb\u65e5\u671f" in real_estate.columns:
        parsed_start_dates = pd.to_datetime(real_estate["\u8cb8\u6b3e\u8d77\u59cb\u65e5\u671f"], errors="coerce")
        real_estate["\u8cb8\u6b3e\u8d77\u59cb\u65e5\u671f"] = parsed_start_dates.dt.strftime("%Y-%m-%d").fillna(
            real_estate["\u8cb8\u6b3e\u8d77\u59cb\u65e5\u671f"].astype(str)
        )
    else:
        parsed_start_dates = pd.Series(pd.NaT, index=real_estate.index)
        real_estate["\u8cb8\u6b3e\u8d77\u59cb\u65e5\u671f"] = "\u820a\u6b04\u4f4d\u672a\u63d0\u4f9b"

    if "\u8cb8\u6b3e\u5e74\u9650" not in real_estate.columns:
        real_estate["\u8cb8\u6b3e\u5e74\u9650"] = pd.NA

    if all(column in real_estate_df.columns for column in REAL_ESTATE_NEW_SCHEDULE_COLUMNS):
        total_periods = real_estate["\u8cb8\u6b3e\u5e74\u9650"] * 12
        elapsed_periods = calculate_elapsed_months(parsed_start_dates)
        real_estate["\u7e3d\u671f\u6578"] = total_periods
        real_estate["\u5df2\u904e\u671f\u6578"] = elapsed_periods
        real_estate["\u81ea\u52d5\u8a08\u7b97\u5269\u9918\u671f\u6578"] = (total_periods - elapsed_periods).clip(lower=0)
    else:
        real_estate["\u5269\u9918\u671f\u6578"] = parse_number(real_estate["\u5269\u9918\u671f\u6578"])
        real_estate["\u7e3d\u671f\u6578"] = pd.NA
        real_estate["\u5df2\u904e\u671f\u6578"] = pd.NA
        real_estate["\u81ea\u52d5\u8a08\u7b97\u5269\u9918\u671f\u6578"] = real_estate["\u5269\u9918\u671f\u6578"].clip(lower=0)

    real_estate["\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa"] = real_estate["\u6708\u7e73\u91d1\u984d"] * real_estate["\u81ea\u52d5\u8a08\u7b97\u5269\u9918\u671f\u6578"]
    real_estate["\u6bcf\u5e74\u623f\u8cb8\u652f\u51fa"] = real_estate["\u6708\u7e73\u91d1\u984d"] * 12
    real_estate["\u73fe\u503c\u6263\u672a\u4f86\u623f\u8cb8\u652f\u51fa\u5f8c\u9918\u984d"] = (
        real_estate["\u623f\u7522\u73fe\u503c"] - real_estate["\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa"]
    )
    real_estate["\u672a\u4f86\u623f\u8cb8\u652f\u51fa / \u623f\u7522\u73fe\u503c"] = (
        real_estate["\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa"] / real_estate["\u623f\u7522\u73fe\u503c"]
    ).where(real_estate["\u623f\u7522\u73fe\u503c"] != 0)
    real_estate["\u5269\u9918\u5e74\u6578"] = real_estate["\u81ea\u52d5\u8a08\u7b97\u5269\u9918\u671f\u6578"] / 12
    real_estate["\u539f\u59cb\u8cb8\u6b3e\u6bd4"] = (
        real_estate["\u8cb8\u6b3e\u7e3d\u984d"] / real_estate["\u623f\u7522\u73fe\u503c"]
    ).where(real_estate["\u623f\u7522\u73fe\u503c"] != 0)
    return real_estate


def empty_real_estate_analysis():
    return pd.DataFrame(
        columns=REAL_ESTATE_BASE_REQUIRED_COLUMNS
        + REAL_ESTATE_NEW_SCHEDULE_COLUMNS
        + [
            "\u7e3d\u671f\u6578",
            "\u5df2\u904e\u671f\u6578",
            "\u81ea\u52d5\u8a08\u7b97\u5269\u9918\u671f\u6578",
            "\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa",
            "\u6bcf\u5e74\u623f\u8cb8\u652f\u51fa",
            "\u73fe\u503c\u6263\u672a\u4f86\u623f\u8cb8\u652f\u51fa\u5f8c\u9918\u984d",
            "\u672a\u4f86\u623f\u8cb8\u652f\u51fa / \u623f\u7522\u73fe\u503c",
            "\u5269\u9918\u5e74\u6578",
            "\u539f\u59cb\u8cb8\u6b3e\u6bd4",
        ]
    )


def fetch_twse_stock_info(stock_id):
    stock_id = str(stock_id).strip()
    session = requests.Session()
    session.trust_env = False

    for market_prefix in ["tse", "otc"]:
        try:
            url = (
                "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                f"?ex_ch={market_prefix}_{stock_id}.tw&json=1&delay=0"
            )
            response = session.get(url, timeout=CSV_READ_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            rows = data.get("msgArray") or []
            if rows and rows[0].get("c"):
                return rows[0]
        except Exception:
            continue

    return None


def parse_twse_price(info):
    if not info:
        return None

    for key in ["z", "pz"]:
        value = info.get(key)
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price

    return None


def save_transactions_cache(transactions):
    transactions.to_csv(CSV_CACHE_FILE, index=False, encoding="utf-8-sig")


def load_transactions_cache():
    if not CSV_CACHE_FILE.exists():
        return None

    try:
        return pd.read_csv(CSV_CACHE_FILE)
    except Exception:
        return None


def classify_transaction(transaction_type):
    transaction_type = str(transaction_type).strip()

    if any(keyword in transaction_type for keyword in EXCLUDED_TRANSACTION_KEYWORDS):
        return "\u672a\u7d0d\u5165"
    if any(keyword in transaction_type for keyword in BUY_KEYWORDS):
        return "\u8cb7\u9032"
    if any(keyword in transaction_type for keyword in SELL_KEYWORDS):
        return "\u8ce3\u51fa"
    return "\u672a\u7d0d\u5165"


def build_holdings(transactions):
    positions = {}

    for _, row in transactions.iterrows():
        stock_id = str(row["\u4ee3\u78bc"]).strip()
        stock_name = str(row["\u5546\u54c1\u540d\u7a31"]).strip()
        quantity = abs(row["\u6210\u4ea4\u80a1\u6578"])
        trade_cost = (
            abs(row["\u6210\u4ea4\u50f9\u91d1"])
            + abs(row["\u624b\u7e8c\u8cbb"])
            + abs(row["\u4ea4\u6613\u7a05"])
        )
        transaction_kind = row["\u4ea4\u6613\u5206\u985e"]

        position = positions.setdefault(
            stock_id,
            {
                "\u80a1\u7968\u4ee3\u865f": stock_id,
                "\u80a1\u7968\u540d\u7a31": stock_name,
                "lots": [],
            },
        )
        position["\u80a1\u7968\u540d\u7a31"] = stock_name

        if transaction_kind == "\u8cb7\u9032":
            position["lots"].append(
                {
                    "\u6301\u6709\u80a1\u6578": quantity,
                    "\u7e3d\u6210\u4ea4\u50f9\u91d1": abs(row["\u6210\u4ea4\u50f9\u91d1"]),
                    "\u7e3d\u6295\u5165\u6210\u672c": trade_cost,
                }
            )
        elif transaction_kind == "\u8ce3\u51fa":
            remaining_sell_quantity = quantity

            while remaining_sell_quantity > 0 and position["lots"]:
                lot = position["lots"][0]
                lot_quantity = lot["\u6301\u6709\u80a1\u6578"]
                consumed_quantity = min(remaining_sell_quantity, lot_quantity)
                consumed_ratio = consumed_quantity / lot_quantity if lot_quantity else 0

                lot["\u6301\u6709\u80a1\u6578"] -= consumed_quantity
                lot["\u7e3d\u6210\u4ea4\u50f9\u91d1"] -= lot["\u7e3d\u6210\u4ea4\u50f9\u91d1"] * consumed_ratio
                lot["\u7e3d\u6295\u5165\u6210\u672c"] -= lot["\u7e3d\u6295\u5165\u6210\u672c"] * consumed_ratio
                remaining_sell_quantity -= consumed_quantity

                if lot["\u6301\u6709\u80a1\u6578"] <= 0:
                    position["lots"].pop(0)

    holdings_rows = []
    for position in positions.values():
        holding_quantity = sum(lot["\u6301\u6709\u80a1\u6578"] for lot in position["lots"])
        total_trade_amount = sum(lot["\u7e3d\u6210\u4ea4\u50f9\u91d1"] for lot in position["lots"])
        total_cost = sum(lot["\u7e3d\u6295\u5165\u6210\u672c"] for lot in position["lots"])

        if holding_quantity <= 0:
            continue

        holdings_rows.append(
            {
                "\u80a1\u7968\u4ee3\u865f": position["\u80a1\u7968\u4ee3\u865f"],
                "\u80a1\u7968\u540d\u7a31": position["\u80a1\u7968\u540d\u7a31"],
                "\u6301\u6709\u80a1\u6578": holding_quantity,
                "\u7e3d\u6210\u4ea4\u50f9\u91d1": total_trade_amount,
                "\u7e3d\u6295\u5165\u6210\u672c": total_cost,
            }
        )

    holdings = pd.DataFrame(holdings_rows)
    if holdings.empty:
        return pd.DataFrame(
            columns=[
                "\u80a1\u7968\u4ee3\u865f",
                "\u80a1\u7968\u540d\u7a31",
                "\u6301\u6709\u80a1\u6578",
                "\u7e3d\u6210\u4ea4\u50f9\u91d1",
                "\u6210\u4ea4\u5747\u50f9",
                "\u7e3d\u6295\u5165\u6210\u672c",
                "\u5e73\u5747\u6210\u672c",
            ]
        )

    holdings["\u6210\u4ea4\u5747\u50f9"] = (
        holdings["\u7e3d\u6210\u4ea4\u50f9\u91d1"] / holdings["\u6301\u6709\u80a1\u6578"]
    )
    holdings["\u5e73\u5747\u6210\u672c"] = (
        holdings["\u7e3d\u6295\u5165\u6210\u672c"] / holdings["\u6301\u6709\u80a1\u6578"]
    )
    return holdings.sort_values("\u80a1\u7968\u4ee3\u865f").reset_index(drop=True)


@st.cache_data(ttl=3600)
def get_current_price(stock_id):
    twse_info = fetch_twse_stock_info(stock_id)
    twse_price = parse_twse_price(twse_info)
    if twse_price is not None:
        return twse_price

    if yf is None:
        return None

    stock_id = str(stock_id).strip()
    for suffix in [".TW", ".TWO"]:
        ticker_symbol = f"{stock_id}{suffix}"
        try:
            try:
                from curl_cffi import requests as curl_requests

                session = curl_requests.Session(impersonate="chrome")
                session.trust_env = False
                ticker = yf.Ticker(ticker_symbol, session=session)
            except Exception:
                ticker = yf.Ticker(ticker_symbol)
            history = ticker.history(period="5d", interval="1d", auto_adjust=False)
        except Exception:
            continue

        if not history.empty and "Close" in history.columns:
            close_prices = history["Close"].dropna()
            if not close_prices.empty:
                price = float(close_prices.iloc[-1])
                if price > 0:
                    return price

        try:
            fast_info = ticker.fast_info
            for key in ["lastPrice", "regularMarketPreviousClose", "previousClose"]:
                price = fast_info.get(key)
                if price and float(price) > 0:
                    return float(price)
        except Exception:
            continue

    return None


@st.cache_data(ttl=3600)
def get_stock_name(stock_id):
    twse_info = fetch_twse_stock_info(stock_id)
    if twse_info:
        name = twse_info.get("n")
        if name:
            return str(name)

    if yf is None:
        return None

    stock_id = str(stock_id).strip()
    for suffix in [".TW", ".TWO"]:
        ticker_symbol = f"{stock_id}{suffix}"
        try:
            try:
                from curl_cffi import requests as curl_requests

                session = curl_requests.Session(impersonate="chrome")
                session.trust_env = False
                ticker = yf.Ticker(ticker_symbol, session=session)
            except Exception:
                ticker = yf.Ticker(ticker_symbol)
            info = ticker.info
        except Exception:
            continue

        name = info.get("longName") or info.get("shortName") or info.get("displayName")
        if name:
            return str(name)

    return None


def add_price_analysis(holdings, cash):
    holdings = holdings.copy()
    holdings["\u985e\u578b"] = holdings.apply(classify_asset_type, axis=1)
    holdings["\u73fe\u50f9"] = holdings["\u80a1\u7968\u4ee3\u865f"].apply(get_current_price)
    holdings["\u76ee\u524d\u5e02\u503c"] = holdings["\u6301\u6709\u80a1\u6578"] * holdings["\u73fe\u50f9"]
    holdings["\u9810\u4f30\u8ce3\u51fa\u624b\u7e8c\u8cbb"] = holdings["\u76ee\u524d\u5e02\u503c"].apply(
        estimate_brokerage_fee
    )
    holdings["\u9810\u4f30\u8ce3\u51fa\u4ea4\u6613\u7a05"] = holdings.apply(
        estimate_transaction_tax,
        axis=1,
    )
    holdings["\u9810\u4f30\u8ce3\u51fa\u8cbb\u7528"] = (
        holdings["\u9810\u4f30\u8ce3\u51fa\u624b\u7e8c\u8cbb"] + holdings["\u9810\u4f30\u8ce3\u51fa\u4ea4\u6613\u7a05"]
    )
    holdings["\u672a\u5be6\u73fe\u640d\u76ca"] = holdings["\u76ee\u524d\u5e02\u503c"] - holdings["\u7e3d\u6295\u5165\u6210\u672c"]
    holdings["\u9810\u4f30\u640d\u76ca"] = (
        holdings["\u672a\u5be6\u73fe\u640d\u76ca"] - holdings["\u9810\u4f30\u8ce3\u51fa\u8cbb\u7528"]
    )
    holdings["\u5831\u916c\u7387"] = (
        holdings["\u672a\u5be6\u73fe\u640d\u76ca"] / holdings["\u7e3d\u6295\u5165\u6210\u672c"]
    ).where(holdings["\u7e3d\u6295\u5165\u6210\u672c"] != 0)
    holdings["\u9810\u4f30\u5831\u916c\u7387"] = (
        holdings["\u9810\u4f30\u640d\u76ca"] / holdings["\u7e3d\u6295\u5165\u6210\u672c"]
    ).where(holdings["\u7e3d\u6295\u5165\u6210\u672c"] != 0)

    total_market_value = holdings["\u76ee\u524d\u5e02\u503c"].sum(skipna=True)
    total_assets = total_market_value + cash
    if total_market_value:
        holdings["\u6301\u80a1\u4f54\u6bd4"] = holdings["\u76ee\u524d\u5e02\u503c"] / total_market_value
    else:
        holdings["\u6301\u80a1\u4f54\u6bd4"] = pd.NA
    if total_assets:
        holdings["\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"] = holdings["\u76ee\u524d\u5e02\u503c"] / total_assets
        holdings["\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"] = cash / total_assets
    else:
        holdings["\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"] = pd.NA
        holdings["\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"] = pd.NA

    recommendation_columns = holdings.apply(generate_recommendation, axis=1, result_type="expand")
    recommendation_columns.columns = ["\u64cd\u4f5c\u5efa\u8b70", "\u5efa\u8b70\u7406\u7531"]
    holdings = pd.concat([holdings, recommendation_columns], axis=1)
    return holdings


def classify_asset_type(row):
    stock_id = str(row["\u80a1\u7968\u4ee3\u865f"]).strip()
    stock_name = str(row["\u80a1\u7968\u540d\u7a31"]).strip()
    text = f"{stock_id} {stock_name}".upper()

    if stock_id in ETF_TYPE_CODES or any(keyword.upper() in text for keyword in ETF_TYPE_KEYWORDS):
        return "ETF / \u50b5\u5238 ETF"
    return "\u500b\u80a1"


def generate_recommendation(row):
    return_rate = row["\u5831\u916c\u7387"]
    holding_ratio = row["\u6301\u80a1\u4f54\u6bd4"]
    asset_type = row["\u985e\u578b"]
    cash_ratio = row.get("\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b", pd.NA)

    if pd.isna(return_rate) or pd.isna(holding_ratio):
        return (
            "\u8cc7\u6599\u4e0d\u8db3",
            "\u73fe\u50f9\u6216\u5e02\u503c\u7121\u6cd5\u53d6\u5f97\uff0c\u6682\u6642\u7121\u6cd5\u7522\u751f\u898f\u5247\u5efa\u8b70\u3002",
        )

    return_percent = abs(return_rate) * 100
    ratio_percent = holding_ratio * 100

    if asset_type == "ETF / \u50b5\u5238 ETF":
        if holding_ratio >= ETF_NO_ADD_THRESHOLD:
            return (
                "\u66ab\u4e0d\u52a0\u78bc\uff0c\u6301\u80a1\u6bd4\u91cd\u504f\u9ad8",
                f"\u6b64\u70ba ETF \u6216\u50b5\u5238 ETF\uff0c\u76ee\u524d\u6301\u80a1\u4f54\u6bd4\u7d04 {ratio_percent:.1f}%\uff0c\u5df2\u9ad8\u65bc 35%\uff0c\u5efa\u8b70\u5148\u63a7\u5236\u6bd4\u91cd\u3002",
            )
        if holding_ratio >= ETF_CONCENTRATION_WARNING_THRESHOLD:
            return (
                "\u6301\u80a1\u6bd4\u91cd\u504f\u9ad8\uff0c\u52a0\u78bc\u524d\u8acb\u5148\u8a55\u4f30\u914d\u7f6e",
                f"\u6b64\u70ba ETF \u6216\u50b5\u5238 ETF\uff0c\u76ee\u524d\u6301\u80a1\u4f54\u6bd4\u7d04 {ratio_percent:.1f}%\uff0c\u5df2\u9ad8\u65bc 30%\uff0c\u5efa\u8b70\u7559\u610f\u55ae\u4e00 ETF \u6bd4\u91cd\u504f\u9ad8\u3002",
            )
        if return_rate <= -0.05 and holding_ratio < 0.25:
            return apply_cash_context(
                (
                "\u53ef\u5206\u6279\u8cb7\u9032",
                f"\u76ee\u524d\u4f4e\u65bc\u6210\u672c\u7d04 {return_percent:.1f}%\uff0c\u4e14\u6301\u80a1\u4f54\u6bd4\u7d04 {ratio_percent:.1f}% \u672a\u904e\u9ad8\uff0c\u53ef\u8003\u616e\u5206\u6279\u800c\u975e\u4e00\u6b21\u8cb7\u9032\u3002",
                ),
                cash_ratio,
                is_buy_candidate=True,
            )
        if return_rate >= 0.15 and holding_ratio < ETF_CONCENTRATION_WARNING_THRESHOLD:
            return (
                "\u7e8c\u62b1\uff0c\u53ef\u8996\u8cc7\u91d1\u9700\u6c42\u5c0f\u5e45\u8abf\u7bc0",
                f"\u76ee\u524d\u7372\u5229\u7d04 {return_percent:.1f}%\uff0c\u6301\u80a1\u4f54\u6bd4\u7d04 {ratio_percent:.1f}% \u5c1a\u672a\u904e\u9ad8\uff0c\u53ef\u7e8c\u62b1\u4e26\u8996\u8cc7\u91d1\u9700\u6c42\u5c0f\u5e45\u8abf\u7bc0\u3002",
            )
        if -0.05 < return_rate < 0.15:
            return (
                "\u7e8c\u62b1\u89c0\u5bdf",
                f"\u76ee\u524d\u5831\u916c\u7387\u7d04 {return_rate * 100:.1f}%\uff0c\u4ecd\u5728\u89c0\u5bdf\u5340\u9593\uff0c\u53ef\u7e7c\u7e8c\u8ddf\u8e64\u914d\u7f6e\u8207\u8cc7\u91d1\u9700\u6c42\u3002",
            )

    if holding_ratio >= STOCK_NO_ADD_THRESHOLD:
        return (
            "\u55ae\u4e00\u500b\u80a1\u6bd4\u91cd\u504f\u9ad8\uff0c\u4e0d\u5efa\u8b70\u52a0\u78bc",
            f"\u6b64\u70ba\u500b\u80a1\uff0c\u76ee\u524d\u6301\u80a1\u4f54\u6bd4\u7d04 {ratio_percent:.1f}%\uff0c\u55ae\u4e00\u500b\u80a1\u96c6\u4e2d\u5ea6\u504f\u9ad8\uff0c\u5efa\u8b70\u5148\u63a7\u5236\u98a8\u96aa\u3002",
        )
    if holding_ratio >= STOCK_CONCENTRATION_WARNING_THRESHOLD:
        return (
            "\u63d0\u9192\u98a8\u96aa\u96c6\u4e2d\uff0c\u52a0\u78bc\u524d\u8acb\u5148\u8a55\u4f30",
            f"\u6b64\u70ba\u500b\u80a1\uff0c\u76ee\u524d\u6301\u80a1\u4f54\u6bd4\u7d04 {ratio_percent:.1f}%\uff0c\u5df2\u9ad8\u65bc 15%\uff0c\u9700\u7559\u610f\u55ae\u4e00\u500b\u80a1\u98a8\u96aa\u96c6\u4e2d\u3002",
        )
    if return_rate <= -0.20:
        return (
            "\u98a8\u96aa\u504f\u9ad8\uff0c\u5148\u6aa2\u67e5\u57fa\u672c\u9762\uff0c\u4e0d\u5efa\u8b70\u76f2\u76ee\u6524\u5e73",
            f"\u6b64\u70ba\u500b\u80a1\u4e14\u8655\u65bc\u8f03\u5927\u8657\u640d\uff0c\u76ee\u524d\u4f4e\u65bc\u6210\u672c\u7d04 {return_percent:.1f}%\uff0c\u5efa\u8b70\u5148\u78ba\u8a8d\u516c\u53f8\u57fa\u672c\u9762\uff0c\u4e0d\u5efa\u8b70\u55ae\u7d14\u6524\u5e73\u3002",
        )
    if -0.20 < return_rate <= -0.10:
        return (
            "\u8657\u640d\u4e2d\uff0c\u66ab\u4e0d\u6025\u8457\u52a0\u78bc",
            f"\u76ee\u524d\u4f4e\u65bc\u6210\u672c\u7d04 {return_percent:.1f}%\uff0c\u8655\u65bc\u8657\u640d\u5340\u9593\uff0c\u5efa\u8b70\u5148\u89c0\u5bdf\u8d70\u52e2\u8207\u57fa\u672c\u9762\u8b8a\u5316\u3002",
        )
    if -0.10 < return_rate < 0.15:
        return (
            "\u7e8c\u62b1\u89c0\u5bdf",
            f"\u76ee\u524d\u5831\u916c\u7387\u7d04 {return_rate * 100:.1f}%\uff0c\u5c1a\u672a\u9054\u660e\u986f\u52a0\u78bc\u6216\u505c\u5229\u689d\u4ef6\uff0c\u53ef\u7e8c\u62b1\u89c0\u5bdf\u3002",
        )
    if return_rate >= 0.15 and holding_ratio < STOCK_CONCENTRATION_WARNING_THRESHOLD:
        return (
            "\u53ef\u7e8c\u62b1\uff0c\u7559\u610f\u662f\u5426\u5206\u6279\u505c\u5229",
            f"\u76ee\u524d\u7372\u5229\u7d04 {return_percent:.1f}%\uff0c\u4e14\u6301\u80a1\u4f54\u6bd4\u7d04 {ratio_percent:.1f}% \u672a\u904e\u9ad8\uff0c\u53ef\u7e8c\u62b1\u4e26\u7559\u610f\u5206\u6279\u505c\u5229\u3002",
        )
    if return_rate >= 0.15 and holding_ratio >= STOCK_CONCENTRATION_WARNING_THRESHOLD:
        return (
            "\u53ef\u8a55\u4f30\u90e8\u5206\u505c\u5229\uff0c\u964d\u4f4e\u55ae\u4e00\u500b\u80a1\u98a8\u96aa",
            f"\u76ee\u524d\u7372\u5229\u7d04 {return_percent:.1f}%\uff0c\u4e14\u55ae\u4e00\u6301\u80a1\u4f54\u6bd4\u7d04 {ratio_percent:.1f}%\uff0c\u53ef\u8003\u616e\u90e8\u5206\u505c\u5229\u964d\u4f4e\u6ce2\u52d5\u98a8\u96aa\u3002",
        )

    return ("\u7e8c\u62b1\u89c0\u5bdf", "\u76ee\u524d\u672a\u89f8\u767c\u660e\u78ba\u52a0\u78bc\u6216\u6e1b\u78bc\u689d\u4ef6\uff0c\u5efa\u8b70\u7e7c\u7e8c\u89c0\u5bdf\u3002")


def apply_cash_context(recommendation, cash_ratio, is_buy_candidate=False):
    action, reason = recommendation

    if pd.isna(cash_ratio):
        return action, reason

    if cash_ratio < CASH_CRITICAL_THRESHOLD and is_buy_candidate:
        return (
            "\u73fe\u91d1\u6c34\u4f4d\u904e\u4f4e\uff0c\u5f37\u70c8\u4e0d\u5efa\u8b70\u7a4d\u6975\u52a0\u78bc",
            reason
            + f"\u76ee\u524d\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u7d04 {cash_ratio * 100:.1f}%\uff0c\u5df2\u4f4e\u65bc 10%\uff0c\u5f37\u70c8\u5efa\u8b70\u4fdd\u7559\u73fe\u91d1\uff0c\u4e0d\u8981\u7a4d\u6975\u52a0\u78bc\u3002",
        )

    if cash_ratio < CASH_LOW_THRESHOLD and is_buy_candidate:
        return (
            "\u73fe\u91d1\u6c34\u4f4d\u504f\u4f4e\uff0c\u4e0d\u5efa\u8b70\u7a4d\u6975\u52a0\u78bc",
            reason
            + f"\u76ee\u524d\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u7d04 {cash_ratio * 100:.1f}%\uff0c\u5df2\u4f4e\u65bc 20%\uff0c\u73fe\u91d1\u6c34\u4f4d\u504f\u4f4e\uff0c\u4e0d\u5efa\u8b70\u7a4d\u6975\u52a0\u78bc\u3002",
        )

    if is_buy_candidate:
        return (
            action,
            reason + "\u5efa\u8b70\u5206\u6279\uff0c\u4e0d\u8981\u4e00\u6b21\u6295\u5165\u5168\u90e8\u73fe\u91d1\u3002",
        )

    return action, reason


def is_etf(stock_id):
    return str(stock_id).strip().startswith("00")


def estimate_brokerage_fee(market_value):
    if pd.isna(market_value):
        return pd.NA
    return int(market_value * BROKERAGE_FEE_RATE)


def estimate_transaction_tax(row):
    market_value = row["\u76ee\u524d\u5e02\u503c"]
    if pd.isna(market_value):
        return pd.NA

    tax_rate = ETF_TRANSACTION_TAX_RATE if is_etf(row["\u80a1\u7968\u4ee3\u865f"]) else STOCK_TRANSACTION_TAX_RATE
    return int(market_value * tax_rate)


def format_currency(value):
    if pd.isna(value):
        return "\u7121\u6cd5\u53d6\u5f97"
    return f"{value:,.0f}"


def format_price(value):
    if pd.isna(value):
        return "\u7121\u6cd5\u53d6\u5f97"
    return f"{value:,.2f}"


def format_percent(value):
    if pd.isna(value):
        return "\u7121\u6cd5\u53d6\u5f97"
    return f"{value:.2%}"


def format_months(value):
    if pd.isna(value):
        return "\u7121\u6cd5\u53d6\u5f97"
    return f"{value:,.1f} \u500b\u6708"


def format_interest_rate(value):
    if pd.isna(value):
        return "\u7121\u6cd5\u53d6\u5f97"
    rate = value / 100 if value > 1 else value
    return f"{rate:.2%}"


def render_mortgage_cashflow_check(post_cash, monthly_mortgage_total):
    if not monthly_mortgage_total:
        return

    buffer_months = post_cash / monthly_mortgage_total
    if buffer_months < 6:
        st.error(
            f"買進後現金緩衝不足 6 個月房貸，不建議執行。"
            f"目前約可支撐 {buffer_months:.1f} 個月。"
        )
    elif buffer_months < 12:
        st.warning(
            f"買進後現金緩衝低於 12 個月房貸，建議降低買入金額或分批。"
            f"目前約可支撐 {buffer_months:.1f} 個月。"
        )
    else:
        st.success(f"買進後仍保有 12 個月以上房貸緩衝，目前約可支撐 {buffer_months:.1f} 個月。")


def find_holding(holdings, stock_id):
    matched = holdings[holdings["\u80a1\u7968\u4ee3\u865f"].astype(str).eq(str(stock_id).strip())]
    if matched.empty:
        return None
    return matched.iloc[0]


def get_default_trade_price(holdings, stock_id):
    holding = find_holding(holdings, stock_id)
    if holding is not None and not pd.isna(holding["\u73fe\u50f9"]):
        return float(holding["\u73fe\u50f9"])
    return 0.0


def estimate_trade_fee(stock_id, market_value, action):
    brokerage_fee = estimate_brokerage_fee(market_value)
    transaction_tax = 0
    if action == "\u8ce3\u51fa":
        tax_rate = ETF_TRANSACTION_TAX_RATE if is_etf(stock_id) else STOCK_TRANSACTION_TAX_RATE
        transaction_tax = int(market_value * tax_rate)
    return brokerage_fee, transaction_tax


def generate_unheld_buy_analysis(stock_id, stock_name, asset_type, price, buy_amount, cash, stock_market_value):
    if price is None or pd.isna(price) or price <= 0:
        return {
            "\u5efa\u8b70": "\u8cc7\u6599\u4e0d\u8db3",
            "\u5efa\u8b70\u7406\u7531": "\u50c5\u80fd\u4f9d\u73fe\u91d1\u8207\u914d\u7f6e\u6bd4\u4f8b\u521d\u6b65\u8a55\u4f30\uff0c\u4ecd\u9700\u81ea\u884c\u78ba\u8a8d\u57fa\u672c\u9762\u8207\u50f9\u683c\u4f4d\u968e\u3002",
            "\u9810\u4f30\u53ef\u8cb7\u80a1\u6578": pd.NA,
            "\u9810\u4f30\u53ef\u8cb7\u5f35\u6578": pd.NA,
            "\u8cb7\u5165\u5f8c\u8a72\u80a1\u7968\u5e02\u503c": pd.NA,
            "\u8cb7\u5165\u5f8c\u73fe\u91d1": cash,
            "\u8cb7\u5165\u5f8c\u7e3d\u8cc7\u7522": stock_market_value + cash,
            "\u8cb7\u5165\u5f8c\u8a72\u80a1\u7968\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b": pd.NA,
            "\u8cb7\u5165\u5f8c\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b": pd.NA,
        }

    brokerage_fee = estimate_brokerage_fee(buy_amount)
    estimated_quantity = buy_amount / price
    estimated_lots = estimated_quantity / SHARES_PER_LOT
    post_cash = cash - buy_amount - brokerage_fee
    post_stock_value = stock_market_value + buy_amount
    post_total_assets = post_stock_value + post_cash
    post_stock_ratio = buy_amount / post_total_assets if post_total_assets else pd.NA
    post_cash_ratio = post_cash / post_total_assets if post_total_assets else pd.NA

    if post_cash < 0:
        recommendation = "\u73fe\u91d1\u4e0d\u8db3\uff0c\u7121\u6cd5\u8cb7\u5165"
        reason = "\u9810\u8a08\u8cb7\u5165\u91d1\u984d\u52a0\u8a08\u4f30\u7b97\u624b\u7e8c\u8cbb\u5f8c\u5df2\u8d85\u904e\u76ee\u524d\u624b\u52d5\u8f38\u5165\u7684\u73fe\u91d1\u3002"
    elif post_cash_ratio < CASH_CRITICAL_THRESHOLD:
        recommendation = "\u8cb7\u5165\u5f8c\u73fe\u91d1\u6c34\u4f4d\u904e\u4f4e\uff0c\u5f37\u70c8\u4e0d\u5efa\u8b70\u7a4d\u6975\u52a0\u78bc"
        reason = f"\u8cb7\u5165\u5f8c\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u7d04 {post_cash_ratio * 100:.1f}%\uff0c\u4f4e\u65bc 10%\uff0c\u5f37\u70c8\u5efa\u8b70\u4fdd\u7559\u73fe\u91d1\u3002"
    elif post_cash_ratio < CASH_LOW_THRESHOLD:
        recommendation = "\u8cb7\u5165\u5f8c\u73fe\u91d1\u6c34\u4f4d\u504f\u4f4e\uff0c\u4e0d\u5efa\u8b70\u4e00\u6b21\u8cb7\u5165"
        reason = f"\u8cb7\u5165\u5f8c\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u7d04 {post_cash_ratio * 100:.1f}%\uff0c\u4f4e\u65bc 20%\uff0c\u5efa\u8b70\u964d\u4f4e\u91d1\u984d\u6216\u5206\u6279\u3002"
    elif asset_type == "\u500b\u80a1" and post_stock_ratio >= STOCK_NO_ADD_THRESHOLD:
        recommendation = "\u55ae\u4e00\u500b\u80a1\u6bd4\u91cd\u904e\u9ad8\uff0c\u4e0d\u5efa\u8b70\u52a0\u78bc"
        reason = f"\u8cb7\u5165\u5f8c {stock_id} {stock_name} \u4f54\u7e3d\u8cc7\u7522\u7d04 {post_stock_ratio * 100:.1f}%\uff0c\u5df2\u9ad8\u65bc\u500b\u80a1 20% \u9580\u6abb\u3002"
    elif asset_type == "\u500b\u80a1" and post_stock_ratio >= STOCK_CONCENTRATION_WARNING_THRESHOLD:
        recommendation = "\u55ae\u4e00\u500b\u80a1\u6bd4\u91cd\u504f\u9ad8\uff0c\u5efa\u8b70\u964d\u4f4e\u8cb7\u5165\u91d1\u984d\u6216\u5206\u6279"
        reason = f"\u8cb7\u5165\u5f8c {stock_id} {stock_name} \u4f54\u7e3d\u8cc7\u7522\u7d04 {post_stock_ratio * 100:.1f}%\uff0c\u5df2\u9ad8\u65bc\u500b\u80a1 15% \u63d0\u9192\u9580\u6abb\u3002"
    elif asset_type == "ETF / \u50b5\u5238 ETF" and post_stock_ratio >= ETF_NO_ADD_THRESHOLD:
        recommendation = "\u55ae\u4e00 ETF \u6bd4\u91cd\u904e\u9ad8\uff0c\u4e0d\u5efa\u8b70\u52a0\u78bc"
        reason = f"\u8cb7\u5165\u5f8c {stock_id} {stock_name} \u4f54\u7e3d\u8cc7\u7522\u7d04 {post_stock_ratio * 100:.1f}%\uff0c\u5df2\u9ad8\u65bc ETF 35% \u9580\u6abb\u3002"
    elif asset_type == "ETF / \u50b5\u5238 ETF" and post_stock_ratio >= ETF_CONCENTRATION_WARNING_THRESHOLD:
        recommendation = "\u55ae\u4e00 ETF \u6bd4\u91cd\u504f\u9ad8\uff0c\u8acb\u7559\u610f\u914d\u7f6e\u98a8\u96aa"
        reason = f"\u8cb7\u5165\u5f8c {stock_id} {stock_name} \u4f54\u7e3d\u8cc7\u7522\u7d04 {post_stock_ratio * 100:.1f}%\uff0c\u5df2\u9ad8\u65bc ETF 30% \u63d0\u9192\u9580\u6abb\u3002"
    elif asset_type == "ETF / \u50b5\u5238 ETF" and post_stock_ratio < ETF_CONCENTRATION_WARNING_THRESHOLD:
        recommendation = "\u53ef\u4f5c\u70ba\u914d\u7f6e\u578b\u8cb7\u9032\uff0c\u4f46\u5efa\u8b70\u5206\u6279"
        reason = f"\u6b64\u70ba ETF \u6216\u50b5\u5238 ETF\uff0c\u8cb7\u5165\u5f8c\u4f54\u7e3d\u8cc7\u7522\u7d04 {post_stock_ratio * 100:.1f}%\uff0c\u672a\u9054 30%\uff0c\u53ef\u5217\u5165\u914d\u7f6e\u4f46\u4ecd\u5efa\u8b70\u5206\u6279\u3002"
    else:
        recommendation = "\u521d\u6b65\u53ef\u8a55\u4f30\uff0c\u4ecd\u9700\u81ea\u884c\u78ba\u8a8d\u57fa\u672c\u9762\u8207\u50f9\u683c\u4f4d\u968e"
        reason = "\u50c5\u80fd\u4f9d\u73fe\u91d1\u8207\u914d\u7f6e\u6bd4\u4f8b\u521d\u6b65\u8a55\u4f30\uff0c\u4ecd\u9700\u81ea\u884c\u78ba\u8a8d\u57fa\u672c\u9762\u8207\u50f9\u683c\u4f4d\u968e\u3002"

    return {
        "\u5efa\u8b70": recommendation,
        "\u5efa\u8b70\u7406\u7531": reason,
        "\u9810\u4f30\u53ef\u8cb7\u80a1\u6578": estimated_quantity,
        "\u9810\u4f30\u53ef\u8cb7\u5f35\u6578": estimated_lots,
        "\u8cb7\u5165\u5f8c\u8a72\u80a1\u7968\u5e02\u503c": buy_amount,
        "\u8cb7\u5165\u5f8c\u73fe\u91d1": post_cash,
        "\u8cb7\u5165\u5f8c\u7e3d\u8cc7\u7522": post_total_assets,
        "\u8cb7\u5165\u5f8c\u8a72\u80a1\u7968\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b": post_stock_ratio,
        "\u8cb7\u5165\u5f8c\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b": post_cash_ratio,
    }


def build_unheld_status_display(stock_id, stock_name, asset_type, price):
    return pd.DataFrame(
        [
            {
                "\u80a1\u7968\u4ee3\u865f": stock_id,
                "\u80a1\u7968\u540d\u7a31": stock_name,
                "\u985e\u578b": asset_type,
                "\u72c0\u614b": "\u672a\u6301\u6709",
                "\u76ee\u524d\u6301\u6709\u80a1\u6578": 0,
                "\u5e73\u5747\u6210\u672c": "\u4e0d\u9069\u7528",
                "\u73fe\u50f9": format_price(price),
                "\u76ee\u524d\u5e02\u503c": 0,
                "\u6301\u80a1\u4f54\u6bd4": "0.00%",
                "\u672a\u5be6\u73fe\u640d\u76ca": "\u4e0d\u9069\u7528",
                "\u5e33\u9762\u5831\u916c\u7387": "\u4e0d\u9069\u7528",
            }
        ]
    )


def build_simulated_holdings(holdings, stock_id, stock_name, action, quantity, price):
    simulated = holdings[
        [
            "\u80a1\u7968\u4ee3\u865f",
            "\u80a1\u7968\u540d\u7a31",
            "\u6301\u6709\u80a1\u6578",
            "\u73fe\u50f9",
        ]
    ].copy()

    stock_id = str(stock_id).strip()
    stock_name = str(stock_name).strip() or stock_id
    quantity_delta = quantity if action == "\u8cb7\u9032" else -quantity
    matched_index = simulated.index[simulated["\u80a1\u7968\u4ee3\u865f"].astype(str).eq(stock_id)]

    if len(matched_index):
        index = matched_index[0]
        simulated.loc[index, "\u6301\u6709\u80a1\u6578"] += quantity_delta
        simulated.loc[index, "\u73fe\u50f9"] = price
    elif action == "\u8cb7\u9032":
        simulated = pd.concat(
            [
                simulated,
                pd.DataFrame(
                    [
                        {
                            "\u80a1\u7968\u4ee3\u865f": stock_id,
                            "\u80a1\u7968\u540d\u7a31": stock_name,
                            "\u6301\u6709\u80a1\u6578": quantity,
                            "\u73fe\u50f9": price,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    simulated = simulated[simulated["\u6301\u6709\u80a1\u6578"] > 0].copy()
    simulated["\u6a21\u64ec\u5f8c\u5e02\u503c"] = simulated["\u6301\u6709\u80a1\u6578"] * simulated["\u73fe\u50f9"]
    total_market_value = simulated["\u6a21\u64ec\u5f8c\u5e02\u503c"].sum(skipna=True)
    if total_market_value:
        simulated["\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4"] = simulated["\u6a21\u64ec\u5f8c\u5e02\u503c"] / total_market_value
    else:
        simulated["\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4"] = pd.NA
    return simulated.sort_values("\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4", ascending=False)


def build_simulation_display(simulated):
    display = simulated.copy()
    display["\u73fe\u50f9"] = display["\u73fe\u50f9"].apply(format_price)
    display["\u6a21\u64ec\u5f8c\u5e02\u503c"] = display["\u6a21\u64ec\u5f8c\u5e02\u503c"].apply(format_currency)
    display["\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4"] = display["\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4"].apply(
        format_percent
    )
    return display[
        [
            "\u80a1\u7968\u4ee3\u865f",
            "\u80a1\u7968\u540d\u7a31",
            "\u6301\u6709\u80a1\u6578",
            "\u73fe\u50f9",
            "\u6a21\u64ec\u5f8c\u5e02\u503c",
            "\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4",
        ]
    ]


def build_display_holdings(holdings):
    display = holdings.copy()
    display["\u73fe\u50f9"] = display["\u73fe\u50f9"].apply(format_price)
    display["\u76ee\u524d\u5e02\u503c"] = display["\u76ee\u524d\u5e02\u503c"].apply(format_currency)
    display["\u9810\u4f30\u8ce3\u51fa\u624b\u7e8c\u8cbb"] = display["\u9810\u4f30\u8ce3\u51fa\u624b\u7e8c\u8cbb"].apply(
        format_currency
    )
    display["\u9810\u4f30\u8ce3\u51fa\u4ea4\u6613\u7a05"] = display["\u9810\u4f30\u8ce3\u51fa\u4ea4\u6613\u7a05"].apply(
        format_currency
    )
    display["\u9810\u4f30\u8ce3\u51fa\u8cbb\u7528"] = display["\u9810\u4f30\u8ce3\u51fa\u8cbb\u7528"].apply(
        format_currency
    )
    display["\u672a\u5be6\u73fe\u640d\u76ca"] = display["\u672a\u5be6\u73fe\u640d\u76ca"].apply(format_currency)
    display["\u9810\u4f30\u640d\u76ca"] = display["\u9810\u4f30\u640d\u76ca"].apply(format_currency)
    display["\u5831\u916c\u7387"] = display["\u5831\u916c\u7387"].apply(format_percent)
    display["\u9810\u4f30\u5831\u916c\u7387"] = display["\u9810\u4f30\u5831\u916c\u7387"].apply(format_percent)
    display["\u6301\u80a1\u4f54\u6bd4"] = display["\u6301\u80a1\u4f54\u6bd4"].apply(format_percent)
    display["\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"] = display["\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"].apply(format_percent)
    display["\u7e3d\u6210\u4ea4\u50f9\u91d1"] = display["\u7e3d\u6210\u4ea4\u50f9\u91d1"].apply(format_currency)
    display["\u6210\u4ea4\u5747\u50f9"] = display["\u6210\u4ea4\u5747\u50f9"].apply(format_price)
    display["\u7e3d\u6295\u5165\u6210\u672c"] = display["\u7e3d\u6295\u5165\u6210\u672c"].apply(format_currency)
    display["\u5e73\u5747\u6210\u672c"] = display["\u5e73\u5747\u6210\u672c"].apply(format_price)
    display = display.rename(
        columns={
            "\u5831\u916c\u7387": "\u5e33\u9762\u5831\u916c\u7387",
            "\u9810\u4f30\u5831\u916c\u7387": "\u6263\u9664\u4ea4\u6613\u6210\u672c\u5f8c\u5831\u916c\u7387",
            "\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b": "\u7e3d\u8cc7\u7522\u6bd4\u4f8b",
        }
    )
    return display[
        [
            "\u80a1\u7968\u4ee3\u865f",
            "\u80a1\u7968\u540d\u7a31",
            "\u985e\u578b",
            "\u64cd\u4f5c\u5efa\u8b70",
            "\u5efa\u8b70\u7406\u7531",
            "\u6301\u6709\u80a1\u6578",
            "\u7e3d\u6210\u4ea4\u50f9\u91d1",
            "\u6210\u4ea4\u5747\u50f9",
            "\u7e3d\u6295\u5165\u6210\u672c",
            "\u5e73\u5747\u6210\u672c",
            "\u73fe\u50f9",
            "\u76ee\u524d\u5e02\u503c",
            "\u9810\u4f30\u8ce3\u51fa\u624b\u7e8c\u8cbb",
            "\u9810\u4f30\u8ce3\u51fa\u4ea4\u6613\u7a05",
            "\u9810\u4f30\u8ce3\u51fa\u8cbb\u7528",
            "\u672a\u5be6\u73fe\u640d\u76ca",
            "\u9810\u4f30\u640d\u76ca",
            "\u5e33\u9762\u5831\u916c\u7387",
            "\u6263\u9664\u4ea4\u6613\u6210\u672c\u5f8c\u5831\u916c\u7387",
            "\u6301\u80a1\u4f54\u6bd4",
            "\u7e3d\u8cc7\u7522\u6bd4\u4f8b",
        ]
    ]


def build_display_real_estate(real_estate):
    display = real_estate.copy()
    for column in [
        "\u623f\u7522\u73fe\u503c",
        "\u6708\u7e73\u91d1\u984d",
        "\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa",
    ]:
        display[column] = display[column].apply(format_currency)
    display["\u5269\u9918\u5e74\u6578"] = display["\u5269\u9918\u5e74\u6578"].apply(format_price)
    display["\u5e74\u5229\u7387"] = display["\u5e74\u5229\u7387"].apply(format_interest_rate)
    return display[
        [
            "\u8cc7\u7522\u540d\u7a31",
            "\u985e\u578b",
            "\u623f\u7522\u73fe\u503c",
            "\u6708\u7e73\u91d1\u984d",
            "\u5e74\u5229\u7387",
            "\u8cb8\u6b3e\u8d77\u59cb\u65e5\u671f",
            "\u8cb8\u6b3e\u5e74\u9650",
            "\u81ea\u52d5\u8a08\u7b97\u5269\u9918\u671f\u6578",
            "\u5269\u9918\u5e74\u6578",
            "\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa",
            "\u5099\u8a3b",
        ]
    ]


def build_display_advanced_real_estate(real_estate):
    display = real_estate.copy()
    for column in [
        "\u8cb8\u6b3e\u7e3d\u984d",
        "\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa",
        "\u623f\u7522\u73fe\u503c",
        "\u73fe\u503c\u6263\u672a\u4f86\u623f\u8cb8\u652f\u51fa\u5f8c\u9918\u984d",
    ]:
        display[column] = display[column].apply(format_currency)
    display["\u5e74\u5229\u7387"] = display["\u5e74\u5229\u7387"].apply(format_interest_rate)
    display["\u672a\u4f86\u623f\u8cb8\u652f\u51fa / \u623f\u7522\u73fe\u503c"] = display[
        "\u672a\u4f86\u623f\u8cb8\u652f\u51fa / \u623f\u7522\u73fe\u503c"
    ].apply(format_percent)
    display["\u539f\u59cb\u8cb8\u6b3e\u6bd4"] = display["\u539f\u59cb\u8cb8\u6b3e\u6bd4"].apply(format_percent)
    return display[
        [
            "\u8cc7\u7522\u540d\u7a31",
            "\u8cb8\u6b3e\u7e3d\u984d",
            "\u5e74\u5229\u7387",
            "\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa",
            "\u672a\u4f86\u623f\u8cb8\u652f\u51fa / \u623f\u7522\u73fe\u503c",
            "\u539f\u59cb\u8cb8\u6b3e\u6bd4",
            "\u623f\u7522\u73fe\u503c",
            "\u73fe\u503c\u6263\u672a\u4f86\u623f\u8cb8\u652f\u51fa\u5f8c\u9918\u984d",
        ]
    ]


def build_real_estate_summary_table(real_estate_value_total, future_mortgage_payment_total):
    return pd.DataFrame(
        [
            {
                "\u623f\u7522\u73fe\u503c\u5408\u8a08": format_currency(real_estate_value_total),
                "\u672a\u4f86\u623f\u8cb8\u7e3d\u652f\u51fa\u5408\u8a08": format_currency(future_mortgage_payment_total),
            }
        ]
    )


def build_today_conclusions(holdings, total_assets, cash_ratio, overall_return, real_estate_summary):
    conclusions = []

    if total_assets and cash_ratio < CASH_CRITICAL_THRESHOLD:
        conclusions.append(
            (
                "error",
                f"現金水位很低，目前約佔總資產 {cash_ratio * 100:.1f}%，強烈建議先保留資金，不要積極加碼。",
            )
        )
    elif total_assets and cash_ratio < CASH_LOW_THRESHOLD:
        conclusions.append(
            (
                "warning",
                f"現金水位偏低，目前約佔總資產 {cash_ratio * 100:.1f}%，建議保留資金，不宜積極加碼。",
            )
        )

    etf_high = holdings[
        (holdings["類型"].eq("ETF / 債券 ETF"))
        & (holdings["佔總資產比例"] >= ETF_CONCENTRATION_WARNING_THRESHOLD)
    ]
    if not etf_high.empty:
        names = "、".join(etf_high["股票代號"].astype(str))
        conclusions.append(("warning", f"{names} 等 ETF 比重偏高，後續加碼需要更保守。"))

    stock_high = holdings[
        (holdings["類型"].eq("個股"))
        & (holdings["佔總資產比例"] >= STOCK_CONCENTRATION_WARNING_THRESHOLD)
    ]
    if not stock_high.empty:
        names = "、".join(stock_high["股票代號"].astype(str))
        conclusions.append(("warning", f"{names} 等單一個股集中度偏高，請注意波動風險。"))

    if total_assets and overall_return > 0 and cash_ratio >= CASH_LOW_THRESHOLD:
        conclusions.append(("success", "整體帳面報酬率為正，且現金水位仍有餘裕，可依原本計畫分批布局。"))

    monthly_mortgage_total = real_estate_summary["monthly_mortgage_total"]
    mortgage_buffer_months = real_estate_summary["mortgage_buffer_months"]
    stock_cash_total = real_estate_summary["stock_cash_total"]
    mortgage_safety_level_12m = real_estate_summary["mortgage_safety_level_12m"]

    if not pd.isna(mortgage_buffer_months):
        if mortgage_buffer_months < 6:
            conclusions.append(("error", "現金緩衝偏低，建議先提高現金水位，不宜積極加碼投資。"))
        elif mortgage_buffer_months <= 12:
            conclusions.append(("warning", "現金緩衝尚可，但投資加碼建議分批，避免壓縮房貸現金流。"))
        else:
            conclusions.append(("success", "現金緩衝相對充足，可依投資計畫分批布局。"))

    if monthly_mortgage_total > MONTHLY_MORTGAGE_WARNING_AMOUNT:
        conclusions.append(("warning", "每月房貸支出較高，需優先確保穩定現金流。"))

    if mortgage_safety_level_12m and stock_cash_total < mortgage_safety_level_12m:
        conclusions.append(("warning", "短期流動性偏緊，建議降低高風險投資加碼。"))

    if not conclusions:
        conclusions.append(("info", "目前沒有明顯集中或現金水位警訊，可以先維持觀察並按計畫調整。"))

    return conclusions


def build_watchlist(holdings):
    watch_rows = []

    for _, row in holdings.iterrows():
        reasons = []
        actions = []
        stock_id = row["股票代號"]
        stock_name = row["股票名稱"]
        asset_type = row["類型"]
        return_rate = row["報酬率"]
        asset_ratio = row["佔總資產比例"]

        if pd.isna(row["現價"]):
            reasons.append("現價無法取得")
            actions.append("先確認報價來源或股票代號")
        if not pd.isna(return_rate) and return_rate < -0.20:
            reasons.append(f"帳面報酬率低於 -20%（約 {return_rate * 100:.1f}%）")
            actions.append("先檢查基本面，不建議只因跌深而攤平")
        if not pd.isna(return_rate) and return_rate > 0.20:
            reasons.append(f"帳面報酬率高於 20%（約 {return_rate * 100:.1f}%）")
            actions.append("可檢視是否需要分批停利或調節")
        if asset_type == "個股" and not pd.isna(asset_ratio) and asset_ratio > STOCK_CONCENTRATION_WARNING_THRESHOLD:
            reasons.append(f"單一個股佔總資產超過 15%（約 {asset_ratio * 100:.1f}%）")
            actions.append("降低單一個股集中風險")
        if (
            asset_type == "ETF / 債券 ETF"
            and not pd.isna(asset_ratio)
            and asset_ratio > ETF_CONCENTRATION_WARNING_THRESHOLD
        ):
            reasons.append(f"單一 ETF 佔總資產超過 30%（約 {asset_ratio * 100:.1f}%）")
            actions.append("後續加碼保守，避免配置過度集中")

        if reasons:
            watch_rows.append(
                {
                    "股票代號": stock_id,
                    "股票名稱": stock_name,
                    "原因": "；".join(reasons),
                    "建議動作": "；".join(dict.fromkeys(actions)),
                }
            )

    return pd.DataFrame(watch_rows, columns=["股票代號", "股票名稱", "原因", "建議動作"])


st.set_page_config(page_title="投資分析工具", layout="wide")
st.title("投資分析工具")
st.warning(
    "本工具為規則式投資輔助分析，並非保證獲利或正式投資建議。"
    "現價資料來源可能延遲，實際下單前請以券商報價為準。"
)
st.info(
    "目前成本採 FIFO 先進先出法估算；若券商成本計算方式不同，結果可能與券商 App 略有差異。"
)
st.info(
    "現價會優先嘗試台灣證交所 / 櫃買中心報價，再使用 yfinance 備援；資料可能不是即時報價，僅供估算參考。"
)
st.info(
    "預估損益已扣除估算賣出手續費與交易稅；手續費以 0.1425% 估算，"
    "ETF 交易稅以 0.1% 估算，一般股票交易稅以 0.3% 估算。"
)
st.info(
    "帳面報酬率：只看目前市值與成本的差異。\n\n"
    "扣除交易成本後報酬率：估算如果賣出後，扣除手續費與交易稅後的結果。\n\n"
    "持股佔比：看這檔標的在所有股票裡佔多少。\n\n"
    "總資產比例：看這檔標的在股票加現金總資產裡佔多少。"
)

settings = load_settings()
saved_cash = float(settings.get("cash", 0.0))
cash = st.number_input("現金", min_value=0.0, value=saved_cash, step=1000.0)

control_columns = st.columns([1, 1, 6])
if control_columns[0].button("保存現金"):
    settings["cash"] = cash
    save_settings(settings)
    st.success("現金已保存，下次開啟會自動帶入。")
if control_columns[1].button("重新讀取資料"):
    st.cache_data.clear()
    st.rerun()

transactions_df, csv_error = read_holdings_csv(HOLDINGS_CSV_URL)

if transactions_df is not None:
    st.session_state["last_successful_transactions_df"] = transactions_df.copy()
    save_transactions_cache(transactions_df)
    st.success("Google 試算表讀取成功")
else:
    st.error(
        "Google 試算表 CSV 讀取失敗："
        + str(csv_error)
        + "\n\n可能原因：\n"
        + "- 網路暫時中斷\n"
        + "- Google 試算表 CSV 連結無法連線\n"
        + "- 公司防火牆或防毒軟體可能阻擋 Python 連線"
    )

    if "last_successful_transactions_df" in st.session_state:
        transactions_df = st.session_state["last_successful_transactions_df"].copy()
        st.warning("目前使用上一次成功讀取的資料")
    else:
        cached_transactions_df = load_transactions_cache()
        if cached_transactions_df is not None:
            transactions_df = cached_transactions_df
            st.session_state["last_successful_transactions_df"] = transactions_df.copy()
            st.warning("目前使用本機上一次成功讀取的快取資料")
        else:
            st.error("尚未有成功讀取過的 Google CSV 資料，目前無法分析。")
            st.stop()

missing_columns = [column for column in REQUIRED_COLUMNS if column not in transactions_df.columns]

if missing_columns:
    st.error("交易紀錄缺少必要欄位：" + "、".join(missing_columns))
    st.stop()

working_df = transactions_df.copy()
for column in NUMERIC_COLUMNS:
    working_df[column] = parse_number(working_df[column])

working_df["交易分類"] = working_df["交易別"].apply(classify_transaction)
working_df["交易日期_排序"] = pd.to_datetime(working_df["交易日期"], errors="coerce")
working_df["原始順序"] = range(len(working_df))
working_df = working_df.sort_values(["交易日期_排序", "原始順序"], na_position="last")

included_transactions_df = working_df[working_df["交易分類"].isin(["買進", "賣出"])]
excluded_transactions_df = working_df[working_df["交易分類"] == "未納入"]
holdings_df = build_holdings(included_transactions_df)
holdings_df = add_price_analysis(holdings_df, cash)

real_estate_raw_df, real_estate_error = read_real_estate_csv()
real_estate_status_message = None
if real_estate_raw_df is not None:
    missing_real_estate_base_columns = [
        column for column in REAL_ESTATE_BASE_REQUIRED_COLUMNS if column not in real_estate_raw_df.columns
    ]
    has_new_real_estate_schedule = all(
        column in real_estate_raw_df.columns for column in REAL_ESTATE_NEW_SCHEDULE_COLUMNS
    )
    has_old_real_estate_schedule = all(
        column in real_estate_raw_df.columns for column in REAL_ESTATE_OLD_SCHEDULE_COLUMNS
    )
    if missing_real_estate_base_columns:
        real_estate_df = empty_real_estate_analysis()
        real_estate_status_message = "不動產資料讀取失敗：缺少必要欄位：" + "、".join(missing_real_estate_base_columns)
    elif not has_new_real_estate_schedule and not has_old_real_estate_schedule:
        real_estate_df = empty_real_estate_analysis()
        real_estate_status_message = (
            "不動產資料讀取失敗：請提供「貸款起始日期」與「貸款年限」，"
            "或保留舊欄位「剩餘期數」作為備援。"
        )
    else:
        real_estate_df = build_real_estate_analysis(real_estate_raw_df)
else:
    real_estate_df = empty_real_estate_analysis()
    real_estate_status_message = "不動產資料讀取失敗：" + str(real_estate_error)

stock_market_value = holdings_df["目前市值"].sum(skipna=True)
total_cost = holdings_df["總投入成本"].sum()
total_unrealized_profit = holdings_df["未實現損益"].sum(skipna=True)
total_estimated_profit = holdings_df["預估損益"].sum(skipna=True)
overall_return = total_unrealized_profit / total_cost if total_cost else 0
estimated_return = total_estimated_profit / total_cost if total_cost else 0
total_assets = stock_market_value + cash
cash_ratio = cash / total_assets if total_assets else 0

real_estate_value_total = real_estate_df["房產現值"].sum() if not real_estate_df.empty else 0
future_mortgage_payment_total = real_estate_df["未來房貸總支出"].sum() if not real_estate_df.empty else 0
monthly_mortgage_total = real_estate_df["月繳金額"].sum() if not real_estate_df.empty else 0
yearly_mortgage_total = real_estate_df["每年房貸支出"].sum() if not real_estate_df.empty else 0
conservative_balance_total = (
    real_estate_df["現值扣未來房貸支出後餘額"].sum() if not real_estate_df.empty else 0
)
overall_asset_total = stock_market_value + cash + real_estate_value_total
estimated_net_assets = overall_asset_total - future_mortgage_payment_total
overall_loan_pressure_ratio = (
    future_mortgage_payment_total / real_estate_value_total if real_estate_value_total else pd.NA
)
real_estate_asset_ratio = real_estate_value_total / overall_asset_total if overall_asset_total else pd.NA
stock_cash_total = stock_market_value + cash
mortgage_buffer_months = cash / monthly_mortgage_total if monthly_mortgage_total else pd.NA
mortgage_safety_level_12m = monthly_mortgage_total * 12
cash_gap_to_12m_mortgage = mortgage_safety_level_12m - cash

st.header("現金流與資產總覽")
overall_row_1 = st.columns(4)
overall_row_1[0].metric("股票市值", format_currency(stock_market_value))
overall_row_1[1].metric("現金", format_currency(cash))
overall_row_1[2].metric("房產現值合計", format_currency(real_estate_value_total))
overall_row_1[3].metric("總資產", format_currency(overall_asset_total))

overall_row_2 = st.columns(4)
overall_row_2[0].metric("每月房貸支出合計", format_currency(monthly_mortgage_total))
overall_row_2[1].metric("每年房貸支出合計", format_currency(yearly_mortgage_total))
overall_row_2[2].metric("股票現金合計", format_currency(stock_cash_total))
overall_row_2[3].metric("現金可支撐房貸月數", format_months(mortgage_buffer_months))

cashflow_row_3 = st.columns(2)
cashflow_row_3[0].metric("12 個月房貸安全水位", format_currency(mortgage_safety_level_12m))
cashflow_row_3[1].metric("現金缺口", format_currency(max(cash_gap_to_12m_mortgage, 0)))

if cash_gap_to_12m_mortgage > 0:
    st.warning(f"距離 12 個月房貸安全水位仍差 {format_currency(cash_gap_to_12m_mortgage)} 元。")
else:
    st.success("現金已達 12 個月房貸安全水位。")

st.caption(
    "未來房貸總支出 = 月繳金額 × 自動計算剩餘期數，包含未來利息，不等於銀行貸款本金餘額。"
    "主要用來評估現金流壓力，不代表銀行實際貸款本金餘額。"
)

st.header("投資資產總覽")
overview_row_1 = st.columns(3)
overview_row_1[0].metric("股票與現金合計", format_currency(total_assets))
overview_row_1[1].metric("股票總市值", format_currency(stock_market_value))
overview_row_1[2].metric("現金", format_currency(cash))

overview_row_2 = st.columns(3)
overview_row_2[0].metric("帳面損益", format_currency(total_unrealized_profit))
overview_row_2[1].metric("整體帳面報酬率", format_percent(overall_return))
overview_row_2[2].metric("預估實際損益", format_currency(total_estimated_profit))

st.header("不動產與貸款狀況")
st.caption(
    "未來房貸總支出 = 月繳金額 × 自動計算剩餘期數，包含未來利息，不等於銀行貸款本金餘額。"
    "主要用來評估現金流壓力，不代表銀行實際貸款本金餘額。"
)
st.caption(
    "剩餘期數由貸款起始日期與貸款年限自動估算，"
    "實際仍可能因提前還款、寬限期、利率調整而與銀行資料不同。"
)
if real_estate_status_message:
    st.error(real_estate_status_message)
else:
    st.dataframe(build_display_real_estate(real_estate_df), use_container_width=True)
    with st.expander("進階不動產估算資料"):
        st.dataframe(build_display_advanced_real_estate(real_estate_df), use_container_width=True)
        st.table(build_real_estate_summary_table(real_estate_value_total, future_mortgage_payment_total))

real_estate_summary = {
    "monthly_mortgage_total": monthly_mortgage_total,
    "mortgage_buffer_months": mortgage_buffer_months,
    "stock_cash_total": stock_cash_total,
    "mortgage_safety_level_12m": mortgage_safety_level_12m,
}

st.header("今日重點結論")
for level, message in build_today_conclusions(
    holdings_df,
    total_assets,
    cash_ratio,
    overall_return,
    real_estate_summary,
):
    if level == "error":
        st.error(message)
    elif level == "warning":
        st.warning(message)
    elif level == "success":
        st.success(message)
    else:
        st.info(message)

st.header("買入 / 賣出模擬器")
st.caption("集中度提醒：個股 15% 提醒、20% 不建議加碼；ETF 30% 提醒、35% 不建議加碼。")
simulator_columns = st.columns([1, 1, 1])
simulation_action = simulator_columns[0].selectbox("操作類型", ["買進", "賣出", "只查詢"])
simulation_stock_id = simulator_columns[1].text_input("股票代號", value="", placeholder="例如：0050、2330、00679B")

simulation_stock_id = simulation_stock_id.strip().upper()
current_holding = find_holding(holdings_df, simulation_stock_id) if simulation_stock_id else None

if simulation_stock_id:
    lookup_price = (
        float(current_holding["現價"])
        if current_holding is not None and not pd.isna(current_holding["現價"])
        else get_current_price(simulation_stock_id)
    )

    if lookup_price is None or pd.isna(lookup_price):
        st.error("無法取得此股票現價，請確認代號是否正確。")

    if current_holding is not None:
        st.subheader("已持有標的分析")
        held_status = pd.DataFrame(
            [
                {
                    "股票名稱": current_holding["股票名稱"],
                    "目前持有股數": current_holding["持有股數"],
                    "平均成本": format_price(current_holding["平均成本"]),
                    "現價": format_price(current_holding["現價"]),
                    "未實現損益": format_currency(current_holding["未實現損益"]),
                    "帳面報酬率": format_percent(current_holding["報酬率"]),
                    "持股佔比": format_percent(current_holding["持股佔比"]),
                    "操作建議": current_holding["操作建議"],
                    "建議理由": current_holding["建議理由"],
                }
            ]
        )
        st.dataframe(held_status, use_container_width=True)
        st.info(f"{simulation_stock_id} 已在持股中，目前建議是「{current_holding['操作建議']}」。{current_holding['建議理由']}")

        if simulation_action == "只查詢":
            st.success("這次只查詢，不改變現金與持股；可用上方資料判斷是否需要後續操作。")
        else:
            input_columns = st.columns(2)
            simulation_price = input_columns[0].number_input(
                "模擬成交價",
                min_value=0.0,
                value=float(lookup_price) if lookup_price else 0.0,
                step=0.01,
            )
            if simulation_action == "買進":
                simulation_lots = input_columns[1].number_input("預計買入張數", min_value=0.0, value=1.0, step=1.0)
                simulation_quantity = simulation_lots * SHARES_PER_LOT
            else:
                simulation_quantity = input_columns[1].number_input(
                    "預計賣出股數",
                    min_value=0.0,
                    value=0.0,
                    step=100.0,
                )

            if simulation_price > 0 and simulation_quantity > 0:
                simulation_market_value = simulation_price * simulation_quantity
                simulation_brokerage_fee, simulation_transaction_tax = estimate_trade_fee(
                    simulation_stock_id,
                    simulation_market_value,
                    simulation_action,
                )
                simulation_total_fee = simulation_brokerage_fee + simulation_transaction_tax

                if simulation_action == "買進":
                    simulated_cash = cash - simulation_market_value - simulation_brokerage_fee
                    plain_result = (
                        f"若買進 {simulation_quantity:,.0f} 股，約需 {format_currency(simulation_market_value + simulation_brokerage_fee)} 元，"
                        f"交易後現金約為 {format_currency(simulated_cash)} 元。"
                    )
                else:
                    holding_quantity = float(current_holding["持有股數"])
                    if simulation_quantity > holding_quantity:
                        st.warning("模擬賣出股數大於目前持有股數，請確認是否輸入過多。")
                    simulated_cash = cash + simulation_market_value - simulation_total_fee
                    plain_result = (
                        f"若賣出 {simulation_quantity:,.0f} 股，扣除估算費稅後，交易後現金約為 "
                        f"{format_currency(simulated_cash)} 元。"
                    )

                simulated_holdings = build_simulated_holdings(
                    holdings_df,
                    simulation_stock_id,
                    str(current_holding["股票名稱"]),
                    simulation_action,
                    simulation_quantity,
                    simulation_price,
                )
                simulated_stock_market_value = simulated_holdings["模擬後市值"].sum(skipna=True)
                simulated_total_assets = simulated_stock_market_value + simulated_cash
                simulated_holdings["類型"] = simulated_holdings.apply(classify_asset_type, axis=1)

                simulation_metrics = st.columns(5)
                simulation_metrics[0].metric("模擬交易金額", format_currency(simulation_market_value))
                simulation_metrics[1].metric("估算手續費", format_currency(simulation_brokerage_fee))
                simulation_metrics[2].metric("估算交易稅", format_currency(simulation_transaction_tax))
                simulation_metrics[3].metric("模擬後現金", format_currency(simulated_cash))
                simulation_metrics[4].metric("模擬後總資產", format_currency(simulated_total_assets))

                st.info(plain_result)
                if simulation_action == "買進":
                    render_mortgage_cashflow_check(simulated_cash, monthly_mortgage_total)

                no_add_rows = simulated_holdings[
                    (
                        (simulated_holdings["類型"].eq("個股"))
                        & (simulated_holdings["模擬後持股佔比"] >= STOCK_NO_ADD_THRESHOLD)
                    )
                    | (
                        (simulated_holdings["類型"].eq("ETF / 債券 ETF"))
                        & (simulated_holdings["模擬後持股佔比"] >= ETF_NO_ADD_THRESHOLD)
                    )
                ]
                warning_rows = simulated_holdings[
                    (
                        (simulated_holdings["類型"].eq("個股"))
                        & (simulated_holdings["模擬後持股佔比"] >= STOCK_CONCENTRATION_WARNING_THRESHOLD)
                    )
                    | (
                        (simulated_holdings["類型"].eq("ETF / 債券 ETF"))
                        & (simulated_holdings["模擬後持股佔比"] >= ETF_CONCENTRATION_WARNING_THRESHOLD)
                    )
                ]

                if not no_add_rows.empty:
                    names = "、".join(no_add_rows["股票代號"].astype(str) + " " + no_add_rows["股票名稱"].astype(str))
                    st.warning("模擬後部位偏集中，" + names + " 已達不建議加碼門檻。")
                elif not warning_rows.empty:
                    names = "、".join(warning_rows["股票代號"].astype(str) + " " + warning_rows["股票名稱"].astype(str))
                    st.warning("模擬後需要留意集中度：" + names + " 的比重偏高。")
                else:
                    st.success("模擬後沒有觸發集中度警訊。")

                if simulated_cash < 0:
                    st.warning("模擬後現金會變成負數，代表資金不足或需要降低交易金額。")

                st.dataframe(build_simulation_display(simulated_holdings), use_container_width=True)
    else:
        st.subheader("未持有標的買入分析")
        lookup_name = get_stock_name(simulation_stock_id) or "未持有標的"
        unheld_asset_type = classify_asset_type(pd.Series({"股票代號": simulation_stock_id, "股票名稱": lookup_name}))
        st.dataframe(
            build_unheld_status_display(simulation_stock_id, lookup_name, unheld_asset_type, lookup_price),
            use_container_width=True,
        )

        if simulation_action == "賣出":
            st.warning("這檔目前不在持股中，無法模擬賣出；可改用買進或只查詢。")
        elif simulation_action == "只查詢":
            st.info("這檔目前未持有，只能先依現價、現金與配置比例做初步觀察。")
        else:
            planned_buy_columns = st.columns(2)
            planned_buy_lots = planned_buy_columns[0].number_input("預計買入張數", min_value=0.0, value=0.0, step=1.0)
            planned_buy_amount_input = planned_buy_columns[1].number_input(
                "預計投入金額",
                min_value=0.0,
                value=0.0,
                step=1000.0,
            )
            planned_buy_amount = planned_buy_amount_input
            if planned_buy_lots > 0 and lookup_price is not None and not pd.isna(lookup_price):
                planned_buy_amount = planned_buy_lots * SHARES_PER_LOT * lookup_price

            if planned_buy_amount > 0:
                analysis = generate_unheld_buy_analysis(
                    simulation_stock_id,
                    lookup_name,
                    unheld_asset_type,
                    lookup_price,
                    planned_buy_amount,
                    cash,
                    stock_market_value,
                )
                analysis_metrics = st.columns(7)
                analysis_metrics[0].metric("預計買入張數", format_price(planned_buy_lots))
                analysis_metrics[1].metric("預估可買張數", format_price(analysis["預估可買張數"]))
                analysis_metrics[2].metric("預估可買股數", format_price(analysis["預估可買股數"]))
                analysis_metrics[3].metric("買入後該股票市值", format_currency(analysis["買入後該股票市值"]))
                analysis_metrics[4].metric("買入後現金", format_currency(analysis["買入後現金"]))
                analysis_metrics[5].metric("買入後總資產", format_currency(analysis["買入後總資產"]))
                analysis_metrics[6].metric("買入後佔總資產比例", format_percent(analysis["買入後該股票佔總資產比例"]))

                if any(keyword in analysis["建議"] for keyword in ["不建議", "偏高", "過高", "不足"]):
                    st.warning(analysis["建議"] + "：" + analysis["建議理由"])
                else:
                    st.info(analysis["建議"] + "：" + analysis["建議理由"])
                render_mortgage_cashflow_check(analysis["買入後現金"], monthly_mortgage_total)

st.header("需要注意的標的")
watchlist_df = build_watchlist(holdings_df)
if watchlist_df.empty:
    st.success("目前沒有標的觸發注意條件。")
else:
    st.dataframe(watchlist_df, use_container_width=True)

st.header("目前持股明細")
st.caption("持股佔比：佔股票總市值比例。總資產比例：佔股票市值＋現金的比例。")
with st.expander("查看完整持股明細"):
    st.dataframe(build_display_holdings(holdings_df), use_container_width=True)

st.header("未納入持股計算的交易")
with st.expander("查看未納入持股計算的交易"):
    st.dataframe(
        excluded_transactions_df.drop(columns=["交易日期_排序", "原始順序"], errors="ignore"),
        use_container_width=True,
    )

st.header("原始交易紀錄")
with st.expander("查看原始交易紀錄"):
    st.dataframe(transactions_df, use_container_width=True)
