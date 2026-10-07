"""`batch/jobs/backtest.run` 을 처음부터 끝까지 실제로 돌리는 틀 (docs/infra.md 25.460).

**왜 필요한가.** 실행 기록(`factor_ic`)·진행 칸·분기 스냅샷 기준을 지키는 검사가 지금까지
`inspect.getsource` 로 **소스 글자**만 봤다. 글자는 맞는데 실행 결과가 틀리면 못 잡는다.
여기서는 메모리 SQLite 에 **실제 migrations/*.sql** 을 적용하고, 합성 데이터(국내 34종목, 영업일 약 2년)를
넣은 뒤 `run("KR", years=2)` 을 그대로 돌려 **DB 에 남은 것**을 본다.

- 가짜 클라이언트는 `batch/core/turso.TursoClient` 의 겉모습(execute → ResultSet, batch, close, with)만 흉내 낸다.
  결과 객체는 진짜 `turso.ResultSet` 을 쓴다 — `.dicts()`·`.scalar()`·`.last_insert_rowid` 가 운영과 같다
- 네트워크는 막는다(`requests` 가 불리면 실패). 시각은 2026-09-28 로 고정한다
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from batch.core import db
from batch.core.turso import ResultSet
from batch.jobs import backtest as backtest_module

오늘 = date(2026, 9, 28)
가격_시작 = date(2024, 8, 26)  # run 이 읽는 since(오늘 − 2년 − 30일) 바로 앞
가격_끝 = date(2026, 9, 25)  # 금요일. 오늘(월) 전 마지막 영업일
유니버스_기준일 = "2026-09-25"

종목수 = 34
#: IC 는 한 달에 30종목 이상이어야 값이 나온다(`factor_ic.MIN_STOCKS`). 발생액은 금융업을 빼고 세므로
#: 금융 아닌 종목이 30을 넘게, SUE 는 분기 공시 종목이 30을 넘게 잡는다. 종목을 늘리면 실행이 그만큼 느려진다
큰_업종 = 28
#: 금융업(KSIC 64) — 발생액 IC 에서 빠지는 종목
금융_종목 = 3
#: 분기 공시를 싣는 종목 수
분기_종목수 = 31


class 고정시각(datetime):
    """`backtest.run` 의 `datetime.now(UTC)` 를 오늘로 묶는다."""

    @classmethod
    def now(cls, tz: Any = None) -> 고정시각:  # type: ignore[override]
        return cls(오늘.year, 오늘.month, 오늘.day, 0, 0, tzinfo=tz or UTC)


class 가짜클라이언트:
    """`TursoClient` 흉내. 메모리 SQLite 위에서 문장을 그대로 돌리고, 돌린 문장을 `기록` 에 남긴다."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.기록: list[tuple[str, list[Any]]] = []
        self.닫힘 = False

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        인자 = list(args or [])
        self.기록.append((sql, 인자))
        cur = self.conn.execute(sql, 인자)
        columns = [c[0] for c in cur.description] if cur.description else []
        rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
        self.conn.commit()
        rowid = cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else None
        return ResultSet(columns=columns, rows=rows, affected_rows=max(cur.rowcount, 0), last_insert_rowid=rowid)

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        # 운영의 batch 는 한 트랜잭션이다. 여기서는 순서대로 돌리면 충분하다(실패하면 테스트가 깨진다)
        return [self.execute(sql, args) for sql, args in statements]

    def close(self) -> None:
        # 검사가 끝난 뒤에도 DB 를 들여다봐야 하므로 연결은 닫지 않고 표시만 한다
        self.닫힘 = True

    def __enter__(self) -> 가짜클라이언트:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def 행들(self, sql: str, args: list[Any] | None = None) -> list[tuple[Any, ...]]:
        """검사용 읽기 — `기록` 에 남기지 않는다."""
        return self.conn.execute(sql, args or []).fetchall()


