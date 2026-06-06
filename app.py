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
ETF_TYPE_KEYWORDS = ["ETF", "\u53f0\u706350", "\u9ad8\u606f", "\u9ad8\u80a1\u606f", "\u7f8e\u50b5", "\u50b5", "\u516c\u53f8\u50b5"]
ETF_TYPE_CODES = {"0050", "00679B", "00687B", "00919", "00929", "00878", "00885"}
SHARES_PER_LOT = 1000


def parse_number(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip(),
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
                "\u5831\u916c\u7387": "\u4e0d\u9069\u7528",
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
            "\u5831\u916c\u7387",
            "\u9810\u4f30\u5831\u916c\u7387",
            "\u6301\u80a1\u4f54\u6bd4",
            "\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b",
        ]
    ]


st.set_page_config(page_title="\u6295\u8cc7\u5206\u6790\u5de5\u5177", layout="wide")
st.title("\u6295\u8cc7\u5206\u6790\u5de5\u5177")
st.warning(
    "\u672c\u5de5\u5177\u70ba\u898f\u5247\u5f0f\u6295\u8cc7\u8f14\u52a9\u5206\u6790\uff0c"
    "\u4e26\u975e\u4fdd\u8b49\u7372\u5229\u6216\u6b63\u5f0f\u6295\u8cc7\u5efa\u8b70\u3002"
    "\u73fe\u50f9\u8cc7\u6599\u4f86\u6e90\u53ef\u80fd\u5ef6\u9072\uff0c"
    "\u5be6\u969b\u4e0b\u55ae\u524d\u8acb\u4ee5\u5238\u5546\u5831\u50f9\u70ba\u6e96\u3002"
)
st.info(
    "\u76ee\u524d\u6210\u672c\u63a1 FIFO \u5148\u9032\u5148\u51fa\u6cd5\u4f30\u7b97\uff0c"
    "\u82e5\u5238\u5546\u6210\u672c\u8a08\u7b97\u65b9\u5f0f\u4e0d\u540c\uff0c"
    "\u7d50\u679c\u53ef\u80fd\u8207\u5238\u5546 App \u7565\u6709\u5dee\u7570\u3002"
)
st.info(
    "\u73fe\u50f9\u8cc7\u6599\u4f86\u6e90\u70ba yfinance\uff0c"
    "\u53ef\u80fd\u4e0d\u662f\u5373\u6642\u5831\u50f9\uff0c\u50c5\u4f9b\u4f30\u7b97\u53c3\u8003\u3002"
)
st.info(
    "\u9810\u4f30\u640d\u76ca\u5df2\u6263\u9664\u4f30\u7b97\u8ce3\u51fa\u624b\u7e8c\u8cbb\u8207\u4ea4\u6613\u7a05\uff1b"
    "\u624b\u7e8c\u8cbb\u4ee5 0.1425% \u4f30\u7b97\uff0cETF \u4ea4\u6613\u7a05\u4ee5 0.1% \u4f30\u7b97\uff0c"
    "\u4e00\u822c\u80a1\u7968\u4ea4\u6613\u7a05\u4ee5 0.3% \u4f30\u7b97\u3002"
)

settings = load_settings()
saved_cash = float(settings.get("cash", 0.0))
cash = st.number_input("\u73fe\u91d1", min_value=0.0, value=saved_cash, step=1000.0)
if st.button("\u4fdd\u5b58\u73fe\u91d1"):
    settings["cash"] = cash
    save_settings(settings)
    st.success("\u73fe\u91d1\u5df2\u4fdd\u5b58\u5230 settings.json")
if st.button("\u91cd\u65b0\u8b80\u53d6\u8cc7\u6599"):
    st.cache_data.clear()
    st.rerun()

transactions_df, csv_error = read_holdings_csv(HOLDINGS_CSV_URL)

if transactions_df is not None:
    st.session_state["last_successful_transactions_df"] = transactions_df.copy()
    save_transactions_cache(transactions_df)
    st.success("Google \u8a66\u7b97\u8868\u8b80\u53d6\u6210\u529f")
