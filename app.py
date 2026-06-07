# -*- coding: utf-8 -*-
import json
import re
import subprocess
from html import escape
from io import StringIO
from pathlib import Path
from urllib.parse import quote
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
ETF_TYPE_CODES = {"0050", "00679B", "00687B", "00919", "00929", "00878", "00885", "00830", "006208", "00881", "00927"}
STOCK_ALIASES = {
    "群創": "3481",
    "群創光電": "3481",
    "聯電": "2303",
    "聯華電子": "2303",
    "台積電": "2330",
    "台積": "2330",
    "中華": "2204",
    "中華車": "2204",
    "元大台灣50": "0050",
    "台灣50": "0050",
    "群益台灣精選高息": "00919",
    "群益高息": "00919",
    "國泰費城半導體": "00830",
    "費半": "00830",
}
BOND_THEME_CODES = {"00679B", "00687B"}
BROAD_MARKET_ETF_CODES = {"0050", "006208", "00850", "00922"}
SEMICONDUCTOR_ETF_CODES = {"00830", "00881", "00927"}
AI_ELECTRONICS_CODES = {
    "0050",
    "00830",
    "00881",
    "00927",
    "2303",
    "2330",
    "3481",
    "2204",
}
AI_ELECTRONICS_KEYWORDS = ["半導體", "電子", "AI", "晶片", "台積", "聯電", "群創", "費城半導體"]
DEFENSIVE_KEYWORDS = ["高息", "高股息", "債", "美債", "公司債"]
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
    quote = get_market_quote(stock_id)
    return quote["price"]


@st.cache_data(ttl=3600)
def get_price_history(stock_id, period="3mo", interval="1d"):
    stock_id = str(stock_id).strip().upper()

    for suffix in [".TW", ".TWO"]:
        ticker_symbol = f"{stock_id}{suffix}"
        yahoo_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"
        params = {"range": period, "interval": interval}
        headers = {"User-Agent": "Mozilla/5.0"}

        for request_getter in ["requests", "curl_cffi"]:
            try:
                if request_getter == "requests":
                    session = requests.Session()
                    session.trust_env = False
                    response = session.get(yahoo_url, params=params, headers=headers, timeout=CSV_READ_TIMEOUT_SECONDS)
                else:
                    from curl_cffi import requests as curl_requests

                    response = curl_requests.get(
                        yahoo_url,
                        params=params,
                        headers=headers,
                        timeout=CSV_READ_TIMEOUT_SECONDS,
                        impersonate="chrome",
                    )

                response.raise_for_status()
                result = response.json().get("chart", {}).get("result") or []
                closes = (result[0].get("indicators", {}).get("quote") or [{}])[0].get("close") or []
                prices = [float(value) for value in closes if value and float(value) > 0]
                if len(prices) >= 2:
                    return prices[-60:]
            except Exception:
                continue

    if yf is None:
        return []

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

            history = ticker.history(period=period, interval=interval, auto_adjust=False)
        except Exception:
            continue

        if history.empty or "Close" not in history.columns:
            continue

        close_prices = history["Close"].dropna()
        close_prices = close_prices[close_prices > 0]
        if len(close_prices) >= 2:
            return [float(value) for value in close_prices.tail(60).tolist()]

    return []


def build_sparkline_svg(prices, positive=True):
    if len(prices) < 2:
        return '<div class="sparkline-empty">走勢暫無資料</div>'

    width = 260
    height = 62
    padding = 5
    min_price = min(prices)
    max_price = max(prices)
    price_range = max_price - min_price
    if price_range == 0:
        price_range = max_price if max_price else 1

    points = []
    for index, price in enumerate(prices):
        x = padding + index * (width - padding * 2) / (len(prices) - 1)
        y = height - padding - ((price - min_price) / price_range) * (height - padding * 2)
        points.append(f"{x:.1f},{y:.1f}")

    line_color = "#e9465b" if positive else "#0fa968"
    fill_color = "rgba(233, 70, 91, 0.12)" if positive else "rgba(15, 169, 104, 0.12)"
    area_points = f"{padding},{height - padding} " + " ".join(points) + f" {width - padding},{height - padding}"
    return f"""
        <svg class="sparkline" viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" aria-label="最近 3 個月真實收盤價走勢">
            <polyline points="{area_points}" fill="{fill_color}" stroke="none"></polyline>
            <polyline points="{' '.join(points)}" fill="none" stroke="{line_color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"></polyline>
        </svg>
    """


@st.cache_data(ttl=900)
def get_finmind_quote(stock_id):
    stock_id = str(stock_id).strip().upper()
    start_date = (pd.Timestamp.today().normalize() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    finmind_url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start_date,
    }

    for request_getter in ["requests", "curl_cffi"]:
        try:
            if request_getter == "requests":
                session = requests.Session()
                session.trust_env = False
                response = session.get(finmind_url, params=params, timeout=CSV_READ_TIMEOUT_SECONDS)
            else:
                from curl_cffi import requests as curl_requests

                response = curl_requests.get(
                    finmind_url,
                    params=params,
                    timeout=CSV_READ_TIMEOUT_SECONDS,
                    impersonate="chrome",
                )

            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or []
            if not data:
                continue
            rows = sorted(data, key=lambda item: item.get("date", ""))
            latest = rows[-1]
            close_price = latest.get("close")
            if close_price and float(close_price) > 0:
                return {
                    "price": float(close_price),
                    "date": latest.get("date", ""),
                    "volume": latest.get("Trading_Volume"),
                }
        except Exception:
            continue
    return None


@st.cache_data(ttl=300)
def get_market_quote(stock_id):
    stock_id = str(stock_id).strip().upper()
    quote = {
        "price": None,
        "source": None,
        "is_fallback": False,
        "message": "行情資料暫時無法更新",
    }

    twse_info = fetch_twse_stock_info(stock_id)
    twse_price = None
    twse_price_key = None
    if twse_info:
        for key in ["z", "pz"]:
            value = twse_info.get(key)
            try:
                parsed_price = float(value)
            except (TypeError, ValueError):
                continue
            if parsed_price > 0:
                twse_price = parsed_price
                twse_price_key = key
                break

    if twse_price is not None:
        if twse_price_key == "z":
            quote.update(
                {
                    "price": twse_price,
                    "source": "TWSE MIS 即時成交價",
                    "message": "已使用 TWSE MIS 即時或近即時行情。",
                }
            )
        else:
            quote.update(
                {
                    "price": twse_price,
                    "source": "TWSE MIS 近即時參考價",
                    "is_fallback": True,
                    "message": "即時成交價暫時不可用，已使用 TWSE MIS 近即時參考價。",
                }
            )
        return quote

    if yf is None:
        finmind_quote = get_finmind_quote(stock_id)
        if finmind_quote:
            quote.update(
                {
                    "price": finmind_quote["price"],
                    "source": "FinMind 最近收盤價",
                    "is_fallback": True,
                    "message": "即時行情暫時不可用，已使用 FinMind 最近收盤價。",
                }
            )
        return quote

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
        except Exception:
            continue

        try:
            fast_info = ticker.fast_info
            for key in ["lastPrice", "regularMarketPreviousClose", "previousClose"]:
                price = fast_info.get(key)
                if price and float(price) > 0:
                    is_fallback = key != "lastPrice"
                    quote.update(
                        {
                            "price": float(price),
                            "source": "Yahoo Finance 近即時價格" if not is_fallback else "Yahoo Finance 最近收盤價",
                            "is_fallback": is_fallback,
                            "message": (
                                "已使用 Yahoo Finance 近即時行情。"
                                if not is_fallback
                                else "即時行情暫時不可用，已使用 Yahoo Finance 最近收盤價。"
                            ),
                        }
                    )
                    return quote
        except Exception:
            pass

        try:
            history = ticker.history(period="5d", interval="1d", auto_adjust=False)
        except Exception:
            continue

        if not history.empty and "Close" in history.columns:
            close_prices = history["Close"].dropna()
            if not close_prices.empty:
                price = float(close_prices.iloc[-1])
                if price > 0:
                    quote.update(
                        {
                            "price": price,
                            "source": "Yahoo Finance 最近收盤價",
                            "is_fallback": True,
                            "message": "即時行情暫時不可用，已使用 Yahoo Finance 最近收盤價。",
                        }
                    )
                    return quote

    finmind_quote = get_finmind_quote(stock_id)
    if finmind_quote:
        quote.update(
            {
                "price": finmind_quote["price"],
                "source": "FinMind 最近收盤價",
                "is_fallback": True,
                "message": "即時行情暫時不可用，已使用 FinMind 最近收盤價。",
            }
        )
        return quote

    return quote


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


def format_compact_currency(value):
    if pd.isna(value):
        return "\u7121\u6cd5\u53d6\u5f97"
    value = float(value)
    abs_value = abs(value)
    if abs_value >= 100000000:
        return f"{value / 100000000:,.2f}\u5104"
    if abs_value >= 10000:
        return f"{value / 10000:,.1f}\u842c"
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


def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def extract_stock_from_question(question, holdings):
    question = str(question).strip().upper()
    code_match = re.search(r"(?<![A-Z0-9])(\d{4,6}[A-Z]?)(?![A-Z0-9])", question)
    if code_match:
        return code_match.group(1)

    for alias, stock_id in STOCK_ALIASES.items():
        if alias.upper() in question:
            return stock_id

    if holdings.empty:
        return ""

    for _, row in holdings.iterrows():
        stock_name = str(row["\u80a1\u7968\u540d\u7a31"]).strip().upper()
        stock_id = str(row["\u80a1\u7968\u4ee3\u865f"]).strip().upper()
        if stock_name and stock_name in question:
            return stock_id

    return ""


def detect_question_intent(question):
    text = str(question)
    if any(keyword in text for keyword in ["賣", "減碼", "停損", "出場"]):
        return "sell"
    if any(keyword in text for keyword in ["適合我", "適合", "可以嗎"]):
        return "fit"
    if any(keyword in text for keyword in ["加碼", "買", "進場", "佈局", "布局"]):
        return "buy"
    if any(keyword in text for keyword in ["大跌", "崩", "下跌", "怎麼辦", "恐慌", "殺盤"]):
        return "market"
    return "hold"


def append_unique(items, item):
    if item and item not in items:
        items.append(item)


def clamp(value, minimum=0, maximum=100):
    return max(minimum, min(maximum, value))


