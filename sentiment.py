"""News headline fetching and VADER sentiment scoring.

Provider chain per symbol: Finnhub company-news (FINNHUB_API_KEY) ->
Alpha Vantage NEWS_SENTIMENT (ALPHAVANTAGE_API_KEY) -> yfinance .news.
Keys are read from the environment (.env loaded like notify.py); when a
key is absent the next provider is used, so the module works with no
keys at all. Per-symbol errors print a warning and yield no headlines.

Scoring uses vaderSentiment (no nltk corpora needed): each headline
title gets a compound score in -1..+1 and the symbol score is the mean.
"""

import os
import sys
from datetime import datetime, timedelta

import requests
import yfinance as yf
from dotenv import load_dotenv

# Load environment variables from .env if present (same as notify.py).
load_dotenv()

# A headline is (source, title).
Headline = tuple[str, str]

FINNHUB_URL = "https://finnhub.io/api/v1"
ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
REQUEST_TIMEOUT = 15

SENTIMENT_THRESHOLDS = [
    (0.5, "Bullish"),
    (0.15, "Leaning Bullish"),
    (-0.15, "Neutral"),
    (-0.5, "Leaning Bearish"),
]

_analyzer = None


def _vader():
    """Lazily construct the VADER analyzer (its lexicon load is not free)."""
    global _analyzer
    if _analyzer is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        _analyzer = SentimentIntensityAnalyzer()
    return _analyzer


def news_source() -> str:
    """Provider(s) actually serving headlines, for report footers.

    Before the first fetch, predicts from the configured keys.
    """
    if _providers_used:
        return "+".join(sorted(_providers_used))
    if _finnhub_key():
        return "finnhub"
    if _alphavantage_key():
        return "alphavantage"
    return "yfinance"


# Providers that served at least one request this run (fallbacks included).
_providers_used: set[str] = set()

# A key that answered 401/403 is dead; stop retrying it for this run.
_finnhub_disabled = False
_alphavantage_disabled = False


def _finnhub_key() -> str | None:
    if _finnhub_disabled:
        return None
    return os.getenv("FINNHUB_API_KEY")


def _alphavantage_key() -> str | None:
    if _alphavantage_disabled:
        return None
    return os.getenv("ALPHAVANTAGE_API_KEY")


def _reject_key(exc: Exception, name: str) -> None:
    """Disable a provider key for this run when the API rejects it."""
    global _finnhub_disabled, _alphavantage_disabled
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return
    if exc.response.status_code not in (401, 403):
        return
    if name == "finnhub" and not _finnhub_disabled:
        _finnhub_disabled = True
    elif name == "alphavantage" and not _alphavantage_disabled:
        _alphavantage_disabled = True
    else:
        return
    print(
        f"Warning: {name.upper()}_API_KEY rejected "
        f"({exc.response.status_code}); using fallbacks for the rest of this run",
        file=sys.stderr,
    )


