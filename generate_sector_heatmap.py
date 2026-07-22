"""Sector allocation heatmap: portfolio sectors as a Plotly treemap.

Groups the portfolio holdings into sectors, sizes each box by the sector's
share of the total portfolio value, and colors it green/red by the sector's
value-weighted daily change. Sectors holding semiconductor stocks are split
into "Semiconductors" and "<Sector> (other)" sub-boxes. Writes a
self-contained sector_heatmap.html.
"""

import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf

from db import (
    get_holdings,
    get_latest_quotes,
    get_sectors,
    init_db,
    resolve_db_path,
    upsert_sector,
)
from ticker import fetch_live_quotes

OUTPUT_PATH = Path(__file__).with_name("sector_heatmap.html")
UNKNOWN_SECTOR = "Unknown"
SEMICONDUCTOR_INDUSTRY = "Semiconductors"
# Colorscale symmetric range (+-%) when no sector exceeds it.
MIN_COLOR_RANGE = 3.0


def fetch_sector_info(symbol: str) -> tuple[str | None, str | None]:
    """Fetch (sector, industry) for a symbol from yfinance.

    Either value is None when yfinance does not report it. Raises ValueError
    with a per-symbol message on fetch failure.
    """
    try:
        info = yf.Ticker(symbol).info
    except Exception as exc:
        raise ValueError(f"{symbol}: sector fetch failed ({exc})")
    if not isinstance(info, dict):
        return None, None
    return info.get("sector") or None, info.get("industry") or None


def _new_agg() -> dict:
    return {"value": 0.0, "chg_base": 0.0, "chg_sum": 0.0}


def _add_to_agg(agg: dict, value: float, change: float | None) -> None:
    agg["value"] += value
    if change is not None:
        agg["chg_base"] += value
        agg["chg_sum"] += value * float(change)


def aggregate_sectors(
    holdings: dict[str, dict],
    quotes: dict[str, dict],
    sectors: dict[str, dict],
) -> list[dict]:
    """Group holdings into sectors: total value, portfolio weight, daily change.

    holdings: {symbol: {"quantity": float}} (only quantity is used)
    quotes:   {symbol: {"price": float, "change_pct": float|None}}
    sectors:  {symbol: {"sector": str, "industry": str|None}}

    Only positions with quantity > 0 and a known price count. Symbols without
    a sector fall into "Unknown". Any sector holding semiconductor-industry
    stocks is split into child rows "Semiconductors" and "<Sector> (other)"
    whose parent is that sector. change_pct is the value-weighted average of
    the stocks' daily changes within the group, or None when no stock has a
    change value.

    Returns rows as {name, parent, value, weight_pct, change_pct}: top-level
    sectors (parent=None) sorted by value descending, each immediately
    followed by its child rows (parent=sector name), also value-descending.
    """
    positions = []
    for symbol, holding in holdings.items():
        quantity = float(holding.get("quantity") or 0)
        quote = quotes.get(symbol)
        if quantity <= 0 or not quote or not quote.get("price"):
            continue
        value = quantity * float(quote["price"])
        info = sectors.get(symbol) or {}
        sector = info.get("sector") or UNKNOWN_SECTOR
        industry = info.get("industry") or ""
        positions.append((sector, industry, value, quote.get("change_pct")))

    total = sum(value for _, _, value, _ in positions)
    if total <= 0:
        return []

    semi_sectors = {s for s, ind, _, _ in positions if ind == SEMICONDUCTOR_INDUSTRY}
    leaves: dict[str, dict] = {}  # sectors without a semiconductor split
    children: dict[tuple[str, str], dict] = {}  # (sector, child name) -> agg
    for sector, industry, value, change in positions:
        if sector in semi_sectors:
            child = (
                SEMICONDUCTOR_INDUSTRY
                if industry == SEMICONDUCTOR_INDUSTRY
                else f"{sector} (other)"
            )
            _add_to_agg(children.setdefault((sector, child), _new_agg()), value, change)
        else:
            _add_to_agg(leaves.setdefault(sector, _new_agg()), value, change)

    def make_row(name: str, parent: str | None, agg: dict) -> dict:
        return {
            "name": name,
            "parent": parent,
            "value": agg["value"],
            "weight_pct": agg["value"] / total * 100,
            "change_pct": (
                agg["chg_sum"] / agg["chg_base"] if agg["chg_base"] > 0 else None
            ),
        }

    sector_values = {sector: agg["value"] for sector, agg in leaves.items()}
    for (sector, _child), agg in children.items():
        sector_values[sector] = sector_values.get(sector, 0.0) + agg["value"]

    rows = []
    for sector in sorted(sector_values, key=sector_values.get, reverse=True):
        if sector in leaves:
            rows.append(make_row(sector, None, leaves[sector]))
            continue
        parent_agg = _new_agg()
        child_rows = []
        for (s, child), agg in children.items():
            if s != sector:
                continue
            _add_to_agg(parent_agg, agg["value"], None)
            parent_agg["chg_base"] += agg["chg_base"]
            parent_agg["chg_sum"] += agg["chg_sum"]
            child_rows.append(make_row(child, sector, agg))
        rows.append(make_row(sector, None, parent_agg))
        rows.extend(sorted(child_rows, key=lambda r: r["value"], reverse=True))
    return rows


