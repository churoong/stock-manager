"""25.1662 — 핵심 H 백테스트는 판정 실행(예약·따라잡기·웹)과 다른 동시 실행 그룹이다 (11-03 판정 실행 감사 [권고])."""

from pathlib import Path

BT = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "backtest.yml").read_text(encoding="utf-8")


def test_핵심H_만_다른_그룹() -> None:
    assert "group: backtest-${{ inputs.what == '핵심H' && 'core' || 'main' }}" in BT
    assert "cancel-in-progress: false" in BT
    # 핵심 H 작업의 조건과 같은 입력 이름·값이어야 그룹이 맞게 갈린다
    assert "if: ${{ github.event_name == 'workflow_dispatch' && inputs.what == '핵심H' }}" in BT
