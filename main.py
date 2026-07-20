import yfinance as yf


def fetch_latest_price(ticker: str) -> float | None:
    """Fetch the latest closing price for a given ticker."""
    try:
        data = yf.Ticker(ticker)
        history = data.history(period="1d")
        if history.empty:
            return None
        return float(history["Close"].iloc[-1])
    except Exception:
        return None


def main() -> None:
    ticker = "AAPL"
    price = fetch_latest_price(ticker)
    if price is None:
        print(f"Could not fetch price for {ticker}")
        raise SystemExit(1)
    print(f"{ticker}: ${price:.2f}")


if __name__ == "__main__":
    main()