def normalize_ratio(value):
    if pd.isna(value):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def classify_stock_theme(stock_id, stock_name, asset_type=None):
    stock_id = str(stock_id).strip().upper()
    stock_name = str(stock_name).strip()
    text = f"{stock_id} {stock_name}".upper()

    if stock_id in BOND_THEME_CODES or "債" in stock_name:
        return {"theme": "bond", "label": "債券/防守", "is_ai_electronics": False, "is_defensive": True}
    if stock_id in SEMICONDUCTOR_ETF_CODES or any(keyword.upper() in text for keyword in ["費半", "半導體", "晶片"]):
        return {"theme": "semiconductor", "label": "半導體/AI", "is_ai_electronics": True, "is_defensive": False}
    if stock_id in AI_ELECTRONICS_CODES or any(keyword.upper() in text for keyword in AI_ELECTRONICS_KEYWORDS):
        return {"theme": "electronics", "label": "電子/AI", "is_ai_electronics": True, "is_defensive": False}
    if stock_id in BROAD_MARKET_ETF_CODES or "台灣50" in stock_name:
        return {"theme": "broad_market", "label": "台股大盤型", "is_ai_electronics": True, "is_defensive": False}
    if any(keyword in stock_name for keyword in DEFENSIVE_KEYWORDS) or asset_type == "ETF / 債券 ETF":
        return {"theme": "defensive", "label": "收益/防守", "is_ai_electronics": False, "is_defensive": True}
    return {"theme": "single_stock", "label": "個股", "is_ai_electronics": False, "is_defensive": False}


@st.cache_data(ttl=900)
def get_yahoo_chart_snapshot(stock_id, period="1y", interval="1d"):
    stock_id = str(stock_id).strip().upper()
    symbol_candidates = [stock_id] if stock_id.startswith("^") or "=" in stock_id else [f"{stock_id}.TW", f"{stock_id}.TWO"]
    headers = {"User-Agent": "Mozilla/5.0"}

    for ticker_symbol in symbol_candidates:
        yahoo_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"
        params = {"range": period, "interval": interval}

        for request_getter in ["requests", "curl_cffi"]:
            try:
                if request_getter == "requests":
                    session = requests.Session()
                    session.trust_env = False
                    response = session.get(yahoo_url, params=params, headers=headers, timeout=CSV_READ_TIMEOUT_SECONDS)
                else:
                    from curl_cffi import requests as curl_requests

                    response = curl_requests.get(
                        yahoo_url,
                        params=params,
                        headers=headers,
                        timeout=CSV_READ_TIMEOUT_SECONDS,
                        impersonate="chrome",
                    )

                response.raise_for_status()
                result = response.json().get("chart", {}).get("result") or []
                if not result:
                    continue

                quote_data = (result[0].get("indicators", {}).get("quote") or [{}])[0]
                closes = [float(value) for value in quote_data.get("close") or [] if value and float(value) > 0]
                volumes = [float(value) for value in quote_data.get("volume") or [] if value is not None]
                meta = result[0].get("meta", {}) or {}
                if len(closes) >= 2:
                    return {"symbol": ticker_symbol, "closes": closes, "volumes": volumes, "meta": meta}
            except Exception:
                continue

    return {"symbol": "", "closes": [], "volumes": [], "meta": {}}


@st.cache_data(ttl=3600)
def get_yahoo_valuation_snapshot(stock_id):
    if yf is None:
        return {"pe": None}

    stock_id = str(stock_id).strip().upper()
    for suffix in [".TW", ".TWO"]:
        try:
            try:
                from curl_cffi import requests as curl_requests

                session = curl_requests.Session(impersonate="chrome")
                session.trust_env = False
                ticker = yf.Ticker(f"{stock_id}{suffix}", session=session)
            except Exception:
                ticker = yf.Ticker(f"{stock_id}{suffix}")

            info = ticker.info or {}
            pe = info.get("trailingPE") or info.get("forwardPE")
            return {"pe": float(pe) if pe else None}
        except Exception:
            continue
    return {"pe": None}


def summarize_chart_snapshot(chart_snapshot):
    closes = chart_snapshot.get("closes") or []
    volumes = chart_snapshot.get("volumes") or []
    if len(closes) < 2:
        return {
            "change_30d": None,
            "year_line_distance": None,
            "volume_ratio": None,
            "heat_label": "行情資料不足",
            "volume_label": "量能資料不足",
            "trend_label": "趨勢資料不足",
        }

    latest = closes[-1]
    base_30 = closes[-31] if len(closes) >= 31 else closes[0]
    change_30d = latest / base_30 - 1 if base_30 else None
    year_values = closes[-200:] if len(closes) >= 60 else closes
    year_average = sum(year_values) / len(year_values)
    year_line_distance = latest / year_average - 1 if year_average else None

    volume_ratio = None
    if len(volumes) >= 21:
        recent_volume = volumes[-1]
        average_volume = sum(volumes[-21:-1]) / 20
        if average_volume:
            volume_ratio = recent_volume / average_volume

    if change_30d is None:
        heat_label = "近期方向不明"
    elif change_30d >= 0.12:
        heat_label = "近期偏熱"
    elif change_30d <= -0.10:
        heat_label = "近期偏弱"
    else:
        heat_label = "近期平穩"

    if year_line_distance is None:
        trend_label = "趨勢資料不足"
    elif year_line_distance >= 0.12:
        trend_label = "價格站在長期均線上方，追價要保守"
    elif year_line_distance <= -0.12:
        trend_label = "價格低於長期平均，適合先觀察是否止穩"
    else:
        trend_label = "價格接近長期平均"

    if volume_ratio is None:
        volume_label = "量能資料不足"
    elif volume_ratio >= 1.8:
        volume_label = "成交量明顯放大，短線波動可能較高"
    elif volume_ratio <= 0.6:
        volume_label = "成交量偏低，買賣要更分批"
    else:
        volume_label = "成交量大致正常"

    return {
        "change_30d": change_30d,
        "year_line_distance": year_line_distance,
        "volume_ratio": volume_ratio,
        "heat_label": heat_label,
        "volume_label": volume_label,
        "trend_label": trend_label,
    }


def summarize_valuation(valuation_snapshot, asset_type):
    pe = valuation_snapshot.get("pe")
    if asset_type == "ETF / 債券 ETF":
        return "ETF 不以單一本益比判斷，重點看配置是否重複"
    if pe is None or pd.isna(pe):
        return "估值資料不足，先不要只靠價格判斷"
    if pe >= 28:
        return "估值偏高，較不適合追價"
    if pe <= 12:
        return "估值不算貴，但仍要看配置與現金"
    return "估值大致中性"


def build_user_risk_profile(holdings, cash, stock_market_value, overall_asset_total, monthly_mortgage_total, mortgage_buffer_months):
    cash_buffer_months = None if pd.isna(mortgage_buffer_months) else float(mortgage_buffer_months)
    stock_exposure_ratio = stock_market_value / overall_asset_total if overall_asset_total else 0
    cash_ratio = cash / overall_asset_total if overall_asset_total else 0
    max_single_ratio = 0.0
    max_single_name = ""
    electronics_ratio = 0.0

    if not holdings.empty:
        ratio_column = "\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"
        if ratio_column in holdings.columns:
            sorted_holdings = holdings.dropna(subset=[ratio_column]).sort_values(ratio_column, ascending=False)
            if not sorted_holdings.empty:
                top_row = sorted_holdings.iloc[0]
                max_single_ratio = normalize_ratio(top_row[ratio_column])
                max_single_name = f"{top_row['\u80a1\u7968\u4ee3\u865f']} {top_row['\u80a1\u7968\u540d\u7a31']}"

            for _, row in holdings.iterrows():
                theme = classify_stock_theme(row["\u80a1\u7968\u4ee3\u865f"], row["\u80a1\u7968\u540d\u7a31"], row.get("\u985e\u578b"))
                if theme["is_ai_electronics"]:
                    electronics_ratio += normalize_ratio(row.get(ratio_column, 0))

    conservative_mode = (
        stock_exposure_ratio > 0.75
        or max_single_ratio > 0.15
        or electronics_ratio > 0.50
        or (cash_buffer_months is not None and cash_buffer_months < 6)
    )

    notes = []
    if cash_buffer_months is not None and cash_buffer_months < 6:
        notes.append(f"現金可支撐 {format_months(cash_buffer_months)} 房貸，先偏保守。")
    if stock_exposure_ratio > 0.75:
        notes.append(f"股票總曝險約 {format_percent(stock_exposure_ratio)}，新增買進要更小筆。")
    if max_single_ratio > 0.15:
        notes.append(f"{max_single_name} 占比約 {format_percent(max_single_ratio)}，單一持股偏集中。")
    if electronics_ratio > 0.50:
        notes.append(f"電子/AI 相關曝險約 {format_percent(electronics_ratio)}，同類標的不要一次加太多。")
    if not notes:
        notes.append("現金、曝險與集中度目前沒有明顯失衡。")

    return {
        "cash_buffer_months": cash_buffer_months,
        "stock_exposure_ratio": stock_exposure_ratio,
        "cash_ratio": cash_ratio,
        "max_single_ratio": max_single_ratio,
        "max_single_name": max_single_name,
        "electronics_ratio": electronics_ratio,
        "conservative_mode": conservative_mode,
        "notes": notes,
    }


def build_stock_situation(stock_id, stock_name, asset_type, quote):
    chart = get_yahoo_chart_snapshot(stock_id)
    chart_summary = summarize_chart_snapshot(chart)
    valuation = summarize_valuation(get_yahoo_valuation_snapshot(stock_id), asset_type)
    theme = classify_stock_theme(stock_id, stock_name, asset_type)

    price = quote.get("price")
    if price is None and chart.get("closes"):
        price = chart["closes"][-1]

    if theme["theme"] in ["semiconductor", "electronics"]:
        market_label = "會跟電子與 AI 題材情緒連動"
    elif theme["is_defensive"]:
        market_label = "偏收益或防守型，重點是不要買到配置過重"
    else:
        market_label = "主要看個股本身與大盤情緒"

    return {
        "price": price,
        "theme": theme,
        "heat_label": chart_summary["heat_label"],
        "trend_label": chart_summary["trend_label"],
        "volume_label": chart_summary["volume_label"],
        "valuation_label": valuation,
        "market_label": market_label,
        "change_30d": chart_summary["change_30d"],
        "year_line_distance": chart_summary["year_line_distance"],
        "volume_ratio": chart_summary["volume_ratio"],
        "data_available": price is not None and not pd.isna(price),
    }


@st.cache_data(ttl=900)
def get_market_context():
    market_items = [
        ("^TWII", "台股大盤"),
        ("^SOX", "費半指數"),
        ("TX=F", "台指期"),
    ]
    summaries = []
    for symbol, label in market_items:
        snapshot = get_yahoo_chart_snapshot(symbol, period="3mo", interval="1d")
        closes = snapshot.get("closes") or []
        if len(closes) < 2:
            summaries.append({"label": label, "state": "資料暫時無法更新", "change": None})
            continue
        base = closes[-21] if len(closes) >= 21 else closes[0]
        change = closes[-1] / base - 1 if base else None
        if change is None:
            state = "資料不足"
        elif change >= 0.06:
            state = "近期偏熱"
        elif change <= -0.06:
            state = "近期偏弱"
        else:
            state = "近期平穩"
        summaries.append({"label": label, "state": state, "change": change})
    return summaries


