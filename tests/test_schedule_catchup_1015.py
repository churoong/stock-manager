"""빠진 GitHub 예약 따라잡기 (docs/infra.md 25.1015)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import schedule_catchup as sc  # noqa: E402


def t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_가장_최근_예약_시각() -> None:
    # 2026-10-08 은 목요일
    assert sc.last_due("40 22 * * 0-4", t("2026-10-08T02:00")) == t("2026-10-07T22:40")  # 수 22:40
    assert sc.last_due("41 22 * * 6", t("2026-10-08T02:00")) == t("2026-10-03T22:41")  # 지난 토
    assert sc.last_due("11 17 3 * *", t("2026-10-08T02:00")) == t("2026-10-03T17:11")
    assert sc.last_due("11 17 3 * *", t("2026-10-20T02:00")) is None  # LOOKBACK 밖
    assert sc.last_due("23 17 5 1,4,7,10 *", t("2026-10-06T00:00")) == t("2026-10-05T17:23")
    assert sc.last_due("17 9 * * 1-5", t("2026-10-08T09:16")) == t("2026-10-07T09:17")


def test_예비와_지연_여유와_오래된_것은_빼고_묻는다() -> None:
    글 = 'on:\n  schedule:\n    - cron: "40 22 * * 0-4" # 주석\n  workflow_dispatch:\n'
    assert sc.crons(글) == ["40 22 * * 0-4"] and sc.has_dispatch(글)
    assert sc.candidate("sentiment-kr.yml", 글, t("2026-10-08T02:00")) == t("2026-10-06T22:40")  # 8시간 전 기준
    assert sc.candidate("sentiment-kr.yml", 글, t("2026-10-08T07:00")) == t("2026-10-07T22:40")
    assert sc.candidate("daily-kr.yml", 글, t("2026-10-08T07:00")) is None


def test_예약_뒤_실행이_없을_때만_깨운다(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "a.yml").write_text('on:\n  schedule:\n    - cron: "40 22 * * *"\n  workflow_dispatch:\n')
    (wf / "b.yml").write_text('on:\n  schedule:\n    - cron: "40 22 * * *"\n  workflow_dispatch:\n')
    (wf / "c.yml").write_text('on:\n  schedule:\n    - cron: "40 22 * * *"\n')  # 수동 실행 없음
    (wf / "daily-kr.yml").write_text('on:\n  schedule:\n    - cron: "40 22 * * *"\n  workflow_dispatch:\n')
    부름: list[tuple[str, str]] = []

    def call(method, url, token, body=None):  # noqa: ANN001, ANN202
        부름.append((method, url.rsplit("/workflows/", 1)[-1]))
        if method == "GET":
            return 200, json.dumps({"workflow_runs": [{"id": 1}] if "/b.yml/" in url else []}).encode()
        return 204, b""

    out = sc.run("o/r", "tok", call, t("2026-10-08T07:00"), tmp_path)
    posts = [u for m, u in 부름 if m == "POST"]
    assert posts == ["a.yml/dispatches"]
    assert "깨움 1개 (a.yml)" in out and "수동 실행 없음 c.yml" in out
    assert not any("daily-kr" in u for _, u in 부름)


def test_우리_워크플로의_cron_을_모두_읽는다() -> None:
    """형식이 바뀌어 못 읽는 cron 이 생기면 조용히 따라잡기 밖으로 빠진다."""
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        for c in sc.crons(path.read_text(encoding="utf-8")):
            sc.last_due(c, t("2026-10-08T02:00"))  # 예외가 나지 않아야 한다


def test_주간_요약은_이번_주에_이미_보냈으면_다시_보내지_않는다(monkeypatch) -> None:  # noqa: ANN001
    """따라잡기와 늦게 온 GitHub 예약이 겹쳐도 한 통 (25.1015)."""
    from datetime import timedelta

    from batch.core import db
    from batch.jobs import weekly_summary as ws
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    어제 = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    mem.conn.execute("INSERT INTO batch_runs (job_name, started_at, status) VALUES ('weekly_summary', ?, 'success')",
                     [어제])  # fmt: skip
    시작: list = []
    monkeypatch.setattr(ws.db, "start_batch_run", lambda *a, **k: 시작.append(1) or 1)
    assert ws.run() == 0 and 시작 == []


def test_한_번에_깨우는_수에_상한이_있다(tmp_path: Path) -> None:
    """25.1029 — 새 저장소 첫 따라잡기가 주간 작업 12개를 한꺼번에 깨워 같은 날 Turso 월 한도에 걸렸다."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for i in range(sc.MAX_PER_RUN + 4):
        (wf / f"w{i:02d}.yml").write_text('on:\n  schedule:\n    - cron: "40 22 * * *"\n  workflow_dispatch:\n')

    def call(method, url, token, body=None):  # noqa: ANN001, ANN202
        return (200, json.dumps({"workflow_runs": []}).encode()) if method == "GET" else (204, b"")

    부름: list[str] = []

    def 세는_call(method, url, token, body=None):  # noqa: ANN001, ANN202
        if method == "POST":
            부름.append(url)
        return call(method, url, token, body)

    out = sc.run("o/r", "tok", 세는_call, t("2026-10-08T07:00"), tmp_path)
    assert len(부름) == sc.MAX_PER_RUN
    assert f"다음 회로 미룸 4개(한 번에 {sc.MAX_PER_RUN}개까지)" in out
