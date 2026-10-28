"""전략 깔때기 0단계 — 위원회 회사(장기보유 D·안정 E)의 리플레이 변형 (docs/funnel.md 4장 ②, docs/infra.md 25.1904).

핵심 H 판정 변형(`VARIANTS`)은 그대로 두고 탐색 변형(`EXPLORE_VARIANTS`)에 넣는다. 위원회 회사는 하루 재료의 같은 안건·모집단으로
운영 `committee_step` 과 같은 `co.meet` 결과(가결 행)를 미리 고름으로 넘겨 산다.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from batch.services import company as co
from batch.services import core_strategy as cs
from batch.services import paper as pp
from tests.test_core_strategy_1532 import _끝까지, 모의


def test_핵심_판정_변형은_그대로_다섯이다() -> None:
    """11-03 기본 실행·시도 수(`CORE_REGISTERED_TRIALS`)가 보는 목록 — 탐색 변형이 섞이지 않는다."""
    assert sorted(cs.VARIANTS) == ["core_b", "core_g", "core_h", "core_r1", "core_r2"]
    assert not set(cs.VARIANTS) & set(cs.EXPLORE_VARIANTS)


def test_탐색_변형_칸은_운영_계좌와_같다() -> None:
    """마이그레이션 0075 의 D·E 칸(매도 규칙·진입) — 상위 몇 종목은 운영처럼 헌장 `top_n` 이 정한다."""
    글 = Path("migrations/0075_paper_more_accounts.sql").read_text(encoding="utf-8")
    for 이름, v in cs.EXPLORE_VARIANTS.items():
        줄 = 글.split(f"('{v.account}', ")[1].split("\n")[0]
        assert f"'{v.sell_rule}', 1, " in 줄 and f"'{v.entry}', " in 줄, (이름, 줄)
        assert v.selection == "committee" and v.account in co.CHARTERS


def test_모르는_변형은_두_목록을_다_알려_준다() -> None:
    with pytest.raises(ValueError, match="core_d"):
        cs.make_core_strategy("core_x", object(), sessions=[])  # type: ignore[arg-type]


@pytest.mark.parametrize("variant", sorted(cs.EXPLORE_VARIANTS))
def test_위원회_변형이_가결_종목만_사고판다(variant: str) -> None:
    s, r, 주문, _셈 = _끝까지(variant)
    v = cs.EXPLORE_VARIANTS[variant]
    assert s.v.top_n == int(co.CHARTERS[v.account]["top_n"])  # 헌장이 덮는다
    assert len(r.curve) == len(모의) and all(x > 0 and math.isfinite(x) for _d, x in r.curve)
    assert sum(len(o.buys) for o in 주문.values()) >= 1  # 합성 자료에서 E 는 가결이 드물다(산 날 2)
    사유 = {c for lg in s.log for cs_ in lg.sells.values() for c in cs_}
    assert 사유 <= set(pp.SELL_RULES[v.sell_rule]) | {"미룬 매도"}, 사유
    # 산 결정은 모두 "위원회 가결" — 점수 상위가 아니라 가결 행만 배분했다
    산 = [d for lg in s.log for d in lg.decisions if d["kind"] == "buy"]
    assert 산 and {d["reason"] for d in 산} == {"위원회 가결"}
    # 부결·표 순 밖 안건은 운영과 같은 사유의 skip 으로 원장에 남는다(결정 단위 잣대의 "안 산 새 후보")
    안산 = [d for lg in s.log for d in lg.decisions if d["kind"] == "skip"]
    assert any(d["reason"].startswith("위원회") or "부결" in d["reason"] or "주관" in d["reason"] for d in 안산), 안산[:3]


def test_장기보유는_손절로_팔지_않는다() -> None:
    """D 의 매도 규칙 `time` — 기간초과만(운영 0075). 손절이 선 날에도 들고 간다."""
    s, *_ = _끝까지("core_d")
    assert not any("손절" in c for lg in s.log for c in lg.sells.values())


def test_가결_행은_표_순이다() -> None:
    """운영 `committee_step` 의 `가결행` — 가결(표 순) 종목마다 기간 순 신호 행."""
    from types import SimpleNamespace

    from tests.test_core_strategy_1532 import _행

    rows = [_행(1, "2024-01-05", 10_000.0, horizon=h) for h in ("long", "short")]
    rows += [_행(2, "2024-01-05", 10_000.0, horizon="mid"), _행(3, "2024-01-05", 10_000.0)]
    dv = SimpleNamespace(rows=rows, approved={"E": [SimpleNamespace(stock_id=2), SimpleNamespace(stock_id=1)]})
    고른 = cs.committee_rows(dv, "E")  # type: ignore[arg-type]
    assert [(r.stock_id, r.horizon) for r in 고른] == [(2, "mid"), (1, "short"), (1, "long")]
    assert cs.committee_rows(dv, "D") == []  # type: ignore[arg-type]


# ── 탐색 실행(전략 깔때기 ①, 25.1906) ─────────────────────────────────────────


def test_탐색_실행은_따로_싣고_시도_수_하한은_누적이다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--explore` 는 `backtest_explore` 로 — 웹 핵심 H 화면(`backtest_core`)·판정과 섞이지 않는다.
    DSR 시도 수 하한 = 코호트 1(`REGISTERED_TRIALS`) + 탐색 변형 수(funnel.md 2.1). H 진단·짝 판정은 내지 않는다."""
    import json

    from batch.jobs import backtest as jb
    from batch.jobs import core_backtest as cj
    from tests.test_core_backtest_job_1534 import _db, 오늘

    mem = _db(monkeypatch)
    assert cj.run_core("KR", years=1, today=오늘, force=True, explore=True) == 0
    행 = mem.execute("SELECT strategy, top_n, warnings_json FROM backtest_runs ORDER BY id").dicts()
    assert [r["strategy"] for r in 행] == sorted(cs.EXPLORE_VARIANTS)
    # 위원회 변형의 저장 top_n 은 헌장 값(E 10, D 5)
    assert {r["strategy"]: r["top_n"] for r in 행} == {"core_d": 5, "core_e": 10}
    assert all(cj.WARN_EXPLORE in json.loads(r["warnings_json"]) for r in 행)
    assert mem.execute("SELECT COUNT(*) FROM batch_runs WHERE job_name = ?", [cj.JOB_NAME]).scalar() == 0
    로그 = json.loads(mem.execute("SELECT step_log FROM batch_runs WHERE job_name = ?",
                                 [cj.EXPLORE_JOB_NAME]).scalar())  # fmt: skip
    assert 로그["params"]["explore"] is True
    assert 로그["params"]["trial_floor"] == jb.REGISTERED_TRIALS + len(cs.EXPLORE_VARIANTS) == cj.explore_trial_floor()
    assert 로그["verdict"].startswith("탐색 실행") and "skipped" in 로그["holding_eq"]


def test_두_목록을_섞어_돌리지_않는다() -> None:
    from batch.jobs import core_backtest as cj

    with pytest.raises(ValueError):
        cj.run_core("KR", variants=("core_b", "core_d"))
    with pytest.raises(ValueError):
        cj.run_core("KR", variants=("core_h",), explore=True)
