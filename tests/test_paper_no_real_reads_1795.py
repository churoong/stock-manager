"""가상모의투자·KIS모의투자 경로는 실제 장부(`trades`·`positions`·`sell_flags`)를 **읽지도** 않는다 (docs/infra.md 25.1795).

2026-10-15 사용자 지시 — 모의 계좌는 실제 보유를 매매 판단에 쓰지 않는다(CLAUDE.md 매매 규칙, docs/paper.md). 쓰지 않는 것은
`test_paper_job_1445` 가 표 행 수로 보지만, 읽는 것은 지키는 시험이 없었다(사용자 물음 "내 계좌에 보유종목을 모의투자 매수/매도
종목 선정할때 감안하는거야?" 에 답하며 코드로만 확인했다). 실제 수익률 곡선(`portfolio_values`)은 성과 비교로만 읽어 허용.

모듈 전체를 보는 것과, 실제 장부도 다루는 공용 모듈에서 가상모의투자가 빌려 쓰는 함수만 보는 것 두 갈래다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
실제_장부 = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(trades|positions|sell_flags)\b")

#: 통째로 가상모의투자·KIS모의투자 쪽인 모듈
모의_모듈 = (
    "batch/jobs/paper.py",
    "batch/jobs/broker_mirror.py",
    "batch/jobs/broker_stats.py",
    "batch/jobs/company.py",
    "batch/jobs/desk.py",
    "batch/jobs/intraday_guard.py",
    "batch/services/paper.py",
    "batch/services/broker_mirror.py",
    "batch/services/broker_stats.py",
    "batch/services/company.py",
    "batch/services/desk.py",
    "batch/services/holding.py",
    "batch/services/holding_eq.py",
    "batch/services/holding_scores.py",
    "batch/services/risk_gate.py",
    "batch/services/report_picks.py",
    "batch/services/sell_flags.py",
    # 가상모의투자가 가져다 쓰는 계산 모듈
    "batch/services/signals.py",
    "batch/services/backtest.py",
    "batch/services/stop_vol.py",
    "batch/services/adjust.py",
    "batch/services/adjust_drift.py",
    "batch/services/fx.py",
)
#: 실제 장부도 다루는 공용 모듈에서 가상모의투자가 부르는 함수 — {파일: 함수들}
빌려_쓰는_함수 = {
    "batch/jobs/sell_flags.py": ("enrich",),
    "batch/jobs/monitor_targets.py": ("action_factors", "action_dates", "_유상_권리락"),
}


def _문자열(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _걸린것(texts: list[str]) -> list[str]:
    return [t[:120] for t in texts if 실제_장부.search(t)]


def test_모의_모듈은_실제_장부를_읽지_않는다() -> None:
    for 경로 in 모의_모듈:
        트리 = ast.parse((ROOT / 경로).read_text(encoding="utf-8"))
        assert not _걸린것(_문자열(트리)), 경로


def test_빌려_쓰는_공용_함수도_실제_장부를_읽지_않는다() -> None:
    for 경로, 이름들 in 빌려_쓰는_함수.items():
        트리 = ast.parse((ROOT / 경로).read_text(encoding="utf-8"))
        함수 = {n.name: n for n in ast.walk(트리) if isinstance(n, ast.FunctionDef)}
        for 이름 in 이름들:
            assert 이름 in 함수, f"{경로}:{이름} 이 없다 — 목록을 고친다"
            assert not _걸린것(_문자열(함수[이름])), f"{경로}:{이름}"


def test_paper_가_부르는_공용_함수는_목록에_있다() -> None:
    """가상모의투자가 공용 모듈의 새 함수를 부르기 시작하면 이 목록에 넣고 위 검사를 받게 한다."""
    본문 = (ROOT / "batch/jobs/paper.py").read_text(encoding="utf-8")
    부름 = {
        "batch/jobs/sell_flags.py": set(re.findall(r"\bsf_job\.([a-z_]\w*)\(", 본문)),
        "batch/jobs/monitor_targets.py": set(re.findall(r"\bmonitor_targets\.([a-z_]\w*)\(", 본문)),
    }
    for 경로, 이름들 in 부름.items():
        assert 이름들 <= set(빌려_쓰는_함수[경로]), (경로, 이름들 - set(빌려_쓰는_함수[경로]))


def test_잣대가_실제_질의를_잡는다() -> None:
    assert 실제_장부.search("SELECT stock_id FROM positions WHERE quantity > 0")
    assert 실제_장부.search("SELECT 1 FROM stocks s JOIN trades t ON t.stock_id = s.id")
    assert not 실제_장부.search("SELECT * FROM paper_positions")  # 가상 계좌 표는 괜찮다
    assert not 실제_장부.search("SELECT date, twr_index FROM portfolio_values")  # 성과 비교 — 허용
