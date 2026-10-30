"""시세 사본의 시가·고가·저가 열 (25.2027, 깔때기 ⑥ 첫 단 — docs/funnel.md 4장).

- 새 사본은 시가·고가·저가를 Turso 와 같은 값으로 든다 — 그림자 장부 `load_bars` 가 사본에서 읽는다
- 예전 사본(열 일곱)은 처음부터 만들 수 없는 실행(`--no-build` — 일일 배치·백테스트)에서 **지우지 않고** 예전 열만 맞춘다.
  시가를 찾는 질의는 열 없음으로 Turso 로 간다(예전과 같다). 만들어도 되는 실행에서 지우고 새로 만든다
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from batch.core import price_replica as rep
from batch.jobs import signal_outcomes
from tests.test_price_replica import _원격, 지금


def _시가(원격) -> None:
    원격.execute("UPDATE prices SET open = close - 1, high = close + 2, low = close - 3")


def _예전_사본(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE prices (id INTEGER, stock_id INTEGER NOT NULL, date TEXT NOT NULL, close REAL,"
        " adj_close REAL, volume INTEGER, value INTEGER, UNIQUE (stock_id, date));"
        "CREATE INDEX idx_prices_id ON prices (id);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
    )
    return conn


def test_새_사본은_시가_고가_저가를_들고_그림자_시세를_사본에서_읽는다(tmp_path: Path) -> None:
    원격 = _원격()
    _시가(원격)
    사본 = rep.open_replica(tmp_path / "p.db")
    assert rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)["usable"]
    q = "SELECT stock_id, date, open, high, low FROM prices ORDER BY stock_id, date"
    assert 원격.execute(q).rows == [tuple(r) for r in 사본.execute(q).fetchall()]
    클라 = rep.ReplicaClient(원격, 사본)
    ids = [1, 2, 3, 4, 5]
    got = signal_outcomes.load_bars(클라, ids, "2026-08-01")
    assert got == signal_outcomes.load_bars(원격, ids, "2026-08-01") and got
    assert 클라.local_queries == 1, "그림자 시세 읽기가 사본으로 가지 않았다"


def test_예전_사본은_만들지_말라는_실행에서_지우지_않고_예전_열만_맞춘다(tmp_path: Path) -> None:
    원격 = _원격()
    _시가(원격)
    path = tmp_path / "p.db"
    예전 = _예전_사본(path)
    예전.close()
    # 예전 코드로 만든 것처럼 — 예전 열로 채운다
    사본 = rep.open_replica(path)
    assert not rep.has_ohl(사본)
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)  # 비었으니 만든다 → 새 열
    assert rep.has_ohl(사본)

    # 이제 진짜 예전 사본(행 있음) — 만들지 말라는 실행
    path2 = tmp_path / "old.db"
    예전 = _예전_사본(path2)
    예전.executemany(
        rep._UPSERT_OLD,
        [tuple(r[:7]) for r in 원격.execute(rep._PULL_NEW, [0, 10**6]).rows],
    )
    예전.executemany("INSERT INTO meta VALUES (?, ?)", [("backend", "turso"), ("max_id", "0"),
                                                       ("synced_at", 지금.isoformat())])  # fmt: skip
    예전.commit()
    예전.close()
    사본 = rep.open_replica(path2)
    assert not rep.has_ohl(사본)
    결과 = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금)
    assert 결과["usable"] and 결과.get("ohl_pending") and not rep.has_ohl(사본)
    assert 사본.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == len(원격.execute("SELECT id FROM prices").rows)
    # 새 행도 예전 열로 받는다
    원격.execute("INSERT INTO prices (stock_id, date, close, adj_close, volume, value, open, high, low, currency, source,"
                 " fetched_at) VALUES (1, '2026-09-30', 500, 500, 1, 1, 499, 502, 497, 'KRW', 't', 't')")  # fmt: skip
    rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금)
    assert 사본.execute("SELECT close FROM prices WHERE stock_id = 1 AND date = '2026-09-30'").fetchone() == (500.0,)
    # 시가 질의는 사본에서 실패해 Turso 로 — 값은 같다
    클라 = rep.ReplicaClient(원격, 사본)
    assert signal_outcomes.load_bars(클라, [1, 2], "2026-08-01") == signal_outcomes.load_bars(원격, [1, 2], "2026-08-01")
    assert 클라.local_queries == 0

    # 만들어도 되는 실행 — 지우고 새 열로 다시 받는다
    결과 = rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    assert 결과["usable"] and 결과.get("ohl_rebuild") and rep.has_ohl(사본)
    q = "SELECT stock_id, date, close, adj_close, open, high, low FROM prices ORDER BY stock_id, date"
    assert 원격.execute(q).rows == [tuple(r) for r in 사본.execute(q).fetchall()]


def _스크립트():
    import importlib.util

    spec = importlib.util.spec_from_file_location("price_replica_script", Path(__file__).resolve().parent.parent
                                                  / "scripts" / "price_replica.py")  # fmt: skip
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_스크립트는_예전_사본을_진도_문을_거쳐서만_새로_만든다(tmp_path: Path, monkeypatch) -> None:
    원격 = _원격()
    _시가(원격)
    원격.reads = type("R", (), {"total": 0})()
    원격.close = lambda: None
    mod = _스크립트()
    monkeypatch.setattr(mod.backend, "resolved_backend", lambda: "turso")
    monkeypatch.setattr(mod.backend, "TursoClient", lambda use_replica=False: 원격)
    물음: list[int] = []

    def 문(_remote, 허락: bool = False):
        물음.append(1)
        return 허락, "시험"

    path = tmp_path / "old.db"
    예전 = _예전_사본(path)
    예전.executemany(rep._UPSERT_OLD, [tuple(r[:7]) for r in 원격.execute(rep._PULL_NEW, [0, 10**6]).rows])
    예전.executemany("INSERT INTO meta VALUES (?, ?)", [("backend", "turso"), ("max_id", "0"),
                                                       ("synced_at", 지금.isoformat())])  # fmt: skip
    예전.commit()
    예전.close()
    # --no-build 면 묻지도 않는다(일일 배치·백테스트)
    monkeypatch.setattr(mod, "_gate_allows_build", 문)
    monkeypatch.setattr("sys.argv", ["x", "sync", "--path", str(path), "--no-build"])
    assert mod.main() == 0 and 물음 == []
    # --ohl-rebuild 없는 작업(신호 성적표·밴드)은 묻지도 않는다 — 다시 만들기는 지표 작업 하나만(25.2032)
    monkeypatch.setattr("sys.argv", ["x", "sync", "--path", str(path)])
    assert mod.main() == 0 and 물음 == []
    assert not rep.has_ohl(sqlite3.connect(str(path)))
    # 진도 문이 막으면 예전 열 그대로
    monkeypatch.setattr("sys.argv", ["x", "sync", "--path", str(path), "--ohl-rebuild"])
    assert mod.main() == 0 and 물음 == [1]
    assert not rep.has_ohl(sqlite3.connect(str(path)))
    # 진도 문이 열면 새 열로
    monkeypatch.setattr(mod, "_gate_allows_build", lambda r: 문(r, True))
    assert mod.main() == 0 and 물음 == [1, 1]
    assert rep.has_ohl(sqlite3.connect(str(path)))


def test_백테스트_로더는_시가를_같은_수정_계열로_읽고_안_물으면_시가_열을_찾지_않는다(tmp_path: Path) -> None:
    """깔때기 ⑥ 둘째 단(25.2029) — `opens` 를 주면 원 시가 × 수정종가 ÷ 원 종가. 안 주면 질의에 `open` 이 없어
    예전 사본(시가 열 없음)에서도 시세 읽기가 사본에 남는다(Turso 로 물러나지 않는다)."""
    from batch.jobs import backtest

    원격 = _원격()
    _시가(원격)
    원격.execute("UPDATE prices SET adj_close = close / 2 WHERE stock_id = 3")  # 수정 계수 0.5
    원격.execute("UPDATE prices SET open = NULL WHERE stock_id = 4 AND date = '2026-08-02'")
    ids = [3, 4]
    시가: dict = {}
    가격, _ = backtest.load_prices_and_turnover(원격, ids, "2026-08-01", opens=시가)
    원 = dict(((r[0], r[1]), (r[2], r[3])) for r in 원격.execute("SELECT stock_id, date, open, close FROM prices").rows)
    for sid in ids:
        for day, px in 가격[sid].items():
            o, cl = 원[(sid, day)]
            if o is None:
                assert day not in 시가[sid]
            else:
                assert abs(시가[sid][day] - o * px / cl) < 1e-9
    assert abs(시가[3]["2026-08-01"] - (100.0 + 3 - 1) * 0.5) < 1e-9 and "2026-08-02" not in 시가[4]
    # 시가 0(거래정지일 등) 은 넣지 않는다, 수정종가가 빈 날은 직전 계수로 메운 값(`adj.fill_adjusted`)으로 옮긴다
    # (25.2032, 25.2029 교차검증 [권고] — 원 adj_close 를 쓰면 25.1101 의 분할 전 구간 원값 섞임과 같은 꼴)
    원격2 = _원격()
    _시가(원격2)
    원격2.execute("UPDATE prices SET adj_close = close / 2 WHERE stock_id = 3")
    원격2.execute("UPDATE prices SET open = 0 WHERE stock_id = 4 AND date = '2026-08-03'")
    원격2.execute("UPDATE prices SET adj_close = NULL WHERE stock_id = 3 AND date = '2026-08-05'")
    시가3: dict = {}
    backtest.load_prices_and_turnover(원격2, ids, "2026-08-01", opens=시가3)
    assert "2026-08-03" not in 시가3[4] and "2026-08-03" in 시가3[3]
    원시가 = 원격2.execute("SELECT open FROM prices WHERE stock_id = 3 AND date = '2026-08-05'").rows[0][0]
    assert abs(시가3[3]["2026-08-05"] - 원시가 * 0.5) < 1e-9

    # 예전 사본 — 시가를 안 물으면 사본에서, 물으면 Turso 로(값은 같다)
    path = tmp_path / "old.db"
    예전 = _예전_사본(path)
    예전.executemany(rep._UPSERT_OLD, [tuple(r[:7]) for r in 원격.execute(rep._PULL_NEW, [0, 10**6]).rows])
    예전.commit()
    클라 = rep.ReplicaClient(원격, 예전)
    assert backtest.load_prices_and_turnover(클라, ids, "2026-08-01") == backtest.load_prices_and_turnover(
        원격, ids, "2026-08-01"
    )
    assert 클라.local_queries == 1
    시가2: dict = {}
    backtest.load_prices_and_turnover(클라, ids, "2026-08-01", opens=시가2)
    assert 시가2 == 시가 and 클라.local_queries == 1


def test_새_사본은_고친_시가도_범위_다시_받기로_따라간다(tmp_path: Path) -> None:
    """있던 행의 시가·고가·저가가 Turso 에서 바뀌면(다시 받기·수정 — 범위가 `prices_touched` 에 적힌다) 사본도 따라간다.
    표본 지문은 시가를 보지 않으므로 이 길이 유일한 맞춤이다(25.2032, 25.2027 교차검증 [권고])."""
    원격 = _원격()
    _시가(원격)
    사본 = rep.open_replica(tmp_path / "p.db")
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    원격.execute("UPDATE prices SET open = open + 7, high = high + 8, low = low - 9 WHERE stock_id = 3"
                 " AND date >= '2026-08-10'")  # fmt: skip
    원격.execute("INSERT INTO prices (stock_id, date, close, adj_close, volume, value, open, high, low, currency, source,"
                 " fetched_at) VALUES (4, '2026-09-30', 500, 500, 1, 1, 499, 502, 497, 'KRW', 't', 't')")  # fmt: skip
    결과 = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금, pending={"stocks": {"3": "2026-08-10"}})
    assert 결과["usable"] and 결과["repulled"] > 0
    q = "SELECT stock_id, date, close, adj_close, open, high, low FROM prices ORDER BY stock_id, date"
    assert 원격.execute(q).rows == [tuple(r) for r in 사본.execute(q).fetchall()]


def test_사본_질의의_열은_열_목록과_같다() -> None:
    """받는 질의·넣는 문장의 열이 `PRICE_COLS`(새)·`OLD_COLS`(예전)와 같은 순서 — 행 튜플을 그대로 넣는다(25.2032)."""
    import re as _re

    def 열(sql: str) -> tuple[str, ...]:
        m = _re.search(r"SELECT (.+?) FROM", sql) or _re.search(r"INSERT INTO prices \((.+?)\)", sql)
        assert m
        return tuple(x.strip() for x in m.group(1).split(","))

    for sql in (rep._PULL_NEW, rep._PULL_RANGE, rep._UPSERT):
        assert 열(sql) == rep.PRICE_COLS, sql
    for sql in (rep._PULL_NEW_OLD, rep._PULL_RANGE_OLD, rep._UPSERT_OLD):
        assert 열(sql) == rep.OLD_COLS, sql


def test_예전_사본을_다시_만들다_끊겨도_빈_표가_쓸_수_있는_사본으로_읽히지_않는다(tmp_path: Path, monkeypatch) -> None:
    """DROP 앞에 usable=0 을 적는다(25.2032, 25.2027 교차검증 [참고])."""
    import pytest as _pytest

    원격 = _원격()
    path = tmp_path / "old.db"
    예전 = _예전_사본(path)
    예전.executemany(rep._UPSERT_OLD, [tuple(r[:7]) for r in 원격.execute(rep._PULL_NEW, [0, 10**6]).rows])
    예전.executemany("INSERT INTO meta VALUES (?, ?)", [("backend", "turso"), ("max_id", "0"), ("usable", "1"),
                                                       ("synced_at", 지금.isoformat())])  # fmt: skip
    예전.commit()
    예전.close()
    사본 = rep.open_replica(path)

    def 끊김(*_a, **_k):
        raise RuntimeError("다시 받다 끊김")

    monkeypatch.setattr(rep, "_pull_new", 끊김)
    with _pytest.raises(RuntimeError):
        rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    사본.close()
    assert rep.usable_replica(path, now=지금) is None
