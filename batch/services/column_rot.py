"""조용히 썩는 열 감시 (docs/health.md 7장, docs/infra.md 25.948).

신선도(/status)는 "행이 들어오고 있나" 를 본다. 그런데 행은 매일 들어오는데 **그 안의 값이 비어 가거나 멈춘** 고장이
있었다 — 미국 재무가 열 하나만 null 로 쌓이고(25.882 DART 키 누락처럼 공시가 0건으로 "성공"), 거래대금이 어느 날부터
안 들어오고, 수정주가가 종가와 같은 값으로 굳는 식이다. 행 수로는 보이지 않는다. 그런 열은 몇 주 뒤 점수가 이상해질
때에야 들킨다.

여기서는 열마다 **최근 7일을 그 전 90일과 견준다.** 두 가지만 본다:
- 빈 값 비율(NULL / 행) 이 기준 기간보다 20%p 넘게 늘었다 → "비어 간다"
- 고유값 비율(DISTINCT / 비지 않은 행) 이 기준 기간의 절반 아래로 떨어졌다 → "값이 멈췄다"(같은 값이 되풀이된다)
둘 다 **자기 과거**와 견준다. 미국 시세의 거래대금은 원래 없어(yfinance 가 주지 않는다) 비율이 늘 높다 — 그래서 절대
문턱이 아니라 변화를 본다. 최근 7일에 비지 않은 행이 50개 아래면 판단하지 않는다(종목 몇 개의 우연).

읽기만 한다. 표마다 질의 하나(CTE 로 최근·기준을 한 번에)라 주 1회 열한 열에 20만 행 남짓이다 — 월 예산의 0.05%.
주간 운영 요약(`jobs/weekly_summary`)이 돌려 한 절로 싣고 `batch_runs.step_log.rot` 에 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 최근 창(일)과 기준 창(일). 기준은 최근을 뺀 그 앞 기간이다
RECENT_DAYS = 7
BASELINE_DAYS = 90
#: 표본 하한 — 빈 값 검사는 최근 행이 이보다 적으면, 고유값 검사는 최근 **비지 않은** 행이 이보다 적으면
#: 판단하지 않는다. 종목 몇 개의 우연으로 비율이 크게 흔들린다 (빈 값 검사를 비지 않은 행으로 막던 것은 25.976 에서
#: 고쳤다)
MIN_ROWS = 50
#: 빈 값 비율이 기준보다 이만큼(절대값, 0.20 = 20%p) 넘게 늘면 "비어 간다"
NULL_JUMP = 0.20
#: 고유값 비율이 기준의 이 배수 아래면 "값이 멈췄다". 0.5 = 절반
DISTINCT_DROP = 0.5

#: 감시하는 (표, 날짜 열, 값 열들). 바깥에서 받아 쌓는 값과 그로 계산해 쌓는 값 — 둘 다 조용히 썩을 수 있다.
#: 표마다 질의 하나(`QUERIES`)이고 그 질의의 열 이름이 여기와 같아야 한다 — `tests/test_column_rot_948.py` 가 대 본다
WATCHED: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("prices", "date", ("close", "adj_close", "volume", "value")),
    ("scores", "as_of_date", ("total_score", "sentiment_score")),
    ("sentiment_scores", "as_of_date", ("sentiment",)),
    ("performance_metrics", "as_of_date", ("sharpe", "beta")),
    ("signals", "as_of_date", ("suggested_amount",)),
    ("fx_rates", "date", ("rate",)),
)

#: 표마다 하나. 매개변수는 (최근 시작일, 기준 시작일) 둘. **글자 그대로 둔다** — 표·열 이름을
#: `tests/test_sql_schema.py` 가 마이그레이션과 대조할 수 있게(동적으로 이어 붙이면 그 그물 밖이다)
QUERIES: dict[str, str] = {
    "prices": (
        "WITH w AS (SELECT close, adj_close, volume, value, date >= ? AS r FROM prices WHERE date >= ?)"
        " SELECT SUM(r) AS n7, SUM(1 - r) AS n90,"
        "  SUM(r AND close IS NULL) AS close_null7,"
        "  SUM((1 - r) AND close IS NULL) AS close_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN close END) AS close_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN close END) AS close_d90,"
        "  SUM(r AND adj_close IS NULL) AS adj_close_null7,"
        "  SUM((1 - r) AND adj_close IS NULL) AS adj_close_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN adj_close END) AS adj_close_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN adj_close END) AS adj_close_d90,"
        "  SUM(r AND volume IS NULL) AS volume_null7,"
        "  SUM((1 - r) AND volume IS NULL) AS volume_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN volume END) AS volume_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN volume END) AS volume_d90,"
        "  SUM(r AND value IS NULL) AS value_null7,"
        "  SUM((1 - r) AND value IS NULL) AS value_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN value END) AS value_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN value END) AS value_d90"
        " FROM w"
    ),
    "scores": (
        "WITH w AS (SELECT total_score, sentiment_score, as_of_date >= ? AS r FROM scores WHERE as_of_date >= ?)"
        " SELECT SUM(r) AS n7, SUM(1 - r) AS n90,"
        "  SUM(r AND total_score IS NULL) AS total_score_null7,"
        "  SUM((1 - r) AND total_score IS NULL) AS total_score_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN total_score END) AS total_score_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN total_score END) AS total_score_d90,"
        "  SUM(r AND sentiment_score IS NULL) AS sentiment_score_null7,"
        "  SUM((1 - r) AND sentiment_score IS NULL) AS sentiment_score_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN sentiment_score END) AS sentiment_score_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN sentiment_score END) AS sentiment_score_d90"
        " FROM w"
    ),
    "sentiment_scores": (
        "WITH w AS (SELECT sentiment, as_of_date >= ? AS r FROM sentiment_scores WHERE as_of_date >= ?)"
        " SELECT SUM(r) AS n7, SUM(1 - r) AS n90,"
        "  SUM(r AND sentiment IS NULL) AS sentiment_null7,"
        "  SUM((1 - r) AND sentiment IS NULL) AS sentiment_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN sentiment END) AS sentiment_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN sentiment END) AS sentiment_d90"
        " FROM w"
    ),
    "performance_metrics": (
        "WITH w AS (SELECT sharpe, beta, as_of_date >= ? AS r FROM performance_metrics WHERE as_of_date >= ?)"
        " SELECT SUM(r) AS n7, SUM(1 - r) AS n90,"
        "  SUM(r AND sharpe IS NULL) AS sharpe_null7,"
        "  SUM((1 - r) AND sharpe IS NULL) AS sharpe_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN sharpe END) AS sharpe_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN sharpe END) AS sharpe_d90,"
        "  SUM(r AND beta IS NULL) AS beta_null7,"
        "  SUM((1 - r) AND beta IS NULL) AS beta_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN beta END) AS beta_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN beta END) AS beta_d90"
        " FROM w"
    ),
    "signals": (
        "WITH w AS (SELECT suggested_amount, as_of_date >= ? AS r FROM signals WHERE as_of_date >= ?)"
        " SELECT SUM(r) AS n7, SUM(1 - r) AS n90,"
        "  SUM(r AND suggested_amount IS NULL) AS suggested_amount_null7,"
        "  SUM((1 - r) AND suggested_amount IS NULL) AS suggested_amount_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN suggested_amount END) AS suggested_amount_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN suggested_amount END) AS suggested_amount_d90"
        " FROM w"
    ),
    "fx_rates": (
        "WITH w AS (SELECT rate, date >= ? AS r FROM fx_rates WHERE date >= ?)"
        " SELECT SUM(r) AS n7, SUM(1 - r) AS n90,"
        "  SUM(r AND rate IS NULL) AS rate_null7,"
        "  SUM((1 - r) AND rate IS NULL) AS rate_null90,"
        "  COUNT(DISTINCT CASE WHEN r THEN rate END) AS rate_d7,"
        "  COUNT(DISTINCT CASE WHEN NOT r THEN rate END) AS rate_d90"
        " FROM w"
    ),
}


@dataclass(frozen=True)
class ColumnCounts:
    """열 하나의 최근(7일)·기준(그 앞 90일) 수. `from_row` 가 질의 결과 한 행에서 꺼낸다."""

    table: str
    column: str
    n7: int
    null7: int
    distinct7: int
    n90: int
    null90: int
    distinct90: int

    @property
    def null_ratio7(self) -> float | None:
        return self.null7 / self.n7 if self.n7 else None

    @property
    def null_ratio90(self) -> float | None:
        return self.null90 / self.n90 if self.n90 else None

    @property
    def distinct_ratio7(self) -> float | None:
        살아있음 = self.n7 - self.null7
        return self.distinct7 / 살아있음 if 살아있음 > 0 else None

    @property
    def distinct_ratio90(self) -> float | None:
        살아있음 = self.n90 - self.null90
        return self.distinct90 / 살아있음 if 살아있음 > 0 else None


@dataclass(frozen=True)
class Finding:
    table: str
    column: str
    kind: str  # "null" 비어 간다 · "frozen" 값이 멈췄다 · "empty" 최근 행 없음
    text: str


def _int(v: Any) -> int:
    return int(v or 0)


def from_row(table: str, columns: tuple[str, ...], row: dict[str, Any]) -> list[ColumnCounts]:
    n7, n90 = _int(row.get("n7")), _int(row.get("n90"))
    return [
        ColumnCounts(
            table, c, n7, _int(row.get(f"{c}_null7")), _int(row.get(f"{c}_d7")),
            n90, _int(row.get(f"{c}_null90")), _int(row.get(f"{c}_d90")),
        )
        for c in columns
    ]  # fmt: skip


def judge(counts: list[ColumnCounts]) -> list[Finding]:
    """열마다 두 가지를 본다. 최근 행이 없으면 한 표에 한 줄(신선도가 보는 것이라 열마다 적지 않는다)."""
    out: list[Finding] = []
    행없음: set[str] = set()
    for c in counts:
        if c.n7 == 0:
            if c.table not in 행없음:
                행없음.add(c.table)
                글 = f"{c.table}: 최근 {RECENT_DAYS}일 행 없음 (/status 신선도가 봅니다)"
                out.append(Finding(c.table, "", "empty", 글))
            continue
        if c.n90 == 0:
            continue  # 견줄 과거가 없다
        # 빈 값 검사의 표본은 **최근 행 전체**다 (25.976). 예전에는 "비지 않은 행" 이 하한 아래면 건너뛰어, 열이
        # 최근에 **통째로 빈** 가장 나쁜 경우(500행 중 500행 빈 값)가 표본 부족으로 조용히 빠졌다 — 60% 빈 값은 잡고
        # 100% 는 놓쳤다
        r7, r90 = c.null_ratio7, c.null_ratio90
        if c.n7 >= MIN_ROWS and r7 is not None and r90 is not None and r7 - r90 > NULL_JUMP:
            글 = f"{c.table}.{c.column}: 빈 값 {r90:.0%} → {r7:.0%} (최근 {c.n7:,}행) — 비어 갑니다"
            out.append(Finding(c.table, c.column, "null", 글))
            continue
        if c.n7 - c.null7 < MIN_ROWS:
            continue  # 고유값 비율은 비지 않은 행으로 낸다 — 적으면 우연으로 흔들린다
        d7, d90 = c.distinct_ratio7, c.distinct_ratio90
        if d7 is not None and d90 is not None and d90 > 0 and d7 < d90 * DISTINCT_DROP:
            글 = f"{c.table}.{c.column}: 고유값 비율 {d90:.2f} → {d7:.2f} — 같은 값이 되풀이됩니다"
            out.append(Finding(c.table, c.column, "frozen", 글))
    return out


def render(findings: list[Finding], checked: int, max_lines: int) -> list[str]:
    """주간 요약의 한 절. 이상이 없어도 **점검했다는 한 줄**은 남긴다 — 조용함과 안 봄을 가르기 위해."""
    head = f"데이터 열 점검 (최근 {RECENT_DAYS}일 vs 그 전 {BASELINE_DAYS}일, {checked}열)"
    if not findings:
        return [head + " — 비어 가거나 멈춘 열 없음"]
    lines = [head]
    for f in findings[:max_lines]:
        lines.append(f"  {f.text}")
    if len(findings) > max_lines:
        lines.append(f"  … 외 {len(findings) - max_lines}")
    return lines


def scan(client: Any, recent_from: str, baseline_from: str) -> tuple[list[Finding], int, list[str]]:
    """표마다 질의 하나. 한 표가 실패해도 나머지는 본다 — (발견, 점검한 열 수, 못 본 표 이유)."""
    findings: list[Finding] = []
    checked = 0
    errors: list[str] = []
    for table, _date_col, columns in WATCHED:
        try:
            rows = client.execute(QUERIES[table], [recent_from, baseline_from]).dicts()
        except Exception as e:  # noqa: BLE001 — 감시가 요약을 막지 않는다
            errors.append(f"{table}: {e}")
            continue
        row = rows[0] if rows else {}
        counts = from_row(table, columns, row)
        checked += len(counts)
        findings += judge(counts)
    return findings, checked, errors
