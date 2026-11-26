"""관문 "운영 무사고" 의 검산 어긋난 날을 실행 기록에서도 센다 (docs/infra.md 25.2378, 결함 루프 2026-11-26 [참고]).

`paper_daily.recon_ok` 는 (계좌, 한국 날짜) 한 행이라 같은 날 뒤 실행이 덮는다 — 국내 아침에 어긋나 텔레그램·운용 화면에
알린 날이 저녁 미국 실행이 다시 쓴 행(recon_ok=1) 뒤 관문 무사고에서 사라질 수 있었다. 실행 기록(`step_log.paper.accounts`)은
덮이지 않는다 — 두 쪽의 합집합(엄격해지기만 한다).
"""

from __future__ import annotations

import json

from batch.core import db
from batch.jobs import desk as jd
from tests.test_portfolio_job import MemClient


def _행(mem, started: str, accounts: object) -> None:  # type: ignore[no-untyped-def]
    mem.conn.execute(
        "INSERT INTO batch_runs (job_name, trade_date, status, started_at, step_log) VALUES ('daily_kr', ?, 'partial', ?, ?)",
        [started[:10], started, json.dumps({"paper": {"accounts": accounts, "account_skips": {}}})],
    )


def test_질의가_검산_어긋남을_센다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _행(mem, "2026-11-25T23:40:00+00:00", {"A": {"recon_ok": False}, "B": {"recon_ok": True}})
    _행(mem, "2026-11-26T12:30:00+00:00", {"A": {"recon_ok": True}})
    _행(mem, "2026-11-27T23:40:00+00:00", {})
    mem.conn.execute("INSERT INTO batch_runs (job_name, trade_date, status, started_at, step_log)"
                     " VALUES ('daily_kr', '2026-11-28', 'partial', '2026-11-28T23:40:00+00:00', '{')")  # 깨진 기록
    줄 = mem.conn.execute(jd.BATCH_RUNS_SQL, ["daily_kr", "daily_us", "2026-01-01"]).fetchall()
    칸 = [d[0] for d in mem.conn.execute(jd.BATCH_RUNS_SQL, ["daily_kr", "daily_us", "2026-01-01"]).description]
    값 = sorted((dict(zip(칸, r, strict=True)) for r in 줄), key=lambda r: r["started_at"])
    assert [bool(r["recon_bad"]) for r in 값] == [True, False, False, False]


def test_한국_날짜() -> None:
    assert jd._한국날("2026-11-25T23:40:00+00:00") == "2026-11-26"  # 국내 아침 08:40 KST
    assert jd._한국날("2026-11-26T12:30:00+00:00") == "2026-11-26"  # 미국 밤 21:30 KST
    assert jd._한국날("2026-11-26T15:30:00") == "2026-11-27"  # 시간대 없으면 UTC
    assert jd._한국날(None) is None and jd._한국날("글") is None


def test_관문이_덮인_날도_센다(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """평가 행은 그날 통과(덮임)인데 아침 실행이 어긋남을 적었으면 무사고가 아니다"""
    from datetime import UTC, datetime

    잡힌: dict[str, int] = {}

    def 관문(*a, **k):  # noqa: ANN002, ANN003, ANN202
        잡힌["recon"] = k["recon_bad_days"]
        return {"ok": True}

    monkeypatch.setattr(jd.desk, "gate", 관문)
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    지금 = datetime.now(UTC)
    _행(mem, 지금.replace(microsecond=0).isoformat(), {"A": {"recon_ok": False}})
    jd._관문판(mem, 지금, {}, "H", set(), {}, since="", version=1)  # type: ignore[arg-type]
    assert 잡힌.get("recon") == 1
    잡힌.clear()
    jd._관문판(mem, 지금, {}, "H", {jd._한국날(지금.isoformat()) or ""}, {}, since="", version=1)  # type: ignore[arg-type]
    assert 잡힌.get("recon") == 1  # 같은 날이 두 쪽에 다 있으면 하루


def test_창_밖_실행은_세지_않는다(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """25.2380 (25.2378 교차검증 [권고]) — 실행 기록은 창보다 12일 앞부터 읽는다. 창 밖 날짜가 세어지지 않는 것은 뒤의
    `d >= 창` 거름 하나에 걸려 있어 묶는다"""
    from datetime import UTC, datetime, timedelta

    잡힌: dict[str, int] = {}
    monkeypatch.setattr(jd.desk, "gate", lambda *a, **k: 잡힌.update(recon=k["recon_bad_days"]) or {"ok": True})
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    지금 = datetime.now(UTC)
    밖 = 지금 - timedelta(days=jd.desk.GATE_INCIDENT_DAYS + 5)  # 앞당김(12일) 안, 창 밖
    _행(mem, 밖.replace(microsecond=0).isoformat(), {"A": {"recon_ok": False}})
    jd._관문판(mem, 지금, {}, "H", set(), {}, since="", version=1)  # type: ignore[arg-type]
    assert 잡힌.get("recon") == 0
