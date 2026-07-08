"""Generate a daily HTML market summary using FinanceDataReader.

The script is designed for GitHub Actions, but it can also be run locally:

    python src/main.py

It writes both a dated report and reports/latest.html.
"""

from __future__ import annotations

import html
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import FinanceDataReader as fdr

KST = ZoneInfo("Asia/Seoul")
REPORT_DIR = Path("reports")
LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class MarketItem:
    section: str
    name: str
    symbol: str


@dataclass(frozen=True)
class MarketResult:
    section: str
    name: str
    symbol: str
    value: float | None
    change_percent: float | None
    direction: str
    trade_date: str | None
    error: str | None = None


MARKET_ITEMS: tuple[MarketItem, ...] = (
    MarketItem("국내", "코스피", "KS11"),
    MarketItem("국내", "코스닥", "KQ11"),
    MarketItem("해외", "다우 산업", "DJI"),
    MarketItem("해외", "나스닥 종합", "IXIC"),
    MarketItem("해외", "상해 종합", "SSEC"),
    MarketItem("해외", "니케이225", "N225"),
    MarketItem("환율", "원/달러", "USD/KRW"),
    MarketItem("환율", "중국 위안/달러", "USD/CNY"),
    MarketItem("상품", "금", "GC=F"),
    MarketItem("상품", "은", "SI=F"),
    MarketItem("상품", "WTI", "CL=F"),
)


def fetch_market_item(item: MarketItem, now: datetime) -> MarketResult:
    """Fetch the latest completed value and previous close for a market item."""

    start = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        frame = fdr.DataReader(item.symbol, start, end)
        if frame.empty or "Close" not in frame.columns:
            raise ValueError("No close-price data returned")

        frame = frame.dropna(subset=["Close"])
        if len(frame) < 2:
            raise ValueError("Not enough data points to calculate daily change")

        latest = frame.iloc[-1]
        previous = frame.iloc[-2]
        latest_close = float(latest["Close"])
        previous_close = float(previous["Close"])
        change_percent = ((latest_close - previous_close) / previous_close) * 100

        trade_date = frame.index[-1]
        trade_date_text = trade_date.strftime("%Y-%m-%d") if hasattr(trade_date, "strftime") else str(trade_date)

        if change_percent > 0:
            direction = "up"
        elif change_percent < 0:
            direction = "down"
        else:
            direction = "flat"

        return MarketResult(
            section=item.section,
            name=item.name,
            symbol=item.symbol,
            value=latest_close,
            change_percent=change_percent,
            direction=direction,
            trade_date=trade_date_text,
        )
    except Exception as exc:  # noqa: BLE001 - keep one failed symbol from breaking the report.
        print(f"Failed to fetch {item.symbol}: {exc}", file=sys.stderr)
        return MarketResult(
            section=item.section,
            name=item.name,
            symbol=item.symbol,
            value=None,
            change_percent=None,
            direction="error",
            trade_date=None,
            error=str(exc),
        )


def group_results(results: Iterable[MarketResult]) -> dict[str, list[MarketResult]]:
    grouped: dict[str, list[MarketResult]] = {}
    for result in results:
        grouped.setdefault(result.section, []).append(result)
    return grouped


def format_value(value: float | None) -> str:
    if value is None:
        return "데이터 없음"
    return f"{value:,.2f}"


def format_change(result: MarketResult) -> str:
    if result.change_percent is None:
        return ""
    arrow = {"up": "▲", "down": "▼", "flat": "■"}.get(result.direction, "")
    return f"{arrow} {abs(result.change_percent):.2f}%"


def render_html(results: list[MarketResult], generated_at: datetime) -> str:
    grouped = group_results(results)
    generated_at_text = generated_at.strftime("%Y-%m-%d %H:%M:%S KST")

    sections_html: list[str] = []
    for section_name, section_results in grouped.items():
        cards = []
        for result in section_results:
            error = f"<div class='error'>{html.escape(result.error)}</div>" if result.error else ""
            trade_date = html.escape(result.trade_date or "-")
            cards.append(
                f"""
                <article class="market-card {html.escape(result.direction)}">
                  <h3>{html.escape(result.name)}</h3>
                  <div class="value">{format_value(result.value)}</div>
                  <div class="change">{format_change(result)}</div>
                  <div class="meta">{html.escape(result.symbol)} · 기준일 {trade_date}</div>
                  {error}
                </article>
                """.strip()
            )

        sections_html.append(
            f"""
            <section class="market-section">
              <h2>{html.escape(section_name)}</h2>
              <div class="market-grid">
                {''.join(cards)}
              </div>
            </section>
            """.strip()
        )

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>전일 시장 요약</title>
  <style>
    :root {{
      --text: #2f3437;
      --muted: #7b858f;
      --border: #e5e7eb;
      --section-border: #b8bec5;
      --up: #c66b5f;
      --down: #4f91b8;
      --flat: #6b7280;
      --bg: #ffffff;
    }}
    body {{
      margin: 0;
      background: #f6f7f9;
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
    }}
    main {{
      width: min(100% - 32px, 480px);
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 24px;
      font-weight: 800;
    }}
    .generated-at {{
      margin: 0 0 28px;
      color: var(--muted);
      font-size: 13px;
    }}
    .market-section {{ margin-top: 26px; }}
    .market-section h2 {{
      margin: 0;
      padding-bottom: 9px;
      border-bottom: 2px solid var(--section-border);
      font-size: 18px;
      font-weight: 800;
    }}
    .market-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      background: var(--bg);
      border-left: 1px solid var(--border);
    }}
    .market-card {{
      min-height: 104px;
      padding: 14px 10px;
      text-align: center;
      border-right: 1px solid var(--border);
      border-bottom: 1px solid var(--border);
      box-sizing: border-box;
    }}
    .market-card h3 {{
      margin: 0 0 10px;
      font-size: 15px;
      font-weight: 800;
    }}
    .value {{
      font-size: 15px;
      font-weight: 700;
      line-height: 1.3;
    }}
    .change {{
      margin-top: 2px;
      font-size: 13px;
      font-weight: 700;
    }}
    .up .value, .up .change {{ color: var(--up); }}
    .down .value, .down .change {{ color: var(--down); }}
    .flat .value, .flat .change {{ color: var(--flat); }}
    .error .value, .error .change {{ color: var(--muted); }}
    .meta {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 11px;
    }}
    .error {{
      margin-top: 6px;
      color: #b45309;
      font-size: 11px;
      word-break: keep-all;
    }}
    @media (max-width: 380px) {{
      .market-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>전일 시장 요약</h1>
    <p class="generated-at">생성 시각: {html.escape(generated_at_text)}</p>
    {''.join(sections_html)}
  </main>
</body>
</html>
"""


def write_reports(document: str, generated_at: datetime) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = REPORT_DIR / f"{generated_at.strftime('%Y-%m-%d')}.html"
    latest_path = REPORT_DIR / "latest.html"
    dated_path.write_text(document, encoding="utf-8")
    latest_path.write_text(document, encoding="utf-8")
    print(f"Wrote {dated_path}")
    print(f"Wrote {latest_path}")


def main() -> None:
    now = datetime.now(KST)
    results = [fetch_market_item(item, now) for item in MARKET_ITEMS]
    document = render_html(results, now)
    write_reports(document, now)


if __name__ == "__main__":
    main()
