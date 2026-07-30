"""Market-moving macro news crawler.

Periodically searches the news providers for macro topics (Fed/rates,
geopolitics, oil, tariffs, big tech/regulation) — not just watchlist
stocks. High-priority topics alert via Telegram immediately; the rest
accumulate into a digest the premarket report renders. The enable
toggle, interval, and topic definitions live in settings.json
(market_news_* keys); the web loop re-reads the toggle each round so
enabling/disabling works without a restart.

Provider chain per round: Futu keyword search (one query per topic,
within Futu's 10-searches-per-30s limit) -> Finnhub general news ->
yfinance SPY news; topic keywords filter the pool in every case.
Classification is deliberately rules-based: deterministic, free, and it
cannot hallucinate. An AI summary layer can be added on top later.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import requests

import futu_source
from sentiment import _alphavantage_key, _finnhub_key, fetch_yfinance_headlines

STATE_PATH = Path(__file__).resolve().parent / "data" / "market_news_seen.json"
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"
REQUEST_TIMEOUT = 15
MAX_DIGEST_ITEMS = 40
SEEN_TTL_DAYS = 7
MAX_SEEN_KEYS = 5000

# Each topic: one Futu search query (keeps the round within the Futu
# rate limit), keywords to filter/classify hits, and a priority —
# "high" alerts via Telegram immediately, "digest" feeds the premarket
# report's market-news section. Overridable via market_news_topics in
# settings.json.
DEFAULT_TOPICS = {
    "Fed & Rates": {
        "query": "Federal Reserve",
        "keywords": [
            "fed", "fomc", "interest rate", "rate cut", "rate hike",
            "cpi", "inflation", "powell", "treasury",
        ],
        "priority": "high",
    },
    "Geopolitics": {
        "query": "war",
        "keywords": [
            "war", "ceasefire", "middle east", "iran", "israel",
            "ukraine", "russia", "missile", "sanctions", "nato",
        ],
        "priority": "high",
    },
    "Oil & Energy": {
        "query": "oil",
        "keywords": ["oil", "crude", "opec", "brent", "wti"],
        "priority": "digest",
    },
    "Trade & Tariffs": {
        "query": "tariff",
        "keywords": [
            "tariff", "trade war", "trade deal", "export curb",
            "export control", "chip ban",
        ],
        "priority": "digest",
    },
    "Big Tech & Regulation": {
        "query": "antitrust",
        "keywords": [
            "antitrust", "regulation", "breakup", "sec probe",
            "ai chip", "export control",
        ],
        "priority": "digest",
    },
}


def topics_for(settings: dict) -> dict:
    """Topic config from settings, falling back to DEFAULT_TOPICS."""
    topics = settings.get("market_news_topics")
    return topics if isinstance(topics, dict) and topics else DEFAULT_TOPICS


def match_topic(title: str, keywords: list[str]) -> bool:
    """Case-insensitive substring match of any keyword in the title."""
    lowered = title.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _item_key(item: dict) -> str:
    """Dedup key: URL when present, else the normalized title."""
    url = str(item.get("url") or "").strip()
    if url:
        return url
    return " ".join(str(item.get("title") or "").lower().split())


def _parse_time(value: str) -> datetime | None:
    """Futu's publish_time: full timestamp, or 'M/D' for recent items."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d"):
        try:
            parsed = datetime.strptime(value.strip(), fmt)
            if fmt == "%m/%d":
                parsed = parsed.replace(year=datetime.now().year)
            return parsed
        except ValueError:
            continue
    return None


def load_state(path: Path = STATE_PATH) -> dict:
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state is not an object")
    except (OSError, ValueError, json.JSONDecodeError):
        state = {}
    state.setdefault("seen", {})
    state.setdefault("seeded", [])
    state.setdefault("digest", [])
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def _prune_seen(state: dict, now: datetime) -> None:
    """Drop seen keys older than SEEN_TTL_DAYS; cap the dict size."""
    cutoff = (now - timedelta(days=SEEN_TTL_DAYS)).isoformat()
    seen = state["seen"]
    for key in [k for k, ts in seen.items() if str(ts) < cutoff]:
        del seen[key]
    if len(seen) > MAX_SEEN_KEYS:
        for key in sorted(seen, key=lambda k: str(seen[k]))[: len(seen) - MAX_SEEN_KEYS]:
            del seen[key]


