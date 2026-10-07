"""D1 따라잡기 읽기 예산 문 (docs/infra.md 25.844)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

루트 = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("d1_read_gate", 루트 / "scripts" / "d1_read_gate.py")
gate = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(gate)  # type: ignore[union-attr]


def test_웹_몫을_남기지_못하면_미룬다() -> None:
    # 2026-10-01 실측: 점수 앞 남은 읽기 913,193 — 웹 몫 100만을 남기면 들어가면 안 됐다
    ok, text = gate.decide(913_193, 1_200_000)
    assert ok is False and "내일로 미룹니다" in text
    assert gate.decide(4_000_000, 1_200_000)[0] is True
    assert gate.decide(None, 1_200_000)[0] is True  # 못 재면 들어간다


def test_따라잡기_4_8단계가_문을_거친다() -> None:
    글 = (루트 / ".github" / "workflows" / "d1-catchup.yml").read_text(encoding="utf-8")
    for 단계 in ("4. 과거 시세", "5. 성과 지표", "6. 점수", "7. 신호", "8. 장중 감시"):
        assert f'python scripts/d1_read_gate.py "{단계}"' in 글, 단계
    # 문은 그 단계의 작업보다 앞에 있다
    assert 글.index('d1_read_gate.py "6. 점수"') < 글.index("python -m batch.jobs.scores --market KR")


def test_미룸은_78_만이다() -> None:
    assert gate.DEFER_EXIT == 78
    글 = (루트 / ".github" / "workflows" / "d1-catchup.yml").read_text(encoding="utf-8")
    assert 글.count('if [ "$gate" = "78" ]; then exit 0; fi') == 5


# ---------------------------------------------------------------- 25.847 같은 날 뒤에 도는 일일 배치 몫


def test_평일_따라잡기는_그날_밤_국내_일일_배치_몫을_남긴다() -> None:
    from datetime import UTC, datetime

    # 2026-10-15(목) 00:05 UTC — 23:27 UTC 국내 일일 배치와 22:40 감성 수집이 남아 있다
    뒤 = gate.later_today(datetime(2026, 10, 15, 0, 5, tzinfo=UTC))
    assert [이름 for 이름, _ in 뒤] == ["국내 감성 수집", "국내 일일 배치"]
    # 남은 400만에서 웹 100만만 남기면 점수(120만)가 들어갔다 — 일일 배치 몫까지 남기면 미룬다
    assert gate.decide(4_000_000, 1_200_000)[0] is True
    ok, text = gate.decide(4_000_000, 1_200_000, later=뒤)
    assert ok is False and "국내 일일 배치" in text
    # 금·토 UTC 에는 국내 일일 배치가 없다 — 웹 몫만
    assert gate.later_today(datetime(2026, 10, 16, 0, 5, tzinfo=UTC)) == []
    # 일일 배치가 이미 돈 뒤(23:30 UTC)에는 남기지 않는다
    assert gate.later_today(datetime(2026, 10, 15, 23, 30, tzinfo=UTC)) == []


def test_미룬_단계를_출력에_적고_알림과_복귀_점검이_가른다(tmp_path, monkeypatch) -> None:
    import json
    import sys

    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    gate.mark_deferred()
    assert out.read_text(encoding="utf-8") == "deferred=true\n"

    sys.path.insert(0, str(루트 / "scripts"))
    import catchup_report
    import return_check

    steps = {k: {"outcome": "success", "outputs": {}} for k in ("backfill", "metrics", "scores", "signals", "targets")}
    steps["scores"]["outputs"] = {"deferred": "true"}
    글 = json.dumps(steps)
    assert catchup_report.deferred_steps(글) == ["6. 점수"]
    본문 = catchup_report.compose(catchup_report.Facts(), "success", [], gate.datetime.now(gate.UTC), ["6. 점수"])
    assert "내일로 미룬 단계: 6. 점수" in 본문
    assert "6. 점수 미룸(읽기 문)" in return_check.뒷단계가_돌았나(글).본것


def test_주_월_작업도_몫에_든다_25_852() -> None:
    from datetime import UTC, datetime

    # 2026-11-01 은 일요일이자 1일 — 필수·전체 백업, 감성, 일일 배치가 모두 남는다
    이름들 = [이름 for 이름, _ in gate.later_today(datetime(2026, 11, 1, 0, 5, tzinfo=UTC))]
    assert 이름들 == ["필수 백업", "전체 백업", "국내 감성 수집", "국내 일일 배치"]
    # 월요일엔 실적 일정·밸류 밴드·신호 성과
    월 = [이름 for 이름, _ in gate.later_today(datetime(2026, 10, 5, 0, 5, tzinfo=UTC))]
    assert {"실적 일정", "밸류 밴드", "신호 성과"} <= set(월)
    # 워크플로 예약과 같은 시각이다 — 예약을 옮기면 여기도 옮긴다
    루트_wf = 루트 / ".github" / "workflows"
    for 파일, 예약 in (("backup.yml", '"13 16 * * 0"'), ("backup.yml", '"47 16 1 * *"'), ("backup.yml", '"47 16 8 * *"'), ("valuation-bands.yml", '"53 17 * * 1"'),
                     ("signal-outcomes.yml", '"7 21 * * 1"'), ("earnings-calendar.yml", '"53 15 * * 1"'),
                     ("daily-kr.yml", '"27 23 * * 0-4"'), ("sentiment-kr.yml", '"40 22 * * 0-4"'),
                     ("etf.yml", '"19 17 2 * *"'), ("sectors.yml", '"43 18 3 * *"'), ("accumulation.yml", '"17 19 6 * *"'),
                     ("dividends.yml", '"41 18 5 4,5 *"')):  # fmt: skip
        assert 예약 in (루트_wf / 파일).read_text(encoding="utf-8"), (파일, 예약)


def test_매월_작업과_달_조건_25_854() -> None:
    from datetime import UTC, datetime

    이름 = lambda d: [n for n, _ in gate.later_today(d)]  # noqa: E731
    assert "ETF" in 이름(datetime(2026, 10, 2, 0, 5, tzinfo=UTC))
    assert "업종" in 이름(datetime(2026, 10, 3, 0, 5, tzinfo=UTC))
    assert "적립 후보" in 이름(datetime(2026, 10, 6, 0, 5, tzinfo=UTC))
    assert "배당" in 이름(datetime(2027, 4, 5, 0, 5, tzinfo=UTC))
    assert "배당" not in 이름(datetime(2026, 10, 5, 0, 5, tzinfo=UTC))  # 4·5월만


def test_아침_실행이_실패한_날은_예비_실행_몫을_남긴다_25_871() -> None:
    from datetime import UTC, datetime

    금요일_0005 = datetime(2026, 10, 2, 0, 5, tzinfo=UTC)
    assert "국내 일일 배치" not in [n for n, _ in gate.later_today(금요일_0005)]  # 금 UTC 는 23:27 배치가 없다
    뒤 = gate.late_daily_pending(금요일_0005, morning_succeeded=False)
    assert [n for n, _ in 뒤] == ["국내 일일 배치 예비(늦은 리포트)"]
    assert gate.late_daily_pending(금요일_0005, morning_succeeded=None) != []  # 모르면 남긴다
    assert gate.late_daily_pending(금요일_0005, morning_succeeded=True) == []
    assert gate.late_daily_pending(datetime(2026, 10, 2, 0, 40, tzinfo=UTC), False) == []  # 예비가 이미 지났다
    assert gate.late_daily_pending(datetime(2026, 10, 3, 0, 5, tzinfo=UTC), False) == []  # 토요일
    assert gate.decide(4_000_000, 1_200_000, later=뒤)[0] is False


def test_전체_백업은_한_달에_두_번이고_둘_다_전체다_25_872() -> None:
    글 = (루트 / ".github" / "workflows" / "backup.yml").read_text(encoding="utf-8")
    for 예약 in ("47 16 1 * *", "47 16 8 * *"):
        assert f"github.event.schedule == '{예약}'" in 글  # SCOPE 가 full 이 된다
