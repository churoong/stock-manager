"""유니버스 갱신 직후 새·재편입 종목의 성과 지표를 낸다 (docs/infra.md 25.1819).

월요일 국내·미국 일일 배치는 일요일 밤 새 유니버스를 쓰는데 주간 지표 작업은 화요일 02:23 KST 에 돈다 — 새로 편입된
종목은 지표 행이 없어 리스크 팩터가 빠진 채 4팩터로 재정규화되고, 신호 비중이 변동성·MDD 를 몰라 줄지 않았다.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from batch.core import db
from batch.jobs import metrics as metrics_job
from batch.jobs import scores as scores_job
from batch.services import history as hs
from tests.test_metrics_beta import MemClient, 계열, 기준일, 수익률, 시세넣기

ROOT = Path(__file__).resolve().parent.parent


def _장부() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid in (1, 2, 3, 4):
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 'active', 't', 't')",
            [sid, f"00000{sid}", f"종목{sid}"],
        )

    def 편입(날: str, sid: int, included: int = 1) -> None:
        c.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, ?, ?, 'KRW', 't')",
            [날, sid, included],
        )

    def 지표(sid: int, 날: str) -> None:
        c.execute(
            "INSERT INTO performance_metrics (stock_id, as_of_date, window, mdd, data_points, calc_version, created_at)"
            " VALUES (?, ?, '1Y', -0.2, 250, 1, 't')",
            [sid, 날],
        )

    편입("2026-09-11", 4)  # 지난 스냅샷에만 — 최신에서 빠졌다
    for sid in (1, 2, 3):
        편입("2026-09-18", sid)  # 최신 스냅샷 편입
    편입("2026-09-18", 4, included=0)
    지표(2, "2026-09-14")  # 지난주 지표 — 있다
    지표(3, "2026-06-30")  # 6월에 빠졌다 돌아온 재편입 — 몇 달 묵어 없는 것으로
    지표(4, "2026-06-30")  # 유니버스 밖 — 내지 않는다
    return mem


def test_최신_편입_가운데_지표가_없거나_묵은_종목만_고른다() -> None:
    mem = _장부()
    고른 = metrics_job.missing_stocks(mem, {"KR": 기준일}, ("KR",))  # type: ignore[arg-type]
    assert sorted(sid for sid, _, _ in 고른) == [1, 3]
    assert metrics_job.MISSING_STALE_DAYS == scores_job.METRICS_STALE_DAYS


def test_빠진_종목만_계산하고_이름을_갈라_적고_시장_분포는_두고_간다(monkeypatch) -> None:  # noqa: ANN001
    mem = _장부()
    시세넣기(mem, 1, 계열(1000, 수익률(400)))
    시장 = {"as_of": "2026-09-11", "h": {"1": {"n": 5000, "median": 0.01, "up": 0.55}}}
    db.set_setting(mem, hs.market_key("KR"), 시장)  # type: ignore[arg-type]
    monkeypatch.setattr(metrics_job, "TursoClient", lambda: mem)
    monkeypatch.setattr(metrics_job.cal, "default_as_of", lambda _c: 기준일)
    assert metrics_job.run(["1Y"], countries=("KR",), only_missing=True) == 0
    낸 = dict(mem.conn.execute("SELECT stock_id, mdd FROM performance_metrics WHERE as_of_date = ?", [기준일]).fetchall())
    assert set(낸) == {1, 3} and 낸[1] is not None  # 3 은 시세가 없어 값 없는 행(표본 부족), 2·4 는 대상이 아니다
    이름 = mem.conn.execute("SELECT job_name FROM batch_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert 이름 == metrics_job.JOB_NAME_MISSING != metrics_job.JOB_NAME
    남은 = json.loads(mem.conn.execute("SELECT value FROM settings WHERE key = ?", [hs.market_key("KR")]).fetchone()[0])
    assert 남은["h"]["1"]["n"] == 5000
    # 가격 패턴은 내지 않는다(25.1821) — 그 몇 종목끼리 "함께 움직이는 종목" 을 내 전 시장 기준 행을 덮었다
    assert mem.conn.execute("SELECT COUNT(*) FROM price_patterns").fetchone()[0] == 0
    assert metrics_job.EVENT_FIN_SQL not in mem.질의 and metrics_job.EVENT_FLOWS_SQL not in mem.질의  # 나라 단위 사건 이력
    # 빠진 종목이 없으면 성공으로 닫는다(실패 알림이 아니다)
    mem.conn.execute("DELETE FROM universe_members WHERE stock_id IN (1, 3)")
    assert metrics_job.run(["1Y"], countries=("KR",), only_missing=True) == 0
    상태 = mem.conn.execute("SELECT status FROM batch_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert 상태 == "success"


def test_유니버스_워크플로가_갱신_뒤_빠진_지표를_낸다() -> None:
    글 = (ROOT / ".github" / "workflows" / "universe.yml").read_text(encoding="utf-8")
    갱신 = 글.index("python -m batch.jobs.universe --market KR")
    지표 = 글.index("python -m batch.jobs.metrics --only-missing")
    assert 갱신 < 지표
    단계 = 글[글.rindex("- name:", 0, 지표) : 지표]
    assert "if: ${{ !cancelled() }}" in 단계  # 한 나라 유니버스가 실패해도 돈다
    assert "market_wide=ticker is None and not only_missing" in inspect.getsource(metrics_job.run)


def test_점수_작업은_지표_행이_없는_유니버스_종목을_센다() -> None:
    """묵음 검사(`stale_metrics`)는 행이 있는 종목만 셌다 — 새 편입 단계가 못 돌면 아무 말이 없었다 (25.1819)"""
    글 = inspect.getsource(scores_job.run)
    assert "지표없음 = missing_metrics(universe, 성과지표)" in 글
    # 동작 (25.1821, 교차검증 [권고]) — 행이 없는 종목만 센다. 값이 빈 행은 행이 있다
    유니버스 = [{"stock_id": 1}, {"stock_id": 2}, {"stock_id": 3}]
    assert scores_job.missing_metrics(유니버스, {2: {"mdd": None}}) == 2
    assert scores_job.missing_metrics(유니버스, {1: {}, 2: {}, 3: {}}) == 0
    assert '{"missing_metrics": 지표없음}' in 글
