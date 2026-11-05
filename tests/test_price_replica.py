"""시세 사본 (docs/infra.md 25.888).

사본이 틀리면 지표·밴드·백테스트가 **틀린 시세로** 계산한다 — 비용을 아끼려다 추천을 틀리게 하면 안 된다.
그래서 "바뀐 것을 모두 받는가" 와 "모르면 Turso 로 가는가" 를 묶는다.
"""

from __future__ import annotations

import json
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
    # 25.2150 전 코드의 실행 — 쓰기 앞 기록 표지가 없다
    원격.execute(
        "UPDATE batch_runs SET status = 'skipped', error_text = ?, finished_at = ?, step_log = NULL WHERE id = ?",
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


class Test_with_문:
    """25.2130 — 사본 클라이언트도 `with TursoClient() as c:` 를 받는다. 특수 메서드는 `__getattr__` 로 넘어가지 않아
    11-03 미국 배치(사본이 10-25 뒤 처음 쓰인 날)에서 리포트 경고 둘이 TypeError 로 빠지고 배치가 partial 로 끝났다."""

    def test_with_로_열고_닫으면_사본과_원격을_닫는다(self, 사본) -> None:
        원격 = _원격()
        rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
        닫힘: list[str] = []
        원격.close = lambda: 닫힘.append("원격")  # type: ignore[method-assign]
        with rep.ReplicaClient(원격, 사본) as c:
            rs = c.execute("SELECT close FROM prices WHERE stock_id = 1 LIMIT 1")
            assert rs.rows and c.local_queries == 1
        assert 닫힘 == ["원격"]
        with pytest.raises(Exception, match="closed"):
            사본.execute("SELECT 1")

    def test_모든_클라이언트가_with_를_받는다(self) -> None:
        """DB 클라이언트 셋(Turso·D1·사본) 모두 — 부르는 쪽은 어느 것이 올지 모른다(`core/client.TursoClient`)"""
        from batch.core.d1 import D1Client
        from batch.core.turso import TursoClient as Turso

        for 형 in (Turso, D1Client, rep.ReplicaClient):
            assert hasattr(형, "__enter__") and hasattr(형, "__exit__"), 형


def test_점수_신호_창_안의_with_가_사본에서도_돈다(사본, monkeypatch: pytest.MonkeyPatch) -> None:
    """리포트 경고 함수(`daily._건너뛴_까닭`·`_계산_저하`)가 사본 창 안에서 `with TursoClient()` 로 읽는다 — 사본 클라이언트가
    와도 읽기 실패 글 대신 제대로 된 답(None·빈 목록)을 낸다(25.2130)"""
    from batch.jobs import daily

    원격 = _원격()
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    monkeypatch.setattr(daily, "TursoClient", lambda: rep.ReplicaClient(원격, 사본))
    assert daily._건너뛴_까닭("signals", "2026-01-01") is None
    assert daily._계산_저하("scores", "signals", "2026-01-01") == []


def test_팩토리_길로_사본이_와도_리포트_경고가_실제_글을_낸다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """운영 길 그대로 — `PRICE_REPLICA_PATH` 를 두면 `core/client.TursoClient()` 가 `usable_replica` 를 거쳐 사본 클라이언트를
    돌려준다. 그 상태에서 신호 건너뜀·계산 저하 기록을 리포트 경고 글로 낸다(25.2130 교차검증 [참고] — 앞 시험은
    `daily.TursoClient` 를 갈아 끼워 팩토리 길을 지나지 않고, 빈 기록만 봤다)"""
    import json

    from batch.core import client as cl
    from batch.core import turso
    from batch.jobs import daily

    경로 = tmp_path / "prices.db"
    원격 = _원격()
    rep.sync(원격, rep.open_replica(경로), backend="turso", allow_build=True, now=datetime.now(UTC))
    for job, 상태, 기록 in (("signals", "skipped", {"reason": "기준일 종가 없는 종목이 많음"}),
                          ("scores", "success", {"sentiment_read_error": "x"})):  # fmt: skip
        원격.execute("INSERT INTO batch_runs (job_name, started_at, status, step_log) VALUES (?, '2026-11-03T13:50', ?, ?)",
                     [job, 상태, json.dumps(기록, ensure_ascii=False)])  # fmt: skip
    원격.close = lambda: None  # type: ignore[method-assign]  # with 가 닫아도 다음 호출이 같은 가짜 원격을 쓴다
    monkeypatch.setattr(cl, "resolved_backend", lambda: cl.TURSO)
    monkeypatch.setattr(turso, "TursoClient", lambda *a, **k: 원격)
    monkeypatch.setenv(rep.REPLICA_ENV, str(경로))
    with cl.TursoClient() as c:
        assert isinstance(c, rep.ReplicaClient)
    assert daily._건너뛴_까닭("signals", "2026-11-03") == (
        "신호를 계산하지 않았습니다 — 기준일 종가 없는 종목이 많음. 앞서 계산된 신호를 그대로 씁니다(기준일은 추천 머리에)"
    )
    assert any("감성 점수를 읽지 못해" in w for w in daily._계산_저하("scores", "signals", "2026-11-03"))


# ── 25.2150 쓰기 앞 기록·처음 만든 시각 — 정리된 기록 하나로 사본을 통째로 비우지 않는다 ──────────────────────────────


def _정리(원격, 번호: int, 언제: datetime) -> None:  # noqa: ANN001
    원격.execute("UPDATE batch_runs SET status = 'skipped', error_text = ?, finished_at = ? WHERE id = ?",
                 [f"{rep.REAPED_PREFIX}. 6시간 뒤 정리했다", 언제.isoformat(), 번호])  # fmt: skip


def test_시세_작업은_표지를_달고_열고_쓰기_전에_범위를_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_열린_실행", [])
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "_마지막_적은_손댐", [None])
    원격 = _원격()
    다른 = db.start_batch_run(원격, job_name="scores", market="KR", trade_date=None)  # type: ignore[arg-type]
    assert 원격.execute("SELECT step_log FROM batch_runs WHERE id = ?", [다른]).rows[0][0] is None
    번호 = db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    assert json.loads(원격.execute("SELECT step_log FROM batch_runs WHERE id = ?", [번호]).rows[0][0]) == {
        db.PRICES_TOUCHED_WAL_KEY: 1}
    쓴것: list[str | None] = []
    원래execute, 원래batch = 원격.execute, 원격.batch

    def 범위():  # noqa: ANN202 — 시세를 쓰는 순간 실행 기록에 범위가 이미 있어야 한다
        글 = 원래execute("SELECT step_log FROM batch_runs WHERE id = ?", [번호]).rows[0][0]
        return json.loads(글).get(db.PRICES_TOUCHED_KEY, {}).get("min_date")

    def execute(sql, args=None):  # noqa: ANN001, ANN202
        if "INSERT INTO prices" in sql:
            쓴것.append(범위())
        return 원래execute(sql, args) if args is not None else 원래execute(sql)

    def batch(statements):  # noqa: ANN001, ANN202
        if any("INSERT INTO prices" in st[0] for st in statements):
            쓴것.append(범위())
        return 원래batch(statements)

    monkeypatch.setattr(원격, "execute", execute)
    monkeypatch.setattr(원격, "batch", batch)
    db.bulk_upsert_prices(원격, [(3, "2026-09-01", 9.0, 9.0, 9.0, 9.0, 9.0, 1, 1, "KRW", "t", "t", 0.0)])  # type: ignore[arg-type]
    assert 쓴것 and all(d == "2026-09-01" for d in 쓴것)