def has_overlap_with_existing_holdings(target_theme, holdings):
    if holdings.empty:
        return False
    for _, row in holdings.iterrows():
        theme = classify_stock_theme(row["\u80a1\u7968\u4ee3\u865f"], row["\u80a1\u7968\u540d\u7a31"], row.get("\u985e\u578b"))
        if target_theme["theme"] == theme["theme"]:
            return True
        if target_theme["is_ai_electronics"] and theme["is_ai_electronics"]:
            return True
    return False


def calculate_suitability_score(user_profile, stock_situation, current_holding, intent, asset_ratio, return_rate):
    score = 78
    is_buy_like = intent in ["buy", "fit"]
    theme = stock_situation["theme"]
    change_30d = stock_situation.get("change_30d")
    year_distance = stock_situation.get("year_line_distance")
    volume_ratio = stock_situation.get("volume_ratio")

    if is_buy_like and user_profile["cash_buffer_months"] is not None and user_profile["cash_buffer_months"] < 6:
        score -= 18
    if is_buy_like and user_profile["stock_exposure_ratio"] > 0.75:
        score -= 14
    if is_buy_like and user_profile["electronics_ratio"] > 0.50 and theme["is_ai_electronics"]:
        score -= 12
    if asset_ratio > 0.15:
        score -= 16
    if current_holding is not None and is_buy_like and not pd.isna(return_rate) and return_rate > 0.20:
        score -= 12
    if change_30d is not None and change_30d > 0.12 and is_buy_like:
        score -= 10
    if year_distance is not None and year_distance > 0.12 and is_buy_like:
        score -= 8
    if volume_ratio is not None and volume_ratio > 1.8:
        score -= 5
    if theme["is_defensive"] and user_profile["stock_exposure_ratio"] > 0.65:
        score += 6
    if current_holding is not None and intent in ["hold", "sell"] and asset_ratio <= 0.15:
        score += 5
    return int(clamp(round(score), 0, 100))


def pick_decision_conclusion(intent, score, is_held, conservative_mode, asset_ratio, return_rate, stock_situation):
    is_hot = (stock_situation.get("change_30d") or 0) > 0.12 or (stock_situation.get("year_line_distance") or 0) > 0.12

    if intent == "sell":
        if is_held and (asset_ratio > 0.15 or (not pd.isna(return_rate) and return_rate < -0.15)):
            return "建議部分減碼"
        if is_held:
            return "建議續抱"
        return "可觀察等待"

    if intent == "market":
        return "等待"

    if intent in ["buy", "fit"]:
        if conservative_mode or score < 55:
            return "可觀察等待"
        if is_hot:
            return "不建議追價"
        if score >= 75:
            return "適合分批布局"
        return "可觀察等待"

    if is_held:
        if asset_ratio > 0.15 and score < 65:
            return "建議部分減碼"
        return "建議續抱"
    return "可觀察等待"


def build_decision_action(conclusion, price, avg_cost, is_held, cash_buffer_months):
    if price is None or pd.isna(price) or price <= 0:
        return "行情資料暫時無法更新，先不要下買賣決定。"

    if cash_buffer_months is not None and cash_buffer_months < 6 and conclusion in ["適合分批布局", "不建議追價", "可觀察等待"]:
        return "先保留現金，至少補到 6 個月房貸以上再考慮新買進。"

    if conclusion == "適合分批布局":
        return f"小量分批，第一筆不超過可用現金 10%，可等 {format_price(price * 0.97)} 附近再買。"
    if conclusion == "不建議追價":
        return f"先等回檔或整理，接近 {format_price(price * 0.95)} 以下再重新評估。"
    if conclusion == "建議部分減碼":
        return "先減碼 1/3 或把部位降到總資產 15% 以下，不要一次全出。"
    if conclusion == "建議續抱":
        review_price = avg_cost * 0.90 if is_held and avg_cost > 0 else price * 0.92
        return f"續抱觀察；若跌破 {format_price(review_price)} 或持股占比繼續升高，再檢查是否減碼。"
    return "等待價格與現金條件同時改善；不要重壓，也不要 All in。"


def build_decision_assistant_answer(
    question,
    holdings,
    cash,
    stock_market_value,
    overall_asset_total,
    monthly_mortgage_total,
    mortgage_buffer_months,
):
    question = str(question).strip()
    stock_id = extract_stock_from_question(question, holdings)
    intent = detect_question_intent(question)
    user_profile = build_user_risk_profile(
        holdings,
        cash,
        stock_market_value,
        overall_asset_total,
        monthly_mortgage_total,
        mortgage_buffer_months,
    )

    if not stock_id:
        market_context = get_market_context()
        market_notes = [f"{item['label']}：{item['state']}" for item in market_context if item["state"] != "資料暫時無法更新"]
        reasons = user_profile["notes"][:2]
        append_unique(reasons, market_notes[0] if market_notes else "行情資料暫時無法更新。")
        return {
            "stock_id": "",
            "stock_name": "整體市場",
            "quote": None,
            "conclusion": "等待",
            "suitability_score": 62 if user_profile["conservative_mode"] else 72,
            "score_help": "這是目前資產配置的行動適合度，不是市場漲跌預測。",
            "reasons": reasons[:3],
            "action": "先保留現金，不急著加碼；若大盤續跌，分 2 到 3 次小量處理。",
            "context": {
                "現金": format_currency(cash),
                "現金可支應房貸": format_months(user_profile["cash_buffer_months"]),
                "股票總曝險": format_percent(user_profile["stock_exposure_ratio"]),
                "電子/AI曝險": format_percent(user_profile["electronics_ratio"]),
            },
            "stock_summary": ["未指定標的，先用整體資產與市場狀態判斷。"] + market_notes[:2],
            "user_summary": user_profile["notes"][:3],
        }

    quote = get_market_quote(stock_id)
    current_holding = find_holding(holdings, stock_id)
    stock_name = str(current_holding["\u80a1\u7968\u540d\u7a31"]) if current_holding is not None else get_stock_name(stock_id) or stock_id
    asset_type = (
        str(current_holding["\u985e\u578b"])
        if current_holding is not None
        else classify_asset_type(pd.Series({"\u80a1\u7968\u4ee3\u865f": stock_id, "\u80a1\u7968\u540d\u7a31": stock_name}))
    )
    stock_situation = build_stock_situation(stock_id, stock_name, asset_type, quote)

    if not stock_situation["data_available"]:
        return {
            "stock_id": stock_id,
            "stock_name": stock_name,
            "quote": quote,
            "conclusion": "可觀察等待",
            "suitability_score": 50,
            "score_help": "行情資料不足時，適合度只代表保守預設。",
            "reasons": ["行情資料暫時無法更新。", "沒有可靠價格時，不適合做買賣決策。", user_profile["notes"][0]],
            "action": "等行情恢復後，再用現價、持倉成本、現金與曝險重新判斷。",
            "context": {
                "現金": format_currency(cash),
                "現金可支應房貸": format_months(user_profile["cash_buffer_months"]),
                "股票總曝險": format_percent(user_profile["stock_exposure_ratio"]),
            },
            "stock_summary": ["行情資料暫時無法更新"],
            "user_summary": user_profile["notes"][:3],
        }

    is_held = current_holding is not None
    price = stock_situation["price"]
    avg_cost = safe_float(current_holding["\u5e73\u5747\u6210\u672c"]) if is_held else 0
    market_value = safe_float(current_holding["\u76ee\u524d\u5e02\u503c"]) if is_held else 0
    unrealized_profit = safe_float(current_holding["\u672a\u5be6\u73fe\u640d\u76ca"]) if is_held else 0
    return_rate = current_holding["\u5831\u916c\u7387"] if is_held else pd.NA
    asset_ratio = market_value / overall_asset_total if overall_asset_total and is_held else 0
    target_theme = stock_situation["theme"]
    overlap = has_overlap_with_existing_holdings(target_theme, holdings) and not is_held

    score = calculate_suitability_score(user_profile, stock_situation, current_holding, intent, asset_ratio, return_rate)
    if overlap and intent in ["buy", "fit"]:
        score = int(clamp(score - 8))
    conclusion = pick_decision_conclusion(
        intent,
        score,
        is_held,
        user_profile["conservative_mode"],
        asset_ratio,
        return_rate,
        stock_situation,
    )

    reasons = []
    if is_held:
        append_unique(
            reasons,
            f"你已持有，成本 {format_price(avg_cost)}、現價 {format_price(price)}、帳面損益 {format_currency(unrealized_profit)}。",
        )
        append_unique(reasons, f"這檔占總資產 {format_percent(asset_ratio)}，股票總曝險 {format_percent(user_profile['stock_exposure_ratio'])}。")
    else:
        append_unique(reasons, f"你目前未持有，現價約 {format_price(price)}，新增買進會增加 {target_theme['label']} 曝險。")
        if overlap:
            append_unique(reasons, "它和現有持股主題有重疊，新增前要先想清楚是不是需要更多同類部位。")

    append_unique(reasons, f"{stock_situation['heat_label']}，{stock_situation['valuation_label']}。")
    for note in user_profile["notes"]:
        append_unique(reasons, note)
    if quote.get("is_fallback"):
        append_unique(reasons, quote["message"])

    action = build_decision_action(conclusion, price, avg_cost, is_held, user_profile["cash_buffer_months"])
    stock_summary = [
        f"現價約 {format_price(price)}",
        stock_situation["heat_label"],
        stock_situation["trend_label"],
        stock_situation["volume_label"],
        stock_situation["market_label"],
    ]

    return {
        "stock_id": stock_id,
        "stock_name": stock_name,
        "quote": quote,
        "conclusion": conclusion,
        "suitability_score": score,
        "score_help": "這是此標的適不適合你目前配置的分數，不是股票好壞分數。",
        "reasons": reasons[:3],
        "action": action,
        "context": {
            "目前價格": format_price(price),
            "行情來源": quote.get("source") or "Yahoo/最近收盤",
            "持有狀態": "已持有" if is_held else "未持有",
            "平均成本": format_price(avg_cost) if is_held else "未持有",
            "未實現損益": format_currency(unrealized_profit) if is_held else "未持有",
            "持股占總資產": format_percent(asset_ratio) if is_held else "未持有",
            "現金": format_currency(cash),
            "現金可支應房貸": format_months(user_profile["cash_buffer_months"]),
            "股票總曝險": format_percent(user_profile["stock_exposure_ratio"]),
            "電子/AI曝險": format_percent(user_profile["electronics_ratio"]),
        },
        "stock_summary": stock_summary[:5],
        "user_summary": user_profile["notes"][:3],
    }


