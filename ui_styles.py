"""Shared visual language for the stockticker HTML surfaces."""

import html


REPORT_THEME = """
  :root {
    color-scheme: dark;
    --bg: #07111f;
    --surface: #0d1a2b;
    --surface-2: #122238;
    --border: #20344e;
    --text: #e7eef8;
    --muted: #8fa3ba;
    --accent: #58a6ff;
    --accent-2: #5eead4;
    --up: #34d399;
    --down: #fb7185;
    --shadow: 0 18px 48px rgba(0, 0, 0, .22);
  }
  * { box-sizing: border-box; }
  body {
    background:
      radial-gradient(circle at 12% -10%, rgba(88,166,255,.13), transparent 30rem),
      radial-gradient(circle at 88% 0%, rgba(94,234,212,.08), transparent 25rem),
      var(--bg) !important;
    color: var(--text) !important;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif !important;
    line-height: 1.5;
  }
  .app-nav {
    position: sticky; top: 0; z-index: 20;
    display: flex; align-items: center; gap: 8px;
    min-height: 58px; padding: 9px max(18px, calc((100vw - 1180px)/2));
    background: rgba(7,17,31,.86); border-bottom: 1px solid rgba(143,163,186,.16);
    backdrop-filter: blur(16px);
  }
  .app-nav .brand {
    display: flex; align-items: center; gap: 10px; margin-right: auto;
    color: var(--text); text-decoration: none; font-weight: 750; letter-spacing: -.02em;
  }
  .brand-mark {
    display: grid; place-items: center; width: 32px; height: 32px; border-radius: 10px;
    color: #06101c; background: linear-gradient(135deg,var(--accent),var(--accent-2));
    box-shadow: 0 8px 24px rgba(88,166,255,.22);
  }
  .app-nav .nav-link {
    color: var(--muted); text-decoration: none; font-size: .84rem; font-weight: 650;
    padding: 7px 10px; border: 1px solid transparent; border-radius: 9px;
  }
  .app-nav .nav-link:hover { color: var(--text); background: rgba(255,255,255,.04); }
  .app-nav .nav-link.active {
    color: var(--text); background: rgba(88,166,255,.11); border-color: rgba(88,166,255,.22);
  }
  .container { max-width: 1180px !important; padding: 30px 20px 64px !important; }
  h1 { margin: 0 0 4px !important; font-size: clamp(1.55rem,3vw,2.25rem) !important;
       line-height: 1.15; letter-spacing: -.035em; }
  h2 { color: var(--text); letter-spacing: -.015em; }
  h3, .meta, th, .kv .k, summary { color: var(--muted) !important; }
  .card, .legend, .guide {
    background: linear-gradient(145deg,rgba(18,34,56,.92),rgba(13,26,43,.96)) !important;
    border: 1px solid var(--border) !important; border-radius: 16px !important;
    box-shadow: var(--shadow); padding: 20px !important; margin: 18px 0 !important;
  }
  .card:hover { border-color: #2b496d !important; }
  table { border-collapse: separate !important; border-spacing: 0; font-variant-numeric: tabular-nums; }
  th { background: rgba(7,17,31,.34); text-transform: uppercase; letter-spacing: .055em;
       font-size: .72rem; }
  th, td { padding: 10px 11px !important; border-bottom-color: rgba(143,163,186,.12) !important; }
  tbody tr:last-child td { border-bottom: 0 !important; }
  tbody tr:hover td { background: rgba(88,166,255,.035); }
  .pill { border-radius: 999px !important; padding: 3px 9px !important; font-weight: 700; }
  .bull { color: var(--up) !important; }
  .bear { color: var(--down) !important; }
  .neutral { color: var(--muted) !important; }
  .dcfbox { background: rgba(7,17,31,.5) !important; border-color: var(--border) !important;
            border-radius: 12px !important; }
  .pbar, .relbar { background: rgba(143,163,186,.16) !important; }
  details { border-top: 1px solid rgba(143,163,186,.12); padding-top: 10px; }
  summary { padding: 5px 0; font-weight: 650; }
  @media (max-width: 720px) {
    .app-nav { overflow-x: auto; padding: 9px 12px; }
    .app-nav .brand span:last-child { display: none; }
    .app-nav .nav-link { white-space: nowrap; }
    .container { padding: 22px 12px 48px !important; }
    .card { padding: 15px !important; overflow-x: auto; }
  }
"""


def nav_html(active: str) -> str:
    links = (
        ("dashboard", "./", "Dashboard"),
        ("fundamentals", "fundamental_dashboard.html", "Fundamentals"),
        ("premarket", "premarket_report.html", "Pre-market"),
        ("indicators", "indicators_table.html", "Technical"),
        ("heatmap", "sector_heatmap.html", "Heatmap"),
    )
    rendered = "".join(
        f'<a class="nav-link{" active" if key == active else ""}" '
        f'href="{html.escape(href)}">{html.escape(label)}</a>'
        for key, href, label in links
    )
    return (
        '<nav class="app-nav">'
        '<a class="brand" href="./"><span class="brand-mark">↗</span>'
        "<span>Stockticker</span></a>"
        f"{rendered}</nav>"
    )