else:
    st.error(
        "Google \u8a66\u7b97\u8868 CSV \u8b80\u53d6\u5931\u6557\uff1a"
        + str(csv_error)
        + "\n\n\u53ef\u80fd\u539f\u56e0\uff1a\n"
        + "- \u7db2\u8def\u66ab\u6642\u4e2d\u65b7\n"
        + "- Google \u8a66\u7b97\u8868 CSV \u9023\u7d50\u7121\u6cd5\u9023\u7dda\n"
        + "- \u516c\u53f8\u9632\u706b\u7246\u6216\u9632\u6bd2\u8edf\u9ad4\u53ef\u80fd\u963b\u64cb Python \u9023\u7dda"
    )

    if "last_successful_transactions_df" in st.session_state:
        transactions_df = st.session_state["last_successful_transactions_df"].copy()
        st.warning("\u76ee\u524d\u4f7f\u7528\u4e0a\u4e00\u6b21\u6210\u529f\u8b80\u53d6\u7684\u8cc7\u6599")
    else:
        cached_transactions_df = load_transactions_cache()
        if cached_transactions_df is not None:
            transactions_df = cached_transactions_df
            st.session_state["last_successful_transactions_df"] = transactions_df.copy()
            st.warning("\u76ee\u524d\u4f7f\u7528\u672c\u6a5f\u4e0a\u4e00\u6b21\u6210\u529f\u8b80\u53d6\u7684\u5feb\u53d6\u8cc7\u6599")
        else:
            st.error("\u5c1a\u672a\u6709\u6210\u529f\u8b80\u53d6\u904e\u7684 Google CSV \u8cc7\u6599\uff0c\u76ee\u524d\u7121\u6cd5\u5206\u6790\u3002")
            st.stop()

missing_columns = [column for column in REQUIRED_COLUMNS if column not in transactions_df.columns]

if missing_columns:
    st.error("\u4ea4\u6613\u7d00\u9304\u7f3a\u5c11\u5fc5\u8981\u6b04\u4f4d\uff1a" + "\u3001".join(missing_columns))
    st.stop()

working_df = transactions_df.copy()
for column in NUMERIC_COLUMNS:
    working_df[column] = parse_number(working_df[column])

working_df["\u4ea4\u6613\u5206\u985e"] = working_df["\u4ea4\u6613\u5225"].apply(classify_transaction)
working_df["\u4ea4\u6613\u65e5\u671f_\u6392\u5e8f"] = pd.to_datetime(
    working_df["\u4ea4\u6613\u65e5\u671f"],
    errors="coerce",
)
working_df["\u539f\u59cb\u9806\u5e8f"] = range(len(working_df))
working_df = working_df.sort_values(
    ["\u4ea4\u6613\u65e5\u671f_\u6392\u5e8f", "\u539f\u59cb\u9806\u5e8f"],
    na_position="last",
)

included_transactions_df = working_df[working_df["\u4ea4\u6613\u5206\u985e"].isin(["\u8cb7\u9032", "\u8ce3\u51fa"])]
excluded_transactions_df = working_df[working_df["\u4ea4\u6613\u5206\u985e"] == "\u672a\u7d0d\u5165"]
holdings_df = build_holdings(included_transactions_df)
holdings_df = add_price_analysis(holdings_df, cash)

stock_market_value = holdings_df["\u76ee\u524d\u5e02\u503c"].sum(skipna=True)
total_cost = holdings_df["\u7e3d\u6295\u5165\u6210\u672c"].sum()
total_unrealized_profit = holdings_df["\u672a\u5be6\u73fe\u640d\u76ca"].sum(skipna=True)
total_estimated_profit = holdings_df["\u9810\u4f30\u640d\u76ca"].sum(skipna=True)
overall_return = total_unrealized_profit / total_cost if total_cost else 0
estimated_return = total_estimated_profit / total_cost if total_cost else 0
total_assets = stock_market_value + cash
cash_ratio = cash / total_assets if total_assets else 0

metric_row_1 = st.columns(4)
metric_row_1[0].metric("\u80a1\u7968\u7e3d\u5e02\u503c", format_currency(stock_market_value))
metric_row_1[1].metric("\u7e3d\u6295\u5165\u6210\u672c", format_currency(total_cost))
metric_row_1[2].metric("\u7e3d\u672a\u5be6\u73fe\u640d\u76ca", format_currency(total_unrealized_profit))
metric_row_1[3].metric("\u7e3d\u9810\u4f30\u640d\u76ca", format_currency(total_estimated_profit))

metric_row_2 = st.columns(4)
metric_row_2[0].metric("\u6574\u9ad4\u5831\u916c\u7387", format_percent(overall_return))
metric_row_2[1].metric("\u9810\u4f30\u5831\u916c\u7387", format_percent(estimated_return))
metric_row_2[2].metric("\u73fe\u91d1", format_currency(cash))
metric_row_2[3].metric("\u7e3d\u8cc7\u7522", format_currency(total_assets))

