"""국내 내부자 매매 수집 테스트. 픽스처는 실제 DART 응답에서 옮긴 것이다 (tests/fixtures/dart_insider.json).

네트워크를 타지 않는다. 여기서 보는 것은 셋이다.
  1. 부호·콤마·"-" 가 섞인 숫자를 제대로 읽는가 (추측하면 매도가 매수로 둔갑한다)
  2. 창 밖 접수분을 저장하지 않는가 (응답이 2년치라 다 넣으면 쓰기 예산이 날아간다)
  3. 같은 보고서를 두 번 넣지 않는가
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import insider_kr as job
from batch.sources import dart_insider as src
from tests.test_portfolio_job import MemClient

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dart_insider.json").read_text(encoding="utf-8"))


class Test숫자_읽기:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("1,000", 1000), ("-500", -500), ("0", 0), ("-", None), ("", None), (None, None), ("3,400주", None)],
    )
    def test_부호와_콤마(self, text, expected) -> None:
        assert src.parse_number(text) == expected


class Test파싱:
    def test_실제_응답을_읽는다(self) -> None:
        reports, dropped = src.parse_reports(FIXTURE)
        assert dropped == 1  # 보고자·숫자가 "-" 인 마지막 행
        assert [(r.receipt_no, r.action, abs(r.shares_delta)) for r in reports] == [
            ("20240919000037", "buy", 1000),
            ("20241004000101", "sell", 500),
            ("20260211000279", "other", 0),  # 증감 0. 사유를 모르니 buy 로 밀지 않는다
            ("20260901000111", "buy", 200000),
        ]

    def test_직위와_주요주주를_함께_남긴다(self) -> None:
        reports, _ = src.parse_reports(FIXTURE)
        assert reports[0].role == "비등기임원 부사장"
        assert reports[3].role == "등기임원 대표사장 10%이상주주"

    def test_창_밖_접수분은_거른다(self) -> None:
        reports, dropped = src.parse_reports(FIXTURE, since="2026-01-01")
        assert [r.filed_date for r in reports] == ["2026-02-11", "2026-09-01"]
        assert dropped == 1  # 거른 것은 버린 행에 세지 않는다

    def test_소유수도_읽어_둔다(self) -> None:
        reports, _ = src.parse_reports(FIXTURE)
        assert reports[0].shares_after == 3000 and reports[2].shares_after == 0


def test_열_개수() -> None:
    report = src.parse_reports(FIXTURE)[0][0]
    assert len(job.to_row(1, report, "t")) == db.column_count(job._COLS) == 12


def test_적재_끝까지(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job.time, "sleep", lambda _: None)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at,"
        " dart_corp_code) VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't', '00126380')"
    )
    c.execute(
        "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
        " VALUES ('2026-09-14', 1, 1, 'KRW', 't')"
    )

    calls: list[tuple[str, str | None]] = []

    def fake_fetch(corp_code: str, since: str | None = None):
        calls.append((corp_code, since))
        from batch.sources.yfinance_src import FetchResult

        return FetchResult(ok=True, source=src.SOURCE, data=src.parse_reports(FIXTURE, since), limit_state="ok")

    monkeypatch.setattr(src, "fetch_reports", fake_fetch)

    assert job.run(as_of="2026-09-18", lookback_days=30) == 0
    assert calls == [("00126380", "2026-08-19")]  # 창 시작일을 소스에 넘긴다
    rows = c.execute("SELECT filed_date, insider, action, shares, trade_date, price, currency FROM insider_trades").fetchall()
    assert rows == [("2026-09-01", "홍길동", "buy", 200000, None, None, "KRW")]

    # 같은 보고서를 다시 받아도 행이 늘지 않는다
    assert job.run(as_of="2026-09-18", lookback_days=30) == 0
    assert c.execute("SELECT COUNT(*) FROM insider_trades").fetchone()[0] == 1

    step = c.execute("SELECT step_log FROM batch_runs WHERE job_name = 'insider_kr' ORDER BY id").fetchall()[0][0]
    assert json.loads(step)["with_reports"] == 1


def test_한도에_걸리면_멈추고_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job.time, "sleep", lambda _: None)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid in (1, 2):
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, dart_corp_code)"
            " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't', ?)",
            [sid, f"00000{sid}", f"0012638{sid}"],
        )
        c.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES ('2026-09-14', ?, 1, 'KRW', 't')",
            [sid],
        )

    def blocked(corp_code: str, since: str | None = None):
        from batch.sources.yfinance_src import FetchResult

        return FetchResult(ok=False, source=src.SOURCE, error="DART 오류 020: 사용한도 초과", limit_state="blocked")

    monkeypatch.setattr(src, "fetch_reports", blocked)
    assert job.run(as_of="2026-09-18") == 1
    row = c.execute("SELECT status, error_text FROM batch_runs WHERE job_name = 'insider_kr'").fetchone()
    assert row[0] == "partial" and "한도" in row[1]
