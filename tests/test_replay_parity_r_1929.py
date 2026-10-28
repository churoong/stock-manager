"""전략 깔때기 ③ 일치 시험 — 무작위 대조 R1·R2 (docs/funnel.md 4장 ③, docs/infra.md 25.1929).

25.1910 의 틀(tests/test_replay_parity_1910.py — 같은 합성 자료·같은 L0~L3·같은 허용 ①~⑨)을 무작위 대조 변형
(`core_r1`·`core_r2`, 운영 R1·R2 — 씨앗 `계좌|시장|신호 종목` 무작위 대체·시가 일괄 진입·core 매도)에 넓힌다.

**첫 측정의 어긋남(L2 122일·L3 88건)은 시험 틀의 입력 빠짐이었다(엔진·운영 결함 아님).** 1910 DB 에는
`universe_members` 가 없어 운영 `UNIVERSE_SQL` 모집단이 비었다 → `random_substitute` 가 후보 0개로 날마다 모든 자리를 비워
운영 R 은 한 번도 사지 않았다(결정 0행). 리플레이는 `CoreMarket.pool`(시점 유니버스 — 이 자료는 30종목 전부)에서 뽑았다.
그래서 운영 쪽에 **운영이 가졌을 입력**을 넣는다(`운영입력` — 1921 과 같은 자리):
  - 모집단: 30종목 편입 스냅샷 하나(`universe_members`, 워밍업 첫날) — 운영 `UNIVERSE_SQL` 은 가장 늦은 스냅샷만 읽는다
  - 든 대체 종목: 운영 `held_substitutes` 는 **찬 매수 주문의 근거**(`rationale_json` 의 `substitute_of`)로 든 대체 종목을
    잇는다. 베낀 체결(허용 ①)의 주문에는 근거가 없어, 그날 운영이 낸 그 종목 매수 주문의 근거를 붙인다(운영에서는 바로 그
    주문이 찬다). 빼면 든 대체 종목을 잇지 못하고 씨앗을 다시 봐 R2 가 2024-01-30 부터 갈린다(되돌리기 확인 — R1 은
    씨앗 첫 후보가 곧 든 종목이라 이 창에서는 우연히 같다)
  - 판 뒤 쉼: 운영 `최근판`(체결 장부의 매도) — 베낀 매도로 이미 같다. 모집단·쉼 모두 시세 묵음 잣대는 1910 그대로

입력을 넣은 뒤 **무작위 뽑기 자체는 두 길이 날마다 같다**(`_무작위_대조` — {신호 종목: 대체 종목}·빈 자리 까닭, 세 판 모두).
시가 일괄 진입 = 합성 자료의 시가 = 종가라 비용 0 판에서 "시가 대신 t 종가"(funnel.md 4장 표 R "근사") 차이는 없다.

새 허용(1910 의 ①~⑨·1921 의 ⑩ 은 그대로, 넓히지 않는다):
  ⑪ `ALLOW_CAP_EDGE_FLIP` — 종목 상한 끝 뒤집힘(비용 0 판에도 나온다). R 의 대체 행은 신호 비중을 물려받는데 신호 비중
     10% = 종목 상한이라, 산 날 정수 주 내림으로 상한보다 1주 못 미치게(3.4e-10) 들고, 베끼기(허용 ①)의 정수 주 반올림이
     운영 보유를 상한 위(+6e-10~1.4e-9)로 올린다 → 다음 날 리플레이 skip "최소 주문 단위 미만"(남은 몫 < 1주) ↔ 운영 skip
     "종목 비중 상한 도달". 둘 다 사지 않고 사유만 다르다. 허용 ③(정수 주)의 결정 자리 — B·G 창에서는 목표가 상한 아래라
     나오지 않았다. 잣대: 같은 종목 줄 한 쌍, 둘 다 skip·held, 사유가 그 둘, 그날 양쪽 매수 없음, 두 비중 모두 상한 ± 1e-8
     (1주 1.1~1.6e-8 아래), 두 비중 차 ≤ 허용 ③. 어느 날·종목인지 단언한다
  ⑫ `ALLOW_MARKET_SLIPPAGE_*` — 슬리피지 판(`slip`: 운영 설정 수수료 + 엔진 기본 슬리피지 0.10%). R 은 사고(시가 일괄)
     팔(시장가) 때 모두 슬리피지를 낸다 — 운영은 체결 현금에서, 엔진은 회전율 비용으로 자본 전체에 비례로(⑨ 와 같은 기제,
     크기만 크다). 보유 비중 차·더 산 로트 원가 근사·관문 수익률 차가 ⑨·⑦·⑥ 상한을 넘어(실측 아래) 판마다 따로 둔다.
     결정(L1·L2·L3)은 ⑨(다) 그대로의 잣대로 같다
  ⑨(다) 의 매수 사유 이름 — R 의 채우기 자리 매수 사유는 "무작위 대조"(1921 의 "지주사 합의"·"위원회 가결" 과 같은 자리)

**찾은 것(고치지 않음 — 25.1700 R 고르기 규칙 지문, R 판 고정)**: 운영 `jobs/paper.held_substitutes` 는 **지금 든 종목의 찬
매수 주문 전부**를 읽어, 판 뒤 다시 산 종목의 **옛 보유 회차** 자리(`substitute_of`)까지 잇는다. 리플레이(`core_strategy`
`_buys` 의 `든대체`)는 지금 로트(판 뒤 새로 잡힌)의 자리만 잇는다. 이 창의 R2 종목 23(자리 8 → 판 → 자리 27 → 판 → 자리 8)
에서 운영 든 대체 장부가 리플레이보다 한 자리 더 많은 날이 있지만, 그동안 그 옛 자리 신호가 다시 나오지 않아 뽑기는 같았다
(`test_운영_든_대체_장부가_옛_보유_자리를_남긴다`). 다시 나오면 운영은 한 종목을 두 자리로 잇는다(`xfail(strict=True)` 손 사례)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.jobs import paper as job
from batch.services import backtest as bt
from batch.services import core_strategy as cs
from batch.services import paper as pp
from tests import test_replay_parity_1910 as t
from tests.test_paper_job_1445 import Client, 설정

#: 무작위 대조 모집단 스냅샷 날짜 — 워밍업 첫날(운영 `UNIVERSE_SQL` 은 가장 늦은 스냅샷 하나만 읽는다)
_스냅샷 = t.날들[0]

#: ⑪ 종목 상한 끝 뒤집힘 — 두 쪽 보유 비중이 종목 상한에서 이 안. 2026-10-28 실측 리플레이 −3.5e-10·운영 +1.45e-9 (R1
#: 2024-08-28 종목 6, R2 2024-05-30 종목 19). 상한은 1주(1.1~1.6e-8)보다 작게 — 1주 넘게 떨어진 자리의 뒤집힘은 다른 원인이다
ALLOW_CAP_EDGE_FLIP = 1e-8
_상한끝_사유 = frozenset({"최소 주문 단위 미만", "종목 비중 상한 도달"})
#: ⑪ 을 쓴 자리 (변형, 판, 결정일, 종목) → (리플레이 비중 − 상한, 운영 비중 − 상한) — 시험이 어느 날·종목인지 단언한다
상한끝: dict[tuple[str, str, str, int], tuple[float, float]] = {}

#: ⑫ 슬리피지 판의 상한 — 2026-10-28 실측(150 거래일)의 약 두 배. 보유 비중 차 최대 R1 2.60e-4·R2 3.38e-4(⑨ 2.5e-4 넘음),
#: 더 산 로트 원가 상대차 최대 R1 1.02e-4·R2 7.9e-5(⑦ 1e-4 넘음), 관문 수익률 차 최대 R1 0.033·R2 0.063%p(⑥ 0.05 넘음)
ALLOW_MARKET_SLIPPAGE_BOOKING = 7e-4
ALLOW_MARKET_SLIPPAGE_AVERAGING_REL = 2.5e-4
ALLOW_MARKET_SLIPPAGE_GATE_PCTP = 0.13

#: 슬리피지 판 — 운영 설정 수수료(1910 `fees` 와 같음) + 엔진 기본 슬리피지(`bt.Costs.slippage_pct` 0.10%, 운영 `costs_for` 와
#: 같은 값). 베끼기는 R 의 시장가 매수·매도에 그 슬리피지를 `make_fill` 로 붙인다
_비용 = {"kr_buy_pct": 0.015, "kr_sell_pct": 0.015}
t.비용판.setdefault("slip", (_비용, bt.Costs.for_country("KR", _비용, {})))
t.판_상한.setdefault("slip", (ALLOW_MARKET_SLIPPAGE_AVERAGING_REL, ALLOW_MARKET_SLIPPAGE_BOOKING))

#: id(리플레이) → 결정일 → 운영 `held_substitutes`(그날 run_step 바로 앞) — 리플레이는 (변형, 판)마다 하나다
운영든대체: dict[tuple[int, str], dict[int, int]] = {}


def _db_무작위(aid: str, fees: dict) -> Client:
    """1910 DB + 무작위 대조의 모집단(`universe_members` 편입 개별주 — 운영 `UNIVERSE_SQL`)."""
    c = t._db(aid, fees)
    for u in t._자료()[0]:
        c.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, ?, 1, 'KRW', 't')", [_스냅샷, u["stock_id"]])  # fmt: skip
    return c


def _판단전(c: Client, rp: t.리플레이, day: str, cutoff: str) -> None:
    """베낀 매수 체결에 그날 운영이 낸 그 종목 첫 매수 주문의 근거(`substitute_of`)를 붙이고, 운영이 오늘 이을 든 대체
    종목 장부를 적는다."""
    c.conn.execute(
        "UPDATE paper_orders SET rationale_json = (SELECT o.rationale_json FROM paper_orders o"
        " WHERE o.account_id = paper_orders.account_id AND o.stock_id = paper_orders.stock_id"
        " AND o.decided_on = paper_orders.decided_on AND o.side = 'buy' AND o.tranche < 90 ORDER BY o.id LIMIT 1)"
        " WHERE side = 'buy' AND tranche = ? AND rationale_json IS NULL", [t._베낌_회차["buy"]])  # fmt: skip
    aid = rp.s.v.account
    든 = pp.positions([f for f in job._fills(c)[0] if f.account_id == aid])
    운영든대체[(id(rp), day)] = job.held_substitutes(c, aid, set(든))


def _없음(_c: Client, _day: str) -> None:
    return None


def _상한끝(variant: str) -> Any:
    """⑪ 거르개 — 1910 `_L2` 가 ⑨(다) 앞에 부른다(`t.줄쌍_더`)."""

    def 거르개(판: str, day: str, 리플만: list, 운만: list, h: t.운영하루) -> set[int]:
        rp = t._리플레이(variant, 판)
        산 = t._날별(rp)[day].buys
        상한 = 설정["max_weight_per_stock"] / 100
        견딘: set[int] = set()
        for sid in {x[0] for x in 리플만} & {x[0] for x in 운만}:
            r = [x for x in 리플만 if x[0] == sid]
            o = [x for x in 운만 if x[0] == sid]
            if len(r) != 1 or len(o) != 1:
                continue
            (_a, rk, rr, rh), (_b, ok, orr, oh) = r[0], o[0]
            if (rk, ok) != ("skip", "skip") or {rr, orr} != _상한끝_사유 or not (rh is True and oh is True):
                continue
            if sid in 산 or sid in h.buys:
                continue
            wr, wl = rp.비중[day].get(sid, 0.0), h.weights.get(sid, 0.0)
            if (abs(wr - 상한) <= ALLOW_CAP_EDGE_FLIP and abs(wl - 상한) <= ALLOW_CAP_EDGE_FLIP
                    and abs(wr - wl) <= t.ALLOW_NOTIONAL_EQUITY):  # fmt: skip
                견딘.add(sid)
                상한끝[(variant, 판, day, sid)] = (wr - 상한, wl - 상한)
        return 견딘

    return 거르개


변형들 = ["core_r1", "core_r2"]
t.운영입력들["random"] = t.운영입력(db=_db_무작위, 판단전=_판단전, 시세후=_없음)
t.채우기_사유_더.update({v: frozenset({"무작위 대조"}) for v in 변형들})
t.줄쌍_더.update({v: _상한끝(v) for v in 변형들})


def _무작위_대조(variant: str, 판: str) -> list[str]:
    """L2 아래 한 단 — 그날 무작위 대체 {신호 종목: 대체 종목} 과 빈 자리 까닭(운영 `기록["random"]`·`["random_empty"]`)이
    날마다 같다. 허용 없음."""
    rp, live = t._리플레이(variant, 판), t._운영(variant, 판)
    로그 = t._날별(rp)
    틀림 = []
    for day, h in live.items():
        리플 = {k: 로그[day].random.get(k) for k in ("random", "random_empty")}
        운 = {k: h.record.get(k) for k in ("random", "random_empty")}
        if 리플 != 운:
            틀림.append(f"{day}: 리플레이 {리플} ≠ 운영 {운}")
    return 틀림


def _리플레이_든대체(rp: t.리플레이) -> dict[str, dict[int, int]]:
    """결정일 → 리플레이가 그날 이은 든 대체 종목 장부 {신호 종목: 대체 종목} — `CoreStrategy._buys` 의 `든대체` 를 로그로
    다시 낸다: 로트의 자리는 지금 보유 회차에서 마지막으로 산 날의 대체 자리(판 뒤 다시 사면 새 로트), 나중에 산 것이 이긴다."""
    자리: dict[int, tuple[str, int]] = {}  # 대체 종목 → (마지막 매수일, 신호 자리)
    out: dict[str, dict[int, int]] = {}
    for lg in rp.s.log:
        자리 = {c: x for c, x in 자리.items() if c in lg.held}  # 판(또는 못 산) 로트는 지운다
        out[lg.day] = {k: c for c, (_d, k) in sorted(자리.items(), key=lambda kv: kv[1][0])}
        바꿈 = {c: int(k) for k, c in (lg.random.get("random") or {}).items()}
        for c in lg.buys:
            자리[c] = (lg.day, 바꿈[c])
    return out


# ── 시험 ─────────────────────────────────────────────────────────────────────


def test_합성_창이_무작위_대조_갈래를_지난다() -> None:
    """25.1929 (무작위 대조 일치 시험) — 시험이 헛돌지 않게: 두 계좌가 사고(새로·더 사기) 팔고, 든 대체 종목을 잇고,
    판 뒤 쉼으로 자리를 비우고(빈 자리 까닭), R1 은 섹터 상한 skip 까지 낸다. R1·R2 의 대체 종목이 갈린다(씨앗이 계좌).

    **지나지 않는 갈래**: 자료 문제(묵은 종가·멈춤·개별주 아님)로 다음 후보로 넘어가기·`RANDOM_MAX_TRIES` 소진·유니버스에서
    빠진 든 대체 종목 — 합성 자료는 날마다 30종목 시세가 다 있다. 현금 모자람·위험 점검 blocked·하루 손실 관문 막힘도 없다
    (투자 비중 최대 R1 0.59·R2 0.55)"""
    for variant, 매도사유, 섹터 in (("core_r1", {"목표도달", "손절", "기간초과"}, True),
                                ("core_r2", {"목표도달", "손절"}, False)):  # fmt: skip
        rp = t._리플레이(variant, "zero")
        assert sum(len(o.buys) for o in rp.주문.values()) >= 70, variant
        assert {c for lg in rp.s.log for x in lg.sells.values() for c in x} == 매도사유, variant
        결정 = {(d["kind"], d["reason"], (d.get("detail") or {}).get("held")) for lg in rp.s.log for d in lg.decisions}
        assert {("buy", "무작위 대조", False), ("buy", "무작위 대조", True)} <= 결정, 결정  # 새로 사기·더 사기
        assert (("skip", "섹터 상한 도달", False) in 결정) is 섹터, 결정
        빈 = {w for lg in rp.s.log for w in (lg.random.get("random_empty") or {}).values()}
        assert 빈 and all(w.endswith(f"판 뒤 {pp.REBUY_COOLDOWN_SESSIONS}거래일 안") for w in 빈), 빈
        # 든 대체 종목을 이은 날(그날 대체가 판단 직전 보유) — 씨앗을 다시 보지 않는 길
        assert any(c in lg.held for lg in rp.s.log for c in (lg.random.get("random") or {}).values()), variant
    r1, r2 = (t._날별(t._리플레이(v, "zero"))["2024-01-24"].random["random"] for v in 변형들)
    assert r1.keys() == r2.keys() and r1 != r2, (r1, r2)


@pytest.mark.parametrize("variant", 변형들)
def test_비용_0_이면_장부·매도·결정·매수_비중이_허용_없이_같다(variant: str) -> None:
    """25.1929 — 수수료·세금·슬리피지 0 판. L0~L3 과 그 아래 무작위 대체(대체 종목·빈 자리 까닭)가 날마다 허용 ③(2e-7)
    안에서 같다 — 시가 일괄 진입의 "시가 대신 t 종가" 는 합성 자료(시가 = 종가)라 차이가 없다. 견딘 것은 ⑪ 한 자리뿐."""
    for 단 in (t._L0, t._L1, t._L2, t._L3, _무작위_대조):
        틀림 = 단(variant, "zero")
        assert not 틀림, (단.__name__, 틀림[:5])
    assert t._관문(variant, "zero") <= 1e-4  # 운영 기록 `gate_day.gate` 의 소수 넷째 자리 반올림만
    aid = cs.variant_spec(variant).account
    assert not [k for k in t.베낌_0주 if k[0] == aid]  # ⑩ 은 R 에 쓰지 않는다(상한 등록 없음 — 나오면 1910 처럼 멈춘다)
    본 = {(d, sid) for (v, 판, d, sid) in 상한끝 if v == variant and 판 == "zero"}
    assert 본 == {"core_r1": {("2024-08-28", 6)}, "core_r2": {("2024-05-30", 19)}}[variant], 본
    # 리플레이는 상한 바로 아래(1주 못 미침), 운영은 베끼기 반올림으로 상한 바로 위
    assert all(r < 0 < o for (v, 판, _d, _s), (r, o) in 상한끝.items() if v == variant and 판 == "zero")


#: 비용 판마다 ⑨(다) 채우기 뒤집힘을 견딘 자리 — 2026-10-28 실측. 같음(==) 으로 단언한다(사라져도 알아채게, 25.1918)
#: 모두 든 종목을 상한(10%)까지 채우는 자리 — 비용 0 판 ⑪ 자리(R1 08-28 종목 6·R2 05-30 종목 19)도 비용 판에서는 ⑨ 크기라 ⑨(다)
_견딘_자리 = {
    ("core_r1", "fees"): {("2024-08-28", 6)},
    ("core_r2", "fees"): {("2024-03-04", 19), ("2024-06-17", 19)},
    ("core_r1", "slip"): {("2024-03-04", 4), ("2024-05-30", 4), ("2024-08-28", 6)},
    ("core_r2", "slip"): {("2024-03-04", 19), ("2024-05-30", 19), ("2024-06-17", 19)},
}


@pytest.mark.parametrize("판", ["fees", "slip"])
@pytest.mark.parametrize("variant", 변형들)
def test_비용_판도_이름_붙은_허용_안에서_같다(variant: str, 판: str) -> None:
    """25.1929 — `fees`(운영 설정 수수료·시행일 거래세, 슬리피지 0 — 1910 과 같은 판)는 1910 의 상한 그대로(⑦·⑨·⑥),
    `slip`(+ 슬리피지 0.10%, R 의 시장가 매수·매도)은 ⑫ 상한으로. L1(허용 없음)·L2(⑨(다) — 사유 "무작위 대조")·L3(⑨(나))·
    무작위 대체(허용 없음)는 두 판 모두 같다."""
    for 단 in (t._L0, t._L1, t._L2, t._L3, _무작위_대조):
        틀림 = 단(variant, 판)
        assert not 틀림, (단.__name__, 틀림[:5])
        if 단 is t._L2:  # 견딘날은 (변형, 결정일) 열쇠라 판끼리 덮인다 — L2 바로 뒤에 읽는다
            본 = {(d, sid) for (v, d), 쌍 in t.견딘날.items() if v == variant for sid in 쌍}
    assert 본 == _견딘_자리[(variant, 판)], 본
    assert t._관문(variant, 판) <= (t.ALLOW_GATE_RETURN_PCTP if 판 == "fees" else ALLOW_MARKET_SLIPPAGE_GATE_PCTP)
    assert not [
        k for k in 상한끝 if k[0] == variant and k[1] == 판
    ]  # ⑪ 은 비용 판에서 쓰이지 않았다(⑨(다) 가 먼저 크다)


def test_슬리피지_판은_1910_상한을_넘는다() -> None:
    """25.1929 ⑫ 가 따로 있어야 하는 까닭 — 슬리피지 판의 실측이 1910 상한을 넘는다(⑫ 를 지우고 ⑦·⑨ 로 재면 L0 이 깨진다).
    `fees` 판은 1910 상한 안이다(⑫ 는 슬리피지 몫만)."""

    def 최대(variant: str, 판: str) -> tuple[float, float]:
        rp, live = t._리플레이(variant, 판), t._운영(variant, 판)
        흔들림 = max(t._흔들림(rp, h, s) for d, h in live.items() for s in set(rp.비중[d]) | set(h.weights))
        원가 = max(abs(x[2] / h.lots[s][2] - 1) for d, h in live.items() for s, x in rp.장부[d].items() if x[3])
        return 흔들림, 원가

    s1, s2 = 최대("core_r1", "slip"), 최대("core_r2", "slip")
    assert max(s1[0], s2[0]) > t.ALLOW_ENGINE_COST_BOOKING and max(s1[1], s2[1]) > t.ALLOW_COST_AVERAGING_REL
    assert max(t._관문(v, "slip") for v in 변형들) > t.ALLOW_GATE_RETURN_PCTP
    for v in 변형들:
        f = 최대(v, "fees")
        assert f[0] <= t.ALLOW_ENGINE_COST_BOOKING and f[1] <= t.ALLOW_COST_AVERAGING_REL, (v, f)


def test_운영_든_대체_장부가_옛_보유_자리를_남긴다() -> None:
    """25.1929 찾은 것 — 운영 `held_substitutes` 는 지금 든 종목의 찬 매수 주문을 **보유 회차와 무관하게** 다 읽는다. R2 종목
    23 은 자리 8(01-29 매수 → 02-15 손절) → 자리 27(04-08 → 06-04 목표도달, 07-03 → 07-29 손절) → 자리 8(08-12) 로 다시 사여,
    운영 장부에는 옛 회차 자리가 함께 남는다. 리플레이(지금 로트의 자리)와 갈린 날은 그 셋뿐이고, 그동안 옛 자리 신호(8·27)가
    다시 나오지 않아 뽑기(`_무작위_대조`)는 같았다. R1 은 갈린 날이 없다."""
    for variant in 변형들:
        rp = t._리플레이(variant, "zero")
        t._운영(variant, "zero")
        리플 = _리플레이_든대체(rp)
        갈림 = {d: (리플[d], 운영든대체[(id(rp), d)]) for d in t.모의[:-1] if 리플[d] != 운영든대체[(id(rp), d)]}
        if variant == "core_r1":
            assert not 갈림, list(갈림.items())[:3]
            continue
        # 운영에만 있는 자리는 늘 종목 23 의 옛 회차 자리
        더 = {(d, k, c) for d, (r, o) in 갈림.items() for k, c in o.items() if r.get(k) != c}
        assert {c for _d, _k, c in 더} == {23} and all(set(r.items()) <= set(o.items()) for r, o in 갈림.values())
        assert {k for _d, k, _c in 더} == {8, 27}, 더
        assert min(d for d, *_ in 더) == "2024-04-09" and len(갈림) == len({d for d, *_ in 더}), 갈림
        # 그날들에 옛 자리 신호가 없었다 — 그래서 뽑기가 같았다
        assert not [d for d, _k, _c in 더 if _k in {r.stock_id for r in rp.재료[d][1]}]
        # 25.1931 — run_step 감지 호출 자리: 장부 상태(`substitute_overlap`)는 R2 에 51일(2024-04-09~08-29) 켜지고, 뽑기가
        # 기울 수 있는 날(`_hit` — 옛 자리 신호가 나온 날)은 0. R1 은 둘 다 없다(위 `continue`)
        live = t._운영(variant, "zero")
        상태 = {d: h.record.get("substitute_overlap") for d, h in live.items() if h.record.get("substitute_overlap")}
        assert len(상태) == 51 and min(상태) == "2024-04-09" and max(상태) == "2024-08-29", (len(상태), min(상태))
        assert {str(v) for v in 상태.values()} == {"{'23': [8, 27]}"}
        assert not [d for d, h in live.items() if h.record.get("substitute_overlap_hit")]
    assert not [d for d, h in t._운영("core_r1", "zero").items() if h.record.get("substitute_overlap")]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,  # 25.1930 — 다른 예외(예전엔 외래키 IntegrityError)로는 xfail 이 지나가지 못하게
    reason="25.1929 찾은 것 — 운영 held_substitutes 가 옛 보유 회차의 자리를 잇는다(고치지 않음: "
    "25.1700 R 고르기 규칙 지문·R 판 고정, docs/infra.md 25.1929)",
)
def test_든_대체_종목은_지금_보유_회차의_자리만_잇는다() -> None:
    """손 사례 — 종목 1 을 자리 8 로 샀다 팔고, 자리 27 로 다시 샀다. 지금 든 1 은 자리 27 의 대체다(리플레이 `Lot.substitute_of`).
    운영은 {8: 1, 27: 1} — 자리 8 신호가 다시 오면 1 을 자리 8 로도 잇는다. 종목 상한(10%) 때문에 그 종목을 상한 위로 사지는
    않는다(`build_portfolio` 가 종목별로 묶는다). 실제 피해는 자리 8 이 새 무작위 종목을 뽑지 못하는 것 — R 의 투자 종목 수·
    속도가 줄어 H 관문 "R 대비" 기준선이 기운다(25.1930 바로잡음 — 처음엔 "두 자리 돈" 이라 적었다 `[확인필요: 실행으로]`)."""
    c = Client(accounts=("R2",))

    def 주문(oid: int, side: str, 자리: int | None, 날: str) -> None:
        근거 = json.dumps({"signal": {"criteria": [{"substitute_of": 자리}]}}) if 자리 is not None else None
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
            " decided_on, fill_session, tranche, reason, rule_version, status, created_at, rationale_json) VALUES (?, 'R2',"
            " 'KR', 1, ?, 'market', NULL, 1, 'KRW', ?, ?, ?, 't', 1, 'filled', 't', ?)",
            [oid, side, 날, 날, 1 if side == "buy" else 0, 근거])  # fmt: skip

    주문(1, "buy", 8, "2024-01-29")
    주문(2, "sell", None, "2024-02-15")
    주문(3, "buy", 27, "2024-04-08")
    # 종목 1 — 시험 Client 의 stocks 표에 있는 종목(25.1930: 예전 5 는 외래키로 깨져 결함이 아닌 까닭으로 xfail 했다)
    assert job.held_substitutes(c, "R2", {1}) == {27: 1}


def test_든_대체_겹침_감지() -> None:
    """25.1930 (25.1929 교차검증 [권고]) — 고르기 논리(지문)는 R 판 올림 때 고치고, 그 전까지 한 종목이 두 자리 이상인 날을
    실행 기록(`accounts.<R>.substitute_overlap`)·경고 로그에 남긴다. 위 손 사례의 운영 결과 {8: 1, 27: 1} 이 잡힌다"""
    assert job.substitute_overlap({8: 1, 27: 1, 3: 2}) == {"1": [8, 27]}
    assert job.substitute_overlap({8: 1, 27: 2}) == {}
    assert job.substitute_overlap({}) == {}


def test_옛_자리_신호가_나온_날만_닿음() -> None:
    """25.1931 (25.1930 교차검증 [권고]) — 장부 상태는 그 종목을 드는 동안 날마다 켜져, "보이면 R 판 올림을 앞당긴다" 문턱이
    해 없는 날에도 걸렸다. 지금 회차의 자리(가장 나중 매수의 `substitute_of`) 신호는 정상이라 빼고, **옛 자리** 신호가 나온
    날만 `_hit` 이다. 손 사례 DB(종목 1: 자리 8 → 판 → 자리 27)로 지금 회차의 자리를 읽는다"""
    겹침 = {"1": [8, 27]}
    assert job.substitute_overlap_hits(겹침, {1: 27}, {8, 99}) == {"1": [8]}
    assert job.substitute_overlap_hits(겹침, {1: 27}, {27, 99}) == {}
    assert job.substitute_overlap_hits(겹침, {}, {27}) == {"1": [27]}  # 지금 회차를 모르면 넓게
    c = Client(accounts=("R2",))
    for oid, side, 자리, 날 in ((1, "buy", 8, "2024-01-29"), (2, "sell", None, "2024-02-15"), (3, "buy", 27, "2024-04-08")):
        근거 = json.dumps({"signal": {"criteria": [{"substitute_of": 자리}]}}) if 자리 is not None else None
        c.conn.execute(
            "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
            " decided_on, fill_session, tranche, reason, rule_version, status, created_at, rationale_json) VALUES (?, 'R2',"
            " 'KR', 1, ?, 'market', NULL, 1, 'KRW', ?, ?, ?, 't', 1, 'filled', 't', ?)",
            [oid, side, 날, 날, 1 if side == "buy" else 0, 근거])  # fmt: skip
    assert job.current_substitute_slots(c, "R2", {1}) == {1: 27}
    assert job.substitute_overlap(job.held_substitutes(c, "R2", {1})) == {"1": [8, 27]}
    from batch.services import desk

    p = {"accounts": {"R2": {"substitute_overlap": {"1": [8, 27]}}}}
    assert not [x for x in desk.checks(halted=False, paper=p, broker=None, recon_bad=[], warnings=0) if "옛 대체" in x["text"]]
    p["accounts"]["R2"]["substitute_overlap_hit"] = {"1": [8]}
    assert [x["level"] for x in desk.checks(halted=False, paper=p, broker=None, recon_bad=[], warnings=0)
            if "옛 자리 신호" in x["text"]] == ["normal"]
    # run_step 이 부르는 감지 함수 그대로(25.1932 — 양성 경로): 옛 자리 8 신호가 나온 날 `_hit`, 지금 회차 27 이면 없음
    def 행(sid: int) -> Any:
        return type("행", (), {"stock_id": sid})()

    기록: dict = {}
    job.record_substitute_overlap(c, "R2", job.held_substitutes(c, "R2", {1}), [행(8), 행(99)], 기록)
    assert 기록 == {"substitute_overlap": {"1": [8, 27]}, "substitute_overlap_hit": {"1": [8]}}
    글 = [x["text"] for x in desk.checks(halted=False, paper={"accounts": {"R2": 기록}}, broker=None, recon_bad=[],
                                        warnings=0) if "옛 자리 신호" in x["text"]]  # fmt: skip
    assert 글 and "(1건)" in 글[0] and "{" not in 글[0], 글  # 내부 번호를 싣지 않는다
    기록 = {}
    job.record_substitute_overlap(c, "R2", job.held_substitutes(c, "R2", {1}), [행(27)], 기록)
    assert 기록 == {"substitute_overlap": {"1": [8, 27]}}