def build_chat_action_line(conclusion, price, avg_cost, is_held, conservative_mode):
    if pd.isna(price) or price <= 0:
        return "先等待行情恢復，再做買賣判斷。"

    if conclusion == "建議買":
        target_price = price * 0.98
        if is_held and avg_cost > 0:
            target_price = min(target_price, avg_cost * 0.97)
        return f"只用小額分批，價格低於 {format_price(target_price)} 且現金仍高於 6 個月房貸時再買。"

    if conclusion == "不買":
        return f"除非現金補回 6 個月房貸以上，且價格回到 {format_price(price * 0.95)} 以下，否則先不加碼。"

    if conclusion == "減碼":
        return f"若反彈到 {format_price(price * 1.03)} 附近仍無法改善持股壓力，可分批減碼。"

    if conclusion == "續抱":
        stop_review_price = avg_cost * 0.90 if is_held and avg_cost > 0 else price * 0.92
        return f"續抱觀察；若跌破 {format_price(stop_review_price)}，重新檢查是否需要減碼。"

    if conservative_mode:
        return "先把現金補到 6 個月房貸以上，再評估下一筆買進。"
    return f"等待價格接近 {format_price(price * 0.97)}，或大盤止穩後再分批處理。"


def build_investment_assistant_answer(
    question,
    holdings,
    cash,
    stock_market_value,
    overall_asset_total,
    monthly_mortgage_total,
    mortgage_buffer_months,
):
    return build_decision_assistant_answer(
        question,
        holdings,
        cash,
        stock_market_value,
        overall_asset_total,
        monthly_mortgage_total,
        mortgage_buffer_months,
    )

    question = str(question).strip()
    stock_id = extract_stock_from_question(question, holdings)
    intent = detect_question_intent(question)
    cash_buffer_months = None if pd.isna(mortgage_buffer_months) else float(mortgage_buffer_months)
    conservative_mode = cash_buffer_months is not None and cash_buffer_months < 6
    stock_exposure_ratio = stock_market_value / overall_asset_total if overall_asset_total else 0
    cash_ratio_all_assets = cash / overall_asset_total if overall_asset_total else 0

    if not stock_id:
        reasons = []
        if conservative_mode:
            append_unique(reasons, f"現金只夠 {format_months(cash_buffer_months)} 房貸，先保守。")
        append_unique(reasons, f"股票總曝險約 {format_percent(stock_exposure_ratio)}。")
        append_unique(reasons, "遇到大跌時先控制節奏，不做重壓或 All in。")
        return {
            "stock_id": "",
            "stock_name": "整體市場",
            "quote": None,
            "conclusion": "等待",
            "reasons": reasons[:3],
            "action": "先保留至少 6 個月房貸現金；若要買，只分批且單筆不要讓現金低於安全水位。",
            "context": {
                "現金": format_currency(cash),
                "現金可支應房貸": format_months(cash_buffer_months),
                "股票總曝險": format_percent(stock_exposure_ratio),
            },
        }

    quote = get_market_quote(stock_id)
    price = quote["price"]
    current_holding = find_holding(holdings, stock_id)
    stock_name = (
        str(current_holding["\u80a1\u7968\u540d\u7a31"])
        if current_holding is not None
        else get_stock_name(stock_id) or stock_id
    )

    if price is None or pd.isna(price):
        return {
            "stock_id": stock_id,
            "stock_name": stock_name,
            "quote": quote,
            "conclusion": "等待",
            "reasons": ["行情資料暫時無法更新。", "沒有可靠價格時，不適合做買賣決策。"],
            "action": "等行情恢復後，再用目前價格、成本與現金水位重新判斷。",
            "context": {
                "現金": format_currency(cash),
                "現金可支應房貸": format_months(cash_buffer_months),
                "股票總曝險": format_percent(stock_exposure_ratio),
            },
        }

    is_held = current_holding is not None
    avg_cost = safe_float(current_holding["\u5e73\u5747\u6210\u672c"]) if is_held else 0
    market_value = safe_float(current_holding["\u76ee\u524d\u5e02\u503c"]) if is_held else 0
    unrealized_profit = safe_float(current_holding["\u672a\u5be6\u73fe\u640d\u76ca"]) if is_held else 0
    return_rate = current_holding["\u5831\u916c\u7387"] if is_held else pd.NA
    asset_ratio = market_value / overall_asset_total if overall_asset_total and is_held else 0
    asset_type = (
        str(current_holding["\u985e\u578b"])
        if is_held
        else classify_asset_type(pd.Series({"\u80a1\u7968\u4ee3\u865f": stock_id, "\u80a1\u7968\u540d\u7a31": stock_name}))
    )
    concentration_limit = ETF_NO_ADD_THRESHOLD if asset_type == "ETF / \u50b5\u5238 ETF" else STOCK_NO_ADD_THRESHOLD
    high_concentration = asset_ratio >= concentration_limit

    reasons = []
    if conservative_mode:
        append_unique(reasons, f"現金只夠 {format_months(cash_buffer_months)} 房貸，預設偏保守。")
    elif monthly_mortgage_total:
        append_unique(reasons, f"現金可支應約 {format_months(cash_buffer_months)} 房貸。")
    else:
        append_unique(reasons, f"現金水位約占總資產 {format_percent(cash_ratio_all_assets)}。")

    if is_held:
        append_unique(
            reasons,
            f"成本 {format_price(avg_cost)}、現價 {format_price(price)}、未實現損益 {format_currency(unrealized_profit)}。",
        )
        append_unique(reasons, f"{stock_name} 占總資產約 {format_percent(asset_ratio)}，股票總曝險約 {format_percent(stock_exposure_ratio)}。")
    else:
        append_unique(reasons, f"{stock_name} 目前價格約 {format_price(price)}，但尚未在持股內。")
        append_unique(reasons, f"股票總曝險約 {format_percent(stock_exposure_ratio)}。")

    if quote["is_fallback"]:
        append_unique(reasons, quote["message"])

    return_rate_value = safe_float(return_rate, pd.NA)
    if intent == "sell" and is_held:
        if high_concentration or (not pd.isna(return_rate_value) and return_rate_value < -0.15):
            conclusion = "減碼"
        else:
            conclusion = "續抱"
    elif intent == "buy":
        if conservative_mode or high_concentration:
            conclusion = "不買"
        elif is_held and not pd.isna(return_rate_value) and return_rate_value > 0.20:
            conclusion = "等待"
        else:
            conclusion = "建議買"
    elif intent == "market":
        conclusion = "等待"
    else:
        conclusion = "等待" if conservative_mode or not is_held else "續抱"

    if conclusion in ["建議買", "不買"]:
        append_unique(reasons, "即使條件符合，也只適合小額分批，不建議重壓或 All in。")

    return {
        "stock_id": stock_id,
        "stock_name": stock_name,
        "quote": quote,
        "conclusion": conclusion,
        "reasons": reasons[:3],
        "action": build_chat_action_line(conclusion, price, avg_cost, is_held, conservative_mode),
        "context": {
            "目前價格": format_price(price),
            "行情來源": quote["source"] or "無",
            "平均成本": format_price(avg_cost) if is_held else "未持有",
            "未實現損益": format_currency(unrealized_profit) if is_held else "未持有",
            "持股占總資產": format_percent(asset_ratio) if is_held else "未持有",
            "現金": format_currency(cash),
            "現金可支應房貸": format_months(cash_buffer_months),
            "股票總曝險": format_percent(stock_exposure_ratio),
        },
    }


