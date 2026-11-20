"""업종 수집의 다시 시도 예약 (docs/infra.md 25.2241).

2026-11-03 23:39 UTC 월 1회 업종 수집이 Turso 읽기 진도 문에 두 시장 다 `skipped` 였다 — "이번 달 74,462,936행 + 예상
300,000행 = 74,762,936행, 허용선 74,759,259". 허용선은 달 한도가 아니라 **진도선**(지난 몫 + 여유 5%)이다. 다시 시도
예약이 없어 업종이 다음 달까지 묵었다(주간 요약 "sectors: 마지막 실행 42일 전").
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from batch.jobs import sectors

뿌리 = Path(__file__).resolve().parent.parent
WF = 뿌리 / ".github" / "workflows" / "sectors.yml"


def _gate():
    spec = importlib.util.spec_from_file_location("turso_read_gate_2241", 뿌리 / "scripts" / "turso_read_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wf() -> dict:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _crons() -> list[str]:
    data = _wf()
    return [c["cron"] for c in (data.get(True) or data["on"])["schedule"]]


class _결과:
    def __init__(self, v):  # noqa: ANN001
        self.v = v

    def scalar(self):  # noqa: ANN202
        return self.v


class _가짜DB:
    """`ran_within` 의 MAX(finished_at) 질의에 정해 둔 값을 돌려준다."""

    def __init__(self, last: str | None, *, 깨짐: bool = False) -> None:
        self.last, self.깨짐, self.args = last, 깨짐, []

    def execute(self, sql: str, args=None):  # noqa: ANN001, ANN202
        if self.깨짐:
            raise RuntimeError("down")
        self.args.append(args)
        return _결과(self.last)


# ── 원인 재현 ──────────────────────────────────────────────────────────


def test_11월_3일_미룸은_진도선이었다() -> None:
    """허용선 74,759,259 는 월 한도가 아니라 진도선(3일 23:39 → 지난 몫 약 10% + 여유 5%)이다 — 같은 달 뒤면 들어간다."""
    g = _gate()
    ok, 글 = g.decide(74_462_936, 300_000, datetime(2026, 11, 3, 23, 39, 12, tzinfo=UTC))
    assert not ok and "허용선 74,759,2" in 글
    # 같은 쓴 양으로 일주일 뒤(하루 핵심 몫 600만씩 더 써도) 10일 다시 시도는 들어간다
    ok, _ = g.decide(74_462_936 + 6_000_000 * 7, 300_000, datetime(2026, 11, 10, 18, 43, tzinfo=UTC))
    assert ok


# ── 워크플로 예약 ────────────────────────────────────────────────────────


def test_월_1회에_다시_시도_예약이_둘_있다() -> None:
    assert _crons() == ["43 18 3 * *", "43 18 10 * *", "43 18 17 * *"]


def test_다시_시도_예약은_이번_달_돈_시장을_건너뛴다() -> None:
    본문 = WF.read_text(encoding="utf-8")
    env = _wf()["jobs"]["run"]["env"]
    assert env["SCHED"] == "${{ github.event.schedule || inputs.missed_schedule }}"
    assert 'if [ -n "$SCHED" ]; then LIMIT="$LIMIT --skip-if-done-this-month"; fi' in 본문
    # 따라잡기(schedule_catchup)가 깨운 실행도 예약처럼 본다 (25.1305)
    data = _wf()
    assert "missed_schedule" in (data.get(True) or data["on"])["workflow_dispatch"]["inputs"]


def test_마지막_다시_시도만_진도선을_보지_않는다() -> None:
    raw = _wf()["jobs"]["run"]["env"]["TURSO_READ_NO_PACE"]
    assert raw == "${{ (github.event.schedule || inputs.missed_schedule) == '43 18 17 * *' && '1' || '' }}"
    assert _crons()[-1] == "43 18 17 * *"
    assert _wf()["jobs"]["run"]["env"]["TURSO_READ_ESTIMATE"] == "300000"  # 문 자체는 그대로


def test_따라잡기가_다시_시도_cron_을_넘긴다() -> None:
    sys.path.insert(0, str(뿌리 / "scripts"))
    import schedule_catchup

    body = schedule_catchup.dispatch_body(WF.read_text(encoding="utf-8"), datetime(2026, 11, 11, 6, 0, tzinfo=UTC))
    assert body["inputs"] == {"missed_schedule": "43 18 10 * *"}


# ── 이번 달 성공 확인 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("last", "기대"),
    [
        ("2026-11-03T23:50:00+00:00", True),  # 이번 달 성공
        ("2026-10-31T23:59:00+00:00", False),  # 지난 달 — 다시 돈다
        ("2026-10-03T19:00:00+00:00", False),  # 11-03 미룸 전의 마지막 성공
        (None, False),  # 기록 없음
    ],
)
def test_이번_달_성공만_센다(monkeypatch: pytest.MonkeyPatch, last: str | None, 기대: bool) -> None:
    지금 = datetime(2026, 11, 10, 18, 43, tzinfo=UTC)

    class _dt(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return 지금

    monkeypatch.setattr("batch.core.db.datetime", _dt)
    db = _가짜DB(last)
    assert sectors.done_this_month(db, "KR", 지금) is 기대
    assert db.args[0][0] == "sectors" and db.args[0][-1] == "KR", "시장별로 본다 — 국내만 돈 달에 미국은 다시 돈다"
    assert "partial" in db.args[0], "partial 도 돈 것으로 센다 (25.611)"


def test_못_읽으면_돌린다() -> None:
    assert sectors.done_this_month(_가짜DB(None, 깨짐=True), "US", datetime(2026, 11, 17, tzinfo=UTC)) is False


def test_플래그면_돌지_않고_0(monkeypatch: pytest.MonkeyPatch) -> None:
    class _클라:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *a):  # noqa: ANN002, ANN204
            return False

    돌았다 = []
    monkeypatch.setattr(sectors, "TursoClient", _클라)
    monkeypatch.setattr(sectors, "done_this_month", lambda c, m: True)
    monkeypatch.setattr(sectors, "run", lambda *a: 돌았다.append(a) or 0)
    monkeypatch.setattr(sys, "argv", ["sectors", "--market", "KR", "--skip-if-done-this-month"])
    assert sectors.main() == 0 and not 돌았다
    # 플래그가 없으면(사람이 손으로) 늘 돈다
    monkeypatch.setattr(sys, "argv", ["sectors", "--market", "KR"])
    sectors.main()
    assert 돌았다 == [("KR", None)]
