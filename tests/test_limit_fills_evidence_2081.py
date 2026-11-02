"""회차 지정가 판의 결정 단위 판정·청산 짝은 **찬** 매수만 센다 (25.2081, 전략 깔때기 ④ — 25.1918 기록 "`DayLog.buys` 는 낸 비중").

예전엔 `--limit-fills` 실행(잡 `backtest_limit`)의 결정 단위 증거(`core_evidence.first_buys`)가 안 찬 회차를 산 것으로 세
판정판 줄의 대조 분위 p·BH q 가 실제로 사지 않은 종목의 성과로 나왔고, 청산 짝(`exit_pair.episodes`)은 안 찬 회차를
"엔진이 못 산 것(vanished)" 으로 셌다. 전략이 다음 날 맞추며(`_reconcile`) 어제 회차 가운데 찬 종목을 `filled_limits` 로
적고, 두 곳은 `exit_pair.filled_buys` 로 그것만 센다. 전량 모드(회차 없음)는 그대로다.
"""

from __future__ import annotations

import dataclasses

from batch.services import core_evidence as ce
from batch.services import exit_pair as xp
from tests import test_core_strategy_1532 as t32
from tests import test_limit_fills_1914 as t14


def test_안_찬_회차는_산_것으로_세지_않는다() -> None:
    """cutoff 종가 100 → 회차 100·98·96. 산 날 종가 101 이면 하나도 안 찬다 — 결정 단위 매수 0, 청산 짝 에피소드 0"""
    s, _r, 날들 = t14._전략("core_b", [100.0, 101.0, 101.0, 101.0])
    lg = t32._날별(s)[날들[1]]
    assert 1 in lg.limits and 1 in lg.buys  # 낸 비중은 그대로 기록(결정 원장 꼴)
    assert lg.filled_limits == frozenset() and xp.filled_buys(lg) == []
    assert ce.first_buys(s.log) == []
    eps, 센 = xp.episodes(s.log)
    assert eps == [] and 센["lots"] == 0 and 센["vanished"] == 0


def test_한_회차라도_차면_산_것이다() -> None:
    s, _r, 날들 = t14._전략("core_b", [100.0, 99.0, 99.0, 99.0])
    lg = t32._날별(s)[날들[1]]
    assert lg.filled_limits == frozenset({1}) and xp.filled_buys(lg) == [1]
    assert ce.first_buys(s.log) == [(lg.cutoff, 1)]
    assert xp.episodes(s.log)[1]["lots"] == 1


def test_전량_모드는_그대로_buys_를_센다() -> None:
    s, _r, 날들 = t14._전략("core_b", [100.0, 101.0, 101.0, 101.0], limit_fills=False)
    lg = t32._날별(s)[날들[1]]
    assert not lg.limits and xp.filled_buys(lg) == list(lg.buys) == [1]
    assert ce.first_buys(s.log) == [(lg.cutoff, 1)]


def test_끝까지_찬_종목은_그날_종가로_다시_센_것과_같다() -> None:
    """1532 합성 자료(정지·상장폐지 포함)로 core_b 를 끝까지 — 회차를 낸 날마다 `filled_limits` = 그날 종가 ≤ 지정가인 회차가
    하나라도 있는 종목(그 날 종가로 독립하게 다시 셈). 마지막 날은 다음 판단이 없어 비었다. 결정 단위 매수는 낸 것보다 적고,
    청산 짝의 '엔진이 못 산 것' 은 0(예전엔 안 찬 회차가 모두 여기로 셌다)"""
    s, _r, prices = t14._끝까지(True)
    회차날 = [lg for lg in s.log if lg.limits]
    assert len(회차날) >= 10
    안참 = 0
    for lg in 회차날[:-1] if 회차날[-1] is s.log[-1] else 회차날:
        다시 = {sid for sid, 회차들 in lg.limits.items()
                if (종가 := prices[sid].get(lg.day)) is not None and any(종가 <= 가 for w, 가 in 회차들 if w > 0)}  # fmt: skip
        assert lg.filled_limits == 다시, lg.day
        안참 += len(set(lg.limits) - 다시)
    assert 안참 >= 5
    if s.log[-1].limits:
        assert s.log[-1].filled_limits == frozenset()  # 모름 — 안 셈
    # 예전 셈(낸 비중 그대로) — 회차 표시를 지운 기록으로
    예전 = [dataclasses.replace(lg, limits={}) for lg in s.log]
    산, 낸 = set(ce.first_buys(s.log)), set(ce.first_buys(예전))
    찬날 = {(lg.cutoff, sid) for lg in s.log for sid in xp.filled_buys(lg)}
    assert 산 <= 찬날 and len(산) < len(낸)
    # 예전 셈은 안 찬 회차를 셌고(낸 − 찬), 그 회차가 에피소드 창(`ATTRIBUTION_EPISODE_DAYS`)을 열어 며칠 뒤 **실제로 산**
    # 날을 지웠다(산 − 낸) — 둘 다 있다
    assert 낸 - 찬날 and 산 - 낸, (len(낸 - 찬날), len(산 - 낸))
    assert xp.episodes(s.log)[1]["vanished"] == 0 < xp.episodes(예전)[1]["vanished"]


def test_찬_것만_세는_목록은_buys_순서를_지킨다() -> None:
    가짜 = type("L", (), {"buys": {3: 0.1, 1: 0.1, 2: 0.1}, "limits": {1: ((0.1, 9.0),)}, "filled_limits": frozenset()})
    assert xp.filled_buys(가짜) == [3, 2]
    옛꼴 = type("L", (), {"buys": {3: 0.1, 1: 0.1}})  # limits·filled_limits 없는 옛 기록
    assert xp.filled_buys(옛꼴) == [3, 1]