def render_assistant_answer_card(answer, compact=False):
    target_title = f"{answer.get('stock_name', '')} {answer.get('stock_id', '')}".strip() or "整體市場"
    score = answer.get("suitability_score")
    score_html = (
        f'<div class="score-pill" title="{escape(str(answer.get("score_help", "適合度分數")))}">適合度 {int(score)}/100</div>'
        if score is not None
        else ""
    )
    reasons_html = "".join(
        f'<div class="assistant-reason">{escape(str(reason))}</div>'
        for reason in answer.get("reasons", [])[:3]
    )
    stock_summary = answer.get("stock_summary") or []
    user_summary = answer.get("user_summary") or []
    summary_items = stock_summary[:3] + user_summary[:3]
    if compact:
        summary_items = summary_items[:3]
    summary_html = "".join(
        f'<div class="assistant-summary-item">{escape(str(item))}</div>'
        for item in summary_items
    )
    context_items = list((answer.get("context") or {}).items())
    if compact:
        context_items = context_items[:4]
    context_html = "".join(
        f'<div class="assistant-context-item"><span>{escape(str(key))}</span><strong>{escape(str(value))}</strong></div>'
        for key, value in context_items
    )

    st.markdown(
        f"""
        <div class="assistant-answer-card">
            <div class="assistant-answer-title">
                <h4>{escape(target_title)}</h4>
                {score_html}
            </div>
            <div class="assistant-result-card">
                <div>
                    <div class="result-label">結論</div>
                    <div class="result-conclusion">{escape(str(answer.get('conclusion', '等待')))}</div>
                </div>
            </div>
            <div class="assistant-section-label">原因</div>
            <div class="assistant-reasons">{reasons_html}</div>
            <div class="assistant-action">行動：{escape(str(answer.get('action', '先等待資料更新。')))}</div>
            <div class="assistant-section-label">判斷摘要</div>
            <div class="assistant-summary-grid">{summary_html}</div>
            <div class="assistant-section-label">使用資料</div>
            <div class="assistant-context-grid">{context_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_investment_assistant_answer(answer):
    quote = answer.get("quote")
    if quote and quote.get("message"):
        if quote.get("price") is None:
            st.error("行情資料暫時無法更新")
        elif quote.get("is_fallback"):
            st.warning(quote["message"])
        else:
            st.caption(quote["message"])

    render_assistant_answer_card(answer, compact=False)


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


def inject_dashboard_css():
    st.markdown(
        """
        <style>
        :root {
            --card-bg: rgba(255, 255, 255, 0.94);
            --line: #e6ebf7;
            --ink: #172033;
            --muted: #6d7890;
            --primary: #4f73ff;
            --violet: #7b5cff;
            --green: #0fa968;
            --red: #e9465b;
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(96, 119, 255, 0.13), transparent 34rem),
                radial-gradient(circle at top right, rgba(255, 184, 107, 0.12), transparent 28rem),
                #f7f9fd;
            color: var(--ink);
        }
        section[data-testid="stSidebar"] {
            display: none;
        }
        .block-container {
            padding-top: 1.1rem;
            padding-bottom: 2.2rem;
            max-width: 1280px;
            margin: 0 auto;
        }
        .dashboard-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            margin: 0.1rem 0 0.85rem;
            min-height: 52px;
        }
        .dashboard-title h1 {
            margin: 0;
            font-size: 2rem;
            letter-spacing: 0;
        }
        .dashboard-title p {
            margin: 0.25rem 0 0;
            color: var(--muted);
            font-size: 0.95rem;
        }
        .soft-card {
            background: var(--card-bg);
            border: 1px solid var(--line);
            border-radius: 20px;
            box-shadow: 0 16px 38px rgba(53, 73, 118, 0.09);
            padding: 1.05rem;
            min-height: 100%;
        }
        .settings-card {
            background: rgba(255, 255, 255, 0.86);
            border: 1px solid var(--line);
            border-radius: 18px;
            box-shadow: 0 12px 28px rgba(53, 73, 118, 0.07);
            padding: 0.55rem 0.85rem 0.2rem;
            margin-bottom: 0.8rem;
        }
        .metric-card {
            min-height: 132px;
            position: relative;
            overflow: hidden;
        }
        .metric-card::after {
            content: "";
            position: absolute;
            right: -48px;
            bottom: -54px;
            width: 104px;
            height: 104px;
            border-radius: 50%;
            background: var(--accent-soft);
            opacity: 0.38;
            z-index: 0;
            pointer-events: none;
        }
        .metric-card > * {
            position: relative;
            z-index: 1;
        }
        .metric-label {
            color: var(--accent);
            font-size: 0.9rem;
            font-weight: 800;
            margin-bottom: 0.65rem;
        }
        .metric-value {
            color: var(--ink);
            font-size: 1.55rem;
            font-weight: 900;
            line-height: 1.15;
            word-break: keep-all;
            white-space: nowrap;
        }
        .metric-help {
            color: var(--muted);
            font-size: 0.82rem;
            margin-top: 0.5rem;
        }
        .metric-delta {
            color: var(--green);
            font-size: 0.82rem;
            font-weight: 800;
            margin-top: 0.55rem;
        }
        .section-title {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 0.8rem;
            margin-bottom: 0.8rem;
        }
        .section-title h3 {
            margin: 0;
            font-size: 1.12rem;
        }
        .pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            background: #edf3ff;
            color: #3157d5;
            padding: 0.34rem 0.7rem;
            font-size: 0.78rem;
            font-weight: 800;
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            color: var(--muted);
            font-size: 0.86rem;
            font-weight: 700;
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--green);
            box-shadow: 0 0 0 4px rgba(15, 169, 104, 0.12);
        }
        .donut-wrap {
            display: grid;
            grid-template-columns: minmax(150px, 0.9fr) 1fr;
            gap: 1rem;
            align-items: center;
        }
        .donut {
            width: min(190px, 100%);
            aspect-ratio: 1;
            border-radius: 50%;
            background: conic-gradient(#4f73ff 0 var(--stock-deg), #7fd8e4 var(--stock-deg) var(--cash-deg), #ffd872 var(--cash-deg) 360deg);
            display: grid;
            place-items: center;
            margin: 0 auto;
        }
        .donut-inner {
            width: 58%;
            aspect-ratio: 1;
            background: white;
            border-radius: 50%;
            display: grid;
            place-items: center;
            text-align: center;
            box-shadow: inset 0 0 18px rgba(47, 69, 128, 0.08);
            font-weight: 900;
        }
        .legend-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.6rem;
            color: var(--muted);
            margin: 0.55rem 0;
            font-size: 0.88rem;
        }
        .legend-row span {
            min-width: 0;
        }
        .legend-row strong {
            flex: 0 0 auto;
            white-space: nowrap;
        }
        .asset-card {
            min-height: 260px;
        }
        .asset-card .donut-wrap {
            grid-template-columns: 1fr;
            gap: 0.85rem;
        }
        .asset-card .donut {
            width: min(150px, 58%);
        }
        .asset-card .donut-inner {
            font-size: 0.95rem;
        }
        .asset-legend {
            display: grid;
            gap: 0.45rem;
        }
        .asset-legend .legend-row {
            margin: 0;
            border: 1px solid #edf1f8;
            border-radius: 12px;
            padding: 0.5rem 0.6rem;
            background: #fbfcff;
        }
        .asset-breakdown {
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.35rem;
            margin-top: 0.65rem;
        }
        .asset-breakdown-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.8rem;
            color: var(--muted);
            font-size: 0.82rem;
            min-width: 0;
            border: 1px solid #edf1f8;
            border-radius: 12px;
            background: #ffffff;
            padding: 0.48rem 0.6rem;
        }
        .asset-breakdown-row strong {
            color: var(--ink);
            font-weight: 900;
            white-space: nowrap;
        }
        .dot {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 0.45rem;
        }
        .holding-card {
            border: 1px solid var(--line);
            border-radius: 16px;
            padding: 1rem;
            background: linear-gradient(180deg, #ffffff 0%, #fbfcff 100%);
            box-shadow: 0 10px 24px rgba(53, 73, 118, 0.06);
            min-height: 418px;
            height: 100%;
            display: flex;
            flex-direction: column;
        }
        .holdings-grid {
            display: grid;
            grid-template-columns: repeat(var(--cards-per-row, 2), minmax(0, 1fr));
            gap: 1rem;
        }
        .holding-top {
            display: flex;
            justify-content: space-between;
            gap: 0.75rem;
            align-items: flex-start;
            margin-bottom: 0.65rem;
            height: 54px;
        }
        .holding-title {
            min-width: 0;
            flex: 1 1 auto;
        }
        .symbol-badge {
            width: 42px;
            height: 42px;
            border-radius: 13px;
            background: linear-gradient(135deg, #e8eeff, #f4eaff);
            color: var(--violet);
            display: grid;
            place-items: center;
            font-weight: 900;
        }
        .stock-code { font-weight: 900; font-size: 1rem; }
        .stock-name {
            color: var(--muted);
            font-size: 0.82rem;
            margin-top: 0.08rem;
            line-height: 1.35;
            height: 2.7em;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            word-break: break-word;
        }
        .holding-price {
            font-size: 1.35rem;
            font-weight: 900;
            margin: 0.3rem 0 0.45rem;
            line-height: 1.2;
        }
        .sparkline-wrap {
            height: 66px;
            margin: 0.25rem 0 0.75rem;
            border-radius: 12px;
            background: linear-gradient(180deg, rgba(246, 248, 255, 0.9), rgba(255, 255, 255, 0.65));
            overflow: hidden;
            display: flex;
            align-items: center;
        }
        .sparkline {
            width: 100%;
            height: 62px;
            display: block;
        }
        .sparkline-empty {
            width: 100%;
            text-align: center;
            color: var(--muted);
            font-size: 0.8rem;
        }
        .card-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.42rem 0.7rem;
            font-size: 0.76rem;
            color: var(--muted);
            margin-top: auto;
        }
        .card-grid div {
            min-width: 0;
            display: flex;
            justify-content: space-between;
            gap: 0.4rem;
            border-top: 1px solid #eef2f8;
            padding-top: 0.35rem;
        }
        .card-grid strong {
            color: var(--ink);
            display: inline;
            margin-top: 0;
            word-break: break-word;
            text-align: right;
        }
        .positive { color: var(--red); font-weight: 900; }
        .negative { color: var(--green); font-weight: 900; }
        .neutral { color: var(--muted); font-weight: 900; }
        .card-grid strong.positive { color: var(--red); }
        .card-grid strong.negative { color: var(--green); }
        .card-grid strong.neutral { color: var(--muted); }
        .assistant-panel {
            background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
            border: 1px solid var(--line);
            border-radius: 20px;
            padding: 1rem;
            box-shadow: 0 16px 38px rgba(53, 73, 118, 0.09);
            min-height: 520px;
        }
        .chat-bubble {
            border-radius: 18px;
            padding: 0.85rem 1rem;
            background: #eef4ff;
            color: #1f3566;
            margin-bottom: 0.75rem;
            font-weight: 700;
        }
        .assistant-result-card {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.8rem;
            background: linear-gradient(135deg, #f7f9ff, #ffffff);
            border: 1px solid var(--line);
            border-radius: 16px;
            padding: 0.9rem;
            margin: 0.75rem 0;
        }
        .result-label {
            color: var(--muted);
            font-size: 0.78rem;
            font-weight: 800;
            margin-bottom: 0.2rem;
        }
        .result-conclusion {
            color: var(--ink);
            font-size: 1.1rem;
            font-weight: 900;
        }
        .score-pill {
            flex: 0 0 auto;
            border-radius: 999px;
            background: #f0edff;
            color: #5e45d8;
            padding: 0.45rem 0.7rem;
            font-weight: 900;
            font-size: 0.86rem;
            white-space: nowrap;
        }
        .assistant-mini-answer {
            border: 1px solid var(--line);
            background: #fbfcff;
            border-radius: 16px;
            padding: 0.85rem;
            margin-top: 0.75rem;
        }
        .assistant-mini-answer ul {
            margin: 0.4rem 0 0.65rem 1.1rem;
            padding: 0;
        }
        .assistant-mini-answer li {
            margin: 0.25rem 0;
            color: var(--muted);
            font-size: 0.86rem;
        }
        .assistant-action {
            border-radius: 12px;
            background: #edf3ff;
            color: #20366f;
            padding: 0.65rem 0.75rem;
            font-weight: 800;
            font-size: 0.9rem;
        }
        .assistant-answer-card {
            border: 1px solid var(--line);
            border-radius: 18px;
            background: #ffffff;
            box-shadow: 0 12px 30px rgba(53, 73, 118, 0.07);
            padding: 1rem;
            margin-top: 0.85rem;
        }
        .assistant-answer-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.8rem;
            margin-bottom: 0.75rem;
        }
        .assistant-answer-title h4 {
            margin: 0;
            font-size: 1rem;
        }
        .assistant-reasons {
            display: grid;
            gap: 0.45rem;
            margin: 0.75rem 0;
        }
        .assistant-reason {
            border: 1px solid #edf1f8;
            border-radius: 12px;
            background: #fbfcff;
            color: #28344f;
            padding: 0.55rem 0.65rem;
            font-size: 0.9rem;
            line-height: 1.45;
        }
        .assistant-section-label {
            color: var(--muted);
            font-size: 0.78rem;
            font-weight: 900;
            margin-top: 0.75rem;
        }
        .assistant-summary-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.55rem;
            margin-top: 0.5rem;
        }
        .assistant-summary-item {
            border: 1px solid #edf1f8;
            border-radius: 12px;
            background: #fbfcff;
            padding: 0.55rem 0.65rem;
            color: var(--muted);
            font-size: 0.82rem;
            line-height: 1.4;
        }
        .assistant-context-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.45rem;
            margin-top: 0.55rem;
        }
        .assistant-context-item {
            border: 1px solid #edf1f8;
            border-radius: 10px;
            padding: 0.45rem 0.55rem;
            background: #ffffff;
        }
        .assistant-context-item span {
            display: block;
            color: var(--muted);
            font-size: 0.72rem;
            font-weight: 800;
        }
        .assistant-context-item strong {
            display: block;
            color: var(--ink);
            font-size: 0.86rem;
            margin-top: 0.12rem;
            word-break: break-word;
        }
        .entry-card {
            min-height: 154px;
            display: block;
            color: inherit;
            text-decoration: none;
            transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
        }
        .entry-card:hover {
            transform: translateY(-2px);
            border-color: rgba(79, 115, 255, 0.55);
            box-shadow: 0 20px 44px rgba(53, 73, 118, 0.14);
        }
        .entry-title {
            font-size: 1.05rem;
            font-weight: 900;
            margin-bottom: 0.38rem;
        }
        .entry-desc {
            color: var(--muted);
            font-size: 0.86rem;
            min-height: 2.5rem;
            line-height: 1.45;
        }
        .entry-status {
            color: var(--primary);
            font-weight: 900;
            margin-top: 0.8rem;
            font-size: 1rem;
        }
        [title] {
            cursor: help;
        }
        .health-score {
            display: grid;
            grid-template-columns: 112px minmax(0, 1fr);
            align-items: center;
            gap: 0.9rem;
        }
        .score-ring {
            width: 112px;
            aspect-ratio: 1;
            border-radius: 50%;
            background: conic-gradient(#34c96b 0 var(--score-deg), #edf1f8 var(--score-deg) 360deg);
            display: grid;
            place-items: center;
        }
        .score-ring span {
            width: 68%;
            aspect-ratio: 1;
            border-radius: 50%;
            background: white;
            display: grid;
            place-items: center;
            font-size: 1.35rem;
            font-weight: 900;
        }
        .score-ring small {
            font-size: 0.65rem;
            color: var(--muted);
            margin-left: 0.1rem;
        }
        .health-items {
            display: grid;
            gap: 0.48rem;
            min-width: 0;
        }
        .health-item {
            border: 1px solid #edf1f8;
            border-radius: 12px;
            background: #fbfcff;
            padding: 0.5rem 0.6rem;
        }
        .health-item span {
            display: block;
            color: var(--muted);
            font-size: 0.76rem;
            font-weight: 800;
            margin-bottom: 0.16rem;
        }
        .health-item strong {
            display: block;
            color: var(--ink);
            font-size: 0.86rem;
            line-height: 1.35;
            word-break: break-word;
        }
        .news-row {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            padding: 0.72rem 0;
            border-bottom: 1px solid #edf1f8;
            color: #26324a;
            font-size: 0.9rem;
        }
        .news-row:last-child { border-bottom: 0; }
        @media (max-width: 900px) {
            .dashboard-title { display: block; }
            .donut-wrap { grid-template-columns: 1fr; }
            .holdings-grid { grid-template-columns: 1fr !important; }
            .health-score { grid-template-columns: 1fr; }
            .score-ring { margin: 0 auto 0.6rem; }
            .metric-value { font-size: 1.45rem; }
            .entry-desc { min-height: auto; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_metric_card(label, value, help_text="", delta="", accent="#4f73ff", soft="#edf3ff", tooltip=""):
    title_attr = f' title="{escape(tooltip)}"' if tooltip else ""
    st.markdown(
        f"""
        <div class="soft-card metric-card" style="--accent:{accent};--accent-soft:{soft};">
            <div class="metric-label">{escape(label)}</div>
            <div class="metric-value"{title_attr}>{escape(value)}</div>
            <div class="metric-delta">{escape(delta)}</div>
            <div class="metric-help">{escape(help_text)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def set_active_section(section_name):
    st.session_state["active_section"] = section_name


def render_top_settings_card(cash):
    st.markdown('<div class="settings-card">', unsafe_allow_html=True)
    title_col, cash_col, save_col, refresh_col = st.columns([2.2, 1.5, 1, 1])
    with title_col:
        st.markdown("### 投資分析工具")
        st.caption("規則式投資輔助分析，現價資料可能延遲，實際下單前請以券商報價為準。")
    with cash_col:
        updated_cash = st.number_input("本月可用現金", min_value=0.0, value=float(cash), step=1000.0)
    with save_col:
        save_clicked = st.button("保存現金", use_container_width=True)
    with refresh_col:
        refresh_clicked = st.button("重新讀取資料", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)
    return updated_cash, save_clicked, refresh_clicked


def render_entry_card(title, description, status, target_section, key):
    section_url = quote(target_section)
    st.markdown(
        f"""
        <a class="soft-card entry-card" href="?section={section_url}" target="_self">
            <div class="entry-title">{escape(title)}</div>
            <div class="entry-desc">{escape(description)}</div>
            <div class="entry-status">{escape(status)}</div>
        </a>
        """,
        unsafe_allow_html=True,
    )


def render_entry_cards(
    stock_market_value,
    cash,
    total_unrealized_profit,
    mortgage_buffer_months,
    health_score,
    holdings_count,
):
    st.markdown("### 功能入口")
    entries = [
        ("總覽儀表板", "回到資產概況、健康度與投資助理。", f"健康度 {health_score}/100", "總覽儀表板", "entry_dashboard"),
        ("持股分析", "查看持股小卡、集中度與完整持股資料表。", f"{holdings_count} 檔持股", "持股分析", "entry_holdings"),
        ("投資助理", "輸入問題，依持股、現金與風險狀態取得建議。", "可問加碼/賣出/大跌", "投資助理", "entry_assistant"),
        ("現金與房貸", "檢查現金緩衝、房貸月繳與不動產資料。", f"{format_months(mortgage_buffer_months)}", "現金房貸", "entry_cash"),
        ("買賣模擬", "試算買進或賣出後的現金與資產變化。", f"現金 {format_currency(cash)}", "買賣模擬", "entry_simulator"),
        ("原始資料", "查看 Google CSV 交易紀錄與未納入持股項目。", f"損益 {format_currency(total_unrealized_profit)}", "原始資料", "entry_raw"),
    ]
    for start_index in range(0, len(entries), 3):
        columns = st.columns(3)
        for column, entry in zip(columns, entries[start_index : start_index + 3]):
            with column:
                render_entry_card(*entry)


def render_asset_allocation_card(stock_market_value, cash, real_estate_value_total, overall_asset_total):
    safe_total = overall_asset_total if overall_asset_total else 1
    stock_ratio_value = stock_market_value / safe_total
    cash_ratio_value = cash / safe_total
    real_estate_ratio = real_estate_value_total / safe_total
    stock_deg = stock_ratio_value * 360
    cash_deg = (stock_ratio_value + cash_ratio_value) * 360
    st.markdown(
        f"""
        <div class="soft-card asset-card">
            <div class="section-title"><h3>資產配置</h3><span class="pill">總資產占比</span></div>
            <div class="donut-wrap">
                <div class="donut" style="--stock-deg:{stock_deg:.1f}deg;--cash-deg:{cash_deg:.1f}deg;">
                    <div class="donut-inner"><div>股票<br>{stock_ratio_value * 100:.0f}%</div></div>
                </div>
                <div class="asset-card-body">
                    <div class="asset-legend">
                    <div class="legend-row"><span><span class="dot" style="background:#4f73ff"></span>股票</span><strong>{format_percent(stock_ratio_value)}</strong></div>
                    <div class="legend-row"><span><span class="dot" style="background:#7fd8e4"></span>現金</span><strong>{format_percent(cash_ratio_value)}</strong></div>
                    <div class="legend-row"><span><span class="dot" style="background:#ffd872"></span>不動產</span><strong>{format_percent(real_estate_ratio)}</strong></div>
                    </div>
                    <div class="asset-breakdown">
                        <div class="asset-breakdown-row"><span>股票市值</span><strong>{format_currency(stock_market_value)}</strong></div>
                        <div class="asset-breakdown-row"><span>現金</span><strong>{format_currency(cash)}</strong></div>
                        <div class="asset-breakdown-row"><span>房產</span><strong>{format_currency(real_estate_value_total)}</strong></div>
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_holding_card_html(row):
    stock_id = str(row["\u80a1\u7968\u4ee3\u865f"])
    stock_name = str(row["\u80a1\u7968\u540d\u7a31"])
    return_rate = row["\u5831\u916c\u7387"]
    profit = row["\u672a\u5be6\u73fe\u640d\u76ca"]
    if pd.isna(profit) or profit == 0:
        tone_class = "neutral"
        sign = ""
    elif profit > 0:
        tone_class = "positive"
        sign = "\u25b2"
    else:
        tone_class = "negative"
        sign = "\u25bc"
    current_price = format_price(row["\u73fe\u50f9"])
    quantity = row["\u6301\u6709\u80a1\u6578"]
    market_value = format_currency(row["\u76ee\u524d\u5e02\u503c"])
    avg_cost = format_price(row["\u5e73\u5747\u6210\u672c"])
    profit_text = format_currency(profit)
    holding_ratio = format_percent(row.get("\u6301\u80a1\u4f54\u6bd4", pd.NA))
    asset_ratio = format_percent(row.get("\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b", pd.NA))
    price_history = get_price_history(stock_id)
    sparkline_svg = build_sparkline_svg(price_history, positive=tone_class == "positive")
    return f"""
        <div class="holding-card">
            <div class="holding-top">
                <div class="holding-title">
                    <div class="stock-code">{escape(stock_id)}</div>
                    <div class="stock-name" title="{escape(stock_name)}">{escape(stock_name)}</div>
                </div>
                <div class="{tone_class}" title="報酬率：目前未實現損益 / 總投入成本">{escape((sign + ' ') if sign else '')}{escape(format_percent(return_rate))}</div>
            </div>
            <div class="holding-price">$ {escape(current_price)}</div>
            <div class="sparkline-wrap" title="最近 3 個月真實收盤價走勢，資料來源 Yahoo Finance">{sparkline_svg}</div>
            <div class="card-grid">
                <div><span>持有</span><strong>{quantity:,.0f} 股</strong></div>
                <div><span>市值</span><strong>{escape(market_value)}</strong></div>
                <div><span>成本</span><strong>{escape(avg_cost)}</strong></div>
                <div><span>損益</span><strong class="{tone_class}">{escape(profit_text)}</strong></div>
                <div title="持股占比：此股票市值占股票總市值比例"><span>持股占比</span><strong>{escape(holding_ratio)}</strong></div>
                <div title="總資產占比：此股票市值占股票＋現金總資產比例"><span>總資產占比</span><strong>{escape(asset_ratio)}</strong></div>
            </div>
        </div>
        """


def render_holding_card(row):
    st.markdown(build_holding_card_html(row), unsafe_allow_html=True)


def render_holding_cards(holdings, allow_expand=True, cards_per_row=3):
    st.markdown(
        f"""
        <div class="soft-card">
            <div class="section-title">
                <h3>持股總覽</h3>
                <span class="pill">每列最多 {cards_per_row} 檔</span>
            </div>
        """,
        unsafe_allow_html=True,
    )
    if holdings.empty:
        st.info("目前沒有可顯示的持股。")
    else:
        sorted_holdings = holdings.sort_values("\u76ee\u524d\u5e02\u503c", ascending=False)
        show_all = bool(st.session_state.get("show_all_holdings", False))
        visible_holdings = sorted_holdings if show_all else sorted_holdings.head(6)
        for start_index in range(0, len(visible_holdings), cards_per_row):
            columns = st.columns(cards_per_row)
            for column, (_, row) in zip(
                columns,
                visible_holdings.iloc[start_index : start_index + cards_per_row].iterrows(),
            ):
                with column:
                    render_holding_card(row)
        if allow_expand and len(sorted_holdings) > 6:
            button_label = "收合持股" if show_all else f"查看更多（共 {len(sorted_holdings)} 檔）"
            if st.button(button_label, key="toggle_holdings", use_container_width=True):
                st.session_state["show_all_holdings"] = not show_all
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def calculate_portfolio_health_score(holdings, overall_return, stock_market_value, overall_asset_total, mortgage_buffer_months):
    score = 100
    if not pd.isna(mortgage_buffer_months):
        if mortgage_buffer_months < 6:
            score -= 24
        elif mortgage_buffer_months < 12:
            score -= 10
    if not holdings.empty and "\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b" in holdings.columns:
        max_ratio = holdings["\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"].max(skipna=True)
        if not pd.isna(max_ratio) and max_ratio > STOCK_NO_ADD_THRESHOLD:
            score -= 14
    if overall_return < 0:
        score -= 12
    exposure = stock_market_value / overall_asset_total if overall_asset_total else 0
    if exposure > 0.65:
        score -= 10
    return max(0, min(100, int(score)))


def render_health_card(score, mortgage_buffer_months, cash_gap_to_12m_mortgage, holdings):
    status = "良好" if score >= 75 else "注意" if score >= 55 else "偏弱"
    score_deg = score * 3.6
    concentration_note = "持股分散尚可"
    if not holdings.empty and "\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b" in holdings.columns:
        max_row = holdings.sort_values("\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b", ascending=False).iloc[0]
        concentration_note = (
            f"{max_row['\u80a1\u7968\u4ee3\u865f']} 占總資產 "
            f"{format_percent(max_row['\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b'])}"
        )
    cash_note = (
        f"現金可支撐 {format_months(mortgage_buffer_months)}"
        if not pd.isna(mortgage_buffer_months)
        else "目前沒有房貸月繳資料"
    )
    gap_note = (
        f"距 12 個月房貸水位差 {format_currency(max(cash_gap_to_12m_mortgage, 0))}"
        if cash_gap_to_12m_mortgage > 0
        else "現金已達 12 個月房貸安全水位"
    )
    st.markdown(
        f"""
        <div class="soft-card">
            <div class="section-title"><h3>投資組合健康度</h3><span class="pill">{escape(status)}</span></div>
            <div class="health-score">
                <div class="score-ring" title="健康度：依現金水位、集中度、股票曝險計算的簡化分數" style="--score-deg:{score_deg:.1f}deg;"><span>{score}<small>/100</small></span></div>
                <div class="health-items">
                    <div class="health-item"><span>資產配置</span><strong>{escape(concentration_note)}</strong></div>
                    <div class="health-item"><span>現金水位</span><strong>{escape(cash_note)}</strong></div>
                    <div class="health-item"><span>房貸緩衝</span><strong>{escape(gap_note)}</strong></div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_market_overview_card(overall_return, cash_ratio, stock_market_value, total_unrealized_profit):
    cards = [
        ("股票曝險", format_currency(stock_market_value), "目前股票總市值"),
        ("現金比重", format_percent(cash_ratio), "股票與現金資產內"),
        ("帳面損益", format_currency(total_unrealized_profit), format_percent(overall_return)),
    ]
    st.markdown(
        '<div class="soft-card"><div class="section-title"><h3>市場總覽</h3><span class="pill">投組摘要</span></div>',
        unsafe_allow_html=True,
    )
    columns = st.columns(3)
    for column, (title, value, note) in zip(columns, cards):
        with column:
            st.markdown(
                f"""
                <div class="holding-card" style="min-height:120px;">
                    <div class="stock-name">{escape(title)}</div>
                    <div class="holding-price">{escape(value)}</div>
                    <div class="metric-help">{escape(note)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)


def render_news_card(cash_gap_to_12m_mortgage, monthly_mortgage_total, total_unrealized_profit, holdings):
    observations = [
        (
            "現金水位",
            "現金水位偏低，建議優先保留房貸與生活緩衝。"
            if cash_gap_to_12m_mortgage > 0
            else "現金安全水位充足，可維持原定投資節奏。",
        )
    ]
    if not holdings.empty:
        profit_rows = holdings.dropna(subset=["\u5831\u916c\u7387"]).sort_values("\u5831\u916c\u7387", ascending=False)
        if not profit_rows.empty:
            best_row = profit_rows.iloc[0]
            if best_row["\u5831\u916c\u7387"] > 0.2:
                observations.append(
                    (
                        str(best_row["\u80a1\u7968\u4ee3\u865f"]),
                        f"{best_row['\u80a1\u7968\u540d\u7a31']} 帳面獲利較高，短線追價宜保守。",
                    )
                )
        ratio_rows = holdings.dropna(subset=["\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"]).sort_values(
            "\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b",
            ascending=False,
        )
        if not ratio_rows.empty:
            top_row = ratio_rows.iloc[0]
            if top_row["\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"] > STOCK_CONCENTRATION_WARNING_THRESHOLD:
                observations.append(
                    (
                        str(top_row["\u80a1\u7968\u4ee3\u865f"]),
                        f"{top_row['\u80a1\u7968\u540d\u7a31']} 單一標的比重偏高，留意波動。",
                    )
                )
    observations.append(("投資損益", f"目前未實現損益 {format_currency(total_unrealized_profit)}，建議定期檢視。"))
    observations = observations[:3]
    st.markdown(
        '<div class="soft-card"><div class="section-title"><h3>市場觀察</h3><span class="pill">依目前資料</span></div>',
        unsafe_allow_html=True,
    )
    for title, text in observations:
        st.markdown(
            f'<div class="news-row"><strong>{escape(title)}</strong><span>{escape(text)}</span></div>',
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def render_dashboard_assistant(holdings_df, cash, stock_market_value, overall_asset_total, monthly_mortgage_total, mortgage_buffer_months):
    st.markdown(
        '<div class="assistant-panel"><div class="section-title"><h3>投資決策助手</h3><span class="pill">AI</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="chat-bubble">可以問：00919 可以加碼嗎？聯電適合我嗎？台股大跌怎麼辦？</div>',
        unsafe_allow_html=True,
    )
    with st.form("dashboard_assistant_form"):
        assistant_question = st.text_input(
            "輸入投資問題",
            value="",
            placeholder="問一句，例如：00830適合我嗎？",
            label_visibility="collapsed",
        )
        assistant_submitted = st.form_submit_button("送出")
    if assistant_question.strip() and assistant_submitted:
        answer = build_investment_assistant_answer(
            assistant_question,
            holdings_df,
            cash,
            stock_market_value,
            overall_asset_total,
            monthly_mortgage_total,
            mortgage_buffer_months,
        )
        quote = answer.get("quote")
        if quote and quote.get("price") is None:
            st.error("行情資料暫時無法更新")
        elif quote and quote.get("is_fallback"):
            st.warning(quote.get("message", "使用最近收盤價。"))
        render_assistant_answer_card(answer, compact=True)
    else:
        st.caption("我會同時看你的持股、現金、房貸、集中度與標的近期狀況，不會建議重壓或 All in。")
    st.markdown("</div>", unsafe_allow_html=True)


def render_full_assistant_section(holdings_df, cash, stock_market_value, overall_asset_total, monthly_mortgage_total, mortgage_buffer_months):
    render_back_to_dashboard()
    st.header("投資決策助手")
    st.caption("用人話問就可以。系統會同時看你的資產配置、現金水位、房貸壓力、持股集中度與標的行情。")
    st.markdown(
        """
        <div class="chat-bubble">範例：00919可以加碼嗎？群創要不要賣？00830適合我嗎？聯電現在可以布局嗎？台股大跌怎麼辦？</div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("full_investment_assistant_form"):
        assistant_question = st.text_input(
            "輸入你的問題",
            value="",
            placeholder="例如：00830適合我嗎？",
        )
        assistant_submitted = st.form_submit_button("取得建議")

    if assistant_question.strip() and assistant_submitted:
        assistant_answer = build_investment_assistant_answer(
            assistant_question,
            holdings_df,
            cash,
            stock_market_value,
            overall_asset_total,
            monthly_mortgage_total,
            mortgage_buffer_months,
        )
        render_investment_assistant_answer(assistant_answer)
    elif assistant_question.strip():
        st.caption("按「取得建議」後，我會依目前持股、現金與房貸安全水位回答。")
    else:
        st.caption("也可以問未持有標的，例如 00830、聯電；適合度分數代表它適不適合你目前配置，不是股票好壞分數。")


def render_dashboard(
    holdings_df,
    cash,
    stock_market_value,
    total_unrealized_profit,
    overall_return,
    overall_asset_total,
    real_estate_value_total,
    monthly_mortgage_total,
    mortgage_buffer_months,
    cash_gap_to_12m_mortgage,
    cash_ratio,
    data_status_message,
):
    health_score = calculate_portfolio_health_score(
        holdings_df,
        overall_return,
        stock_market_value,
        overall_asset_total,
        mortgage_buffer_months,
    )
    st.markdown(
        f"""
        <div class="dashboard-title">
            <div>
                <h1>總覽儀表板</h1>
                <p>掌握資產全貌，做出更明確的投資決策</p>
            </div>
            <span class="status-badge"><span class="status-dot"></span>{escape(data_status_message)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    metric_columns = st.columns(5)
    with metric_columns[0]:
        render_metric_card(
            "總資產",
            f"$ {format_compact_currency(overall_asset_total)}",
            "股票 + 現金 + 房產",
            accent="#7b5cff",
            soft="#f1edff",
            tooltip=f"完整金額：{format_currency(overall_asset_total)}",
        )
    with metric_columns[1]:
        render_metric_card(
            "股票市值",
            f"$ {format_compact_currency(stock_market_value)}",
            "目前持股市值",
            delta=format_percent(overall_return),
            accent="#2f72ff",
            soft="#eaf2ff",
            tooltip=f"完整金額：{format_currency(stock_market_value)}",
        )
    with metric_columns[2]:
        render_metric_card(
            "現金",
            f"$ {format_compact_currency(cash)}",
            "可動用資金",
            accent="#0f9f7a",
            soft="#e9fbf3",
            tooltip=f"完整金額：{format_currency(cash)}",
        )
    with metric_columns[3]:
        render_metric_card(
            "未實現損益",
            f"$ {format_compact_currency(total_unrealized_profit)}",
            "帳面損益",
            delta=format_percent(overall_return),
            accent="#d8701f",
            soft="#fff2df",
            tooltip=f"完整金額：{format_currency(total_unrealized_profit)}",
        )
    with metric_columns[4]:
        render_metric_card("健康度", f"{health_score}/100", f"現金可支撐 {format_months(mortgage_buffer_months)}", accent="#d83f72", soft="#fff0f5")

    left, right = st.columns([2.6, 1])
    with left:
        render_holding_cards(holdings_df, cards_per_row=3)
    with right:
        render_dashboard_assistant(holdings_df, cash, stock_market_value, overall_asset_total, monthly_mortgage_total, mortgage_buffer_months)

    render_entry_cards(
        stock_market_value,
        cash,
        total_unrealized_profit,
        mortgage_buffer_months,
        health_score,
        len(holdings_df),
    )

    bottom_left, bottom_mid, bottom_right = st.columns(3)
    with bottom_left:
        render_asset_allocation_card(stock_market_value, cash, real_estate_value_total, overall_asset_total)
    with bottom_mid:
        render_health_card(health_score, mortgage_buffer_months, cash_gap_to_12m_mortgage, holdings_df)
    with bottom_right:
        render_news_card(cash_gap_to_12m_mortgage, monthly_mortgage_total, total_unrealized_profit, holdings_df)


def render_back_to_dashboard():
    if st.button("回到總覽儀表板", use_container_width=False):
        set_active_section("總覽儀表板")
        st.rerun()


def render_compact_trade_simulator(holdings_df, cash, stock_market_value, monthly_mortgage_total):
    render_back_to_dashboard()
    st.header("買賣模擬")
    st.caption("以目前行情與既有費率估算交易後現金、股票市值與配置變化。")

    simulator_columns = st.columns([1, 1, 1])
    simulation_action = simulator_columns[0].selectbox("操作類型", ["買進", "賣出", "只查詢"])
    simulation_stock_id = simulator_columns[1].text_input("股票代號", value="", placeholder="例如：0050、2330、00679B")
    simulation_stock_id = simulation_stock_id.strip().upper()

    if not simulation_stock_id:
        st.info("輸入股票代號後開始模擬。")
        return

    current_holding = find_holding(holdings_df, simulation_stock_id)
    lookup_price = (
        float(current_holding["\u73fe\u50f9"])
        if current_holding is not None and not pd.isna(current_holding["\u73fe\u50f9"])
        else get_current_price(simulation_stock_id)
    )
    if lookup_price is None or pd.isna(lookup_price):
        st.error("行情資料暫時無法更新。")
        return

    stock_name = (
        str(current_holding["\u80a1\u7968\u540d\u7a31"])
        if current_holding is not None
        else get_stock_name(simulation_stock_id) or simulation_stock_id
    )

    if simulation_action == "只查詢":
        if current_holding is not None:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "股票代號": simulation_stock_id,
                            "股票名稱": stock_name,
                            "現價": format_price(lookup_price),
                            "持有股數": f"{current_holding['\u6301\u6709\u80a1\u6578']:,.0f}",
                            "平均成本": format_price(current_holding["\u5e73\u5747\u6210\u672c"]),
                            "未實現損益": format_currency(current_holding["\u672a\u5be6\u73fe\u640d\u76ca"]),
                            "報酬率": format_percent(current_holding["\u5831\u916c\u7387"]),
                        }
                    ]
                ),
                use_container_width=True,
            )
        else:
            st.info(f"{simulation_stock_id} 尚未持有，目前參考價格 {format_price(lookup_price)}。")
        return

    input_columns = st.columns(2)
    simulation_price = input_columns[0].number_input("模擬價格", min_value=0.0, value=float(lookup_price), step=0.01)
    if simulation_action == "買進":
        simulation_lots = input_columns[1].number_input("買進張數", min_value=0.0, value=1.0, step=1.0)
        simulation_quantity = simulation_lots * SHARES_PER_LOT
    else:
        default_quantity = float(current_holding["\u6301\u6709\u80a1\u6578"]) if current_holding is not None else 0.0
        simulation_quantity = input_columns[1].number_input("賣出股數", min_value=0.0, value=default_quantity, step=100.0)

    if simulation_price <= 0 or simulation_quantity <= 0:
        return

    simulation_market_value = simulation_price * simulation_quantity
    simulation_brokerage_fee, simulation_transaction_tax = estimate_trade_fee(
        simulation_stock_id,
        simulation_market_value,
        simulation_action,
    )
    simulation_total_fee = simulation_brokerage_fee + simulation_transaction_tax

    if simulation_action == "買進":
        simulated_cash = cash - simulation_market_value - simulation_brokerage_fee
    else:
        if current_holding is None:
            st.warning("目前沒有持有這檔股票，無法模擬賣出。")
            return
        if simulation_quantity > float(current_holding["\u6301\u6709\u80a1\u6578"]):
            st.warning("模擬賣出股數高於目前持有股數。")
        simulated_cash = cash + simulation_market_value - simulation_total_fee

    simulated_holdings = build_simulated_holdings(
        holdings_df,
        simulation_stock_id,
        stock_name,
        simulation_action,
        simulation_quantity,
        simulation_price,
    )
    simulated_stock_market_value = simulated_holdings["\u6a21\u64ec\u5f8c\u5e02\u503c"].sum(skipna=True)
    simulated_total_assets = simulated_stock_market_value + simulated_cash

    metrics = st.columns(5)
    metrics[0].metric("交易金額", format_currency(simulation_market_value))
    metrics[1].metric("手續費", format_currency(simulation_brokerage_fee))
    metrics[2].metric("交易稅", format_currency(simulation_transaction_tax))
    metrics[3].metric("模擬後現金", format_currency(simulated_cash))
    metrics[4].metric("模擬後股票+現金", format_currency(simulated_total_assets))

    if simulation_action == "買進":
        render_mortgage_cashflow_check(simulated_cash, monthly_mortgage_total)
    st.dataframe(build_simulation_display(simulated_holdings), use_container_width=True)


st.set_page_config(page_title="投資分析工具", layout="wide")
inject_dashboard_css()

if "active_section" not in st.session_state:
    st.session_state["active_section"] = "總覽儀表板"
query_section = st.query_params.get("section")
if query_section:
    st.session_state["active_section"] = query_section

settings = load_settings()
saved_cash = float(settings.get("cash", 0.0))
cash, save_cash_clicked, refresh_clicked = render_top_settings_card(saved_cash)

if save_cash_clicked:
    settings["cash"] = cash
    save_settings(settings)
    st.success("現金已保存，下次開啟會自動帶入。")
if refresh_clicked:
    st.cache_data.clear()
    st.rerun()

with st.expander("資料說明"):
    st.caption("本工具為規則式投資輔助分析，並非保證獲利或正式投資建議。")
    st.caption("成本採 FIFO 先進先出法估算；券商 App 可能因成本算法不同而略有差異。")
    st.caption("現價優先使用 TWSE MIS 或 Yahoo Finance，資料可能延遲。")
    st.caption("預估損益已扣除估算賣出手續費與交易稅。")

transactions_df, csv_error = read_holdings_csv(HOLDINGS_CSV_URL)
data_status_message = "即時更新成功"

if transactions_df is not None:
    st.session_state["last_successful_transactions_df"] = transactions_df.copy()
    save_transactions_cache(transactions_df)
    st.success("Google 試算表讀取成功")
else:
    data_status_message = "資料來源異常"
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
        data_status_message = "使用快取資料"
        st.warning("目前使用上一次成功讀取的資料")
    else:
        cached_transactions_df = load_transactions_cache()
        if cached_transactions_df is not None:
            transactions_df = cached_transactions_df
            st.session_state["last_successful_transactions_df"] = transactions_df.copy()
            data_status_message = "使用快取資料"
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
active_section = st.session_state.get("active_section", "總覽儀表板")

if active_section == "總覽儀表板":
    render_dashboard(
        holdings_df,
        cash,
        stock_market_value,
        total_unrealized_profit,
        overall_return,
        overall_asset_total,
        real_estate_value_total,
        monthly_mortgage_total,
        mortgage_buffer_months,
        cash_gap_to_12m_mortgage,
        cash_ratio,
        data_status_message,
    )
    st.stop()

if active_section == "投資助理":
    render_full_assistant_section(
        holdings_df,
        cash,
        stock_market_value,
        overall_asset_total,
        monthly_mortgage_total,
        mortgage_buffer_months,
    )
    st.stop()

if active_section == "持股分析":
    render_back_to_dashboard()
    st.header("持股分析")
    render_holding_cards(holdings_df)
    st.header("需要注意的標的")
    watchlist_df = build_watchlist(holdings_df)
    if watchlist_df.empty:
        st.success("目前沒有需要特別注意的標的。")
    else:
        st.dataframe(watchlist_df, use_container_width=True)
    with st.expander("查看完整持股明細", expanded=True):
        st.dataframe(build_display_holdings(holdings_df), use_container_width=True)
    st.stop()

if active_section == "現金房貸":
    render_back_to_dashboard()
    st.header("現金流與不動產")
    overview_row = st.columns(4)
    overview_row[0].metric("現金", format_currency(cash))
    overview_row[1].metric("每月房貸支出", format_currency(monthly_mortgage_total))
    overview_row[2].metric("現金可支撐房貸月數", format_months(mortgage_buffer_months))
    overview_row[3].metric("12 個月房貸缺口", format_currency(max(cash_gap_to_12m_mortgage, 0)))
    if real_estate_status_message:
        st.error(real_estate_status_message)
    else:
        st.dataframe(build_display_real_estate(real_estate_df), use_container_width=True)
        with st.expander("進階不動產估算資料"):
            st.dataframe(build_display_advanced_real_estate(real_estate_df), use_container_width=True)
            st.table(build_real_estate_summary_table(real_estate_value_total, future_mortgage_payment_total))
    st.stop()

if active_section == "買賣模擬":
    render_compact_trade_simulator(holdings_df, cash, stock_market_value, monthly_mortgage_total)
    st.stop()

if active_section == "原始資料":
    render_back_to_dashboard()
    st.header("原始資料")
    with st.expander("未納入持股計算的交易", expanded=True):
        st.dataframe(
            excluded_transactions_df.drop(columns=["交易日期_排序", "原始順序"], errors="ignore"),
            use_container_width=True,
        )
    with st.expander("原始交易紀錄", expanded=True):
        st.dataframe(transactions_df, use_container_width=True)
    st.stop()

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

st.header("對話式投資決策助手")
with st.form("investment_assistant_form"):
    assistant_question = st.text_input(
        "輸入你的問題",
        value="",
        placeholder="例如：00919可以加碼嗎、群創要不要賣、台股大跌我該怎麼辦",
    )
    assistant_submitted = st.form_submit_button("取得建議")

if assistant_question.strip() and assistant_submitted:
    assistant_answer = build_investment_assistant_answer(
        assistant_question,
        holdings_df,
        cash,
        stock_market_value,
        overall_asset_total,
        monthly_mortgage_total,
        mortgage_buffer_months,
    )
    render_investment_assistant_answer(assistant_answer)
elif assistant_question.strip():
    st.caption("按「取得建議」後，系統會依目前持股、現金與房貸安全水位回答。")
else:
    st.caption("可輸入標的代號、持股名稱或整體市場問題；系統會依目前持股、現金與房貸安全水位回答。")

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
