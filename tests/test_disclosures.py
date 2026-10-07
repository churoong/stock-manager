"""국내 공시 수집 (docs/data-sources.md 1.2). 파싱은 확인된 필드만 믿고, 대상 질의는 실제 스키마에 돌린다."""

from __future__ import annotations

from batch.jobs import disclosures_kr as job
from batch.sources import dart_disclosures as src
from tests.test_report_picks import SqliteClient

PAYLOAD = {
    "status": "000",
    "message": "정상",
    "list": [
        {"corp_code": "00126380", "rcept_no": "20260915000123", "report_nm": "주요사항보고서(자기주식취득결정)", "rm": "유"},
        {"rcept_no": "20260916000456", "report_nm": " 임원ㆍ주요주주특정증권등소유상황보고서 "},
        {"rcept_no": "", "report_nm": "제목만"},  # 접수번호 없음 → 버린다
        {"rcept_no": "20260917000789"},  # 제목 없음 → 버린다
    ],
}


class Test파싱:
    def test_접수번호_앞_8자리가_접수일이다(self) -> None:
        rows = src.parse_list(PAYLOAD, "00126380")
        assert [r.receipt_no for r in rows] == ["20260915000123", "20260916000456"]
        assert rows[0].disclosed_at == "2026-09-15" and rows[1].disclosed_at == "2026-09-16"
        assert rows[1].title == "임원ㆍ주요주주특정증권등소유상황보고서"
        assert rows[1].corp_code == "00126380"  # 행에 없으면 물어본 회사
        assert rows[0].report_name == "유" and rows[1].report_name is None
        assert rows[0].url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260915000123"

    def test_빈_응답(self) -> None:
        assert src.parse_list({"status": "013", "list": None}, "x") == []


class Test적재:
    def _client(self) -> SqliteClient:
        c = SqliteClient()
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, dart_corp_code)"
            " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '00126380'),"
            " (2, 'B', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '00000002'),"
            " (3, 'C', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', NULL),"
            " (4, 'D', 'NASDAQ', 'US', 'USD', 'active', 't', 't', NULL)"
        )
        c.conn.execute(
            "INSERT INTO monitor_targets (market, stock_id, yahoo_symbol, name, currency, reasons, built_at)"
            " VALUES ('KR', 1, 'A.KS', 'A', 'KRW', '[\"holding\"]', 't'), ('KR', 3, 'C.KS', 'C', 'KRW', '[]', 't')"
        )
        c.conn.execute("INSERT INTO watchlist (stock_id, added_at) VALUES (2, 't')")
        return c

    def test_지켜보는_종목_중_고유번호가_있는_것만(self) -> None:
        # 1 감시 대상, 2 관심, 3 은 고유번호 없음, 4 미국
        assert job.pick_targets(self._client()) == [(1, "00126380"), (2, "00000002")]  # type: ignore[arg-type]

    def test_같은_접수번호는_두_번_들어가지_않는다(self) -> None:
        client = self._client()
        client.execute = _counting(client)  # record_rows_written 이 부른다
        rows = [job.to_row(1, d, "dart_opendart", "now") for d in src.parse_list(PAYLOAD, "00126380")]
        assert len(rows[0]) == 10
        job.store(client, rows)  # type: ignore[arg-type]
        job.store(client, rows)  # type: ignore[arg-type]
        assert client.conn.execute("SELECT COUNT(*) FROM disclosures").fetchone()[0] == 2
        title, url = client.conn.execute("SELECT title, url FROM disclosures WHERE receipt_no = '20260915000123'").fetchone()
        assert "자기주식" in title and url.endswith("20260915000123")


def _counting(client: SqliteClient):
    original = client.execute

    def execute(sql, args=None):
        if "api_usage" in sql:
            return original("SELECT 1", [])
        return original(sql, args)

    return execute


def test_조회_끝날은_한국_날짜다(monkeypatch) -> None:
    """국내 아침 배치(08:27 KST)에서 UTC 날짜는 아직 전날이다 (docs/infra.md 25.204).

    UTC 로 잡으면 오늘 아침 접수된 공시가 하루 늦게 들어온다. 날짜를 현실과 먼 값으로 고정해,
    UTC 시계를 보는 옛 코드로 돌아가면 반드시 어긋나게 한다.
    """
    from datetime import date

    from batch.core import db
    from batch.sources.yfinance_src import FetchResult

    물은것: list[tuple[str, str]] = []

    def 가짜_목록(corp_code: str, bgn: str, end: str) -> FetchResult:
        물은것.append((bgn, end))
        return FetchResult(ok=True, data=[], source="dart_opendart")

    monkeypatch.setattr(job.cal, "local_today", lambda market: date(2030, 1, 2) if market == "KR" else None)
    monkeypatch.setattr(job.src, "fetch_list", 가짜_목록)
    monkeypatch.setattr(job.time, "sleep", lambda _s: None)
    monkeypatch.setattr(db, "record_and_guard", lambda *a, **k: "ok")

    job.collect(Test적재()._client())  # type: ignore[arg-type]

    assert 물은것 and all(end == "20300102" for _bgn, end in 물은것)


def test_한도에_닿은_호출의_공시도_담고_멈춘다(monkeypatch) -> None:
    """카운터가 한도에 닿은 호출의 응답을 받아 놓고 버렸다 — 25.318 과 같은 모양 (docs/infra.md 25.386)."""
    from batch.core import db
    from batch.sources.dart_disclosures import Disclosure
    from batch.sources.yfinance_src import FetchResult

    def 가짜_목록(corp_code: str, bgn: str, end: str) -> FetchResult:
        return FetchResult(ok=True, data=[Disclosure(corp_code, f"R{corp_code}", "유상증자", "2026-09-25")],
                           source="dart_opendart")  # fmt: skip

    monkeypatch.setattr(job.src, "fetch_list", 가짜_목록)
    monkeypatch.setattr(job.time, "sleep", lambda _s: None)
    monkeypatch.setattr(db, "record_and_guard", lambda *a, **k: "blocked")
    담긴: list = []
    monkeypatch.setattr(job, "store", lambda _c, rows: 담긴.extend(rows) or len(rows))

    _대상, 저장, 경고, _덮음 = job.collect(Test적재()._client())  # type: ignore[arg-type]
    assert 저장 == 1 and len(담긴) == 1  # 첫 호출의 공시는 담고 멈춘다
    assert any("한도" in w for w in 경고)


def test_다른_DART_수집도_받은_응답을_담고_멈춘다() -> None:
    from pathlib import Path

    뿌리 = Path(__file__).resolve().parents[1] / "batch" / "jobs"
    for 이름 in ("insider_kr.py", "sectors.py"):
        src = (뿌리 / 이름).read_text(encoding="utf-8")
        assert "받은 응답은 담고 나서 멈춘다" in src, 이름
