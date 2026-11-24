"""깔때기 작동특성 모의실험 (docs/funnel.md 3.2, docs/infra.md 25.2276).

합성 전략을 **실제 판정 함수**(`luck.judge`·`paper.block_bound`)와 **실제 문턱**(`scoreboard.STAGE01_MIN_DSR`·
`desk.GATE_*`)에 넣는지, 씨앗이 같으면 결과가 같은지, 실력 0 의 통과율이 등록한 α 안인지, 효과에 단조인지를 묶는다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from batch.services import desk, luck, scoreboard
from batch.services import funnel_oc as oc
from batch.services import paper as pp

뿌리 = Path(__file__).resolve().parent.parent


def test_같은_씨앗은_같은_결과_다른_씨앗은_다른_난수() -> None:
    a = oc.dsr_pass_rate(1.5, 60, 18, sims=60, seed=3)
    assert a == oc.dsr_pass_rate(1.5, 60, 18, sims=60, seed=3)
    g = oc.gate_pass_rate(56.0, 6.0, sims=300, seed=3)
    assert g == oc.gate_pass_rate(56.0, 6.0, sims=300, seed=3)
    assert g != oc.gate_pass_rate(56.0, 6.0, sims=300, seed=4)


def test_DSR_칸은_실제_판정_함수와_문턱을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`luck.judge` 에 N 하한과 코호트 전부를 넘기고, 문턱은 `scoreboard.STAGE01_MIN_DSR` 그대로"""
    본 = []
    진짜 = luck.judge

    def 엿봄(series, n_floor=None):
        본.append((len(series), n_floor, {len(v) for v in series.values()}))
        return 진짜(series, n_floor=n_floor)

    monkeypatch.setattr(oc.luck, "judge", 엿봄)
    oc.dsr_pass_rate(1.0, 48, 25, cohort=7, sims=3, seed=1)
    assert 본 == [(7, 25, {48})] * 3
    # 문턱을 0 으로 내리면 판정된 줄은 모두 통과, 1 을 넘기면 아무도 못 넘는다 — 문턱이 그 상수다
    monkeypatch.setattr(oc.scoreboard, "STAGE01_MIN_DSR", 0.0)
    assert oc.dsr_pass_rate(0.0, 48, 25, sims=20, seed=1)["pass_rate"] == 1.0
    monkeypatch.setattr(oc.scoreboard, "STAGE01_MIN_DSR", 1.01)
    assert oc.dsr_pass_rate(5.0, 48, 25, sims=20, seed=1)["pass_rate"] == 0.0


def test_DSR_통과율은_실력과_기간에_늘고_시도_수에_준다() -> None:
    기본 = oc.dsr_pass_rate(1.6, 120, 18, sims=150, seed=2)["pass_rate"]
    assert oc.dsr_pass_rate(0.0, 120, 18, sims=150, seed=2)["pass_rate"] < 0.05 < 기본
    assert oc.dsr_pass_rate(2.5, 120, 18, sims=150, seed=2)["pass_rate"] > 기본
    assert oc.dsr_pass_rate(1.6, 60, 18, sims=150, seed=2)["pass_rate"] < 기본
    assert oc.dsr_pass_rate(1.6, 120, 60, sims=150, seed=2)["pass_rate"] < 기본


def test_DSR_은_샤프만_본다_월_표준편차_값은_결과를_바꾸지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    a = oc.dsr_pass_rate(1.6, 60, 18, sims=80, seed=5)
    monkeypatch.setattr(oc, "MONTHLY_SD", 0.08)
    assert oc.dsr_pass_rate(1.6, 60, 18, sims=80, seed=5) == a


def test_전략끼리_함께_움직이면_문턱이_낮아져_통과가_는다() -> None:
    """공통 요인 — 샤프 추정들이 몰려 V 가 준다. rho = 0 은 독립 꼴과 난수 흐름까지 같다"""
    독립 = oc.dsr_pass_rate(1.6, 60, 18, sims=200, seed=4)
    assert oc.dsr_pass_rate(1.6, 60, 18, rho=0.0, sims=200, seed=4) == 독립
    함께 = oc.dsr_pass_rate(1.6, 60, 18, rho=0.8, sims=200, seed=4)
    assert 함께["pass_rate"] > 독립["pass_rate"] + 0.1 and 함께["rho"] == 0.8
    # 실력 0 의 통과는 여전히 드물다(문턱이 낮아져도 N 하한이 받친다)
    assert oc.dsr_pass_rate(0.0, 60, 18, rho=0.8, sims=300, seed=4)["pass_rate"] < 0.05