def _영업일(시작: date, 끝: date) -> list[str]:
    out: list[str] = []
    d = 시작
    while d <= 끝:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _분기_공시일(연도: int, 코드: str) -> str:
    return {"11013": f"{연도}-05-15", "11012": f"{연도}-08-14", "11014": f"{연도}-11-14",
            "11011": f"{연도 + 1}-03-15"}[코드]  # fmt: skip


def _채우기(client: 가짜클라이언트) -> None:
    """합성 데이터. 무작위이되 씨앗을 고정해 늘 같은 값이다."""
    rng = random.Random(458)
    conn = client.conn
    t = "2026-09-25T00:00:00+00:00"

    for sid in range(1, 종목수 + 1):
        if sid <= 큰_업종:
            sector, code = "전자부품", ("KSIC 264" if sid % 2 else None)  # 코드는 일부만
        elif sid <= 큰_업종 + 금융_종목:
            sector, code = "금융", "KSIC 641"
        else:
            sector, code = None, None  # 업종 모름
        conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at,"
            " sector, sector_code, listed_shares, listed_date)"
            " VALUES (?, ?, ?, 'KR', 'KRW', 'active', 'test', ?, ?, ?, ?, '2010-01-04')",
            [sid, f"{sid:06d}", "KOSPI", t, sector, code, 10_000_000 + sid * 100_000],  # 한 시장 (25.810)
        )
        conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, ?, 1, 'KRW', ?)",
            [유니버스_기준일, sid, t],
        )
    # 후보 밖의 상장폐지 종목 — `has_delisted` 가 후보 안에서만 세는지 함께 지나간다
    conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (999, '999999', 'KOSPI', 'KR', 'KRW', 'delisted', 'test', ?)",
        [t],
    )

    # 가격: 종목마다 추세가 다른 무작위 걸음. 시총 5,000억 안팎, 거래대금 수십억 — 시점 유니버스 문턱을 넘는다
    days = _영업일(가격_시작, 가격_끝)
    rows: list[tuple[Any, ...]] = []
    for sid in range(1, 종목수 + 1):
        px = 40_000.0 + sid * 500
        추세 = (sid % 7 - 3) * 0.0004
        for d in days:
            px *= 1 + 추세 + rng.gauss(0, 0.015)
            vol = 200_000 + rng.randint(0, 50_000)
            rows.append((sid, d, px, px, vol, int(px * vol), "KRW", "test", t))
    conn.executemany(
        "INSERT INTO prices (stock_id, date, close, adj_close, volume, value, currency, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    # 재무 스냅샷: 연간(11011) 2019~2025, 분기(11013·11012·11014) 2020~2026 중 오늘까지 접수된 것
    snaps: list[tuple[Any, ...]] = []
    for sid in range(1, 종목수 + 1):
        규모 = 1e11 * (1 + sid / 10)
        for 연도 in range(2019, 2026):
            성장 = 1.05 ** (연도 - 2019) * (1 + rng.uniform(-0.05, 0.05))
            ni = 규모 * 0.08 * 성장
            v = {
                "revenue": 규모 * 성장, "operating_income": 규모 * 0.1 * 성장, "net_income": ni,
                "total_assets": 규모 * 2 * 성장, "total_liabilities": 규모 * 성장, "total_equity": 규모 * 성장,
                "operating_cash_flow": ni * rng.uniform(0.6, 1.4),
                "shares_basic": 1e7 * (1 + rng.uniform(-0.02, 0.03)), "shares_basic_prev": 1e7,
            }  # fmt: skip
            snaps.append((sid, _분기_공시일(연도, "11011"), f"A{sid}-{연도}", 연도, "11011", 1, json.dumps(v)))
        if sid > 분기_종목수:
            continue
        for 연도 in range(2020, 2027):
            for code in ("11013", "11012", "11014"):
                공시일 = _분기_공시일(연도, code)
                if 공시일 > 오늘.isoformat():
                    continue
                q = {"net_income": 규모 * 0.02 * (1.05 ** (연도 - 2020)) * (1 + rng.uniform(-0.3, 0.3))}
                snaps.append((sid, 공시일, f"Q{sid}-{연도}-{code}", 연도, code, 1, json.dumps(q)))
    conn.executemany(
        "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
        " consolidated, payload, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'test', '2026-09-25')",
        snaps,
    )
    conn.commit()


def _준비(monkeypatch: pytest.MonkeyPatch) -> 가짜클라이언트:
    """마이그레이션을 적용하고 데이터를 채운 가짜 클라이언트를 만들어 `run` 이 받게 한다."""
    monkeypatch.setenv("OFFLINE_MODE", "1")
    monkeypatch.setenv("DB_BACKEND", "turso")  # auto 면 D1 을 살피러 나간다

    def _막기(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("백테스트가 네트워크를 불렀다")

    import requests

    monkeypatch.setattr(requests.sessions.Session, "request", _막기)

    client = 가짜클라이언트()
    db.apply_migrations(client)  # 운영과 같은 길로 스키마를 만든다. run 안에서 한 번 더 불러도 할 일이 없다
    _채우기(client)
    client.기록.clear()
    monkeypatch.setattr(backtest_module, "TursoClient", lambda: client)
    monkeypatch.setattr(backtest_module, "datetime", 고정시각)
    return client


@pytest.fixture
def 가짜(monkeypatch: pytest.MonkeyPatch) -> 가짜클라이언트:
    """데이터만 채운 클라이언트. 아직 `run` 을 돌리지 않았다."""
    return _준비(monkeypatch)


#: 한 번 돌린 결과를 여러 검사가 나눠 본다 — 한 번에 5초 남짓 걸린다. 장기 문턱을 하나 넣어
#: 진행 칸이 IC·장기·비용 0 단계를 모두 세는지까지 한 번에 본다
장기_문턱 = (60.0,)


@pytest.fixture(scope="module")
def 실행() -> Iterator[tuple[가짜클라이언트, int]]:
    """(클라이언트, run 의 반환값). 우회 없이 운영과 같은 길로 돈다."""
    with pytest.MonkeyPatch.context() as mp:
        client = _준비(mp)
        돌림 = backtest_module.run("KR", years=2, top_n=10, long_thresholds=장기_문턱)
        yield client, 돌림


def test_여러_번_리밸런스해도_거래대금_표가_살아_있다(가짜: 가짜클라이언트) -> None:
    """`simulate` 가 거래대금 인자 `turnover` 를 회전율 숫자로 덮어써, 첫 매수 뒤 리밸런스에서
    `PriceView.values` 가 숫자에 `.get` 을 불러 죽었다 (docs/infra.md 25.460). 이 틀이 처음 돌며 찾았다."""
    from batch.services import backtest as bt

    받은_종류: list[str] = []
    원래 = bt.PriceView

    class 세는View(원래):  # type: ignore[misc, valid-type]
        def __init__(self, prices: Any, cutoff: str, turnover: Any = None, levels: Any = None) -> None:
            받은_종류.append(type(turnover).__name__)
            super().__init__(prices, cutoff, turnover, levels)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bt, "PriceView", 세는View)
        try:
            assert backtest_module.run("KR", years=2, top_n=10) == 0
        finally:
            db._열린_실행.clear()
    assert 받은_종류 and set(받은_종류) <= {"dict", "NoneType"}, set(받은_종류)


def _진행들(client: 가짜클라이언트) -> list[dict[str, Any]]:
    """`db.note_progress` 가 batch_runs.step_log 에 적은 진행 상태들, 적은 순서대로."""
    return [
        json.loads(args[0])["progress"]
        for sql, args in client.기록
        if sql.startswith("UPDATE batch_runs SET step_log = ?")
    ]


def _마지막_실행(client: 가짜클라이언트) -> tuple[str, dict[str, Any]]:
    rows = client.행들("SELECT status, step_log FROM batch_runs WHERE job_name = 'backtest' ORDER BY id DESC LIMIT 1")
    assert rows, "batch_runs 에 백테스트 행이 없다"
    status, step_log = rows[0]
    return status, json.loads(step_log) if step_log else {}


def test_실행하면_성공_기록과_전략별_결과가_남는다(실행: tuple[가짜클라이언트, int]) -> None:
    가짜, 돌림 = 실행
    assert 돌림 == 0
    assert 가짜.닫힘

    status, step_log = _마지막_실행(가짜)
    assert status in ("success", "partial")
    assert step_log["stocks"] == 종목수

    # 결과 표: 같은 실행 묶음(group_id)에 전략마다 한 행, 곡선도 함께
    group = step_log["group_id"]
    rows = 가짜.행들("SELECT id, strategy FROM backtest_runs WHERE group_id = ?", [group])
    전략들 = [s for _, s in rows]
    기대 = [*backtest_module.STRATEGIES, *(f"long_q{t:g}" for t in 장기_문턱), "composite_no_cost"]
    assert sorted(전략들) == sorted(기대)
    assert step_log["strategies"] == len(전략들)
    for run_id, strategy in rows:
        (곡선수,) = 가짜.행들("SELECT COUNT(*) FROM backtest_curves WHERE run_id = ?", [run_id])[0]
        assert 곡선수 > 100, f"{strategy} 곡선이 비었다"
    # 노출(평균 보유·투자한 달·첫 투자일)이 결과 행에 실린다 (25.786)
    for (지표,) in 가짜.행들("SELECT metrics_json FROM backtest_runs WHERE group_id = ?", [group]):
        m = json.loads(지표)
        assert {"avg_holdings", "invested_share", "first_invested"} <= set(m), m


def test_실행_기록의_팩터_IC_에_모든_이름과_판정이_있다(실행: tuple[가짜클라이언트, int]) -> None:
    """`inspect.getsource` 로 보던 test_factor_ic.test_실행이_IC_를_기록에_남긴다 의 실행판."""
    가짜, 돌림 = 실행
    assert 돌림 == 0
    _, step_log = _마지막_실행(가짜)
    ic = step_log["factor_ic"]
    assert set(ic) == set(backtest_module.IC_NAMES)
    for name in ("sue", "accrual", "net_issuance", "sector_mom"):
        assert name in ic
    for name, v in ic.items():
        assert isinstance(v.get("verdict"), str) and v["verdict"], name
        assert v["months"] >= 0
    # 합성 데이터가 IC 를 실제로 낼 만큼 채워졌는지 — 모두 빈 달이면 틀이 아무것도 재지 않는다
    # sector_mom 은 빠진다 — (시장, 업종) 30종목 이상인 무리가 **둘 이상**이어야 순위가 갈린다(한 무리면 모두 같은 값).
    # 60종목이 넘게 들어 10초 안에 못 돈다. risk 는 3년 창(750 거래일)이 필요해 2년 데이터로는 비어 있다
    for name in ("composite", "value", "momentum", "sue", "accrual", "net_issuance"):
        assert ic[name]["months"] > 0, f"{name} IC 가 한 달도 안 나왔다"
    # 판정에 세는 실행인지 기록만 보고 가를 수 있다 (25.781). 이 틀은 2년·장기 문턱으로 돌아 기본 실행이 아니다
    assert step_log["params"]["years"] != backtest_module.DEFAULT_YEARS
    assert step_log["params"]["default"] is False


def test_진행_칸은_단계마다_하나씩_늘고_전체를_넘지_않는다(실행: tuple[가짜클라이언트, int]) -> None:
    """`inspect.getsource` 로 보던 test_factor_ic.test_진행_칸이_IC_단계를_센다 의 실행판.

    진행은 batch_runs.step_log 의 {"progress": {done, total, current}} 에 적힌다(`db.note_progress`).
    done 은 **시작하는 단계 앞까지 끝낸 수**라 마지막 단계(composite_no_cost)를 시작할 때 total - 1 이다.
    끝나면 `finish_batch_run` 이 step_log 를 결과로 덮어쓴다.
    """
    가짜, 돌림 = 실행
    assert 돌림 == 0
    진행 = _진행들(가짜)
    assert 진행, "진행 상태를 한 번도 적지 않았다"
    total = 진행[0]["total"]
    assert total == len(backtest_module.STRATEGIES) + len(장기_문턱) + 2  # 전략 + 장기 + IC + 비용 0
    assert all(p["total"] == total for p in 진행)
    assert all(p["done"] <= total for p in 진행)
    assert [p["done"] for p in 진행] == list(range(total))  # 멈추거나 건너뛴 칸이 없다
    # 순서: 전략들 → IC → 장기 문턱 → 비용 0. IC 를 센 뒤 장기 단계의 done 이 하나 밀려야 한다
    assert [p["current"] for p in 진행] == [
        *backtest_module.STRATEGIES, "IC", *(f"long_q{t:g}" for t in 장기_문턱), "composite_no_cost",
    ]
    status, step_log = _마지막_실행(가짜)
    assert status == "success" and "progress" not in step_log


def test_장기_문턱만_돌려도_진행_칸이_끝까지_찬다(가짜: 가짜클라이언트) -> None:
    """`only_long` 경로는 IC·전략·비용 0 단계가 없다 — 옛 글자 검사가 보던 `(0 if only_long else 2)` 의 앞쪽이다.
    실행 틀이 이 경로를 돌지 않아 그 글자를 바꿔도 아무것도 깨지지 않았다 (docs/infra.md 25.465, 교차검증)."""
    try:
        assert backtest_module.run("KR", years=2, top_n=10, long_thresholds=(60.0, 80.0), only_long=True) == 0
    finally:
        db._열린_실행.clear()
    진행 = _진행들(가짜)
    assert 진행 and all(p["total"] == 진행[0]["total"] for p in 진행)
    assert [p["current"] for p in 진행][-2:] == ["long_q60", "long_q80"]
    assert [p["done"] for p in 진행] == list(range(진행[0]["total"]))  # 끝까지 한 칸씩


def test_분기_스냅샷은_두_기준을_모두_읽고_기준표시를_단다(가짜: 가짜클라이언트) -> None:
    """기준은 기준일마다 `quarter_series` 가 고른다 (docs/infra.md 25.461, 교차검증). 읽는 쪽이 종목 전체로
    고르면 미래에 처음 낸 연결 공시가 과거 기준일의 별도 계열을 지운다. 그래서 둘 다 읽고 표시를 단다."""
    별도만_있는_분기 = 분기_종목수 + 1  # 연결 연간은 있고, 분기는 별도로만 낸 종목
    별도_회사 = 종목수 + 1  # 연간·분기 모두 별도만 낸 종목
    conn = 가짜.conn
    conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (?, 'SEP001', 'KOSDAQ', 'KR', 'KRW', 'active', 'test', 't')",
        [별도_회사],
    )
    섞을것 = [
        (별도만_있는_분기, "2026-05-15", "S1", 2026, "11013", 0),
        (별도만_있는_분기, "2026-08-14", "S2", 2026, "11012", 0),
        (1, "2026-05-15", "S3", 2026, "11013", 0),  # 연결 분기도 있는 종목에 별도 분기를 하나 더
        (별도_회사, "2025-03-15", "S4", 2024, "11011", 0),
        (별도_회사, "2025-05-15", "S5", 2025, "11013", 0),
    ]
    conn.executemany(
        "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
        " consolidated, payload, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, '{\"basis\": \"OFS\"}', 't', 't')",
        섞을것,
    )
    conn.commit()

    got = backtest_module.load_quarter_snapshots(가짜, "KR")
    별도행 = [r for r in got[별도만_있는_분기] if r["values"].get("basis") == "OFS"]
    assert {r["report_code"] for r in 별도행} == {"11013", "11012"} and not any(r["consolidated"] for r in 별도행)
    assert sorted(r["report_code"] for r in got[별도_회사]) == ["11011", "11013"]
    assert not any(r["consolidated"] for r in got[별도_회사])
    assert {"11011", "11012", "11013", "11014"} <= {r["report_code"] for r in got[1] if r["consolidated"]}