def _finnhub_headlines(symbol: str, hours: int) -> list[Headline]:
    """Company news from Finnhub for the trailing `hours` (day granularity)."""
    token = _finnhub_key()
    today = datetime.now().date()
    start = today - timedelta(days=max(1, hours // 24))
    response = requests.get(
        f"{FINNHUB_URL}/company-news",
        params={
            "symbol": symbol,
            "from": start.isoformat(),
            "to": today.isoformat(),
            "token": token,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list):
        return []
    return [
        (item.get("source", ""), item["headline"])
        for item in items
        if item.get("headline")
    ]


def _alphavantage_headlines(symbol: str, hours: int) -> list[Headline]:
    """News feed from Alpha Vantage NEWS_SENTIMENT, filtered to `hours`."""
    api_key = _alphavantage_key()
    response = requests.get(
        ALPHAVANTAGE_URL,
        params={
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "apikey": api_key,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if "feed" not in payload:
        # Rate-limit notes and error messages arrive as HTTP 200 without a
        # feed; treat them as failures so the chain falls through.
        raise ValueError(f"unexpected Alpha Vantage response: {list(payload)}")
    feed = payload["feed"]
    cutoff = datetime.now() - timedelta(hours=hours)
    items: list[Headline] = []
    for item in feed:
        title = item.get("title")
        if not title:
            continue
        published = item.get("time_published", "")
        try:
            when = datetime.strptime(published, "%Y%m%dT%H%M%S")
        except ValueError:
            when = None
        if when is not None and when < cutoff:
            continue
        items.append((item.get("source", ""), title))
    return items


def fetch_yfinance_headlines(symbol: str, max_items: int = 10) -> list[Headline]:
    """Recent (source, title) items from yfinance .news."""
    items: list[Headline] = []
    for raw in yf.Ticker(symbol).news or []:
        content = raw.get("content", raw)
        title = content.get("title")
        if not title:
            continue
        provider = content.get("provider")
        if isinstance(provider, dict):
            source = provider.get("displayName", "")
        else:
            source = content.get("publisher", "")
        items.append((source, title))
        if len(items) >= max_items:
            break
    return items


def fetch_headlines(symbol: str, hours: int = 24) -> list[Headline]:
    """Recent headlines for a symbol from the best available provider.

    Falls back down the provider chain on missing keys or request errors;
    per-symbol failures warn and return [].
    """
    providers = [
        ("finnhub", _finnhub_key, _finnhub_headlines),
        ("alphavantage", _alphavantage_key, _alphavantage_headlines),
    ]
    for name, key_fn, fetch_fn in providers:
        if not key_fn():
            continue
        try:
            headlines = fetch_fn(symbol, hours)
            _providers_used.add(name)
            return headlines
        except Exception as exc:
            _reject_key(exc, name)
            print(
                f"Warning: {symbol}: {name} headline fetch failed ({exc}); "
                "trying next provider",
                file=sys.stderr,
            )
    try:
        headlines = fetch_yfinance_headlines(symbol)
        _providers_used.add("yfinance")
        return headlines
    except Exception as exc:
        print(f"Warning: {symbol}: news fetch failed ({exc})", file=sys.stderr)
        return []


def fetch_market_headlines(max_items: int = 3) -> list[Headline]:
    """Top overnight market headlines: Finnhub general news, else SPY news."""
    if _finnhub_key():
        try:
            response = requests.get(
                f"{FINNHUB_URL}/news",
                params={"category": "general", "token": _finnhub_key()},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            items = response.json()
            if isinstance(items, list):
                headlines = [
                    (item.get("source", ""), item["headline"])
                    for item in items
                    if item.get("headline")
                ]
                if headlines:
                    _providers_used.add("finnhub")
                    return headlines[:max_items]
        except Exception as exc:
            _reject_key(exc, "finnhub")
            print(
                f"Warning: market headline fetch failed ({exc}); "
                "falling back to SPY news",
                file=sys.stderr,
            )
    try:
        headlines = fetch_yfinance_headlines("SPY", max_items=max_items)
        _providers_used.add("yfinance")
        return headlines
    except Exception as exc:
        print(f"Warning: market news fetch failed ({exc})", file=sys.stderr)
        return []


def score_sentiment(headlines: list[Headline]) -> float | None:
    """Mean VADER compound score of the headline titles, -1..+1.

    Returns None when there are no headlines to score.
    """
    if not headlines:
        return None
    analyzer = _vader()
    scores = [analyzer.polarity_scores(title)["compound"] for _source, title in headlines]
    return round(sum(scores) / len(scores), 3)


def sentiment_label(score: float | None) -> str:
    """Map a -1..+1 sentiment score to its label."""
    if score is None:
        return "n/a"
    for threshold, label in SENTIMENT_THRESHOLDS:
        if score >= threshold:
            return label
    return "Bearish"


def sentiment_bonus(score: float | None) -> int:
    """Confluence-score bonus: +5 bullish, -5 bearish, 0 otherwise."""
    if score is None:
        return 0
    if score >= 0.5:
        return 5
    if score <= -0.5:
        return -5
    return 0
