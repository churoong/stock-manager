"""성과 지표의 **베타**와 읽는 횟수 (docs/infra.md 25.65).

**베타는 2026-09-21 까지 한 번도 계산되지 않았다.** 모든 것이 갖춰져 있었다 —
`docs/metrics.md` 6절의 식, `performance_metrics.beta` 열, `services/metrics.beta()` 와
그 테스트, `scoring.py` 의 `beta_abs` 지표, 종목 화면의 "베타" 줄, 지수를 받는
`index_prices` 배치. 딱 한 줄, **적재가 `compute(market=...)` 에 지수를 넘기는 줄**이 없었다.
`compute` 는 `if market:` 이라 조용히 건너뛰었고, 열은 늘 NULL 이었다. 오류도 경고도 없었다.

그래서 이 파일은 둘을 지킨다.

1. 지수가 있으면 **베타가 실제로 채워진다**. 없으면 **경고가 나온다** — 조용히 비우지 않는다
2. 기간마다 종목 시세를 다시 읽지 않는다. 880종목 × 3기간이면 **2,640 번 왕복**이다
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

import pytest

from batch.core import db
from batch.core.turso import ResultSet
from batch.jobs import metrics as job
from batch.services import trend

기준일 = "2026-09-18"


class MemClient:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.질의: list[str] = []

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        self.질의.append(sql)
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(
            columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid
        )

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in statements]

    def close(self) -> None:
        pass

    def 시세질의수(self) -> int:
        return sum(1 for q in self.질의 if "FROM prices" in q)


def 수익률(일수: int) -> list[float]:
    """**변동이 있는** 일간 수익률. 되풀이되는 고정 패턴이라 값이 늘 같다.

    (2026-09-21) 처음에는 매일 같은 비율로 오르는 계열을 썼는데, 그러면 일간 수익률의
    **분산이 0** 이라 베타가 None 이거나 0 이 된다. 베타를 재려면 움직임이 있어야 한다.
    """
    패턴 = [0.012, -0.008, 0.004, -0.015, 0.02, -0.003, 0.007, -0.011]
    return [패턴[i % len(패턴)] for i in range(일수 - 1)]


def 계열(시작: float, 수익률목록: list[float]) -> list[tuple[str, float]]:
    """(날짜, 종가). 가장 오래된 날이 앞이고 마지막이 기준일이다."""
    끝 = date.fromisoformat(기준일)
    일수 = len(수익률목록) + 1
    값 = 시작
    나온것 = []
    for i in range(일수):
        나온것.append(((끝 - timedelta(days=일수 - 1 - i)).isoformat(), 값))
        if i < len(수익률목록):
            값 *= 1 + 수익률목록[i]
    return 나온것


@pytest.fixture
def client() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '회사', 'KRW', 'active', 't', 't')"
    )
    return mem


def 시세넣기(mem: MemClient, stock_id: int, 계열값: list[tuple[str, float]]) -> None:
    for 날, 값 in 계열값:
        mem.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (?, ?, ?, 'KRW', 'krx_openapi', 't')",
            [stock_id, 날, 값],
        )


def 지수넣기(mem: MemClient, code: str, 계열값: list[tuple[str, float]]) -> None:
    for 날, 값 in 계열값:
        mem.conn.execute(
            "INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
            " VALUES (?, ?, ?, 't', 't')",
            [code, 날, 값],
        )


class Test벤치마크_이름:
    """읽을 이름이 틀리면 지수가 있어도 한 줄도 못 읽는다."""

    def test_실제로_저장되는_코드와_같다(self) -> None:
        # 2026-09-21 까지 "S&P500" 이라고 적혀 있었다. 저장되는 코드는 SP500 이다
        assert set(job.BENCHMARK.values()) <= set(trend.INDEX_SYMBOLS)

    def test_나라마다_다르다(self) -> None:
        # 한국 종목의 베타를 S&P 500 으로 재면 뜻이 달라진다 (docs/metrics.md 6절)
        assert job.BENCHMARK["KR"] != job.BENCHMARK["US"]


class Test베타를_실제로_낸다:
    def test_지수가_있으면_채워진다(self, client: MemClient) -> None:
        시세넣기(client, 1, 계열(1000, 수익률(400)))
        지수넣기(client, "KOSPI", 계열(2500, [r * 0.5 for r in 수익률(400)]))

        총, _, _ = job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]

        행 = client.conn.execute("SELECT beta, benchmark FROM performance_metrics").fetchone()
        assert 총 == 1
        assert 행[0] is not None, "지수를 넣어 두었는데도 베타가 비었다"
        assert 행[1] == "KOSPI"

    def test_지수가_없으면_비우고_알린다(self, client: MemClient) -> None:
        시세넣기(client, 1, 계열(1000, 수익률(400)))

        _, _, warnings = job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]

        행 = client.conn.execute("SELECT beta FROM performance_metrics").fetchone()
        assert 행[0] is None
        assert any("베타를 계산하지 않았습니다" in w for w in warnings), (
            "조용히 비웠다 — 이것이 몇 주 동안 아무도 모른 이유다"
        )

    def test_지수가_짧으면_내지_않는다(self, client: MemClient) -> None:
        """몇 점으로 낸 베타는 베타가 아니다."""
        시세넣기(client, 1, 계열(1000, 수익률(400)))
        지수넣기(client, "KOSPI", 계열(2500, 수익률(job.MIN_BENCHMARK_POINTS - 1)))

        _, _, warnings = job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]

        assert client.conn.execute("SELECT beta FROM performance_metrics").fetchone()[0] is None
        assert any("베타를 계산하지 않았습니다" in w for w in warnings)

    def test_같은_날_다시_돌면_지수_이름도_채운다(self, client: MemClient) -> None:
        """지수 없이 한 번, 지수를 받은 뒤 한 번 — upsert 가 benchmark 를 덮지 않아 (베타 있음, 지수 NULL) 이었다 (25.684)."""
        시세넣기(client, 1, 계열(1000, 수익률(400)))
        job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]
        assert client.conn.execute("SELECT beta, benchmark FROM performance_metrics").fetchone() == (None, None)

        지수넣기(client, "KOSPI", 계열(2500, [r * 0.5 for r in 수익률(400)]))
        job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]
        행 = client.conn.execute("SELECT beta, benchmark FROM performance_metrics").fetchall()
        assert len(행) == 1 and 행[0][0] is not None and 행[0][1] == "KOSPI"

    def test_베타를_못_내면_지수_이름도_비운다(self, client: MemClient) -> None:
        # 지수는 읽을 만큼(60점 이상) 있지만 겹친 날이 1년 하한(200)에 못 미친다 — 베타 None 인데 "KOSPI" 가 적혔다
        시세넣기(client, 1, 계열(1000, 수익률(400)))
        지수넣기(client, "KOSPI", 계열(2500, 수익률(100)))
        job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]
        assert client.conn.execute("SELECT beta, benchmark FROM performance_metrics").fetchone() == (None, None)

    def test_지수의_두_배로_움직이면_베타가_2다(self, client: MemClient) -> None:
        """손으로 확인할 수 있는 값으로 잰다 (CLAUDE.md 계산 규칙).

        종목 수익률이 정확히 지수의 2배면 Cov/Var = 2 다.
        """
        지수수익 = [r * 0.5 for r in 수익률(400)]
        시세넣기(client, 1, 계열(1000, [r * 2 for r in 지수수익]))
        지수넣기(client, "KOSPI", 계열(2500, 지수수익))

        job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]

        베타 = client.conn.execute("SELECT beta FROM performance_metrics").fetchone()[0]
        assert 베타 == pytest.approx(2.0, rel=1e-9)

    def test_지수와_같이_움직이면_베타가_1이다(self, client: MemClient) -> None:
        지수수익 = [r * 0.5 for r in 수익률(400)]
        시세넣기(client, 1, 계열(1000, 지수수익))
        지수넣기(client, "KOSPI", 계열(2500, 지수수익))

        job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]

        베타 = client.conn.execute("SELECT beta FROM performance_metrics").fetchone()[0]
        assert 베타 == pytest.approx(1.0, rel=1e-9)


class Test읽는_횟수:
    def test_기간마다_다시_읽지_않는다(self, client: MemClient) -> None:
        시세넣기(client, 1, 계열(1000, 수익률(400)))
        지수넣기(client, "KOSPI", 계열(2500, [r * 0.5 for r in 수익률(400)]))

        job.compute_all(client, [(1, "005930", "KR")], ["1Y", "3Y", "5Y"], 기준일)  # type: ignore[arg-type]

        # 종목 시세 1번 + 나라 시세 끝날 1번(대부분이 들어온 날). 40일 밖 시장을 찾는 질의는 **빠진 시장이 있을 때만** (25.728)
        assert client.시세질의수() == 2, (
            f"종목 하나·기간 셋에 시세를 {client.시세질의수()}번 읽었다."
            " 880종목이면 그만큼 곱해진다 — D1 은 왕복마다 HTTP 다"
        )

    def test_벤치마크도_한_번만_읽는다(self, client: MemClient) -> None:
        client.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (2, '000660', 'KOSPI', 'KR', '회사2', 'KRW', 'active', 't', 't')"
        )
        for sid in (1, 2):
            시세넣기(client, sid, 계열(1000, 수익률(400)))
        지수넣기(client, "KOSPI", 계열(2500, [r * 0.5 for r in 수익률(400)]))

        job.compute_all(client, [(1, "a", "KR"), (2, "b", "KR")], ["1Y", "3Y"], 기준일)  # type: ignore[arg-type]

        지수질의 = sum(1 for q in client.질의 if "FROM index_prices" in q)
        assert 지수질의 == 1, f"나라 하나인데 지수를 {지수질의}번 읽었다"


class Test나눠_쓴_창이_따로_읽은_것과_같다:
    """가장 긴 창을 한 번 읽어 나누는 것이 **결과를 바꾸면 안 된다.**"""

    def test_1년_창의_표본_수가_따로_읽은_것과_같다(self, client: MemClient) -> None:
        시세넣기(client, 1, 계열(1000, 수익률(900)))
        as_of = date.fromisoformat(기준일)
        따로 = len(
            job.load_prices(
                client,  # type: ignore[arg-type]
                1,
                (as_of - timedelta(days=job.WINDOW_DAYS["1Y"])).isoformat(),
            )
        )

        job.compute_all(client, [(1, "a", "KR")], ["1Y", "5Y"], 기준일)  # type: ignore[arg-type]

        나눠 = client.conn.execute(
            "SELECT data_points FROM performance_metrics WHERE \"window\" = '1Y'"
        ).fetchone()[0]
        assert 나눠 == 따로


def test_나라마다_직전_거래일로_저장한다(client: MemClient) -> None:
    """UTC 날짜로 저장해 같은 실행의 점수(기준 직전 거래일)가 방금 낸 지표를 못 봤다 (docs/infra.md 25.558, 감사 재현)."""
    시세넣기(client, 1, 계열(1000, 수익률(400)))
    job.compute_all(client, [(1, "005930", "KR")], ["1Y"], "2026-09-29", {"KR": "2026-09-28"})  # type: ignore[arg-type]
    assert client.conn.execute("SELECT as_of_date FROM performance_metrics").fetchone()[0] == "2026-09-28"
    import inspect

    원본 = inspect.getsource(job.run)
    assert "cal.default_as_of(c)" in 원본 and "datetime.now(UTC)" not in 원본


def test_기준일_뒤_시세는_넣지_않고_이른_나라_창도_자르지_않는다(client: MemClient) -> None:
    """저장 날짜가 직전 거래일이 되며 그 뒤 시세가 섞일 수 있었고, 늦은 기준일로 읽기 시작을 잡아 이른 나라 창이
    잘렸다 (docs/infra.md 25.561, 교차검증)."""
    시세넣기(client, 1, 계열(1000, 수익률(400)))
    client.conn.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
        " VALUES (1, '2026-09-19', 1, 'KRW', 'krx_openapi', 't')"  # 기준일 뒤 폭락
    )
    # 이 종목(KR)의 기준일은 09-18, 다른 나라는 09-25 — 읽기 시작은 이른 쪽에서 잰다
    job.compute_all(client, [(1, "005930", "KR")], ["1Y"], "2026-09-25", {"KR": 기준일, "US": "2026-09-25"})  # type: ignore[arg-type]
    mdd, 점수 = client.conn.execute("SELECT mdd, data_points FROM performance_metrics").fetchone()
    assert mdd > -0.9, "기준일 뒤 폭락이 섞였다"
    client.conn.execute("DELETE FROM performance_metrics")
    job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일, {"KR": 기준일})  # type: ignore[arg-type]
    assert client.conn.execute("SELECT data_points FROM performance_metrics").fetchone()[0] == 점수


def test_수정주가_사이의_빈_행은_원_종가로_메우지_않는다(client: MemClient) -> None:
    """1:10 분할 전 구간 한가운데 원 종가 한 행이 끼어 MDD −90%·변동성 738% 가 났다 (docs/infra.md 25.701, 감사 재현)."""
    for 날, 원, 수정 in [
        ("2026-09-14", 1000.0, 100.0), ("2026-09-15", 1010.0, None),
        ("2026-09-16", 1020.0, 102.0), ("2026-09-17", 105.0, None),
    ]:
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'KRW', 't', 't')",
            [날, 원, 수정],
        )
    구멍: list[int] = []
    점 = job.load_prices(client, 1, "2026-09-01", 구멍)  # type: ignore[arg-type]
    assert [(p.date.isoformat(), round(p.close, 6)) for p in 점] == [
        # 가운데 빈 행은 직전 계수(0.1)로 옮긴다 (25.709), 끝의 빈 행은 원 종가 그대로
        ("2026-09-14", 100.0), ("2026-09-15", 101.0), ("2026-09-16", 102.0), ("2026-09-17", 105.0),
    ]
    assert 구멍 == [1]


def test_계수_1_인_정상_빈_구간은_원_종가로_잇는다(client: MemClient) -> None:
    """adjust_kr 는 누적 계수 1 인 날을 NULL 로 둔다 — 25.701 은 그것까지 뺐다 (docs/infra.md 25.709, 교차검증)."""
    for 날, 원, 수정 in [
        ("2026-09-14", 1000.0, 500.0), ("2026-09-15", 505.0, None),  # 1:2 분할 뒤 계수가 1 로 돌아온 구간
        ("2026-09-16", 510.0, None), ("2026-09-17", 52.0, 520.0),
    ]:
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'KRW', 't', 't')",
            [날, 원, 수정],
        )
    구멍: list[int] = []
    점 = job.load_prices(client, 1, "2026-09-01", 구멍)  # type: ignore[arg-type]
    assert [p.close for p in 점] == [500.0, 505.0, 510.0, 520.0]
    assert 구멍 == []


def test_적재가_창의_시작을_채웠는지_본다(client: MemClient) -> None:
    """행은 200 이상이지만 1년 창 앞쪽이 비었으면 "1년" 값을 내지 않는다 (docs/infra.md 25.703)."""
    시세넣기(client, 1, 계열(1000, 수익률(250)))  # 250일치 — 1년(365일) 창의 앞 115일이 비었다
    job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]
    행 = client.conn.execute("SELECT data_points, cagr FROM performance_metrics").fetchone()
    assert 행[0] == 250 and 행[1] is None


def test_나라_수집이_멈춰도_창_끝을_그_나라_마지막_시세로_본다(client: MemClient) -> None:
    """나라 수집이 11일 넘게 멈추면 모든 창이 None 이 되어 리스크 축이 통째로 비었다 (docs/infra.md 25.710, 교차검증)."""
    끝 = date.fromisoformat(기준일) - timedelta(days=20)  # 20일 전에 수집이 멈췄다
    for 날, 값 in 계열(1000, 수익률(400)):
        if date.fromisoformat(날) <= 끝:
            시세넣기(client, 1, [(날, 값)])
    job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]
    assert client.conn.execute("SELECT cagr FROM performance_metrics").fetchone()[0] is not None


def test_계수가_1_에_가까운_구간의_구멍도_옮긴다(client: MemClient) -> None:
    """무상증자 계수 0.8 구간의 빈 행이 ±30% 문턱에 안 걸려 +26%·−19% 로 튀었다 (docs/infra.md 25.715, 교차검증 재현)."""
    for 날, 원, 수정 in [
        ("2026-09-14", 1000.0, 800.0), ("2026-09-15", 1010.0, None),
        ("2026-09-16", 1020.0, 816.0), ("2026-09-17", 1030.0, None),
    ]:
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'KRW', 't', 't')",
            [날, 원, 수정],
        )
    구멍: list[int] = []
    점 = job.load_prices(client, 1, "2026-09-01", 구멍)  # type: ignore[arg-type]
    assert [round(p.close, 6) for p in 점] == [800.0, 808.0, 816.0, 1030.0]
    assert 구멍 == [1]


def test_늦게_들어온_한_행이_끝날을_끌어올리지_않고_멈춤을_알린다(client: MemClient) -> None:
    """한 종목의 늦은 행이 나라 끝날을 올려 나머지가 다시 비었다 (25.715, 교차검증 — 25.702 와 같은 문제)."""
    for sid in (2, 3):
        client.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'KOSPI', 'KR', '회사', 'KRW', 'active', 't', 't')",
            [sid, f"00066{sid}"],
        )
    끝 = date.fromisoformat(기준일) - timedelta(days=20)
    for 날, 값 in 계열(1000, 수익률(400)):
        if date.fromisoformat(날) <= 끝:
            for sid in (1, 2, 3):
                시세넣기(client, sid, [(날, 값)])
    시세넣기(client, 2, [(기준일, 1000.0)])  # 종목 2 만 기준일 행이 늦게 들어왔다
    _, _, 경고 = job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]
    assert client.conn.execute("SELECT cagr FROM performance_metrics WHERE stock_id = 1").fetchone()[0] is not None
    assert any("KOSPI 시세가" in w and "뒤로 없습니다" in w for w in 경고)


def test_시장이_40일_넘게_멈춰도_값을_내고_알린다(client: MemClient) -> None:
    """40일 창 밖으로 멈춘 시장은 끝날에서 빠져 모든 값이 None, 경고도 없었다 (docs/infra.md 25.719, 교차검증 재현)."""
    끝 = date.fromisoformat(기준일) - timedelta(days=60)
    for 날, 값 in 계열(1000, 수익률(500)):
        if date.fromisoformat(날) <= 끝:
            시세넣기(client, 1, [(날, 값)])
    _, _, 경고 = job.compute_all(client, [(1, "005930", "KR")], ["1Y"], 기준일)  # type: ignore[arg-type]
    assert client.conn.execute("SELECT cagr FROM performance_metrics").fetchone()[0] is not None
    assert any("KOSPI 시세가" in w for w in 경고)


def test_앞뒤_계수가_같으면_하락일_구멍도_옮긴다(client: MemClient) -> None:
    """로그 거리 규칙은 계수 0.8 구간의 −15% 날 구멍을 원 종가로 이었다 (25.719, 교차검증 재현)."""
    for 날, 원, 수정 in [
        ("2026-09-14", 1000.0, 800.0), ("2026-09-15", 850.0, None), ("2026-09-16", 850.0, 680.0),
    ]:
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'KRW', 't', 't')",
            [날, 원, 수정],
        )
    점 = job.load_prices(client, 1, "2026-09-01")  # type: ignore[arg-type]
    assert [round(p.close, 6) for p in 점] == [800.0, 680.0, 680.0]


def test_미국_작은_시장의_끊긴_종목은_나라_끝날로_본다(client: MemClient) -> None:
    """IEX 유일 종목이 60일 전에 멈추면 제 시장 MAX 가 끝날이 돼 값을 받고 거짓 멈춤 경고가 났다 (docs/infra.md 25.728, 교차검증 재현)."""
    for sid, 시장 in [(11, "NASDAQ"), (12, "NASDAQ"), (13, "IEX")]:
        client.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (?, ?, ?, 'US', 'x', 'USD', 'active', 't', 't')",
            [sid, f"T{sid}", 시장],
        )
    끝 = date.fromisoformat(기준일) - timedelta(days=60)
    for 날, 값 in 계열(100, 수익률(500)):
        for sid in (11, 12):
            시세넣기(client, sid, [(날, 값)])
        if date.fromisoformat(날) <= 끝:
            시세넣기(client, 13, [(날, 값)])
    _, _, 경고 = job.compute_all(client, [(13, "T13", "US")], ["1Y"], 기준일)  # type: ignore[arg-type]
    assert client.conn.execute("SELECT cagr FROM performance_metrics WHERE stock_id = 13").fetchone()[0] is None
    assert not any("시세가" in w and "뒤로 없습니다" in w for w in 경고)