def build_heatmap_html(rows: list[dict], generated_at: str) -> str:
    """Render the sector rows as a self-contained Plotly treemap HTML page.

    Rows with a parent become child boxes inside that sector's box.
    """
    import plotly.graph_objects as go

    changes = [r["change_pct"] for r in rows if r["change_pct"] is not None]
    color_range = MIN_COLOR_RANGE
    if changes:
        color_range = max(color_range, max(abs(c) for c in changes))

    fig = go.Figure(
        go.Treemap(
            ids=[
                f"{r['parent']}/{r['name']}" if r["parent"] else r["name"]
                for r in rows
            ],
            labels=[r["name"] for r in rows],
            parents=[r["parent"] or "" for r in rows],
            values=[r["value"] for r in rows],
            branchvalues="total",
            text=[
                "N/A" if r["change_pct"] is None else f"{r['change_pct']:+.2f}%"
                for r in rows
            ],
            textinfo="label+text",
            customdata=[r["weight_pct"] for r in rows],
            marker=dict(
                colors=[r["change_pct"] if r["change_pct"] is not None else 0 for r in rows],
                colorscale=[[0.0, "#d62728"], [0.5, "#4a4a4a"], [1.0, "#2ca02c"]],
                cmin=-color_range,
                cmax=color_range,
                showscale=True,
                colorbar=dict(title="Daily<br>change %", ticksuffix="%"),
                line=dict(width=2, color="#1a1a1a"),
            ),
            hovertemplate=(
                "%{label}<br>Value: $%{value:,.2f}"
                "<br>Weight: %{customdata:.1f}% of portfolio"
                "<br>Change: %{text}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        title=f"Sector Allocation Heatmap — {generated_at}",
        margin=dict(t=50, l=10, r=10, b=10),
    )
    return fig.to_html(include_plotlyjs=True, full_html=True, config={"responsive": True})


def generate_sector_heatmap(
    settings: dict,
    test: bool = False,
    output_path: Path = OUTPUT_PATH,
) -> Path | None:
    """Generate sector_heatmap.html from the current portfolio holdings.

    Prices come from a batched live yfinance fetch, falling back to the local
    database; sector/industry pairs are cached in the sectors table (fetched
    from yfinance on first use, "Unknown"/"" on failure). Console-only
    output. Returns the output path, or None when there is nothing to plot.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_path = resolve_db_path(settings["db_path"])

    conn = init_db(db_path)
    try:
        holdings = get_holdings(conn)
        symbols = [s for s, h in holdings.items() if (h.get("quantity") or 0) > 0]
        if not symbols:
            print(f"{timestamp} No holdings with quantity > 0; heatmap not generated")
            return None

        sectors = get_sectors(conn)
        # industry=None marks rows cached before the industry column existed.
        missing_sectors = [
            s
            for s in symbols
            if s not in sectors or sectors[s]["industry"] is None
        ]
        for symbol in missing_sectors:
            try:
                sector, industry = fetch_sector_info(symbol)
            except ValueError as exc:
                print(f"Warning: {exc}", file=sys.stderr)
                sector, industry = None, None
            sector = sector or UNKNOWN_SECTOR
            industry = industry or ""
            upsert_sector(conn, symbol, sector, industry, datetime.now().isoformat())
            sectors[symbol] = {"sector": sector, "industry": industry}
        if missing_sectors:
            conn.commit()

        quotes = fetch_live_quotes(symbols)
        missing_quotes = [s for s in symbols if s not in quotes]
        if missing_quotes:
            quotes.update(get_latest_quotes(conn, missing_quotes))
    finally:
        conn.close()

    rows = aggregate_sectors(holdings, quotes, sectors)
    if not rows:
        print(f"{timestamp} No priced positions; heatmap not generated")
        return None

    output_path.write_text(build_heatmap_html(rows, timestamp))

    for row in rows:
        change = "N/A" if row["change_pct"] is None else f"{row['change_pct']:+.2f}%"
        indent = "  " if row["parent"] else ""
        print(
            f"{timestamp} {indent}{row['name']}: {row['weight_pct']:.1f}% of portfolio "
            f"(${row['value']:,.2f}, {change})"
        )
    print(f"{timestamp} Sector heatmap written to {output_path}")
    return output_path
