"""주간 재무 수집이 월요일에 다 받지 못하면 화요일에 다시 (docs/infra.md 25.1087).

월요일 01:17 KST 한 번뿐이라 DART 점검(10-09 한글날부터 이틀 넘게 상태 800)과 겹치면 그 주 재무 갱신(정정 공시
반영)이 통째로 빠졌다. 화요일 실행은 월요일이 **성공**이면 바로 끝난다(partial·failed 는 세지 않는다).
"""

from __future__ import annotations

from pathlib import Path

import yaml

글 = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "financials.yml").read_text(encoding="utf-8")


def test_화요일_다시_돌되_월요일이_성공했으면_건너뛴다() -> None:
    wf = yaml.safe_load(글)
    crons = [x["cron"] for x in wf[True]["schedule"]]  # PyYAML 은 'on' 을 True 로 읽는다
    assert crons == ["17 16 * * 0", "17 16 * * 1"]
    run = next(s["run"] for s in wf["jobs"]["run"]["steps"] if s.get("name") == "수집")
    assert 'if [ "${{ github.event.schedule }}" = "17 16 * * 1" ]; then RETRY="--skip-if-ran-within 3"' in run
    assert "--years ${{ inputs.years || 5 }} $RETRY" in run


def test_건너뛰기는_partial_을_세지_않는다() -> None:
    import inspect

    from batch.jobs import financials

    assert "include_partial=False" in inspect.getsource(financials.main)


def test_재무_작업이_실패를_부르는_쪽에_넘긴다() -> None:
    """참고 분석이 받기 실패를 리포트에 올리려면 재무 작업이 경고를 넘겨야 한다 (25.1087)."""
    import inspect

    from batch.jobs import financials

    assert "failures.extend(warnings)" in inspect.getsource(financials.run)