def test_판단용_과거는_시작일보다_앞에서_읽고_곡선은_시작일부터(가짜: 가짜클라이언트) -> None:
    """시작 30일 전부터만 읽어 종합·모멘텀이 첫 약 12개월, 리스크가 약 28개월 현금이었다 (docs/infra.md 25.532, 감사 재현)."""
    from batch.services import backtest as bt

    읽은: list[str] = []
    원래_읽기 = backtest_module.load_prices_and_turnover
    리밸: list[list[str]] = []
    원래_월초 = bt.month_starts

    def 읽기(client: Any, ids: Any, since: str, *a: Any, **k: Any) -> Any:
        읽은.append(since)
        return 원래_읽기(client, ids, since, *a, **k)

    def 월초(dates: list[str]) -> list[str]:
        리밸.append(list(dates))
        return 원래_월초(dates)

    시작 = (오늘 - timedelta(days=365 * 2 + 30)).isoformat()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(backtest_module, "load_prices_and_turnover", 읽기)
        mp.setattr(bt, "month_starts", 월초)
        try:
            assert backtest_module.run("KR", years=2, top_n=10) == 0
        finally:
            db._열린_실행.clear()
    assert 읽은[0] == (date.fromisoformat(시작) - timedelta(days=backtest_module.WARMUP_CALENDAR_DAYS)).isoformat()
    assert 리밸 and min(리밸[0]) >= 시작  # 곡선·리밸런스는 그대로 시작일부터