def test_표지가_있는_정리된_기록은_적힌_범위만_다시_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_열린_실행", [])
    원격 = _원격()
    번호 = db.start_batch_run(원격, job_name="daily_us", market="US", trade_date=None)  # type: ignore[arg-type]
    _정리(원격, 번호, 지금 + timedelta(minutes=5))
    assert rep.touched_ranges(원격, 지금.isoformat()) == (None, {})  # 쓰기 전에 죽음 — 쓴 것 없음
    원격.execute("UPDATE batch_runs SET step_log = json_set(step_log, '$.prices_touched', json(?)) WHERE id = ?",
                 [json.dumps({"all": False, "min_date": "2026-09-01", "stocks": {"3": "2026-09-01"}}), 번호])  # fmt: skip
    assert rep.touched_ranges(원격, 지금.isoformat()) == (None, {3: "2026-09-01"})


@pytest.mark.parametrize(("job", "trigger", "시작_분전", "모름"), [
    ("daily_kr", "schedule", 60, False),   # 시작 60분 뒤 처음부터 만듦 — 상한 20 + 여유 30 = 50분 안에 죽었다
    ("daily_kr", "schedule", 40, True),    # 상한 + 여유 안 — 만든 뒤에도 썼을 수 있다
    ("daily_us", "dispatch", 60, True),    # 미국 상한 55 + 30
    ("daily_us", "dispatch", 90, False),
    ("adjust_kr", "schedule", 300, True),  # 나머지는 Actions 최대 360분
    ("daily_kr", "manual", 600, True),     # 로컬 손 실행은 상한이 없다
])  # fmt: skip
def test_예전_기록은_처음부터_만든_뒤에_죽을_수_없었으면_비우지_않는다(job: str, trigger: str, 시작_분전: int, 모름: bool) -> None:
    만든때 = 지금.isoformat()
    줄 = (job, (지금 - timedelta(minutes=시작_분전)).isoformat(), trigger, None)
    assert rep._범위_모름(줄, 만든때) is 모름
    assert rep._범위_모름(줄, None) is True  # 처음부터 만든 시각을 모르면 예전처럼
    assert rep._범위_모름((job, 줄[1], trigger, 1), None) is False  # 표지가 있으면 적힌 범위를 믿는다


