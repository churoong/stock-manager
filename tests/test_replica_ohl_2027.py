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
    # 진도 문이 막으면 예전 열 그대로
    monkeypatch.setattr("sys.argv", ["x", "sync", "--path", str(path)])
    assert mod.main() == 0 and 물음 == [1]
    assert not rep.has_ohl(sqlite3.connect(str(path)))
    # 진도 문이 열면 새 열로
    monkeypatch.setattr(mod, "_gate_allows_build", lambda r: 문(r, True))
    assert mod.main() == 0 and 물음 == [1, 1]
    assert rep.has_ohl(sqlite3.connect(str(path)))