def test_읽기_어림은_있는_워밍업만_더한다(가짜: 가짜클라이언트) -> None:
    """백필이 얕으면 워밍업 구간은 비어 있다 — 없는 행을 더해 어림이 약 49% 부풀었다 (docs/infra.md 25.537, 교차검증)."""
    받은: list[int] = []

    def 예산(client: Any, estimate: int, label: str) -> tuple[bool, str]:
        받은.append(estimate)
        return True, "ok"

    # **다른 나라의 깊은 백필이 어림을 부풀리지 않는다** (25.548, 교차검증) — 표 전체 MIN 은 미국 옛 가격 하나로
    # 국내 워밍업을 900일로 늘렸다
    가짜.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)"
        " VALUES (99001, 'OLD', 'NYSE', 'US', 'Old', 'USD', 'active', 't', 't')"
    )
    가짜.conn.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
        " VALUES (99001, '2015-01-02', 1, 'USD', 't', 't')"
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(backtest_module.db, "check_read_budget", 예산)
        mp.setattr(backtest_module, "load_prices_and_turnover", lambda *a, **k: ({}, {}))
        try:
            backtest_module.run("KR", years=2, top_n=10)
        finally:
            db._열린_실행.clear()
    종목 = len(backtest_module.load_universe(가짜, "KR")[0])  # type: ignore[arg-type]
    # 가격이 시작일 바로 앞(가격_시작)부터라 워밍업은 그 며칠뿐이다
    앞선 = (date.fromisoformat((오늘 - timedelta(days=365 * 2 + 30)).isoformat()) - 가격_시작).days
    assert 받은 == [종목 * (2 * backtest_module.TRADING_DAYS_PER_YEAR + max(0, 앞선) * 252 // 365)]
    # 나라 전체를 훑는 조인 MIN 조회를 쓰지 않는다 — 종목마다 색인으로 MIN (25.543·25.548, 교차검증)
    assert not any("MIN(p.date) FROM prices p JOIN" in sql for sql, _ in 가짜.기록)
    assert not any(sql == "SELECT MIN(date) FROM prices" for sql, _ in 가짜.기록)