def test_처음부터_만들면_만든_시각을_적고_정리된_예전_기록에_다시_비우지_않는다(사본, monkeypatch: pytest.MonkeyPatch) -> None:
    """11-05 재현 — 11-04 미국 실행이 죽어 정리되자 사본이 비워지고, 다시 만든 뒤에도 그 전에 죽은 국내 실행 둘이 다음 날
    정리되며 또 비울 뻔했다"""
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)
    monkeypatch.setattr(db, "_열린_실행", [])
    원격 = _원격()
    번호 = db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    원격.execute("UPDATE batch_runs SET step_log = NULL, trigger_source = 'schedule', started_at = ? WHERE id = ?",
                 [(지금 - timedelta(hours=3)).isoformat(), 번호])  # fmt: skip
    r = rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    assert r["usable"] and rep._meta(사본)["built_at"] == 지금.isoformat()
    _정리(원격, 번호, 지금 + timedelta(hours=10))  # 다음 날 다음 실행이 정리
    r = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(hours=11))
    assert r["usable"] and not r.get("cleared")


def test_수정주가_고침도_쓰기_전에_범위를_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2152 (25.2150 교차검증 [권고]) — `adjust_kr` 의 쓰기 앞 기록 줄을 지워도 모든 시험이 통과했다. 옛 행(id 그대로)의
    수정종가를 고치는 길이라 이어 맞추기가 결코 따라잡지 못한다"""
    from batch.jobs import adjust_kr
    from tests.test_zero_close_split_1251 import _db

    monkeypatch.setattr(db, "_열린_실행", [])
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "_마지막_적은_손댐", [None])
    c = _db([("2021-05-03", 50000, 0.0), ("2021-05-04", 0, 0.0), ("2021-05-06", 5100, 2.0), ("2021-05-07", 5100, 0.0)])
    번호 = db.start_batch_run(c, job_name="adjust_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    본: list[object] = []
    원래 = c.batch

    def batch(statements):  # noqa: ANN001, ANN202
        if any("UPDATE prices" in st[0] for st in statements):
            글 = c.conn.execute("SELECT step_log FROM batch_runs WHERE id = ?", [번호]).fetchone()[0]
            본.append(json.loads(글).get(db.PRICES_TOUCHED_KEY))
        return 원래(statements)

    monkeypatch.setattr(c, "batch", batch)
    adjust_kr.adjust_stocks(c, [1])  # type: ignore[arg-type]
    assert 본 and all(x and x["min_date"] <= "2021-05-03" for x in 본)


def test_정리하는_쪽은_죽은_실행이_적은_범위를_덮지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2152 (25.2150 교차검증 [권고], 재현) — 시세를 쓴 프로세스가 시세 작업 실행을 열며 죽은 기록을 정리하면 제 범위로
    덮어 죽은 실행의 종목이 사라졌다"""
    monkeypatch.setattr(db, "_열린_실행", [])
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "_마지막_적은_손댐", [None])
    원격 = _원격()
    죽음 = db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.note_prices_touched([(1, "2026-08-02")])
    db.persist_prices_touched(원격)  # type: ignore[arg-type]
    원격.execute("UPDATE batch_runs SET started_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", [죽음])  # 오래전에 죽음
    monkeypatch.setattr(db, "_열린_실행", [])  # 죽은 프로세스 — 닫지 않았다
    monkeypatch.setattr(db, "_읽기_시작", {})
    monkeypatch.setattr(db, "_가격_손댐", {3: "2026-09-01"})  # 새 프로세스가 이미 쓴 것
    db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]  # 정리
    글 = json.loads(원격.execute("SELECT step_log FROM batch_runs WHERE id = ?", [죽음]).rows[0][0])
    assert 글[db.PRICES_TOUCHED_KEY]["stocks"] == {"1": "2026-08-02"}


