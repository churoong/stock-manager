"""시점 조회 (point-in-time).

백테스트가 미래를 보지 못하게 막는 장치다.

2025 사업보고서는 2026-03-10 에 접수됐다. 2026-03-09 에 서 있었다면
그 숫자를 알 수 없었다. 그날의 투자 판단에 2025년 실적을 쓰면
"결과를 알고 투자한" 셈이 되고, 백테스트 성적이 실제보다 좋게 나온다.

그래서 규칙은 하나다.
  **as_of_date <= 기준일 인 스냅샷만 본다.**

financials 는 정정 공시가 오면 덮어쓴다. 그래서 백테스트는 이 표를 보지 않는다.
financial_snapshots 는 덮어쓰지 않고 쌓는다. 백테스트는 오직 이것만 읽는다.

**이 모듈은 확인용이다 — 백테스트가 쓰는 길이 아니다** (2026-09-20 명확히 함).

여기 있는 조회 함수들은 SQL 로 한 행씩 꺼낸다. `scripts/check_pit.py` 가 이것으로
"접수일 하루 전에는 안 보이고 접수일부터 보인다" 를 **실제 데이터로** 눈으로 확인한다.

백테스트는 종목별 스냅샷 목록을 한 번에 읽어 메모리에서 거른다
(`batch/jobs/backtest.py` 의 `pit_financials`·`stability_from`·`pit_equities`).
날짜마다 SQL 을 치면 수천 번이 되기 때문이다. **규칙은 같고 구현이 둘이다.**

둘을 잇는 것이 `assert_no_lookahead()` 다. 백테스트의 세 함수가 돌려주기 직전에
이것을 부른다. 규칙 문장의 정의처는 여기 하나로 둔다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from batch.core.turso import TursoClient
from batch.sources.dart import ANNUAL_REPORT_CODE  # 미국 10-K 도 이 코드로 쌓는다(backtest.load_snapshots)

log = logging.getLogger(__name__)


@dataclass
class PointInTimeFinancials:
    """어느 시점에 알 수 있었던 재무 한 벌."""

    stock_id: int
    as_of_date: str  # 이 값이 공개된 날
    fiscal_year: int
    report_code: str
    consolidated: bool
    receipt_no: str
    values: dict[str, Any]


def financials_as_of(
    client: TursoClient,
    stock_id: int,
    as_of: str,
    *,
    consolidated: bool | None = None,
) -> PointInTimeFinancials | None:
    """그 날짜에 알 수 있었던 가장 최근 **연간**(사업보고서) 재무를 돌려준다.

    Args:
        as_of: 기준일 YYYY-MM-DD. 이 날 아침에 서 있다고 본다
        consolidated: None(기본)이면 백테스트 `pit_basis_rows`(25.860)와 같은 규칙으로 고른다 — 기준일까지 접수된
            것 중 가장 늦은 사업연도에 연결이 있으면 연결, 없으면 별도. True 면 연결을 쓰고 없으면 별도로 물러나며,
            False 면 별도만 본다

    **연간만 본다** (docs/infra.md 25.892, 9회차 검증). 예전에는 보고서 코드를 거르지 않고 사업연도 내림차순으로
    골라, 다음 해 1분기 보고서(3개월 값)가 접수되면 그것을 "그날 알 수 있던 최신 재무" 로 돌려줬다. 연결·별도도
    "연결이 어느 해든 하나라도 있으면 연결" 이라 백테스트 규칙과 달랐다 — 모듈 설명의 "규칙은 같고 구현이 둘이다" 가
    거짓이었다. 쓰는 곳은 `scripts/check_pit.py` 와 테스트뿐이라 점수·백테스트에는 영향이 없었다.

    기준일 당일에 접수된 보고서는 포함한다. 접수는 장중이나 장 마감 후에
    이뤄지므로 엄밀히는 그날 아침에 몰랐을 수 있다. 다만 일일 배치가
    장 시작 전에 돌면서 전일까지의 데이터를 쓰므로 실무상 문제가 없다.
    """
    rows = _query_snapshots(client, stock_id, as_of)
    if not rows:
        return None
    if consolidated is None:
        늦은해 = max(int(r["fiscal_year"]) for r in rows)
        연결 = any(bool(r["consolidated"]) for r in rows if int(r["fiscal_year"]) == 늦은해)
    elif consolidated:
        # 연결이 없으면 별도라도 쓴다. 없는 것보다 낫다
        연결 = any(bool(r["consolidated"]) for r in rows)
    else:
        연결 = False
    rows = [r for r in rows if bool(r["consolidated"]) == 연결]
    if not rows:
        return None

    row = rows[0]
    try:
        values = json.loads(row["payload"])
    except (TypeError, ValueError):
        log.warning("스냅샷 payload 를 해석하지 못했습니다: stock_id=%s", stock_id)
        values = {}

    return PointInTimeFinancials(
        stock_id=stock_id,
        as_of_date=str(row["as_of_date"]),
        fiscal_year=int(row["fiscal_year"]),
        report_code=str(row["report_code"]),
        consolidated=bool(row["consolidated"]),
        receipt_no=str(row["receipt_no"]),
        values=values,
    )


def _query_snapshots(client: TursoClient, stock_id: int, as_of: str) -> list[dict[str, Any]]:
    """기준일 이전에 공개된 **연간** 스냅샷(연결·별도 둘 다)을 최신순으로.

    정렬 기준이 둘이다.
      1. 회계연도가 큰 것 우선 — 공개일 먼저면 옛 연도의 정정이 최신 연도를 덮었다 (25.549 와 같은 모양, 25.553)
      2. 같은 연도면 공개일이 늦은 것(정정) 우선
    """
    rs = client.execute(
        "SELECT as_of_date, fiscal_year, report_code, consolidated, receipt_no, payload"
        " FROM financial_snapshots"
        " WHERE stock_id = ? AND report_code = ? AND as_of_date <= ?"
        " ORDER BY fiscal_year DESC, as_of_date DESC",
        [stock_id, ANNUAL_REPORT_CODE, as_of],
    )
    return rs.dicts()


def latest_known_date(client: TursoClient, stock_id: int) -> str | None:
    """이 종목에 대해 우리가 가진 가장 최근 공개일."""
    rs = client.execute(
        "SELECT MAX(as_of_date) FROM financial_snapshots WHERE stock_id = ?",
        [stock_id],
    )
    value = rs.scalar()
    return str(value) if value else None


def coverage(client: TursoClient, as_of: str) -> dict[str, int]:
    """기준일에 재무를 알 수 있는 종목이 몇 개인지.

    유니버스 대비 얼마나 덮이는지 보는 용도다. 이 값이 낮으면
    팩터 계산에서 빠지는 종목이 많다는 뜻이다.
    """
    # 분자도 **분모와 같은 집합(국내 최신 유니버스)** 안에서 센다 (docs/infra.md 25.550, DART 감사). 예전에는 모든
    # 스냅샷 종목을 세어 미국 10-K 스냅샷(`us_financials`)과 유니버스 밖 종목까지 들어가 덮임이 100% 를 넘을 수 있었다
    rs = client.execute(
        "SELECT COUNT(DISTINCT fs.stock_id) AS covered FROM financial_snapshots fs"
        " JOIN universe_members um1 ON um1.stock_id = fs.stock_id AND um1.included = 1"
        " JOIN stocks s1 ON s1.id = fs.stock_id AND s1.country = 'KR'"
        " WHERE fs.as_of_date <= ?"
        "   AND um1.snapshot_date = (SELECT MAX(um.snapshot_date) FROM universe_members um"
        "     JOIN stocks su ON su.id = um.stock_id WHERE su.country = 'KR')",
        [as_of],
    )
    covered = int(rs.scalar() or 0)

    # 분모는 국내 유니버스 — 덮임은 DART 재무가 국내 유니버스를 얼마나 덮는지 보는 값이다.
    rs2 = client.execute(
        "SELECT COUNT(*) FROM universe_members um0 JOIN stocks s0 ON s0.id = um0.stock_id"
        " WHERE um0.included = 1 AND s0.country = 'KR'"
        "   AND um0.snapshot_date = (SELECT MAX(um.snapshot_date) FROM universe_members um"
        "     JOIN stocks su ON su.id = um.stock_id WHERE su.country = 'KR')"
    )
    universe = int(rs2.scalar() or 0)

    return {"covered": covered, "universe": universe}


def assert_no_lookahead(as_of: str, snapshot_date: str) -> None:
    """미래 데이터를 쓰려 하면 즉시 멈춘다.

    백테스트 코드가 실수로 최신 재무를 집어 오는 것을 막는 마지막 방어선이다.
    조용히 틀린 결과를 내는 것보다 멈추는 편이 낫다.
    """
    if snapshot_date > as_of:
        raise ValueError(
            f"미래 데이터를 참조했습니다. 기준일 {as_of} 인데 "
            f"공개일 {snapshot_date} 인 재무를 쓰려 했습니다"
        )