def _futu_pool(topics: dict) -> dict[str, list[dict]]:
    """Per-topic records from Futu keyword search, filtered by keywords."""
    pool: dict[str, list[dict]] = {}
    for name, topic in topics.items():
        records = futu_source.search_news(topic["query"], limit=10)
        pool[name] = [
            {
                "topic": name,
                "title": str(r.get("title") or "").strip(),
                "source": str(r.get("source") or "").strip(),
                "url": str(r.get("url") or "").strip(),
                "published": str(r.get("publish_time") or "").strip(),
            }
            for r in records
            if match_topic(str(r.get("title") or ""), topic["keywords"])
        ]
    return pool


def _generic_pool(topics: dict, headlines: list[tuple[str, str]]) -> dict:
    """Classify (source, title) headlines into topics by keywords."""
    pool: dict[str, list[dict]] = {name: [] for name in topics}
    for source, title in headlines:
        for name, topic in topics.items():
            if match_topic(title, topic["keywords"]):
                pool[name].append({
                    "topic": name, "title": title, "source": source,
                    "url": "", "published": "",
                })
    return pool


def fetch_pool(topics: dict) -> dict[str, list[dict]]:
    """News pool for all topics from the best available provider."""
    try:
        return _futu_pool(topics)
    except Exception as exc:
        print(f"Warning: Futu market-news search failed ({exc}); "
              "trying next provider", file=sys.stderr)
    if _finnhub_key():
        try:
            response = requests.get(
                FINNHUB_NEWS_URL,
                params={"category": "general", "token": _finnhub_key()},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            items = response.json()
            if isinstance(items, list):
                return _generic_pool(topics, [
                    (item.get("source", ""), item["headline"])
                    for item in items
                    if item.get("headline")
                ])
        except Exception as exc:
            print(f"Warning: Finnhub market-news fetch failed ({exc})",
                  file=sys.stderr)
    try:
        return _generic_pool(topics, fetch_yfinance_headlines("SPY", 20))
    except Exception as exc:
        print(f"Warning: market-news fetch failed ({exc})", file=sys.stderr)
        return {name: [] for name in topics}


def recent_digest(path: Path = STATE_PATH, max_items: int = 15) -> list[dict]:
    """Newest digest items for the premarket report (newest first)."""
    return load_state(path)["digest"][:max_items]


def run_round(
    settings: dict,
    notify: Callable[[str], None] | None = None,
    state_path: Path = STATE_PATH,
    now: datetime | None = None,
) -> dict:
    """One crawl round: fetch, dedup, alert high-priority, store digest.

    The first round per topic only seeds the seen-set (no alert flood).
    Returns {"alerts": int, "digest": int} counts of new items.
    """
    now = now or datetime.now()
    topics = topics_for(settings)
    state = load_state(state_path)
    _prune_seen(state, now)
    seen = state["seen"]
    seeded = set(state["seeded"])
    fresh_cutoff = now - timedelta(hours=48)
    counts = {"alerts": 0, "digest": 0}

    pool = fetch_pool(topics)
    for name, topic in topics.items():
        first_run = name not in seeded
        for item in pool.get(name, []):
            if not item["title"]:
                continue
            published = _parse_time(item["published"])
            if published is not None and published < fresh_cutoff:
                continue
            key = _item_key(item)
            if not key or key in seen:
                continue
            seen[key] = now.isoformat()
            if first_run:
                continue
            record = {
                "topic": name, "title": item["title"],
                "source": item["source"], "url": item["url"],
                "ts": now.isoformat(),
            }
            if topic.get("priority") == "high":
                if notify is not None:
                    lines = [f"🌍 {name}", item["title"]]
                    metadata = " · ".join(
                        v for v in (item["source"], item["published"]) if v
                    )
                    if metadata:
                        lines.append(metadata)
                    if item["url"]:
                        lines.append(item["url"])
                    try:
                        notify("\n".join(lines))
                    except Exception as exc:
                        print(f"Warning: market-news alert failed ({exc})",
                              file=sys.stderr)
                counts["alerts"] += 1
            else:
                state["digest"].insert(0, record)
                counts["digest"] += 1
        seeded.add(name)

    state["seeded"] = sorted(seeded)
    state["digest"] = state["digest"][:MAX_DIGEST_ITEMS]
    save_state(state, state_path)
    return counts
