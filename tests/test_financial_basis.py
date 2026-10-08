"""**재무 기준: 연결이 있으면 연결, 없으면 별도** (CLAUDE.md "연결 기준 우선", docs/infra.md 25.314).

예전에는 모든 곳이 `consolidated = 1` 이라 별도재무만 내는 회사(국내 유니버스 약 10%)가 재무 없음으로 읽혔다.
둘을 본다.

1. 재무를 읽는 배치 질의가 **모두** 같은 기준 글(`db.FINANCIAL_BASIS_*`)을 쓰는가 — 한 곳만 `= 1` 로 남으면
   점수와 신호가 서로 다른 회사 집합을 본다. 적립(`accumulation`)은 설계상 연결만 본다(docs/accumulation.md G3)
2. 실제로 별도만 있는 회사가 읽히고, 둘 다 있는 회사는 연결만 읽히는가
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from batch.core import db

뿌리 = Path(__file__).resolve().parent.parent
연결만_허용 = {"accumulation.py"}  # 설계상 연결만 (docs/accumulation.md G3)


def _문자열들(path: Path) -> list[str]:
    return [
        n.value for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]  # fmt: skip


def 파이썬이_고르는_질의() -> set[str]:
    """두 기준을 다 읽고 **파이썬이 기준 하나를 고르는** 질의 (25.1049). 분기 실적 추세(`verdicts.QUARTERS_SQL`)는
    웹 분기 표와 같은 규칙(가장 최근 분기의 연결 우선 — 25.550)을 `insights.quarter_trend` 가 적용한다.
    SQL 로 하면 행마다 상관 부질의가 돌아 하루 수십만 행을 읽는다. 규칙은 `test_insights_1041.test_분기_실적_추세` 가 묶는다"""
    from batch.jobs import verdicts

    return {re.sub(r"\s+", " ", verdicts.QUARTERS_SQL)}


def test_재무를_읽는_질의가_모두_같은_기준을_쓴다() -> None:
    기준들 = [re.sub(r"\s+", " ", b) for b in (
        db.FINANCIAL_BASIS_F, db.FINANCIAL_BASIS_TABLE, db.FINANCIAL_BASIS_SNAPSHOTS,
        db.FINANCIAL_BASIS_AT_CUTOFF, db.FINANCIAL_BASIS_F_AS_OF, db.FINANCIAL_BASIS_TABLE_AS_OF,
    )]
    틀린것: list[str] = []
    for path in sorted((뿌리 / "batch" / "jobs").glob("*.py")):
        if path.name in 연결만_허용:
            continue
        for sql in _문자열들(path):
            if "consolidated" not in sql or not re.search(r"FROM (financials|financial_snapshots)\b", sql):
                continue
            if not re.match(r"^\s*SELECT", sql):
                continue
            한줄 = re.sub(r"\s+", " ", sql)
            if 한줄 in 파이썬이_고르는_질의():
                continue
            if not any(b in 한줄 for b in 기준들):
                틀린것.append(f"{path.name}: {한줄[:120]}")
    assert 틀린것 == [], "재무 기준 글을 쓰지 않는 질의:\n" + "\n".join(틀린것)


def test_두_기준을_다_읽는_질의는_분기_계열_하나뿐() -> None:
    """`db.FINANCIAL_BASIS_AT_CUTOFF` 는 열 별칭이다 — 넣기만 하면 위 검사를 통과한다(25.465, 교차검증).
    두 기준을 다 읽고 **기준일마다 고르는 쪽이 있는** 질의만 쓸 수 있다: 지금은 `load_quarter_snapshots` 하나다."""
    별칭 = db.FINANCIAL_BASIS_AT_CUTOFF.split(" AS ")[1]
    쓴곳 = [
        path.name for path in sorted((뿌리 / "batch").rglob("*.py")) if path.name != "db.py"
        for sql in _문자열들(path) if 별칭 in sql
    ]  # fmt: skip
    assert set(쓴곳) == {"backtest.py"}, 쓴곳
    # 상수를 이어 붙여 쓰면 글자 조각에 별칭이 없어 위 검사를 빠져나간다 — 이름으로도 찾는다 (25.467, 교차검증)
    이름으로 = sorted(
        path.name for path in (뿌리 / "batch").rglob("*.py")
        if path.name != "db.py" and "FINANCIAL_BASIS_AT_CUTOFF" in path.read_text(encoding="utf-8")
    )  # fmt: skip
    assert set(이름으로) <= {"backtest.py"}, 이름으로
    from batch.jobs import backtest

    원본 = __import__("inspect").getsource(backtest.load_quarter_snapshots)
    assert 별칭 in 원본, "별칭이 분기 계열이 아닌 질의로 옮겨 갔다 — 거기서도 기준일마다 고르는지 확인하라"


def test_별도만_있는_회사가_읽히고_둘_다_있으면_연결만() -> None:
    from batch.jobs import scores as job
    from tests.test_scores_job import _sqlite_client

    client = _sqlite_client()
    for stock_id, consolidated, revenue in ((1, 0, 100.0), (2, 0, 50.0), (2, 1, 70.0)):
        client.conn.execute(
            "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
            " receipt_no, currency, unit, revenue, source, fetched_at)"
            " VALUES (?, 2025, ?, 'A', ?, '2026-03-10', ?, 'KRW', '원', ?, 't', 't')",
            [stock_id, job.ANNUAL_REPORT_CODE, consolidated, f"r{stock_id}{consolidated}", revenue],
        )
    client.conn.execute("UPDATE stocks SET country = 'KR'")
    읽음 = job.load_financials(client, "KR", "2026-09-27")  # type: ignore[arg-type]
    assert 읽음[1][2025]["revenue"] == 100.0  # 별도만 있는 회사 — 예전에는 빠졌다
    assert 읽음[2][2025]["revenue"] == 70.0  # 둘 다 있으면 연결


def test_연결을_끊은_회사는_별도로_읽는다() -> None:
    """자회사를 처분해 별도만 내게 된 회사가 영구히 옛 연결 연도에 고정됐다 (docs/infra.md 25.550, DART 감사)."""
    from batch.jobs import scores as job
    from tests.test_scores_job import _sqlite_client

    client = _sqlite_client()
    for 연도, 연결, 매출 in ((2023, 1, 900.0), (2023, 0, 500.0), (2024, 0, 520.0), (2025, 0, 540.0)):
        client.conn.execute(
            "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
            " receipt_no, currency, unit, revenue, source, fetched_at)"
            " VALUES (1, ?, ?, 'A', ?, ?, ?, 'KRW', '원', ?, 't', 't')",
            [연도, job.ANNUAL_REPORT_CODE, 연결, f"{연도 + 1}-03-10", f"r{연도}{연결}", 매출],
        )
    client.conn.execute("UPDATE stocks SET country = 'KR'")
    읽음 = job.load_financials(client, "KR", "2026-09-27")  # type: ignore[arg-type]
    assert max(읽음[1]) == 2025 and 읽음[1][2023]["revenue"] == 500.0  # 한 기준(별도)으로 이어진다


def test_별도를_쓰면_근거_문장이_밝힌다() -> None:
    from batch.services import signals as sg

    inp = sg.SignalInput(stock_id=1, ticker="A", name="가", market="KOSDAQ", currency="KRW", fiscal_year=2025,
                         consolidated=False)  # fmt: skip
    assert sg._annual_report(inp) == "사업보고서(별도)"
    assert sg._annual_report(sg.SignalInput(stock_id=1, ticker="A", name="가", market="KOSPI")) == "사업보고서"


def test_덮임은_국내_유니버스_안에서만_센다() -> None:
    """미국 10-K 스냅샷·유니버스 밖 종목까지 세어 덮임이 부풀었다 (docs/infra.md 25.550, DART 감사)."""
    from batch.services import pit
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at) VALUES"
        " (1, '000001', 'KOSPI', 'KR', 'KRW', 'active', 't', 't'), (2, '000002', 'KOSPI', 'KR', 'KRW', 'active', 't', 't'),"
        " (3, '000003', 'KOSPI', 'KR', 'KRW', 'active', 't', 't'), (4, 'AAA', 'NYSE', 'US', 'USD', 'active', 't', 't')"
    )
    for sid in (1, 2):
        c.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES ('2026-09-20', ?, 1, 'KRW', 't')",
            [sid],
        )
    for sid in (1, 3, 4):  # 2 는 없음, 3 은 유니버스 밖, 4 는 미국
        c.conn.execute(
            "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
            " consolidated, payload, source, fetched_at) VALUES (?, '2026-03-10', ?, 2025, '11011', 1, '{}', 't', 't')",
            [sid, f"r{sid}"],
        )
    assert pit.coverage(c, "2026-09-27") == {"covered": 1, "universe": 2}  # type: ignore[arg-type]


def test_기준일_뒤에_처음_낸_연결이_기준일의_기준을_바꾸지_않는다_25_856() -> None:
    """2026-03 에 처음 연결을 낸 회사를 2026-01 기준으로 다시 계산하면 별도 재무가 통째로 빠졌다 (7회차 검증 1).

    점수·성장(중기 신호)·PBR 밴드·장기 신호 자본 — 기준일이 있는 네 질의가 모두 같은 규칙이다.
    """
    from batch.jobs import scores, signals, valuation_bands
    from tests.test_scores_job import _sqlite_client

    client = _sqlite_client()
    for 연도, 연결, 접수 in ((2023, 0, "2024-03-10"), (2024, 0, "2025-03-10"), (2025, 1, "2026-03-10")):
        client.conn.execute(
            "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
            " receipt_no, currency, unit, revenue, operating_income, total_equity, source, fetched_at)"
            " VALUES (1, ?, ?, 'A', ?, ?, ?, 'KRW', '원', 100, 10, 1000, 't', 't')",
            [연도, scores.ANNUAL_REPORT_CODE, 연결, 접수, f"r{연도}{연결}"],
        )
    client.conn.execute("UPDATE stocks SET country = 'KR'")
    기준일 = "2026-01-15"
    assert sorted(scores.load_financials(client, "KR", 기준일)[1]) == [2023, 2024]  # type: ignore[arg-type]
    assert 1 in signals.load_growth(client, "KR", 기준일)  # type: ignore[arg-type]
    assert len(valuation_bands.load_equities(client, "KR", 기준일)[1]) == 2  # type: ignore[arg-type]
    # 장기 신호의 자본과 밴드도 같은 기준으로 (25.861 — 교차검증이 load_band 누락·_latest_equity 무검사를 짚음)
    assert signals._latest_equity(client, 1, 기준일) == 1000  # type: ignore[arg-type]
    from datetime import date, timedelta

    for k in range(300):  # 밴드 최소 표본(250) 이상
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (1, ?, ?, 'KRW', 't', 't')",
            [(date(2025, 1, 1) + timedelta(days=k)).isoformat(), 100 + k % 7],
        )
    밴드 = signals.load_band(client, 1, 기준일, 1000)  # type: ignore[arg-type]
    assert 밴드 is not None  # 예전: 기준이 기준일 뒤 연결로 바뀌어 자본 행이 모두 걸러져 None
    # 기준일 뒤에는 연결로 넘어간다
    assert sorted(scores.load_financials(client, "KR", "2026-04-01")[1]) == [2025]  # type: ignore[arg-type]


def _스냅(연도: int, 연결: bool, 접수: str, 매출: float) -> dict:
    return {"as_of_date": 접수, "fiscal_year": 연도, "consolidated": 연결, "values": {"revenue": 매출, "total_equity": 매출}}


def test_백테스트도_기준일마다_연결_별도를_고른다_25_860() -> None:
    """7회차 제안 1 — 2025 에 처음 연결을 낸 회사가 그 전 기준일에 별도 재무를 모두 잃었다(미래가 과거 표본을 골랐다)."""
    from batch.jobs import backtest as bj

    처음연결 = [_스냅(2022, False, "2023-03-10", 1.0), _스냅(2023, False, "2024-03-10", 2.0),
               _스냅(2024, False, "2025-03-10", 3.0), _스냅(2024, True, "2025-03-10", 30.0),
               _스냅(2025, True, "2026-03-10", 40.0)]  # fmt: skip
    최근, 전년, _ = bj.pit_financials(처음연결, "2024-06-30")
    assert 최근 is not None and 최근["values"]["revenue"] == 2.0 and 전년["values"]["revenue"] == 1.0
    # FY2024 부터 연결이 있으면 그 뒤로는 연결 — 별도와 섞지 않는다
    최근, 전년, _ = bj.pit_financials(처음연결, "2025-06-30")
    assert 최근["values"]["revenue"] == 30.0 and 전년 is None
    assert [e[1] for e in bj.pit_equities(처음연결, "2024-06-30")] == [1.0, 2.0]

    # 연결을 끊은 회사는 끊기 전 기준일에 연결을 쓴다
    끊음 = [_스냅(2022, True, "2023-03-10", 10.0), _스냅(2022, False, "2023-03-10", 1.0),
            _스냅(2023, True, "2024-03-10", 20.0), _스냅(2023, False, "2024-03-10", 2.0),
            _스냅(2024, False, "2025-03-10", 3.0)]  # fmt: skip
    assert bj.pit_financials(끊음, "2024-06-30")[0]["values"]["revenue"] == 20.0
    assert bj.pit_financials(끊음, "2025-06-30")[0]["values"]["revenue"] == 3.0
    assert bj.latest_10k_year(끊음, "2024-06-30") == 2023


def test_백테스트_기준_고르기가_다섯_함수_모두에_걸린다_25_864() -> None:
    """25.860 테스트는 pit_financials 만 지켰다 — 나머지에서 거르기를 빼도 통과했다(25.860~862 교차검증)."""
    from batch.jobs import backtest as bj

    def 행(연도: int, 연결: bool, 접수: str, 영업: float, 자본: float, 현금: float | None = None) -> dict:
        v = {"operating_income": 영업, "total_equity": 자본}
        if 현금 is not None:
            v["operating_cash_flow"] = 현금
        return {"as_of_date": 접수, "fiscal_year": 연도, "consolidated": 연결, "values": v}

    # 연결은 적자, 별도는 흑자 — 섞이면 흑자 연수가 바뀐다. 2024 연결은 기준일 뒤 접수
    # 별도를 먼저 둔다 — 같은 접수일이면 먼저 본 행이 남으므로, 거르기가 없으면 별도가 이긴다
    행들 = [행(y, False, f"{y + 1}-03-10", 1.0, 10.0 + y, 5.0) for y in (2021, 2022, 2023)]
    행들 += [행(y, True, f"{y + 1}-03-10", -1.0, 100.0 + y) for y in (2021, 2022, 2023)]
    행들 += [행(2024, True, "2025-03-10", -1.0, 999.0)]
    기준일 = "2024-06-30"
    assert bj.stability_from(행들, 기준일, 2023) == (0, 3)  # 연결만 — 별도 흑자가 덮지 않는다
    자본 = bj.pit_equities(행들, 기준일)
    assert len(자본) == 3 and all(e[1] > 100 for e in 자본)  # 같은 해 연결·별도가 섞이지 않는다
    최근, _, _ = bj.pit_financials(행들, 기준일)
    assert "operating_cash_flow" not in bj.year_values(행들, 기준일, 최근)  # 별도 값으로 빈 칸을 메우지 않는다
