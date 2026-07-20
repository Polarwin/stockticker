"""Remove duplicate lines from watchlist.txt while preserving order."""

from pathlib import Path

WATCHLIST_PATH = Path(__file__).with_name("watchlist.txt")


def dedup_watchlist(path: Path = WATCHLIST_PATH) -> None:
    if not path.exists():
        print(f"Watchlist file not found: {path}")
        raise SystemExit(1)

    lines = path.read_text().splitlines()
    seen = set()
    deduped = []
    for line in lines:
        normalized = line.strip().upper()
        if not normalized or normalized.startswith("#"):
            deduped.append(line)
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(line)

    path.write_text("\n".join(deduped) + "\n")
    removed = len(lines) - len(deduped)
    print(f"Removed {removed} duplicate line(s).")


if __name__ == "__main__":
    dedup_watchlist()