def test_작업_상한은_워크플로_상한과_같다() -> None:
    """25.2152 (25.2150 교차검증 [권고]) — `JOB_MAX_MINUTES` 는 yml 의 작업 `timeout-minutes` 를 옮겨 적은 값이다. yml 을
    올리고 이 값을 두면 만든 뒤에도 썼을 수 있는 예전 기록을 '모르는 것 없음' 으로 본다"""
    import re
    from pathlib import Path

    뿌리 = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    for job, 파일 in (("daily_kr", "daily-kr.yml"), ("daily_us", "daily-us.yml")):
        상한 = max(int(m) for m in re.findall(r"timeout-minutes:\s*(\d+)", (뿌리 / 파일).read_text(encoding="utf-8")))
        assert rep.JOB_MAX_MINUTES[job] >= 상한, (job, 상한)
    for f in 뿌리.glob("*.yml"):
        for m in re.findall(r"timeout-minutes:\s*(\d+)", f.read_text(encoding="utf-8")):
            assert int(m) <= rep.ACTIONS_MAX_MINUTES, f.name


def test_실패로_닫아도_내_실행에는_범위를_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2153 (25.2152 교차검증 [권고], 재현) — 실패로 닫는 길은 `_열린_실행` 에서 먼저 빼므로 "내 실행" 판정에 걸려
    note 뒤 `fail_open_runs` 면 표지만 남았다(25.888 의 '실패로 닫아도 적는다' 가 빠짐)"""
    monkeypatch.setattr(db, "_열린_실행", [])
    monkeypatch.setattr(db, "_읽기_시작", {})
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "_마지막_적은_손댐", [None])
    원격 = _원격()
    번호 = db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.note_prices_touched([(5, "2026-08-03")])  # 쓰기 앞 기록 없이 — 앞으로 생길 셋째 쓰기 길
    db.fail_open_runs("boom")
    글 = json.loads(원격.execute("SELECT step_log FROM batch_runs WHERE id = ?", [번호]).rows[0][0])
    assert 글[db.PRICES_TOUCHED_KEY]["stocks"] == {"5": "2026-08-03"}


def test_새로_연_시세_작업_실행도_같은_범위를_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2153 (25.2152 ④ 시험, 교차검증 [권고]) — 같은 요약 거르기를 풀지 않으면 뒤에 연 실행 B 는 표지만 쥔다"""
    monkeypatch.setattr(db, "_열린_실행", [])
    monkeypatch.setattr(db, "_읽기_시작", {})
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "_마지막_적은_손댐", [None])
    원격 = _원격()
    db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.note_prices_touched([(3, "2026-09-01")])
    db.persist_prices_touched(원격)  # type: ignore[arg-type]
    둘 = db.start_batch_run(원격, job_name="adjust_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.note_prices_touched([(3, "2026-09-01")])
    db.persist_prices_touched(원격)  # type: ignore[arg-type]
    글 = json.loads(원격.execute("SELECT step_log FROM batch_runs WHERE id = ?", [둘]).rows[0][0])
    assert db.PRICES_TOUCHED_KEY in 글