if total_assets and cash_ratio < CASH_CRITICAL_THRESHOLD:
    st.error(
        "\u73fe\u91d1\u6c34\u4f4d\u904e\u4f4e\uff0c\u5f37\u70c8\u63d0\u9192\u4e0d\u8981\u7a4d\u6975\u52a0\u78bc\u3002"
        f"\u76ee\u524d\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u7d04 {cash_ratio * 100:.1f}%\u3002"
    )
elif total_assets and cash_ratio < CASH_LOW_THRESHOLD:
    st.warning(
        "\u73fe\u91d1\u6c34\u4f4d\u504f\u4f4e\uff0c\u4e0d\u5efa\u8b70\u7a4d\u6975\u52a0\u78bc\u3002"
        f"\u76ee\u524d\u73fe\u91d1\u4f54\u7e3d\u8cc7\u7522\u7d04 {cash_ratio * 100:.1f}%\u3002"
    )

st.subheader("\u6a21\u64ec\u4ea4\u6613")
simulation_stock_id = st.text_input("\u80a1\u7968\u4ee3\u865f", value="", placeholder="\u4f8b\u5982\uff1a0050\u30012330\u300100679B")
current_holding = find_holding(holdings_df, simulation_stock_id)

st.caption(
    "\u96c6\u4e2d\u5ea6\u63d0\u9192\uff1a\u500b\u80a1 15% \u63d0\u9192\u300120% \u4e0d\u5efa\u8b70\u52a0\u78bc\uff1b"
    "ETF 30% \u63d0\u9192\u300135% \u4e0d\u5efa\u8b70\u52a0\u78bc\u3002"
)

