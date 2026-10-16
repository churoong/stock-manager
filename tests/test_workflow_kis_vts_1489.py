"""KIS모의투자 키가 두 시장 일일 배치에 다 넘어가는가 (docs/paper.md 15장, docs/infra.md 25.1489).

미국 베끼기(25.1485)를 넣으면서 `daily-us.yml` 의 배치 단계 env 에 `KIS_VTS_*` 를 빠뜨렸다 — 키를 시크릿에 넣었어도 미국 단계는
"KIS모의투자 키 없음" 으로 조용히 꺼졌을 것이다(경고 없음). 배치가 KIS모의투자 단계를 부르는 시장이면 그 워크플로가 키를 넘겨야 한다
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import yaml

from batch.jobs import broker_mirror, daily

뿌리 = Path(__file__).resolve().parent.parent
키 = ("KIS_VTS_APP_KEY", "KIS_VTS_APP_SECRET", "KIS_VTS_ACCOUNT")


def _배치_env(name: str) -> dict[str, str]:
    wf = yaml.safe_load((뿌리 / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if step.get("id") == "batch":
                return step.get("env") or {}
    raise AssertionError(f"{name} 에 배치 단계(id: batch)가 없다")


@pytest.mark.parametrize("name", ["daily-kr.yml", "daily-us.yml"])
def test_daily_batch_passes_mock_keys(name: str) -> None:
    env = _배치_env(name)
    assert all(env.get(k) == f"${{{{ secrets.{k} }}}}" for k in 키), {k: env.get(k) for k in 키}


def test_broker_step_runs_for_both_markets() -> None:
    """위 시험이 두 시장을 보는 까닭 — 일일 배치가 시장을 가리지 않고 KIS모의투자 단계를 부르고, 단계는 KR·US 를 받는다."""
    원문 = inspect.getsource(daily)
    assert 'if "error" not in (step_log.get("paper") or {}):' in 원문 and 'market == "KR" and "error"' not in 원문
    assert 'market not in ("KR", "US")' in inspect.getsource(broker_mirror.run)
