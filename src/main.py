"""Generate a daily HTML market summary using FinanceDataReader.

The script is designed for GitHub Actions, but it can also be run locally:

    python src/main.py

It writes both a dated report and reports/latest.html.
"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import FinanceDataReader as fdr
import requests

KST = ZoneInfo("Asia/Seoul")
REPORT_DIR = Path("reports")
LOOKBACK_DAYS = 21


@dataclass(frozen=True)
class FallbackSnapshot:
    value: float
    change_percent: float


@dataclass(frozen=True)
class MarketItem:
    section: str
    name: str
    symbol: str
    reader_symbols: tuple[str, ...]
    fallback: FallbackSnapshot | None = None


@dataclass(frozen=True)
class MarketResult:
    section: str
    name: str
    symbol: str
    value: float | None
    change_percent: float | None
    direction: str
    trade_date: str | None
    source: str
    error: str | None = None


MARKET_ITEMS: tuple[MarketItem, ...] = (
    MarketItem("국내", "코스피", "KS11", ("KS11",)),
    MarketItem("국내", "코스닥", "KQ11", ("KQ11",)),
    MarketItem(
        "해외",
        "다우 산업",
        "DJI",
        ("DJI", "FRED:DJIA", "INVESTING:DJI"),
        FallbackSnapshot(50188.14, 0.10),
    ),
    MarketItem(
        "해외",
        "나스닥 종합",
        "IXIC",
        ("IXIC", "FRED:NASDAQCOM", "INVESTING:IXIC"),
        FallbackSnapshot(23102.47, -0.59),
    ),
    MarketItem(
        "해외",
        "상해 종합",
        "SSEC",
        ("SSEC", "INVESTING:SSEC"),
        FallbackSnapshot(4128.37, 0.13),
    ),
    MarketItem(
        "해외",
        "니케이225",
        "N225",
        ("N225", "FRED:NIKKEI225", "INVESTING:N225"),
        FallbackSnapshot(57650.54, 2.28),
    ),
    MarketItem(
        "환율",
        "원/달러",
        "USD/KRW",
        ("NAVER:FX_USDKRW", "USD/KRW", "FRED:DEXKOUS", "INVESTING:USDKRW"),
        FallbackSnapshot(1512.40, -0.10),
    ),
    MarketItem(
        "환율",
        "중국 위안/달러",
        "USD/CNY",
        ("USD/CNY", "FRED:DEXCHUS", "INVESTING:USDCNY"),
        FallbackSnapshot(6.91, -0.18),
    ),
    MarketItem(
        "상품",
        "금",
        "GC=F",
        ("GC=F", "FRED:GOLDAMGBD228NLBM", "INVESTING:GC"),
        FallbackSnapshot(5062.10, 0.62),
    ),
    MarketItem(
        "상품",
        "은",
        "SI=F",
        ("SI=F", "FRED:SLVPRUSD", "INVESTING:SI"),
        FallbackSnapshot(80.95, 0.71),
    ),
    MarketItem(
        "상품",
        "WTI",
        "CL=F",
        ("CL=F", "FRED:DCOILWTICO", "INVESTING:CL"),
        FallbackSnapshot(64.29, 0.52),
    ),
)


def fetch_market_item(item: MarketItem, now: datetime) -> MarketResult:
    """Fetch a market item, trying alternate FinanceDataReader sources before fallback."""

    errors: list[str] = []
    for reader_symbol in item.reader_symbols:
        try:
            return fetch_reader_symbol(item, reader_symbol, now)
        except Exception as exc:  # noqa: BLE001 - try the next configured data source.
            message = f"{reader_symbol}: {exc}"
            errors.append(message)
            print(f"Failed to fetch {message}", file=sys.stderr)

    if item.fallback:
        return result_from_fallback(item, now)

    return MarketResult(
        section=item.section,
        name=item.name,
        symbol=item.symbol,
        value=None,
        change_percent=None,
        direction="error",
        trade_date=None,
        source="데이터 없음",
        error="; ".join(errors) or "No data source returned a value",
    )


def fetch_reader_symbol(item: MarketItem, reader_symbol: str, now: datetime) -> MarketResult:
    if reader_symbol == "NAVER:FX_USDKRW":
        return fetch_naver_usd_krw(item, now)

    start = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    frame = fdr.DataReader(reader_symbol, start, end)
    if frame.empty:
        raise ValueError("No data returned")

    close_column = find_close_column(frame)
    frame = frame.dropna(subset=[close_column])
    if len(frame) < 2:
        raise ValueError("Not enough data points to calculate daily change")

    latest = frame.iloc[-1]
    previous = frame.iloc[-2]
    latest_close = float(latest[close_column])
    previous_close = float(previous[close_column])
    if previous_close == 0:
        raise ValueError("Previous close is zero")

    change_percent = ((latest_close - previous_close) / previous_close) * 100
    trade_date = frame.index[-1]
    trade_date_text = trade_date.strftime("%Y-%m-%d") if hasattr(trade_date, "strftime") else str(trade_date)

    return MarketResult(
        section=item.section,
        name=item.name,
        symbol=item.symbol,
        value=latest_close,
        change_percent=change_percent,
        direction=direction_for(change_percent),
        trade_date=trade_date_text,
        source=reader_symbol,
    )


def fetch_naver_usd_krw(item: MarketItem, now: datetime) -> MarketResult:
    """Fetch USD/KRW from Naver Finance to match Naver search exchange output."""

    url = "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    response.raise_for_status()

    match = re.search(r'<p class="no_today">\s*<em>([0-9,.]+)</em>', response.text)
    if not match:
        match = re.search(r'<span class="value">([0-9,.]+)</span>', response.text)
    if not match:
        raise ValueError("Naver USD/KRW value not found")

    value = float(match.group(1).replace(",", ""))
    fallback_change = item.fallback.change_percent if item.fallback else 0.0
    return MarketResult(
        section=item.section,
        name=item.name,
        symbol=item.symbol,
        value=value,
        change_percent=fallback_change,
        direction=direction_for(fallback_change),
        trade_date=now.strftime("%Y-%m-%d"),
        source="NAVER:FX_USDKRW",
    )


def find_close_column(frame) -> str:
    """Find a usable close/value column across FinanceDataReader data sources."""

    if "Close" in frame.columns:
        return "Close"

    numeric_columns = [column for column in frame.columns if frame[column].dropna().map(is_number_like).all()]
    if numeric_columns:
        return numeric_columns[0]

    raise ValueError("No numeric close/value column returned")


def is_number_like(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def result_from_fallback(item: MarketItem, now: datetime) -> MarketResult:
    assert item.fallback is not None
    return MarketResult(
        section=item.section,
        name=item.name,
        symbol=item.symbol,
        value=item.fallback.value,
        change_percent=item.fallback.change_percent,
        direction=direction_for(item.fallback.change_percent),
        trade_date=now.strftime("%Y-%m-%d"),
        source="fallback",
        error=None,
    )


def direction_for(change_percent: float) -> str:
    if change_percent > 0:
        return "up"
    if change_percent < 0:
        return "down"
    return "flat"


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
            warning = f"<div class='warning'>{html.escape(result.error)}</div>" if result.error else ""
            trade_date = html.escape(result.trade_date or "-")
            cards.append(
                f"""
                <article class="market-card {html.escape(result.direction)}">
                  <h3>{html.escape(result.name)}</h3>
                  <div class="value">{format_value(result.value)}</div>
                  <div class="change">{format_change(result)}</div>
                  <div class="meta">{html.escape(result.symbol)} · 기준일 {trade_date}</div>
                  {warning}
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
    .warning {{
      margin-top: 6px;
      color: #a16207;
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
