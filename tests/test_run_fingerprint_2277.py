"""실행 지문 — 그 실행이 어느 코드·어느 환경이었나 (docs/infra.md 25.2277, docs/roadmap.md 1장 #9).

소급해 만들 수 없는 기록이다. 성공·실패 모두 **이 프로세스가 연 실행에만**, 쓰기 수를 늘리지 않고 적는다.
"""

from __future__ import annotations

import json

import pytest

from batch.core import db
from tests.test_scores_job import _sqlite_client


def _로그(c, 번호: int):
    글 = c.execute("SELECT step_log FROM batch_runs WHERE id = ?", [번호]).rows[0][0]
    return None if 글 is None else json.loads(글)


@pytest.fixture(autouse=True)
def _지문_새로(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_지문", [None])


def test_지문은_커밋과_실행_번호와_파이썬과_의존성을_담는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("GITHUB_RUN_ID", "777")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    f = db.run_fingerprint()
    assert set(f) == {"sha", "run", "py", "deps"}
    assert f["sha"] == "0123456789ab" and f["run"] == "777.2"
    import platform

    assert f["py"] == platform.python_version()
    assert len(f["deps"]) == 10 and int(f["deps"], 16) >= 0
    # 한 번 내고 다시 쓴다 — 환경이 바뀌어도 이 프로세스의 지문은 그대로, 돌려준 것을 고쳐도 원본은 그대로
    monkeypatch.setenv("GITHUB_SHA", "ffff")
    f["sha"] = "고침"
    assert db.run_fingerprint()["sha"] == "0123456789ab"


def test_로컬_실행은_커밋과_실행_번호가_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    for 이름 in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        monkeypatch.delenv(이름, raising=False)
    f = db.run_fingerprint()
    assert f["sha"] is None and f["run"] is None and f["deps"]
    monkeypatch.setattr(db, "_지문", [None])
    monkeypatch.setenv("GITHUB_RUN_ID", "5")  # 재시도 번호가 없으면 1
    assert db.run_fingerprint()["run"] == "5.1"


def test_의존성_지문은_깔린_판이_바뀌면_달라진다(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import metadata

    원래 = db.run_fingerprint()["deps"]

    class _가짜:
        def __init__(self, 이름: str, 판: str) -> None:
            self.metadata, self.version = {"Name": 이름}, 판

    def 판들(판: str):
        return lambda: [_가짜("Pandas", "3.0.5"), _가짜("tzdata", 판)]

    monkeypatch.setattr(metadata, "distributions", 판들("2026.1"))
    monkeypatch.setattr(db, "_지문", [None])
    하나 = db.run_fingerprint()["deps"]
    monkeypatch.setattr(metadata, "distributions", 판들("2026.2"))
    monkeypatch.setattr(db, "_지문", [None])
    둘 = db.run_fingerprint()["deps"]
    assert len({원래, 하나, 둘}) == 3
    # 순서는 지문을 바꾸지 않는다
    monkeypatch.setattr(metadata, "distributions", lambda: [_가짜("tzdata", "2026.2"), _가짜("pandas", "3.0.5")])
    monkeypatch.setattr(db, "_지문", [None])
    assert db.run_fingerprint()["deps"] == 둘
    # 값은 "이름(소문자)==판" 을 **정렬해** 줄바꿈으로 이은 글의 sha1 앞 10자다 — 집합의 차례에 기대지 않는다
    import hashlib

    이름들 = ["zlib-x", "Alpha", "numpy", "requests", "Beta", "yfinance", "tzdata", "pandas"]
    monkeypatch.setattr(metadata, "distributions", lambda: [_가짜(n, "1.0") for n in 이름들] + [_가짜("Alpha", "1.0")])
    monkeypatch.setattr(db, "_지문", [None])
    글 = "\n".join(sorted(f"{n.lower()}==1.0" for n in 이름들))
    assert db.run_fingerprint()["deps"] == hashlib.sha1(글.encode()).hexdigest()[:10]


def test_성공으로_닫으면_step_log_에_같은_쓰기로_실린다() -> None:
    c = _sqlite_client()
    번호 = db.start_batch_run(c, job_name="paper", market="KR", trade_date="2026-11-24")  # type: ignore[arg-type]
    쓰기 = []
    진짜 = c.execute
    c.execute = lambda sql, params=None: (쓰기.append(sql), 진짜(sql, params))[1]  # type: ignore[method-assign]
    db.finish_batch_run(c, 번호, status="success", step_log={"orders": 3})  # type: ignore[arg-type]
    assert _로그(c, 번호)[db.RUN_FINGERPRINT_KEY] == db.run_fingerprint()
    assert len([s for s in 쓰기 if s.startswith("UPDATE batch_runs")]) == 1  # 쓰기가 늘지 않는다


def test_실패로_닫아도_남고_있던_진행_상태는_그대로다() -> None:
    c = _sqlite_client()
    빈 = db.start_batch_run(c, job_name="paper", market="KR", trade_date="2026-11-24")  # type: ignore[arg-type]
    진행 = db.start_batch_run(c, job_name="backtest", market="KR", trade_date=None)  # type: ignore[arg-type]
    db.note_progress(c, 진행, {"done": 3})  # type: ignore[arg-type]
    쓰기 = []
    진짜 = c.execute
    c.execute = lambda sql, params=None: (쓰기.append(sql), 진짜(sql, params))[1]  # type: ignore[method-assign]
    db.finish_batch_run(c, 빈, status="failed", error_text="죽음")  # type: ignore[arg-type]
    db.finish_batch_run(c, 진행, status="failed", error_text="죽음")  # type: ignore[arg-type]
    assert _로그(c, 빈) == {db.RUN_FINGERPRINT_KEY: db.run_fingerprint()}
    assert _로그(c, 진행) == {"progress": {"done": 3}, db.RUN_FINGERPRINT_KEY: db.run_fingerprint()}
    assert len([s for s in 쓰기 if s.startswith("UPDATE batch_runs")]) == 2  # 실행마다 한 번
    행 = c.execute("SELECT status, error_text, finished_at FROM batch_runs WHERE id = ?", [빈]).rows[0]
    assert 행[0] == "failed" and 행[1] == "죽음" and 행[2]


def test_JSON_이_아닌_옛_글은_건드리지_않는다() -> None:
    c = _sqlite_client()
    번호 = db.start_batch_run(c, job_name="x", market=None, trade_date=None)  # type: ignore[arg-type]
    c.execute("UPDATE batch_runs SET step_log = '글자 그대로' WHERE id = ?", [번호])
    db.finish_batch_run(c, 번호, status="failed")  # type: ignore[arg-type]
    assert c.execute("SELECT status, step_log FROM batch_runs WHERE id = ?", [번호]).rows[0] == ("failed", "글자 그대로")


def test_남의_실행을_정리할_때는_내_지문을_적지_않는다() -> None:
    """다른 프로세스가 열고 죽은 실행 — 정리하는 쪽의 코드·환경을 적으면 거짓이 된다"""
    c = _sqlite_client()
    c.execute(
        "INSERT INTO batch_runs (job_name, market, trigger_source, started_at, status, step_log)"
        " VALUES ('paper', 'KR', 'schedule', '2026-11-20T00:00:00+00:00', 'running', ?)",
        [json.dumps({"progress": 1})],
    )
    번호 = c.execute("SELECT MAX(id) FROM batch_runs").rows[0][0]
    db.finish_batch_run(c, 번호, status="failed", error_text="정리")  # type: ignore[arg-type]
    assert _로그(c, 번호) == {"progress": 1}
    db.finish_batch_run(c, 번호, status="failed", step_log={"a": 1})  # type: ignore[arg-type]
    assert _로그(c, 번호) == {"a": 1}


def test_닫는_문장의_인자_자리는_성공과_실패가_같다() -> None:
    """[끝난 때, 상태, step_log 칸, 오류 글, 번호] — 닫기를 흉내 내는 시험 클라이언트들이 이 자리를 읽는다"""
    본: list[tuple[str, list]] = []

    class _C:
        def execute(self, sql, params=None):
            본.append((sql, list(params or [])))
            return type("R", (), {"rows": [], "last_insert_rowid": 9})()

    c = _C()
    번호 = db.start_batch_run(c, job_name="x", market=None, trade_date=None)  # type: ignore[arg-type]
    db.finish_batch_run(c, 번호, status="failed", error_text="왜")  # type: ignore[arg-type]
    sql, params = 본[-1]
    assert sql.startswith("UPDATE batch_runs SET finished_at = ?, status = ?, step_log = ") and sql.count("?") == 5
    assert params[1] == "failed" and params[3] == "왜" and params[4] == 번호
    assert json.loads(params[2]) == db.run_fingerprint()


def test_지문을_못_내도_닫기는_간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2279 (25.2277 교차검증 [권고]) — 배포판 훑기가 던지면(깨진 METADATA) 닫는 UPDATE 앞에서 막혀 모든 작업의
    실행 기록이 running 으로 남았다. 곁다리는 실패를 칸에 적고 닫기는 그대로 간다"""
    from importlib import metadata

    부른수 = []

    def 깨짐():
        부른수.append(1)
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "깨진 METADATA")

    monkeypatch.setattr(metadata, "distributions", 깨짐)
    f = db.run_fingerprint()
    assert f["deps"] is None and f["error"] == "UnicodeDecodeError" and f["py"]
    c = _sqlite_client()
    성공 = db.start_batch_run(c, job_name="paper", market="KR", trade_date="2026-11-24")  # type: ignore[arg-type]
    실패 = db.start_batch_run(c, job_name="paper", market="US", trade_date="2026-11-24")  # type: ignore[arg-type]
    db.finish_batch_run(c, 성공, status="success", step_log={"orders": 1})  # type: ignore[arg-type]
    db.finish_batch_run(c, 실패, status="failed", error_text="죽음")  # type: ignore[arg-type]
    상태 = dict(c.execute("SELECT id, status FROM batch_runs WHERE id IN (?, ?)", [성공, 실패]).rows)
    assert 상태 == {성공: "success", 실패: "failed"}
    assert _로그(c, 성공)[db.RUN_FINGERPRINT_KEY]["error"] == "UnicodeDecodeError"
    assert _로그(c, 실패) == {db.RUN_FINGERPRINT_KEY: f}
    assert len(부른수) == 1  # 실패한 지문도 한 번만 낸다(닫을 때마다 다시 던지지 않는다)


def test_이름_없는_배포판은_지문을_막지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import metadata

    class _빈:
        metadata = {"Name": None}
        version = "1"

    monkeypatch.setattr(metadata, "distributions", lambda: [_빈()])
    f = db.run_fingerprint()
    assert f["deps"] and "error" not in f


def test_닫는_문장_둘은_고정_글이고_열쇠가_같다() -> None:
    assert f"'$.{db.RUN_FINGERPRINT_KEY}'" in db.CLOSE_RUN_FINGERPRINT_SQL
    for sql in (db.CLOSE_RUN_SQL, db.CLOSE_RUN_FINGERPRINT_SQL):
        assert sql.startswith("UPDATE batch_runs SET finished_at = ?, status = ?, step_log = ") and sql.count("?") == 5
        assert sql.endswith(", error_text = ? WHERE id = ?")


def test_빈_step_log_으로_닫는_성공도_지문만_남는다() -> None:
    """실패 정리만이 아니다 — step_log 없이·빈 dict 로 닫는 내 실행 전부(예전엔 NULL)"""
    c = _sqlite_client()
    하나 = db.start_batch_run(c, job_name="x", market=None, trade_date=None)  # type: ignore[arg-type]
    둘 = db.start_batch_run(c, job_name="x", market=None, trade_date=None)  # type: ignore[arg-type]
    db.finish_batch_run(c, 하나, status="success")  # type: ignore[arg-type]
    db.finish_batch_run(c, 둘, status="skipped", step_log={})  # type: ignore[arg-type]
    assert _로그(c, 하나) == _로그(c, 둘) == {db.RUN_FINGERPRINT_KEY: db.run_fingerprint()}
