"""시세 사본 (docs/infra.md 25.888).

사본이 틀리면 지표·밴드·백테스트가 **틀린 시세로** 계산한다 — 비용을 아끼려다 추천을 틀리게 하면 안 된다.
그래서 "바뀐 것을 모두 받는가" 와 "모르면 Turso 로 가는가" 를 묶는다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from batch.core import db
from batch.core import price_replica as rep
from tests.test_scores_job import _sqlite_client

ROOT = Path(__file__).resolve().parent.parent
지금 = datetime(2026, 10, 6, 17, 0, tzinfo=UTC)


def _원격(종목수: int = 3, 일수: int = 30):
    c = _sqlite_client()
    for sid in range(3, 3 + 종목수):
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
            [sid, f"T{sid}"],
        )
    for sid in range(1, 3 + 종목수):
        for d in range(일수):
            day = (datetime(2026, 8, 1) + timedelta(days=d)).date().isoformat()
            c.execute(
                "INSERT INTO prices (stock_id, date, close, adj_close, volume, value, currency, source, fetched_at)"
                " VALUES (?, ?, ?, ?, 100, 1000, 'KRW', 't', 't')",
                [sid, day, 100.0 + d + sid, 100.0 + d + sid],
            )
    return c


def _같나(원격, conn) -> bool:
    q = "SELECT stock_id, date, close, adj_close, volume, value FROM prices ORDER BY stock_id, date"
    return 원격.execute(q).rows == [tuple(r) for r in conn.execute(q).fetchall()]


@pytest.fixture
def 사본(tmp_path: Path):
    return rep.open_replica(tmp_path / "prices.db")


def test_처음_만들면_원격과_같다(사본) -> None:
    원격 = _원격()
    r = rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    assert r["usable"] and r["built"] > 0 and r["mismatch"] == []
    assert _같나(원격, 사본)


def test_비었고_만들지_말라면_쓰지_않는다(사본) -> None:
    r = rep.sync(_원격(), 사본, backend="turso", allow_build=False, now=지금)
    assert not r["usable"]


def test_D1_이면_쓰지_않는다(사본) -> None:
    """D1 의 id 는 Turso 와 다르다 — 섞으면 새 행을 잘못 고른다."""
    r = rep.sync(_원격(), 사본, backend="d1", allow_build=True, now=지금)
    assert not r["usable"]


def test_새_행은_id_로_받는다(사본, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)  # 표본 검증이 대신 고치지 않게
    원격 = _원격()
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    db.bulk_upsert_prices(원격, [(3, "2026-09-15", None, None, None, 200.0, None, 10, 100, "KRW", "t", "t", None)])
    db._가격_손댐.clear()  # 실행 기록 없이 들어온 새 행 — id 만으로 받는지 본다
    r = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(hours=1))
    assert r["new"] == 1 and _같나(원격, 사본)


def test_있던_행을_고친_실행은_그_범위를_다시_받는다(사본, monkeypatch: pytest.MonkeyPatch) -> None:
    """**id 가 그대로인 수정**(다시 받기·수정주가)은 실행 기록의 `prices_touched` 로만 안다."""
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)  # 표본 검증이 대신 고치지 않게
    원격 = _원격()
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    번호 = db.start_batch_run(원격, job_name="adjust_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.bulk_upsert_prices(원격, [(4, "2026-08-03", None, None, None, 999.0, 50.0, 10, 100, "KRW", "t", "t", None)])
    원격.execute("UPDATE batch_runs SET finished_at = ? WHERE id = ?", [(지금 + timedelta(minutes=5)).isoformat(), 번호])
    db.finish_batch_run(원격, 번호, status="success", step_log={"ok": 1})  # type: ignore[arg-type]
    원격.execute("UPDATE batch_runs SET finished_at = ? WHERE id = ?", [(지금 + timedelta(minutes=5)).isoformat(), 번호])
    r = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(hours=1))
    assert r["repulled"] >= 1
    assert _같나(원격, 사본)


def test_전_종목_범위도_다시_받는다(사본, monkeypatch: pytest.MonkeyPatch) -> None:
    """일일 배치는 수천 종목을 써서 `all` 로 줄여 적는다."""
    monkeypatch.setattr(db, "PRICES_TOUCHED_MAX_STOCKS", 1)
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)
    원격 = _원격()
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    번호 = db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.bulk_upsert_prices(원격, [
        (1, "2026-08-20", None, None, None, 1.5, 1.5, 10, 100, "KRW", "t", "t", None),
        (5, "2026-08-21", None, None, None, 2.5, 2.5, 10, 100, "KRW", "t", "t", None),
    ])  # fmt: skip
    db.finish_batch_run(원격, 번호, status="success", step_log={"ok": 1})  # type: ignore[arg-type]
    원격.execute("UPDATE batch_runs SET finished_at = ? WHERE id = ?", [(지금 + timedelta(minutes=5)).isoformat(), 번호])
    rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(hours=1))
    assert _같나(원격, 사본)


def test_넓은_전_종목_범위는_비우고_진도_문에_넘긴다(사본, monkeypatch: pytest.MonkeyPatch) -> None:
    """백필 뒤 `all` 이 몇 년 전부터면 다시 받기가 새로 만들기만큼 비싸다 — 그 길만 진도 문을 안 거쳤다 (25.907, 감사)."""
    monkeypatch.setattr(db, "PRICES_TOUCHED_MAX_STOCKS", 1)
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)
    원격 = _원격()
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    번호 = db.start_batch_run(원격, job_name="backfill_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.bulk_upsert_prices(원격, [
        (1, "2026-08-02", None, None, None, 1.5, 1.5, 10, 100, "KRW", "t", "t", None),
        (5, "2026-08-21", None, None, None, 2.5, 2.5, 10, 100, "KRW", "t", "t", None),
    ])  # fmt: skip
    db.finish_batch_run(원격, 번호, status="success", step_log={"ok": 1})  # type: ignore[arg-type]
    원격.execute("UPDATE batch_runs SET finished_at = ? WHERE id = ?", [(지금 + timedelta(minutes=5)).isoformat(), 번호])
    r = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(days=70))
    assert r["usable"] is False and r["cleared"] is True
    assert 사본.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0  # 다음 주간 작업이 진도 문을 거쳐 새로 만든다


def test_끝맺지_못한_시세_작업은_범위를_모르므로_전부로_본다(사본, monkeypatch: pytest.MonkeyPatch) -> None:
    """강제 종료로 `prices_touched` 가 안 남은 실행 — 다음 실행이 닫으며 남긴 문구로 안다 (25.907, 감사)."""
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)
    monkeypatch.setattr(db, "_열린_실행", [])  # 죽은 프로세스를 흉내 낸다 — 닫지 않고 남긴 실행은 이 테스트 안에만
    원격 = _원격()
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    원격.execute("UPDATE prices SET adj_close = 1.0 WHERE stock_id = 1 AND date = '2026-08-25'")  # 기록 없이 고침
    번호 = db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    원격.execute(
        "UPDATE batch_runs SET status = 'skipped', error_text = ?, finished_at = ? WHERE id = ?",
        [f"{rep.REAPED_PREFIX}. 6시간 뒤 정리했다", (지금 + timedelta(minutes=5)).isoformat(), 번호],
    )
    전체, _ = rep.touched_ranges(원격, 지금.isoformat())
    assert 전체 == "0000-00-00"
    # 시세를 안 고치는 작업이 끝맺지 못한 것은 상관없다
    원격.execute("UPDATE batch_runs SET job_name = 'scores' WHERE id = ?", [번호])
    assert rep.touched_ranges(원격, 지금.isoformat())[0] is None

def test_기록_없이_고친_것은_표본_검증이_잡는다(사본, monkeypatch: pytest.MonkeyPatch) -> None:
    """기록이 빠지는 쓰기 길이 생기면(손으로 고친 SQL 등) 표본이 잡아 다시 받고, 많으면 사본을 쓰지 않는다."""
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 100)  # 표본이 전 종목을 덮게
    원격 = _원격()
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    원격.execute("UPDATE prices SET adj_close = adj_close * 2 WHERE stock_id = 3 AND date = '2026-08-05'")
    r = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(hours=1))
    assert r["mismatch"] == [3] and r["usable"]
    assert _같나(원격, 사본)

    원격.execute("UPDATE prices SET close = close + 1 WHERE date = '2026-08-06'")
    r = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(hours=2))
    assert len(r["mismatch"]) > rep.VERIFY_MAX_MISMATCH and not r["usable"]


def test_낡았거나_못_쓰는_사본은_열지_않는다(tmp_path: Path) -> None:
    path = tmp_path / "prices.db"
    conn = rep.open_replica(path)
    rep.sync(_원격(), conn, backend="turso", allow_build=True, now=지금)
    conn.close()
    assert rep.usable_replica(path, now=지금 + timedelta(hours=1)) is not None
    assert rep.usable_replica(path, now=지금 + timedelta(hours=rep.FRESH_HOURS + 1)) is None
    assert rep.usable_replica(tmp_path / "없음.db", now=지금) is None


class Test읽기_길:
    def _client(self, 사본):
        원격 = _원격()
        rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
        return 원격, rep.ReplicaClient(원격, 사본)

    def test_시세_종목만_읽는_SELECT_는_사본에서(self, 사본) -> None:
        원격, c = self._client(사본)
        사본.execute("UPDATE prices SET close = -1 WHERE stock_id = 1")  # 어디서 읽었는지 가르는 표식
        rs = c.execute("SELECT p.close FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE p.stock_id = 1 LIMIT 1")
        assert rs.rows == [(-1.0,)] and c.local_queries == 1

    def test_다른_표가_끼면_원격(self, 사본) -> None:
        원격, c = self._client(사본)
        사본.execute("UPDATE prices SET close = -1 WHERE stock_id = 1")
        rs = c.execute(
            "SELECT p.close FROM prices p JOIN universe_members u ON u.stock_id = p.stock_id WHERE p.stock_id = 1"
        )
        assert c.local_queries == 0 and all(r[0] != -1 for r in rs.rows)

    def test_점수_신호의_시세_질의는_사본에서_유니버스는_원격(self, 사본) -> None:
        """25.1033 — 시세 계열을 종목 번호(json_each)로 나눠 읽어 사본이 받는다. 등락률은 사본에 없어 원격."""
        import json

        from batch.jobs import scores as sj
        from batch.jobs import signals as sig

        원격, c = self._client(사본)
        ids = json.dumps([1])
        c.execute(sj.SERIES_PRICES_SQL, [ids, "2026-01-01", "2026-12-31"])
        c.execute(sig.RECENT_PRICES_FOR_SQL, [ids, "2026-12-31", "2026-12-31", 60])
        assert c.local_queries == 2
        c.execute(sj.MOVES_SQL, [ids, "2026-01-01", "2026-12-31"])  # change_pct 는 사본에 없다
        c.execute(sj.SERIES_IDS_SQL, ["KR", "KR", "2026-12-31"])  # universe_members 가 낀다
        assert c.local_queries == 2

    def test_사본에_없는_열이면_원격(self, 사본) -> None:
        원격, c = self._client(사본)
        rs = c.execute("SELECT open, source FROM prices WHERE stock_id = 1 LIMIT 1")
        assert c.local_queries == 0 and rs.columns == ["open", "source"]

    def test_시세를_쓰면_그때부터_원격(self, 사본) -> None:
        원격, c = self._client(사본)
        c.execute("UPDATE prices SET close = 5 WHERE stock_id = 1 AND date = '2026-08-01'")
        rs = c.execute("SELECT close FROM prices WHERE stock_id = 1 AND date = '2026-08-01'")
        assert c.dirty and rs.rows == [(5.0,)]

    def test_CTE_이름은_표가_아니다(self) -> None:
        assert rep.referenced_tables(
            "WITH RECURSIVE d(x) AS (SELECT MAX(p.date) FROM prices p UNION ALL SELECT x FROM d) SELECT x FROM d"
        ) == {"prices"}


def test_시세를_쓰는_길은_둘뿐이다() -> None:
    """**사본이 믿는 전제** — 시세를 고치는 길이 모두 `prices_touched` 를 남긴다. 새 길이 생기면 여기서 멈춘다."""
    허용 = {
        ("batch/core/db.py", "bulk_upsert_prices"),
        ("batch/jobs/adjust_kr.py", "UPDATE prices"),
    }
    찾음 = set()
    for path in [*sorted((ROOT / "batch").rglob("*.py")), *sorted((ROOT / "scripts").rglob("*.py"))]:
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if re.search(r"INSERT\s+(OR\s+\w+\s+)?INTO\s+prices\b", text):
            찾음.add((rel, "bulk_upsert_prices" if rel == "batch/core/db.py" else "INSERT INTO prices"))
        if re.search(r"UPDATE\s+prices\b", text):
            찾음.add((rel, "UPDATE prices"))
        if re.search(r"DELETE\s+FROM\s+prices\b", text):
            찾음.add((rel, "DELETE FROM prices"))
    # 사본 자신과 복구 스크립트는 운영 DB 가 아닌 로컬 사본·백업 파일을 다룬다 — 따로 본다
    찾음 -= {("batch/core/price_replica.py", "INSERT INTO prices"), ("batch/core/price_replica.py", "DELETE FROM prices")}
    assert 찾음 <= 허용 | {("scripts/restore_backup.py", "INSERT INTO prices")}, sorted(찾음 - 허용)


def test_과거_시세를_읽는_작업들이_사본에서_같은_값을_읽는다(사본) -> None:
    """사본으로 바꿔도 **결과가 같아야** 한다 — 지표·밴드·성과·스트레스·백테스트의 시세 읽기가 모두 사본으로 가는지도 본다."""
    from batch.jobs import backtest, metrics, signal_outcomes, stress, valuation_bands

    원격 = _원격()
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    ids = [1, 2, 3, 4, 5]
    읽기들 = {
        "metrics": lambda c: [metrics.load_prices(c, sid, "2026-08-01") for sid in ids],
        "valuation_bands": lambda c: valuation_bands.load_prices(c, ids, "2026-08-30"),
        "signal_outcomes": lambda c: signal_outcomes.load_closes(c, ids, "2026-08-01"),
        "stress": lambda c: stress.load_prices(c, ids, "2026-08-01"),
        "backtest": lambda c: backtest.load_prices(c, ids, "2026-08-01"),
    }
    for 이름, 읽기 in 읽기들.items():
        사본_클라 = rep.ReplicaClient(원격, 사본)
        assert 읽기(사본_클라) == 읽기(원격), 이름
        assert 사본_클라.local_queries > 0, f"{이름} 의 시세 읽기가 사본으로 가지 않았다"
