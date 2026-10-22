"""판정 대기 원장(docs/factors.md 12.32)의 실행 기록 키가 코드에 실제로 있다 (docs/infra.md 25.1438, 29회차 P2)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: 아직 구현 전인 29회차 키 — 구현하는 커밋에서 이 목록에서 뺀다
아직: set[str] = set()


def test_원장_키가_코드에_있다() -> None:
    doc = (ROOT / "docs" / "factors.md").read_text(encoding="utf-8")
    절 = doc[doc.index("### 12.32 판정 대기 원장"):doc.index("### 12.6 지난 회차 판정 상태")]
    # 셋째 칸(실행 기록 키)의 첫 `…` — 대소문자·기호를 가리지 않고 잡아야 틀린 키가 조용히 빠지지 않는다
    행들 = [줄.split("|") for 줄 in 절.splitlines() if re.match(r"\| \d+ \|", 줄)]
    keys = {m.group(1).split(".")[-1] for 칸 in 행들 if (m := re.search(r"`([^`]+)`", 칸[3]))}
    코드 = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "batch").rglob("*.py"))
    없음 = sorted(k for k in keys - 아직 if f'"{k}"' not in 코드)
    assert len(행들) >= 27 and len(keys) >= 20 and not 없음, 없음


#: 원장 키마다 그 키를 **쓰는** 파일 (25.1671) — 위 시험은 키 글자가 batch 어디든 있으면 통과해, `trigger`·`sha` 처럼
#: 다른 작업(core)에도 있는 키는 판정 실행이 그 키를 안 써도 통과했다. 판정 실행 정의(#2)는 월 백테스트와 핵심 H 둘 다 쓴다
쓰는곳: dict[str, tuple[str, ...]] = {
    "params": ("jobs/backtest.py", "jobs/core_backtest.py"),
    "trigger": ("jobs/backtest.py", "jobs/core_backtest.py"),
    "sha": ("jobs/backtest.py", "jobs/core_backtest.py"),
    "correlation": ("jobs/daily.py",), "corr_async": ("jobs/daily.py",),
    "high52_split_only": ("jobs/scores.py",), "clusters": ("jobs/broker_stats.py",),
    "single_factor": ("jobs/etf_tilt.py",), "short_turnover_one_day": ("jobs/signals.py",),
    "mdd_recent_worse": ("jobs/signals.py",), "mid_newer_quarter": ("jobs/signals.py",),
    "peer_sample": ("jobs/scores.py",), "small_base": ("jobs/scores.py",), "same_issuer_pairs": ("jobs/daily.py",),
    "part1_sector": ("jobs/daily.py",), "short_limit_up": ("jobs/signals.py",), "impact": ("jobs/daily.py",),
    "fx_vol": ("jobs/daily.py",), "dividend_note": ("jobs/sell_flags.py",), "self_grade": ("jobs/daily.py",),
    "post_touch": ("jobs/signal_outcomes.py",), "neff": ("jobs/etf_tilt.py",), "low_touch": ("jobs/sell_flags.py",),
    "pick_dividend": ("jobs/daily.py",), "part1_post_close": ("jobs/daily.py",), "us_8k_items": ("jobs/daily.py",),
    "tilt_paper": ("jobs/etf_tilt.py",), "horizon_fit": ("jobs/signal_outcomes.py",),
    "holding_eq": ("jobs/core_backtest.py",), "stop_z": ("services/stop_vol.py",), "cash_eq": ("services/desk.py",),
    "exit_pair": ("jobs/core_backtest.py",), "loss_gate": ("jobs/core_backtest.py",),
    "high52_judge": ("jobs/backtest.py",), "high52_overlap": ("jobs/backtest.py",),
}


def test_원장_키를_그_작업이_쓴다() -> None:
    doc = (ROOT / "docs" / "factors.md").read_text(encoding="utf-8")
    절 = doc[doc.index("### 12.32 판정 대기 원장"):doc.index("### 12.6 지난 회차 판정 상태")]
    행들 = [줄.split("|") for 줄 in 절.splitlines() if re.match(r"\| \d+ \|", 줄)]
    # 셋째 칸의 **모든** `…` 와 점으로 나눈 모든 마디(`params.trigger` → params·trigger, `sha` 도)
    keys = {마디 for 칸 in 행들 for 키 in re.findall(r"`([^`]+)`", 칸[3]) for 마디 in 키.split(".")} - 아직
    assert keys == set(쓰는곳), (sorted(keys - set(쓰는곳)), sorted(set(쓰는곳) - keys))
    없음 = []
    for k, 파일들 in 쓰는곳.items():
        # 쓰는 꼴만 — `"k": …`(dict), `["k"] = …`/`["k"][…]`(넣기)
        꼴 = re.compile(rf'"{re.escape(k)}"\s*:|\["{re.escape(k)}"\]\s*(=|\[)')
        없음 += [f"{k} ← {f}" for f in 파일들 if not 꼴.search((ROOT / "batch" / f).read_text(encoding="utf-8"))]
    assert not 없음, 없음


def _안에(src: str, 위: str, 키: str) -> bool:
    """`위` 묶음 **안에** `키` 를 쓰는가 — `["위"]["키"]` 또는 `"위": { … "키": … }`(괄호 짝을 맞춰 그 안만)."""
    if re.search(rf'\["{re.escape(위)}"\]\["{re.escape(키)}"\]', src):
        return True
    for m in re.finditer(rf'"{re.escape(위)}"\s*:\s*\{{', src):
        깊이, i = 1, m.end()
        while i < len(src) and 깊이:
            깊이 += {"{": 1, "}": -1}.get(src[i], 0)
            i += 1
        if re.search(rf'"{re.escape(키)}"\s*:', src[m.end():i]):
            return True
    return False


def test_점으로_이은_키는_그_묶음_안에_쓴다() -> None:
    """25.1671 교차검증 [권고] — 마디를 따로만 보면 `trigger` 를 `params` 밖 맨 위로 옮겨도 통과했다(원장은
    `params.trigger` 로 읽는다). 같은 칸에서 점 없는 키가 점 있는 키 뒤에 오면 같은 묶음이다(`params.trigger`·`sha`)."""
    doc = (ROOT / "docs" / "factors.md").read_text(encoding="utf-8")
    절 = doc[doc.index("### 12.32 판정 대기 원장"):doc.index("### 12.6 지난 회차 판정 상태")]
    경로: set[tuple[str, str]] = set()
    for 줄 in 절.splitlines():
        if not re.match(r"\| \d+ \|", 줄):
            continue
        위 = None
        for 키 in re.findall(r"`([^`]+)`", 줄.split("|")[3]):
            if "." in 키:
                위, 키 = 키.rsplit(".", 1)
            if 위:
                경로.add((위, 키))
    assert ("params", "sha") in 경로 and ("correlation", "corr_async") in 경로
    없음 = [f"{위}.{키} ← {f}" for 위, 키 in sorted(경로) for f in 쓰는곳[키]
            if not _안에((ROOT / "batch" / f).read_text(encoding="utf-8"), 위, 키)]
    assert not 없음, 없음