if simulation_stock_id.strip():
    simulation_stock_id = simulation_stock_id.strip().upper()
    lookup_price = (
        float(current_holding["\u73fe\u50f9"])
        if current_holding is not None and not pd.isna(current_holding["\u73fe\u50f9"])
        else get_current_price(simulation_stock_id)
    )

    if lookup_price is None or pd.isna(lookup_price):
        st.error("\u7121\u6cd5\u53d6\u5f97\u6b64\u80a1\u7968\u73fe\u50f9\uff0c\u8acb\u78ba\u8a8d\u4ee3\u865f\u662f\u5426\u6b63\u78ba")

    if current_holding is not None:
        st.markdown("**\u5df2\u6301\u6709\u6a19\u7684\u5206\u6790**")
        held_status = pd.DataFrame(
            [
                {
                    "\u80a1\u7968\u540d\u7a31": current_holding["\u80a1\u7968\u540d\u7a31"],
                    "\u76ee\u524d\u6301\u6709\u80a1\u6578": current_holding["\u6301\u6709\u80a1\u6578"],
                    "\u5e73\u5747\u6210\u672c": format_price(current_holding["\u5e73\u5747\u6210\u672c"]),
                    "\u73fe\u50f9": format_price(current_holding["\u73fe\u50f9"]),
                    "\u672a\u5be6\u73fe\u640d\u76ca": format_currency(current_holding["\u672a\u5be6\u73fe\u640d\u76ca"]),
                    "\u5831\u916c\u7387": format_percent(current_holding["\u5831\u916c\u7387"]),
                    "\u6301\u80a1\u4f54\u6bd4": format_percent(current_holding["\u6301\u80a1\u4f54\u6bd4"]),
                    "\u64cd\u4f5c\u5efa\u8b70": current_holding["\u64cd\u4f5c\u5efa\u8b70"],
                    "\u5efa\u8b70\u7406\u7531": current_holding["\u5efa\u8b70\u7406\u7531"],
                }
            ]
        )
        st.dataframe(held_status, use_container_width=True)

        simulation_row_1 = st.columns(3)
        simulation_action = simulation_row_1[0].selectbox(
            "\u60f3\u8cb7 / \u60f3\u8ce3",
            ["\u8cb7\u9032", "\u8ce3\u51fa"],
        )
        simulation_price = simulation_row_1[1].number_input(
            "\u6a21\u64ec\u6210\u4ea4\u50f9",
            min_value=0.0,
            value=float(lookup_price) if lookup_price else 0.0,
            step=0.01,
        )
        simulation_quantity = simulation_row_1[2].number_input(
            "\u6a21\u64ec\u5f35\u6578",
            min_value=0.0,
            value=1.0,
            step=1.0,
        )
        simulation_quantity = simulation_quantity * SHARES_PER_LOT

        if simulation_price > 0 and simulation_quantity > 0:
            simulation_market_value = simulation_price * simulation_quantity
            simulation_brokerage_fee, simulation_transaction_tax = estimate_trade_fee(
                simulation_stock_id,
                simulation_market_value,
                simulation_action,
            )
            simulation_total_fee = simulation_brokerage_fee + simulation_transaction_tax

            if simulation_action == "\u8cb7\u9032":
                simulated_cash = cash - simulation_market_value - simulation_brokerage_fee
            else:
                holding_quantity = float(current_holding["\u6301\u6709\u80a1\u6578"])
                if simulation_quantity > holding_quantity:
                    st.warning("\u6a21\u64ec\u8ce3\u51fa\u80a1\u6578\u5927\u65bc\u76ee\u524d\u6301\u6709\u80a1\u6578\u3002")
                simulated_cash = cash + simulation_market_value - simulation_total_fee

            simulated_holdings = build_simulated_holdings(
                holdings_df,
                simulation_stock_id,
                str(current_holding["\u80a1\u7968\u540d\u7a31"]),
                simulation_action,
                simulation_quantity,
                simulation_price,
            )
            simulated_stock_market_value = simulated_holdings["\u6a21\u64ec\u5f8c\u5e02\u503c"].sum(skipna=True)
            simulated_total_assets = simulated_stock_market_value + simulated_cash
            simulated_holdings["\u985e\u578b"] = simulated_holdings.apply(classify_asset_type, axis=1)

            simulation_metrics = st.columns(5)
            simulation_metrics[0].metric("\u6a21\u64ec\u4ea4\u6613\u91d1\u984d", format_currency(simulation_market_value))
            simulation_metrics[1].metric("\u4f30\u7b97\u624b\u7e8c\u8cbb", format_currency(simulation_brokerage_fee))
            simulation_metrics[2].metric("\u4f30\u7b97\u4ea4\u6613\u7a05", format_currency(simulation_transaction_tax))
            simulation_metrics[3].metric("\u6a21\u64ec\u5f8c\u73fe\u91d1", format_currency(simulated_cash))
            simulation_metrics[4].metric("\u6a21\u64ec\u5f8c\u7e3d\u8cc7\u7522", format_currency(simulated_total_assets))

            no_add_rows = simulated_holdings[
                (
                    (simulated_holdings["\u985e\u578b"].eq("\u500b\u80a1"))
                    & (simulated_holdings["\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4"] >= STOCK_NO_ADD_THRESHOLD)
                )
                | (
                    (simulated_holdings["\u985e\u578b"].eq("ETF / \u50b5\u5238 ETF"))
                    & (simulated_holdings["\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4"] >= ETF_NO_ADD_THRESHOLD)
                )
            ]
            warning_rows = simulated_holdings[
                (
                    (simulated_holdings["\u985e\u578b"].eq("\u500b\u80a1"))
                    & (simulated_holdings["\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4"] >= STOCK_CONCENTRATION_WARNING_THRESHOLD)
                )
                | (
                    (simulated_holdings["\u985e\u578b"].eq("ETF / \u50b5\u5238 ETF"))
                    & (simulated_holdings["\u6a21\u64ec\u5f8c\u6301\u80a1\u4f54\u6bd4"] >= ETF_CONCENTRATION_WARNING_THRESHOLD)
                )
            ]

            if not no_add_rows.empty:
                concentrated_rows = no_add_rows
                concentrated_names = "\u3001".join(
                    concentrated_rows["\u80a1\u7968\u4ee3\u865f"].astype(str)
                    + " "
                    + concentrated_rows["\u80a1\u7968\u540d\u7a31"].astype(str)
                )
                st.warning(
                    "\u4e0d\u5efa\u8b70\u52a0\u78bc\uff1a"
                    + concentrated_names
                    + "\u7684\u6a21\u64ec\u5f8c\u6bd4\u91cd\u5df2\u9054\u904e\u9ad8\u9580\u6abb\u3002"
                )
            elif not warning_rows.empty:
                concentrated_rows = warning_rows
                concentrated_names = "\u3001".join(
                    concentrated_rows["\u80a1\u7968\u4ee3\u865f"].astype(str)
                    + " "
                    + concentrated_rows["\u80a1\u7968\u540d\u7a31"].astype(str)
                )
                st.warning("\u96c6\u4e2d\u5ea6\u63d0\u9192\uff1a" + concentrated_names + "\u7684\u6a21\u64ec\u5f8c\u6bd4\u91cd\u504f\u9ad8\u3002")
            else:
                st.success("\u6a21\u64ec\u5f8c\u672a\u89f8\u767c\u96c6\u4e2d\u5ea6\u63d0\u9192\u3002")

            if simulated_cash < 0:
                st.warning("\u6a21\u64ec\u5f8c\u73fe\u91d1\u70ba\u8ca0\u6578\uff0c\u8acb\u78ba\u8a8d\u8cc7\u91d1\u662f\u5426\u8db3\u5920\u3002")

            st.dataframe(build_simulation_display(simulated_holdings), use_container_width=True)
    else:
        st.markdown("**\u672a\u6301\u6709\u6a19\u7684\u8cb7\u5165\u5206\u6790**")
        lookup_name = get_stock_name(simulation_stock_id) or "\u672a\u6301\u6709\u6a19\u7684"
        unheld_asset_type = classify_asset_type(
            pd.Series(
                {
                    "\u80a1\u7968\u4ee3\u865f": simulation_stock_id,
                    "\u80a1\u7968\u540d\u7a31": lookup_name,
                }
            )
        )
        st.dataframe(
            build_unheld_status_display(simulation_stock_id, lookup_name, unheld_asset_type, lookup_price),
            use_container_width=True,
        )

        planned_buy_columns = st.columns(2)
        planned_buy_lots = planned_buy_columns[0].number_input(
            "\u9810\u8a08\u8cb7\u5165\u5f35\u6578",
            min_value=0.0,
            value=0.0,
            step=1.0,
        )
        planned_buy_amount_input = planned_buy_columns[1].number_input(
            "\u9810\u8a08\u8cb7\u5165\u91d1\u984d",
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
            analysis_metrics[0].metric("\u9810\u8a08\u8cb7\u5165\u5f35\u6578", format_price(planned_buy_lots))
            analysis_metrics[1].metric("\u9810\u4f30\u53ef\u8cb7\u5f35\u6578", format_price(analysis["\u9810\u4f30\u53ef\u8cb7\u5f35\u6578"]))
            analysis_metrics[2].metric("\u9810\u4f30\u53ef\u8cb7\u80a1\u6578", format_price(analysis["\u9810\u4f30\u53ef\u8cb7\u80a1\u6578"]))
            analysis_metrics[3].metric("\u8cb7\u5165\u5f8c\u8a72\u80a1\u7968\u5e02\u503c", format_currency(analysis["\u8cb7\u5165\u5f8c\u8a72\u80a1\u7968\u5e02\u503c"]))
            analysis_metrics[4].metric("\u8cb7\u5165\u5f8c\u73fe\u91d1", format_currency(analysis["\u8cb7\u5165\u5f8c\u73fe\u91d1"]))
            analysis_metrics[5].metric("\u8cb7\u5165\u5f8c\u7e3d\u8cc7\u7522", format_currency(analysis["\u8cb7\u5165\u5f8c\u7e3d\u8cc7\u7522"]))
            analysis_metrics[6].metric(
                "\u8cb7\u5165\u5f8c\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b",
                format_percent(analysis["\u8cb7\u5165\u5f8c\u8a72\u80a1\u7968\u4f54\u7e3d\u8cc7\u7522\u6bd4\u4f8b"]),
            )
            if any(keyword in analysis["\u5efa\u8b70"] for keyword in ["\u4e0d\u5efa\u8b70", "\u504f\u9ad8", "\u904e\u9ad8", "\u4e0d\u8db3"]):
                st.warning(analysis["\u5efa\u8b70"] + "\uff1a" + analysis["\u5efa\u8b70\u7406\u7531"])
            else:
                st.info(analysis["\u5efa\u8b70"] + "\uff1a" + analysis["\u5efa\u8b70\u7406\u7531"])

st.subheader("\u76ee\u524d\u6301\u80a1")
st.dataframe(build_display_holdings(holdings_df), use_container_width=True)

st.subheader("\u672a\u7d0d\u5165\u6301\u80a1\u8a08\u7b97\u7684\u4ea4\u6613")
st.dataframe(
    excluded_transactions_df.drop(
        columns=["\u4ea4\u6613\u65e5\u671f_\u6392\u5e8f", "\u539f\u59cb\u9806\u5e8f"],
        errors="ignore",
    ),
    use_container_width=True,
)

st.subheader("\u539f\u59cb\u4ea4\u6613\u7d00\u9304")
st.dataframe(transactions_df, use_container_width=True)
