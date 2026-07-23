"""yfinance fetch layer for fundamentals data.

Every public function wraps network/parsing failures in
ValueError(f"{ticker}: ... ({exc})") so callers can warn-and-continue
per symbol (fetch_risk_free_rate is the exception: it silently falls
back to a default rate). Never pass threads=True to yfinance.
"""

import math
from datetime import date

import yfinance as yf

DEFAULT_RISK_FREE_RATE = 0.045


def _float_or_none(value) -> float | None:
    """Convert a pandas/numpy scalar to float, mapping NaN to None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _iso_date(value) -> str:
    """Normalize a pandas Timestamp / datetime / date to 'YYYY-MM-DD'."""
    if hasattr(value, "date"):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _statement(ticker_obj, *names):
    """Return the first non-empty DataFrame among the given attributes."""
    for name in names:
        try:
            df = getattr(ticker_obj, name)
        except Exception:
            continue
        if df is not None and not df.empty:
            return df
    return None


def _item(df, col, *names) -> float | None:
    """First non-NaN value among line-item `names` in statement column `col`."""
    if df is None:
        return None
    for name in names:
        try:
            value = df.loc[name, col]
        except KeyError:
            continue
        result = _float_or_none(value)
        if result is not None:
            return result
    return None


def fetch_profile(ticker: str) -> dict:
    """Fetch company profile fields from yfinance .info.

    Returns keys matching company_profiles columns plus extras used by
    downstream calculations: beta, forward_eps, dividend_rate,
    shares_outstanding, current_price. Missing fields map to None.
    """
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:
        raise ValueError(f"{ticker}: profile fetch failed ({exc})")
    if not isinstance(info, dict) or not info:
        raise ValueError(f"{ticker}: no profile data returned")

    employees = info.get("fullTimeEmployees")
    try:
        employees = int(employees) if employees is not None else None
    except (TypeError, ValueError):
        employees = None

    shares = _float_or_none(info.get("sharesOutstanding"))
    if shares is None:
        shares = _float_or_none(info.get("impliedSharesOutstanding"))
    price = _float_or_none(info.get("currentPrice"))
    if price is None:
        price = _float_or_none(info.get("regularMarketPrice"))

    return {
        "ticker": ticker,
        "name": info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _float_or_none(info.get("marketCap")),
        "employees": employees,
        "country": info.get("country"),
        "business_summary": info.get("longBusinessSummary"),
        "beta": _float_or_none(info.get("beta")),
        "forward_eps": _float_or_none(info.get("forwardEps")),
        "dividend_rate": _float_or_none(info.get("dividendRate")),
        "shares_outstanding": shares,
        "current_price": price,
    }


def _build_rows(ticker, income, balance, cashflow, report_type) -> list[dict]:
    """Merge income + balance + cashflow statements into row dicts.

    Statements share the same column layout (Timestamps, newest first);
    rows are keyed by fiscal date and returned newest first.
    """
    dates = sorted(
        {c for df in (income, balance, cashflow) if df is not None for c in df.columns},
        reverse=True,
    )
    rows = []
    for col in dates:
        ocf = _item(cashflow, col, "Operating Cash Flow",
                    "Total Cash From Operating Activities")
        capex = _item(cashflow, col, "Capital Expenditure", "Capital Expenditures")
        # CapEx is negative on the cashflow statement, so FCF = OCF + CapEx.
        # A missing CapEx line is treated as 0 (documented approximation).
        fcf = ocf + (capex or 0.0) if ocf is not None else None

        debt = _item(balance, col, "Total Debt")
        if debt is None and balance is not None:
            parts = (
                _item(balance, col, "Short Long Term Debt"),
                _item(balance, col, "Long Term Debt"),
            )
            present = [p for p in parts if p is not None]
            debt = sum(present) if present else None

        rows.append({
            "ticker": ticker,
            "fiscal_date": _iso_date(col),
            "report_type": report_type,
            "revenue": _item(income, col, "Total Revenue"),
            "gross_profit": _item(income, col, "Gross Profit"),
            "operating_income": _item(income, col, "Operating Income"),
            "net_income": _item(income, col, "Net Income"),
            "eps": _item(income, col, "Diluted EPS"),
            "total_assets": _item(balance, col, "Total Assets"),
            "total_liabilities": _item(
                balance, col, "Total Liabilities Net Minority Interest"),
            "shareholders_equity": _item(
                balance, col, "Stockholders Equity",
                "Total Equity Gross Minority Interest"),
            "total_debt": debt,
            "cash_and_equivalents": _item(
                balance, col, "Cash And Cash Equivalents",
                "Cash Cash Equivalents And Short Term Investments"),
            "operating_cash_flow": ocf,
            "free_cash_flow": fcf,
            "capital_expenditure": capex,
            "shares_outstanding": _item(
                income, col, "Diluted Average Shares", "Basic Average Shares"),
            "depreciation_amortization": _item(
                cashflow, col, "Depreciation And Amortization", "Depreciation"),
            "interest_expense": _item(income, col, "Interest Expense"),
            "current_assets": _item(
                balance, col, "Current Assets", "Total Current Assets"),
            "current_liabilities": _item(
                balance, col, "Current Liabilities", "Total Current Liabilities"),
        })
    return rows


def fetch_financials(ticker: str) -> list[dict]:
    """Fetch annual + quarterly financial rows ready for upsert_quarterly_financials.

    Returns annual ('10-K') rows first, then quarterly ('10-Q') rows, each
    block newest-first. When a fiscal date appears in both (the fiscal-year
    end quarter), the quarterly row should win on upsert — this keeps TTM
    sums intact; annual CAGRs then use the remaining annual rows.
    """
    try:
        t = yf.Ticker(ticker)
        q_income = _statement(t, "quarterly_income_stmt", "quarterly_financials")
        a_income = _statement(t, "income_stmt", "financials")
        q_balance = _statement(t, "quarterly_balance_sheet")
        a_balance = _statement(t, "balance_sheet")
        q_cashflow = _statement(t, "quarterly_cashflow")
        a_cashflow = _statement(t, "cashflow")
    except Exception as exc:
        raise ValueError(f"{ticker}: financial statements fetch failed ({exc})")

    if all(df is None for df in (q_income, a_income, q_balance, a_balance,
                                 q_cashflow, a_cashflow)):
        raise ValueError(f"{ticker}: no financial statements available")

    annual = _build_rows(ticker, a_income, a_balance, a_cashflow, "10-K")
    quarterly = _build_rows(ticker, q_income, q_balance, q_cashflow, "10-Q")
    return annual + quarterly


def fetch_earnings(ticker: str) -> list[dict]:
    """Fetch earnings history rows from the yfinance .earnings_dates table.

    surprise_pct is stored as given by yfinance (e.g. 5.2 means +5.2%).
    Guidance and call_sentiment are None (not reliably available).
    Rows are returned newest fiscal date first.
    """
    try:
        df = yf.Ticker(ticker).earnings_dates
    except Exception as exc:
        raise ValueError(f"{ticker}: earnings dates fetch failed ({exc})")
    if df is None or df.empty:
        return []

    rows = []
    for index, record in df.iterrows():
        rows.append({
            "ticker": ticker,
            "fiscal_date": _iso_date(index),
            "eps_actual": _float_or_none(record.get("Reported EPS")),
            "eps_estimate": _float_or_none(record.get("EPS Estimate")),
            "revenue_actual": None,
            "revenue_estimate": None,
            "surprise_pct": _float_or_none(record.get("Surprise(%)")),
            "guidance_eps_low": None,
            "guidance_eps_high": None,
            "guidance_revenue_low": None,
            "guidance_revenue_high": None,
            "call_sentiment": None,
        })
    rows.sort(key=lambda r: r["fiscal_date"], reverse=True)
    return rows


def fetch_price(ticker: str) -> float | None:
    """Latest close from 5d history, falling back to the profile price."""
    try:
        t = yf.Ticker(ticker)
        history = t.history(period="5d")
    except Exception as exc:
        raise ValueError(f"{ticker}: price history fetch failed ({exc})")
    if history is not None and not history.empty:
        price = _float_or_none(history["Close"].iloc[-1])
        if price is not None:
            return price
    try:
        info = t.info
    except Exception as exc:
        raise ValueError(f"{ticker}: price fallback fetch failed ({exc})")
    if isinstance(info, dict):
        price = _float_or_none(info.get("currentPrice"))
        if price is None:
            price = _float_or_none(info.get("regularMarketPrice"))
        return price
    return None


def fetch_risk_free_rate() -> float:
    """10-year Treasury yield (^TNX) as a decimal; DEFAULT_RISK_FREE_RATE on failure."""
    try:
        history = yf.Ticker("^TNX").history(period="5d")
        if history is not None and not history.empty:
            rate = _float_or_none(history["Close"].iloc[-1])
            if rate is not None and rate > 0:
                return rate / 100
    except Exception:
        pass
    return DEFAULT_RISK_FREE_RATE