def test_실력_0_줄의_통과를_따로_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2279 (25.2276 교차검증 [권고]) — '실력 0 이 하나라도 통과' 열이 0 으로 죽어도 잡는 시험이 없었다"""
    r = oc.dsr_pass_rate(0.0, 60, 18, rho=0.5, sims=2000, seed=1)
    assert r["any_null_pass_rate"] == pytest.approx(0.034, abs=0.0005)  # 문서 표의 3.4%
    monkeypatch.setattr(oc.scoreboard, "STAGE01_MIN_DSR", 0.0)
    assert oc.dsr_pass_rate(0.0, 60, 18, sims=10, seed=1)["any_null_pass_rate"] == 1.0
    # 실력 0 줄이 없으면(전략 하나) 거짓 통과도 없다
    assert oc.dsr_pass_rate(0.0, 60, 18, cohort=1, sims=10, seed=1)["any_null_pass_rate"] == 0.0


def test_24개월_미만은_판정_안_함이라_통과가_아니다() -> None:
    assert oc.dsr_pass_rate(9.0, luck.MIN_MONTHS - 1, 18, sims=10, seed=1)["pass_rate"] == 0.0


def test_판정_시점의_묶음_수() -> None:
    """126·189·252 평가 = 달력 182.5·273.75·365일 → 28일 묶음 6·9·13 (내림)"""
    assert [oc.blocks_at(n) for n in desk.GATE_LOOKS] == [6, 9, 13]
    assert oc.blocks_at(125) == 6 and oc.blocks_at(115) == 5  # 115×365/252 = 166.6일 → 5묶음
    assert pp.EVIDENCE_BLOCK_DAYS == 28


def test_관문_칸의_거짓_통과율은_등록한_α_안이다() -> None:
    """실력 0(평균 = 무작위 기준 50) — 첫 시점은 α 0.0221 근처, 세 시점 누적은 0.05 근처를 넘지 않는다(Pocock)"""
    r = oc.gate_pass_rate(desk.GATE_SEL_NULL, 6.0, sims=6000, seed=1)
    첫, _둘, 끝 = r["cumulative_pass"]
    assert 첫 <= desk.GATE_POCOCK_ALPHA + 0.006
    assert 첫 <= _둘 <= 끝 <= 0.06
    # 표준편차는 거짓 통과율을 바꾸지 않는다(같은 난수 — 위치·척도 불변)
    assert oc.gate_pass_rate(desk.GATE_SEL_NULL, 10.0, sims=6000, seed=1)["cumulative_pass"] == r["cumulative_pass"]


def test_관문_칸은_효과에_늘고_누적이며_실제_문턱을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    약 = oc.gate_pass_rate(53.0, 6.0, sims=800, seed=2)["cumulative_pass"]
    강 = oc.gate_pass_rate(58.0, 6.0, sims=800, seed=2)["cumulative_pass"]
    assert all(a < b for a, b in zip(약, 강, strict=True))
    assert 강 == sorted(강)
    # 묶음 하한에 못 미치는 시점은 판정 불가 — 통과가 아니다
    monkeypatch.setattr(oc.desk, "GATE_SEL_MIN_BLOCKS", 7)
    r = oc.gate_pass_rate(70.0, 6.0, sims=200, seed=2)
    assert r["cumulative_pass"][0] == 0.0 and r["cumulative_pass"][1] > 0.9
    # 기준이 `GATE_SEL_NULL` 이다 — 기준을 평균 위로 올리면 통과가 사라진다
    monkeypatch.setattr(oc.desk, "GATE_SEL_NULL", 90.0)
    assert oc.gate_pass_rate(70.0, 6.0, sims=200, seed=2)["cumulative_pass"][-1] == 0.0


def test_관문_칸은_Pocock_α_를_block_bound_에_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    본 = set()
    진짜 = pp.block_bound

    def 엿봄(values, alpha=None):
        본.add((len(values), alpha))
        return 진짜(values, alpha)

    monkeypatch.setattr(oc.pp, "block_bound", 엿봄)
    oc.gate_pass_rate(40.0, 6.0, sims=3, seed=1)
    assert 본 == {(6, desk.GATE_POCOCK_ALPHA), (9, desk.GATE_POCOCK_ALPHA), (13, desk.GATE_POCOCK_ALPHA)}


def test_묶음_하한_검정력과_최소_효과() -> None:
    # 효과 0 의 통과율은 α 근처(이웃 상관 보정은 더 보수적으로만 움직인다)
    assert oc.bound_power(0.0, 12, 0.05, sims=6000, seed=1) <= 0.06
    assert oc.bound_power(0.0, 12, 0.0125, sims=6000, seed=1) <= 0.02
    # 최소 효과 — 그 값에서 검정력이 80% 를 넘고, 조금 밑에서는 못 넘는다(같은 씨앗)
    d = oc.min_effect(12, 0.05, sims=1500, seed=1)
    assert d is not None and 0.5 < d < 1.5
    assert oc.bound_power(d, 12, 0.05, sims=1500, seed=1) >= 0.8 > oc.bound_power(d - 0.02, 12, 0.05, sims=1500, seed=1)
    # 묶음이 늘면 작은 효과를 잡고, α 가 엄하면 큰 효과만 잡는다
    assert oc.min_effect(24, 0.05, sims=1500, seed=1) < d < oc.min_effect(12, 0.0125, sims=1500, seed=1)
    # 묶음이 block_bound 최소에 못 미치면 판정 자체가 없다
    assert oc.min_effect(pp.EVIDENCE_MIN_BLOCKS - 1, 0.05, sims=50) is None


def test_합성_자료만_쓴다_DB_와_운영_결과를_읽지_않는다() -> None:
    글 = inspect.getsource(oc) + (뿌리 / "scripts" / "funnel_oc.py").read_text()
    for 금지 in ("turso", "client", "execute", "SELECT", "requests", "os.environ"):
        assert 금지 not in 글, 금지
    # Actions 워크플로에 두지 않는다(분을 작업 도구로 태우지 않는다)
    assert not [w.name for w in (뿌리 / ".github" / "workflows").glob("*.yml") if "funnel_oc" in w.read_text()]


def test_표의_격자는_고정이고_문서가_그_표를_싣는다() -> None:
    문서 = (뿌리 / "docs" / "funnel.md").read_text()
    assert "### 3.2 작동특성 모의실험" in 문서 and "25.2276" in 문서
    assert oc.TABLE_IR == (0.0, 0.5, 0.8, 1.2, 1.6, 2.0, 2.5) and oc.TABLE_MONTHS == (60, 120, 180)
    assert oc.TABLE_TRIALS == (18, 60) and oc.TABLE_BLOCKS == (6, 9, 12, 13)
    assert oc.TABLE_GATE_MEAN == (50.0, 53.0, 56.0, 60.0) and oc.TABLE_GATE_SD == (6.0, 10.0)
    assert desk.GATE_POCOCK_ALPHA in oc.TABLE_ALPHA and 0.0125 in oc.TABLE_ALPHA
    for 글 in (
        "`python scripts/funnel_oc.py --sims 2000 --seed 1`",
        "새 코호트",
        "합성 자료",
        "이 절은 문턱을 바꾸지 않는다",
    ):
        assert 글 in 문서, 글


def test_문서의_표_값이_함수가_내는_값이다() -> None:
    """문서 표의 칸 몇 개를 같은 횟수·씨앗으로 다시 낸다 — 판정 함수나 문턱이 바뀌면 표를 다시 뽑게"""
    문서 = (뿌리 / "docs" / "funnel.md").read_text()
    절 = 문서[문서.index("### 3.2 작동특성 모의실험") : 문서.index("## 4. 리플레이 엔진 범위")]
    r = oc.gate_pass_rate(50.0, 6.0, sims=4000, seed=1)["cumulative_pass"]
    assert "| 50(실력 0) | 6·10 | " + " | ".join(f"{x * 100:.1f}%" for x in r) + " |" in 절
    r = oc.gate_pass_rate(56.0, 10.0, sims=4000, seed=1)["cumulative_pass"]
    assert "| 56 | 10 | " + " | ".join(f"{x * 100:.1f}%" for x in r) + " |" in 절
    for n in oc.TABLE_BLOCKS:  # (다) 표는 네 줄 전부(25.2279 — 처음엔 6 줄만 묶어 두 칸이 반올림으로 어긋나 있었다)
        칸 = [f"{oc.min_effect(n, a, sims=4000, seed=1):.2f}" for a in oc.TABLE_ALPHA]
        assert f"| {n} | " + " | ".join(칸) + " |" in 절, n
    # 한계 문단의 "한 묶음 늦추면" 숫자(25.2280 — 처음엔 시험 밖이었다): 묶음 5·8·12
    늦 = oc.gate_pass_rate(53.0, 6.0, looks=(105, 168, 233), sims=4000, seed=1)
    assert 늦["blocks"] == [5, 8, 12]
    assert "한 묶음 늦추면 53·6 의 누적이 " + " · ".join(f"{x * 100:.1f}" for x in 늦["cumulative_pass"]) + "%" in 절
    d = oc.dsr_pass_rate(2.0, 60, 18, sims=2000, seed=1)
    assert f"| 2.0 | {d['pass_rate'] * 100:.1f}% |" in 절 and d["any_null_pass_rate"] <= 0.001


def test_표_머리가_실제_문턱을_적는다(monkeypatch: pytest.MonkeyPatch) -> None:
    for 이름 in (
        "TABLE_IR",
        "TABLE_MONTHS",
        "TABLE_TRIALS",
        "TABLE_GATE_MEAN",
        "TABLE_GATE_SD",
        "TABLE_BLOCKS",
        "TABLE_ALPHA",
    ):
        monkeypatch.setattr(oc, 이름, getattr(oc, 이름)[:1])
    줄 = oc.table(sims=20, seed=1)
    assert f"DSR ≥ {scoreboard.STAGE01_MIN_DSR}" in 줄[0] and f"α {desk.GATE_POCOCK_ALPHA}" in 줄[0]
    assert len(줄) == 1 + 4 + 4  # 머리 + 칸 머리 넷 + 격자 한 줄씩
    assert f"상관 {oc.TABLE_RHO}" in 줄[3] and oc.TABLE_RHO == 0.5
