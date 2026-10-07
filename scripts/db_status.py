"""운영 DB 상태를 읽기 전용으로 찍는다 (docs/handoff.md "폰·클라우드 세션에서 이어가기").

왜 있나: 클라우드(폰) 세션에는 .env 가 없어 Turso 를 직접 읽을 수 없다. 이 스크립트를 Actions
(db-status.yml)에서 돌리면 비밀값은 GitHub 시크릿에만 있고, 결과는 Actions 로그로 본다.

실행
  python scripts/db_status.py                        # 기본 점검 묶음
  python scripts/db_status.py --sql "SELECT ..."     # 읽기 질의 하나 (SELECT/WITH 만)
  python scripts/db_status.py --tables               # 표별 행 수 (읽기 예산을 크게 쓴다)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core.client import TursoClient  # noqa: E402

# (제목, 질의). 표가 없으면 그 항목만 오류로 찍고 넘어간다
CHECKS: list[tuple[str, str]] = [
    # "따라잡기 진행"(전체 거래일수·재무·팩터·점수·신호 행 수)은 기본 묶음에서 뺐다 (docs/infra.md 25.980).
    # D1 따라잡기 때 아침마다 보던 숫자였는데, 큰 표를 통째로 세어 2026-10-07 한 번에 Turso 읽기 1,353만 행
    # (월 한도 5억의 2.7%)을 썼다 — 묶음의 나머지 전부는 5만 행. 신선도는 아래 "나라별 채움" 이 기준일로 보인다.
    # 표별 행 수가 꼭 필요하면 `--tables` (읽기 예산을 크게 쓴다고 적어 둔 길)로 본다.
    (
        # **나라를 가려서 본다** (2026-09-21, docs/infra.md 25.75).
        #
        # 위의 "따라잡기 진행" 은 나라를 가리지 않고 센다. D1 로 국내만 돌리는 지금은 그게
        # 곧 국내 숫자지만, **Turso 로 돌아가 미국이 살아나면 두 나라가 한 숫자에 섞인다.**
        # "점수 1,800" 이 국내 900 + 미국 900 인지 국내 1,800 인지 알 수 없게 된다.
        # 25.37 에서 배치가 나라를 안 가려 엉뚱한 날짜를 골랐던 것과 같은 모양이다.
        #
        # 기준일도 함께 본다 — 행 수만으로는 "옛날 것이 남아 있는" 경우를 못 가른다.
        "나라별 채움 (복귀 뒤에는 이쪽을 본다)",
        "SELECT s.country AS 나라,"
        " COUNT(DISTINCT s.id) AS 종목,"
        " (SELECT COUNT(*) FROM universe_members um JOIN stocks x ON x.id = um.stock_id"
        "  WHERE x.country = s.country AND um.included = 1"
        "    AND um.snapshot_date = (SELECT MAX(snapshot_date) FROM universe_members um2"
        "      JOIN stocks x2 ON x2.id = um2.stock_id WHERE x2.country = s.country)) AS 유니버스편입,"
        " (SELECT MAX(p.date) FROM prices p JOIN stocks x ON x.id = p.stock_id"
        "  WHERE x.country = s.country) AS 시세마지막,"
        " (SELECT MAX(sc.as_of_date) FROM scores sc JOIN stocks x ON x.id = sc.stock_id"
        "  WHERE x.country = s.country) AS 점수기준일,"
        " (SELECT COUNT(*) FROM signals sg JOIN stocks x ON x.id = sg.stock_id"
        "  WHERE x.country = s.country AND sg.as_of_date = (SELECT MAX(sg2.as_of_date) FROM signals sg2"
        "    JOIN stocks x2 ON x2.id = sg2.stock_id WHERE x2.country = s.country)) AS 최신신호수,"
        " (SELECT MAX(sg.as_of_date) FROM signals sg JOIN stocks x ON x.id = sg.stock_id"
        "  WHERE x.country = s.country) AS 신호기준일"
        " FROM stocks s WHERE s.status = 'active' GROUP BY s.country ORDER BY s.country",
    ),
    (
        # 하루 한도를 넘겼는지 한눈에. 넘겼으면 그날 뒤 단계는 전부 건너뛰어진다 (infra 25.20)
        "D1 하루 쓰기 카운터 (한도 100,000. 넘으면 그날 뒤 단계가 굶는다)",
        "SELECT window_start AS 날짜, call_count AS 쓴행 FROM api_usage"
        " WHERE api_name = 'd1_writes' AND window_type = 'day'"
        " ORDER BY window_start DESC LIMIT 5",
    ),
    (
        "최근 배치 30건 (batch_runs, UTC)",
        "SELECT job_name, market, trade_date, status, started_at, finished_at,"
        " substr(COALESCE(error_text, step_log, ''), 1, 160) AS note"
        " FROM batch_runs ORDER BY id DESC LIMIT 30",
    ),
    (
        "장중·뉴스 크론 호출 기록 (cron_heartbeats, called_at UTC)",
        "SELECT job, market, called_at, outcome, calls_today, day, substr(COALESCE(detail, ''), 1, 200) AS detail"
        " FROM cron_heartbeats ORDER BY job, market",
    ),
    (
        "최근 알림 10건 (alerts)",
        "SELECT a.created_at, a.market, s.ticker, a.trigger_type, a.sent_at, substr(a.message, 1, 80) AS message"
        " FROM alerts a JOIN stocks s ON s.id = a.stock_id ORDER BY a.id DESC LIMIT 10",
    ),
    (
        "뉴스 수집 (나라·언어별)",
        "SELECT s.country, n.lang, COUNT(*) AS articles, MAX(n.fetched_at) AS last_fetched"
        " FROM news n JOIN stocks s ON s.id = n.stock_id GROUP BY s.country, n.lang",
    ),
    (
        "기사 채점 (방식별)",
        "SELECT method, COUNT(*) AS scored, MAX(created_at) AS last_scored FROM article_sentiments GROUP BY method",
    ),
    (
        "종목 감성 집계 (최근 기준일 5개)",
        "SELECT as_of_date, method, COUNT(*) AS stocks, SUM(sentiment IS NOT NULL) AS with_score"
        " FROM sentiment_scores GROUP BY as_of_date, method ORDER BY as_of_date DESC LIMIT 5",
    ),
    (
        "신호 (나라·기준일별 최근 4개)",
        "SELECT s.country, g.as_of_date, COUNT(*) AS signals, SUM(g.horizon = 'short') AS short,"
        " SUM(g.horizon = 'mid') AS mid, SUM(g.horizon = 'long') AS long,"
        " SUM(g.suggested_amount IS NOT NULL) AS with_amount"
        " FROM signals g JOIN stocks s ON s.id = g.stock_id GROUP BY s.country, g.as_of_date"
        " ORDER BY g.as_of_date DESC LIMIT 4",
    ),
    ("환율 최근 3건 (fx_rates)", "SELECT * FROM fx_rates ORDER BY date DESC LIMIT 3"),
    (
        "밸류에이션 밴드 (기준일별)",
        "SELECT as_of_date, COUNT(*) AS stocks, SUM(p50 IS NOT NULL) AS with_band FROM valuation_bands"
        " GROUP BY as_of_date ORDER BY as_of_date DESC LIMIT 3",
    ),
    (
        "활성 매도 플래그",
        "SELECT f.as_of_date, s.ticker, f.level, f.reason_code FROM sell_flags f JOIN stocks s ON s.id = f.stock_id"
        " WHERE f.is_active = 1 AND f.as_of_date = (SELECT MAX(as_of_date) FROM sell_flags) LIMIT 20",
    ),
    ("마이그레이션 최신 3개", "SELECT version, applied_at FROM schema_migrations ORDER BY version DESC LIMIT 3"),
]

#: **날마다 쌓이고 아무도 잘라 내지 않는 표** (docs/infra.md 25.131).
#:
#: D1 의 500MB 는 **리셋이 없는 한도**다(25.123). 게이지가 "얼마나 찼나" 를 말해 주지만
#: **무엇이 채웠나** 는 아무도 모른다. 용량 경고가 뜬 날 "무엇을 지울까" 를 정하려면
#: 이 수부터 봐야 한다.
#:
#: **기본 점검에 넣지 않는다.** `COUNT(*)` 는 표를 통째로 훑어 읽기 예산을 크게 쓴다
#: (`prices` 만 수십만 행이다). 용량 게이지가 경고를 띄운 날에만 부른다.
#:
#: 목록은 **손으로 고른 것이고 전부가 아니다.** `tests/test_retention.py` 가
#: 마이그레이션의 표와 대조해 빠진 것이 있으면 알려 준다.
자라는표 = [
    "prices", "factors", "scores", "signals", "signal_checks", "performance_metrics",
    "valuation_bands", "sentiment_scores", "universe_members", "news", "article_sentiments",
    "alerts", "health_alerts", "batch_runs", "financials", "financial_snapshots",
    "index_prices", "fx_rates", "stock_dividends", "disclosures", "insider_trades",
    "daily_reports", "report_items", "backtest_curves", "backtest_runs", "market_sessions",
    "market_calendar", "api_usage", "etf_profiles", "etf_picks", "etf_satellite_picks",
    "earnings_calendar", "signal_outcomes", "stock_accum_picks", "portfolio_values",
    "stress_runs", "trades", "dividend_receipts", "trade_lots", "trade_reviews",
]


def 행수_점검(표들: list[str]) -> list[tuple[str, str]]:
    """표별 행 수를 **한 질의로** 묶어 찍는다.

    D1 은 `UNION ALL` 항 수를 좁게 제한하므로(infra 25.5) 가로로 늘어놓고, 그마저도
    길어지지 않게 나눠 보낸다. `한 표가 없어도 나머지는 찍는다` 는 `run()` 이 해 준다.
    """
    묶음 = 8
    나온것 = []
    for 시작 in range(0, len(표들), 묶음):
        조각 = 표들[시작 : 시작 + 묶음]
        칸 = ", ".join(f"(SELECT COUNT(*) FROM {t}) AS {t}" for t in 조각)
        나온것.append((f"표별 행 수 {시작 // 묶음 + 1} (읽기 예산을 크게 쓴다)", f"SELECT {칸}"))
    return 나온것

_WRITE_WORDS = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|pragma|vacuum|reindex|upsert)\b", re.IGNORECASE
)


def is_read_only(sql: str) -> bool:
    """SELECT/WITH 로 시작하고, 문장 하나이며, 쓰기 낱말이 없을 때만 허용한다.

    Turso 토큰에 쓰기 권한이 있어서 이 검사가 유일한 안전장치다. 애매하면 거절한다
    (문자열 안의 'update' 같은 낱말도 거절된다. 읽기 질의에서 그런 경우는 드물다).
    """
    body = sql.strip().rstrip(";").strip()
    if not body or ";" in body:
        return False
    # 실행 계획(`EXPLAIN QUERY PLAN`)도 읽기다 — 운영 플래너가 로컬과 다른 길을 고르는지 본다 (docs/infra.md 25.899)
    if not re.match(r"^(explain\s+query\s+plan\s+)?(select|with)\b", body, re.IGNORECASE):
        return False
    return not _WRITE_WORDS.search(body)


def render(title: str, columns: list[str], rows: list[tuple]) -> str:
    lines = [f"== {title} ({len(rows)}행)", "\t".join(columns)]
    lines += ["\t".join("NULL" if v is None else str(v) for v in row) for row in rows]
    return "\n".join(lines)


def run(checks: list[tuple[str, str]], client=None) -> int:
    client = client or TursoClient()
    failed = 0
    try:
        for title, sql in checks:
            if not is_read_only(sql):
                print(f"== {title}\n거절: 읽기 질의(SELECT/WITH 한 문장)만 허용합니다")
                failed += 1
                continue
            try:
                rs = client.execute(sql)
                print(render(title, list(rs.columns), [tuple(r) for r in rs.rows]))
                # 서버가 훑은 행 — 월 읽기 한도가 세는 값. 질의 하나의 값을 운영에서 바로 잰다 (25.899)
                if getattr(rs, "rows_read", 0):
                    print(f"(서버가 훑은 행 {rs.rows_read:,})")
            except Exception as error:  # noqa: BLE001 — 한 항목이 깨져도 나머지는 찍는다
                print(f"== {title}\n오류: {error}")
                failed += 1
            print()
    finally:
        client.close()
    return 1 if failed and len(checks) == 1 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 DB 상태 (읽기 전용)")
    parser.add_argument("--sql", help="읽기 질의 하나 (SELECT/WITH)")
    parser.add_argument(
        "--tables",
        action="store_true",
        help="표별 행 수도 찍는다. COUNT(*) 라 읽기 예산을 크게 쓴다 — 용량 경고가 뜬 날에만",
    )
    args = parser.parse_args()
    if args.sql and args.sql.strip():
        return run([("사용자 질의", args.sql)])
    return run(행수_점검(자라는표) if args.tables else CHECKS)


if __name__ == "__main__":
    sys.exit(main())
