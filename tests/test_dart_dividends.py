"""DART 배당(alotMatter) 파싱 테스트. 픽스처는 2026-09-17 실제 응답이다. 네트워크를 타지 않는다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import dividends as job
from batch.sources import dart_dividends as dd

FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "dart_alot_matter.json").read_text(encoding="utf-8")
)


def by_year(rows: list[dd.DividendYear]) -> dict[int, dd.DividendYear]:
    return {row.fiscal_year: row for row in rows}


class Test삼성전자_2025:
    rows = by_year(dd.parse_alot_matter(FIXTURE["samsung_2025"], 2025))

    def test_당기_전기_전전기_세_해가_나온다(self) -> None:
        assert sorted(self.rows) == [2023, 2024, 2025]

    def test_단위를_원으로_맞춘다(self) -> None:
        y = self.rows[2025]
        assert y.cash_dividend_total == 11_107_906 * 1_000_000  # 백만원 → 원
        assert y.dps_common == 1_668
        assert y.dps_preferred == 1_669
        assert y.payout_ratio == 25.10 and y.payout_basis == "연결"
        assert y.yield_common == 1.50
        assert y.net_income_consolidated == 44_260_956 * 1_000_000

    def test_접수일이_시점_기준이다(self) -> None:
        y = self.rows[2023]
        assert y.receipt_no == "20260310002820"
        assert y.as_of_date == "2026-03-10"  # 2023 값이지만 이 보고서로는 2026-03-10 에 알 수 있었다
        assert y.report_year == 2025


class Test액면분할_함정:
    def test_주당배당금은_분할로_급감한다_총액은_아니다(self) -> None:
        # 2018 년 50:1 분할. 주당으로 보면 42,500 → 1,416 원으로 "감소" 처럼 보인다
        rows = by_year(dd.parse_alot_matter(FIXTURE["samsung_2018"], 2018))
        assert rows[2017].dps_common == 42_500
        assert rows[2018].dps_common == 1_416
        assert rows[2018].cash_dividend_total > rows[2017].cash_dividend_total


class Test빈_값:
    def test_하이픈은_None(self) -> None:
        rows = by_year(dd.parse_alot_matter(FIXTURE["skhynix_2022"], 2022))
        assert rows[2022].dps_preferred is None  # 우선주 없음 "-"
        assert rows[2022].dps_common == 1_200

    def test_상태가_정상이_아니면_빈_목록(self) -> None:
        assert dd.parse_alot_matter({"status": "013", "message": "조회된 데이타가 없습니다."}, 2025) == []

    def test_별도_성향만_있으면_별도로_표시(self) -> None:
        payload = {
            "status": "000",
            "list": [
                {"rcept_no": "20260320000001", "se": "(별도)현금배당성향(%)", "stock_knd": "-",
                 "thstrm": "30.5", "frmtrm": "-", "lwfr": "-", "stlm_dt": "2025-12-31"},
            ],
        }
        rows = by_year(dd.parse_alot_matter(payload, 2025))
        assert rows[2025].payout_ratio == 30.5 and rows[2025].payout_basis == "별도"
        # 값이 하나도 없는 해는 행을 만들지 않는다 — 빈 행이 옛 보고서의 실제 값을 가렸다 (25.830)
        assert 2024 not in rows and 2023 not in rows

    def test_결산월이_12월이_아니어도_연도는_요청한_사업연도(self) -> None:
        # 재무는 bsns_year 로 적재한다 — 배당도 같은 열쇠여야 짝이 맞는다 (docs/infra.md 25.732)
        payload = {
            "status": "000",
            "list": [
                {"rcept_no": "20260620000001", "se": "(연결)현금배당성향(%)", "stock_knd": "-",
                 "thstrm": "25", "frmtrm": "20", "lwfr": "-", "stlm_dt": "2026-03-31"},
            ],
        }
        rows = by_year(dd.parse_alot_matter(payload, 2025))
        assert sorted(rows) == [2024, 2025]  # 2023 칸은 비었다 (25.830)
        assert rows[2025].payout_ratio == 25 and rows[2024].payout_ratio == 20


class Test배치_도우미:
    def test_여섯_해_보고서를_하나씩(self) -> None:
        # 해마다 하나씩 — 값이 그해 보고서 접수일에 알려진다 (25.476)
        assert job.report_years(2025) == [2025, 2024, 2023, 2022, 2021, 2020]

    def test_행과_열_개수(self) -> None:
        row = job.to_row(1, dd.parse_alot_matter(FIXTURE["samsung_2025"], 2025)[0], "2026-09-17")
        assert len(row) == db.column_count(job._COLS)


class Test전부_실패:
    """키가 틀리거나 DART 점검이면 전부 실패한다 — 그걸 성공으로 끝내지 않는다 (docs/infra.md 25.319)."""

    def _돌리기(self, monkeypatch, 결과):
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        monkeypatch.setattr(job, "targets", lambda _c: [(i, f"{i:08d}") for i in range(1, 51)])
        monkeypatch.setattr(job, "MIN_INTERVAL", 0)
        불린 = []

        def 가짜(corp, year):
            불린.append((corp, year))
            return 결과

        monkeypatch.setattr(job.dd, "fetch_alot_matter", 가짜)
        code = job.run(latest=2025)
        상태 = mem.conn.execute("SELECT status FROM batch_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
        return code, 상태, 불린

    def test_연속_실패면_멈추고_실패로_끝난다(self, monkeypatch) -> None:
        틀린키 = type("R", (), {"ok": False, "error": "010 등록되지 않은 키", "limit_state": "unknown", "data": [], "attempts": 1})()
        code, 상태, 불린 = self._돌리기(monkeypatch, 틀린키)
        assert code == 1 and 상태 == "failed"
        assert len(불린) == job.MAX_CONSECUTIVE_FAILURES  # 1,700여 회가 아니라 문턱(20)에서 멈춘다


def test_DART_한도가_이미_찼으면_부르지_않는다(monkeypatch) -> None:
    """100사마다 세서 한도가 찬 날에도 100번 더 불렀다 (docs/infra.md 25.388)."""
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "targets", lambda _c: [(1, "00000001")])
    monkeypatch.setattr(job.db, "usage_blocked_today", lambda _c, name: name == "dart_opendart")
    monkeypatch.setattr(job.dd, "fetch_alot_matter", lambda *_a: pytest.fail("한도가 찬 날에 불렀다"))
    assert job.run(latest=2025) == 0
    assert mem.conn.execute("SELECT status FROM batch_runs ORDER BY id DESC LIMIT 1").fetchone()[0] == "skipped"


def test_업종_수집도_부르기_전에_본다() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "sectors.py").read_text(encoding="utf-8")
    assert 'if market == "KR" and db.usage_blocked_today(client, "dart_opendart"):' in src


def test_지난해_보고서는_받았으면_건너뛰고_최근_것은_늘_부른다(monkeypatch) -> None:
    """처음 채우기가 시간 제한을 넘으면 다음 실행이 이어 간다 (docs/infra.md 25.478, 교차검증)."""
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    job.db.apply_migrations(mem)
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    for 해 in (2024, 2023):  # 1번 회사는 2024·2023 보고서를 이미 받았다
        mem.conn.execute(
            "INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no, as_of_date, source, fetched_at)"
            " VALUES (1, ?, ?, 'r', '2025-03-01', 't', 't')", [해, 해])
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "targets", lambda _c: [(1, "00000001")])
    monkeypatch.setattr(job, "MIN_INTERVAL", 0)
    불린: list[int] = []
    빈 = type("R", (), {"ok": True, "error": "", "limit_state": "ok", "data": [], "attempts": 1})()
    monkeypatch.setattr(job.dd, "fetch_alot_matter", lambda corp, year: (불린.append(year), 빈)[1])
    job.run(latest=2025)
    assert 불린 == [2025, 2022, 2021, 2020]
