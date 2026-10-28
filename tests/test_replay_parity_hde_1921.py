"""전략 깔때기 ③ 일치 시험 — 지주사 H·위원회 D·E (docs/funnel.md 4장 ③, docs/infra.md 25.1921).

25.1910 의 틀(tests/test_replay_parity_1910.py — 같은 합성 자료·같은 L0~L3·같은 허용 ①~⑨)을 지주사(`core_h`, 운영 H)와
위원회 탐색 변형(`core_d`·`core_e`, 운영 D·E — `cs.EXPLORE_VARIANTS`)에 넓힌다.

**25.1921 첫 측정의 142건 어긋남은 시험 틀의 입력 빠짐이었다(엔진·운영 결함 아님).** 1910 DB(`_db` →
`Client(accounts=(aid,))`)는 0077 헌장 전 계좌로 되돌리고(`charters=False`) 대상 밖 계좌를 지운다 → 운영 `run_step` 의
`헌장들` 이 비어 H 의 `holding_step` 이 날마다 "투표 회사 헌장을 읽지 못함" 으로 빈손(결정 0행·주문 0)이었다. 리플레이는
`CoreMarket` 이 코드 헌장(`co.CHARTERS`)으로 회의를 열어 같은 날 (18, buy, 지주사 합의)·(11, skip, 지주사: 추천 회사 1곳)을
냈다. D·E 도 같은 DB 면 헌장이 비어 판 1(점수 상위)로 산다(`test_헌장_없는_DB_면_지주사가_빈손이다` 가 그 원인을 적는다).

그래서 운영 쪽에 **운영이 가졌을 입력**을 넣는다(`운영입력` — 1910 의 신호 계좌에는 필요 없던 것):
  - 헌장: 마이그레이션 그대로의 A·C·D·E 헌장과 H 의 투표 회사 지문(`charter_json.voters` — 코드 헌장 지문과 같다).
    대상이 아닌 계좌는 개설일을 먼 뒤로 두어 `run_step` 이 평가·매매하지 않되 헌장은 읽힌다. H 판은 운영처럼 D·E 도
    연다 — 같은 실행에서 H 보다 먼저 돌아 회의록을 남기고, H 는 그 회의록의 표를 쓴다(25.1732 `votes_from` "minutes").
    A·C 는 매도 규칙이 사기 전 점검(`PREBUY_CHECK_RULES`)이라 열지 않는다 — H 가 그 둘의 회의를 직접 연다("meeting")
  - 조사 자료의 성과 지표: 리플레이 하루 재료가 쓴 `CoreMarket._risk` 값을 그날 cutoff 의 `performance_metrics` 행으로
    (허용 ⑧ — 같은 재료를 양쪽에. 신호 행의 MDD 와 같은 값). 리스크관리부의 MDD·변동성 백분위가 이것을 본다
  - 지수(KOSPI·KOSDAQ): 날마다 종가와 함께 `index_prices` 에 — 거시부의 국면·H 결정 원장 성과(`fill_outcomes` 의 KOSPI)
증권사·수급·감성·공시 표는 비운다 — 리플레이도 "자료 없음"(backtest.md 10장 조각 1b, funnel.md 4장 표의 H "영구 차이")이라
두 쪽이 같은 빈 부서로 판단한다. 그 영구 차이는 리플레이와 **실제 운영 자료** 사이의 것이지 이 시험의 두 길 사이의 것이 아니다.

새 허용(1910 의 ①~⑨ 는 그대로, 넓히지 않는다):
  ⑩ `ALLOW_TOPUP_BELOW_COST_DRIFT` — 비용 판에서 엔진이 든 종목을 아주 조금 더 샀는데 그 더 산 몫이 엔진 비용 장부(⑨)의
     보유 축소보다 작아, 엔진 수량이 운영 보유보다 적은 날은 0주로 베낀다(운영 매도로 베끼지 않는다 — 다른 든 종목처럼 ⑨
     흔들림으로 남는다). 1910 은 그날 멈췄다(B·G 창에는 없었다). **25.1957 엔진이 비용을 현금에서 낸 뒤 쓰이지 않아 등록을
     거뒀다**(⑨ 도 ③ 크기로 좁혀짐 — 1910 `ALLOW_ENGINE_COST_BOOKING`)
  ⑨(다) 의 매수 사유 이름 — 미리 고른 계좌는 "2부 배분" 자리가 `preselected_reason`(H "지주사 합의"·D·E "위원회 가결")이다.
     뒤집힘의 크기 잣대(그 종목 매수 금액 ≤ ⑨ 흔들림 + ③)는 그대로이고 어느 날·종목인지까지 단언한다
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from batch.core import calendar as cal
from batch.jobs import paper as job
from batch.services import core_strategy as cs
from tests import test_replay_parity_1910 as t
from tests.test_paper_job_1445 import Client, 설정

#: 대상이 아닌 계좌의 개설일 — `jobs/paper.OPEN_GUARD_FROM` 뒤라 run_step 이 평가·매매하지 않는다(헌장은 읽는다)
_닫힌_개설 = "2099-01-01T00:00:00+00:00"
#: 지주사 판에서 함께 여는 투표 회사 — 운영처럼 H 보다 먼저 돌아 회의록을 남긴다(A·C 는 사기 전 점검 규칙이라 닫음)
_함께_여는_투표회사 = {"H": ("D", "E")}

#: ⑩ 더 사기 < 엔진 비용 장부 축소 — 0주로 베낄 수 있는 모자람(보유 비중) 상한이었다(3e-5 — 2026-10-28 실측 최대 1.27e-5,
#: H·E 2024-08-29 종목 5). **25.1957 엔진이 비용을 현금에서 낸 뒤 엔진 수량이 운영 보유보다 작은 날이 없어 등록하지 않는다** —
#: 다시 나오면 1910 `_베끼기` 가 그날 멈춘다(넓히지 않는다)
#: ⑨(다) 채우기 자리의 매수 사유 다른 이름 — 운영 `run_step` 의 `preselected_reason`
_미리고른_사유 = {"core_h": frozenset({"지주사 합의"}), "core_d": frozenset({"위원회 가결"}),
               "core_e": frozenset({"위원회 가결"})}  # fmt: skip


def _db_회사(aid: str, fees: dict) -> Client:
    """마이그레이션 그대로의 계좌·헌장(`charters=True`) — 대상 계좌(H 면 투표 회사 D·E 도)만 연다."""
    c = Client(accounts=None, charters=True)
    열린 = [aid, *_함께_여는_투표회사.get(aid, ())]
    c.conn.execute(f"UPDATE paper_accounts SET created_at = ? WHERE id NOT IN ({','.join('?' * len(열린))})",
                   [_닫힌_개설, *열린])  # fmt: skip
    return t._db(aid, fees, c)


def _판단전(c: Client, rp: t.리플레이, day: str, cutoff: str) -> None:
    """그날 cutoff 의 성과 지표 — 리플레이 하루 재료가 신호 사이징·조사 자료에 쓴 값 그대로(허용 ⑧)."""
    for sid, r in sorted((rp.위험.get(cutoff) or {}).items()):
        c.conn.execute(
            "INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd, volatility_ann, sharpe,"
            " data_points, calc_version, created_at) VALUES (?, ?, '3Y', ?, ?, ?, ?, 250, 1, 't')",
            [sid, cutoff, r.get("cagr"), r.get("mdd"), r.get("volatility_ann"), r.get("sharpe")],
        )  # fmt: skip


def _지수넣기(c: Client, day: str) -> None:
    """그날 KOSPI·KOSDAQ 종가 — 그날 판단이 끝난 뒤에(종목 시세와 같은 때)."""
    for code, by in t._자료()[4].items():
        if day in by:
            c.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
                           " VALUES (?, ?, ?, 't', 't')", [code, day, by[day]])  # fmt: skip


_입력 = t.운영입력(db=_db_회사, 판단전=_판단전, 시세후=_지수넣기)
t.운영입력들.update({"weighted": _입력, "committee": _입력})
t.채우기_사유_더.update(_미리고른_사유)

변형들 = ["core_h", "core_d", "core_e"]


def _원장(variant: str, 판: str) -> tuple[t.리플레이, dict[str, t.운영하루]]:
    return t._리플레이(variant, 판), t._운영(variant, 판)


def _회의_대조(variant: str, 판: str) -> list[str]:
    """L2 아래 한 단 — 지주사는 회사 가중치·표본·추천·두 곳 이상·고름 수, 위원회는 안건·통과·가결 수가 날마다 같다."""
    rp, live = _원장(variant, 판)
    v = cs.variant_spec(variant)
    틀림 = []
    for day, h in live.items():
        dv = rp.하루[day]
        if v.selection == "weighted":
            운 = h.record.get("holding")
            if 운 is None:  # 신호 행이 없는 날 — 운영은 배분 단계를 열지 않는다
                if dv.rows:
                    틀림.append(f"{day}: 운영 지주사 기록 없음, 리플레이 신호 {len(dv.rows)}행")
                continue
            리플 = {"recommended": len(dv.ranked), "eligible": sum(x["eligible"] for x in dv.ranked),
                  "chosen": len(dv.picks), "weights": dv.company_weights,
                  "samples": {c: s["n"] for c, s in dv.company_stats.items()}}  # fmt: skip
            운 = {k: 운.get(k) for k in 리플}
        else:
            운 = h.record.get("committee")
            if 운 is None:
                if dv.rows:
                    틀림.append(f"{day}: 운영 위원회 기록 없음, 리플레이 신호 {len(dv.rows)}행")
                continue
            items, approved = cs._meet(dv, rp.s.charter)
            리플 = {"agenda": len(items), "passed": sum(i.passed for i in items), "approved": len(approved)}
            운 = {k: 운.get(k) for k in 리플}
        if 리플 != 운:
            틀림.append(f"{day}: 리플레이 {리플} ≠ 운영 {운}")
    return 틀림


# ── 시험 ─────────────────────────────────────────────────────────────────────


def test_헌장_없는_DB_면_지주사가_빈손이다() -> None:
    """25.1921 (지주사·위원회 일치 시험) — 142건 어긋남의 원인을 적는다: 1910 의 DB(헌장 전 계좌, 대상 밖 계좌 지움)로는
    운영 H 가 투표 회사 헌장을 못 읽어 사지 않는다. 운영입력(`_db_회사`)이 그 자리를 채운다."""
    rp = t._리플레이("core_h", "zero")
    day = next(d for d in t.모의 if any(x["kind"] == "buy" for lg in rp.s.log if lg.day == d for x in lg.decisions))
    cutoff, rows = rp.재료[day]
    c = t._db("H", t.비용판["zero"][0])
    for d in t.날들[: t.날들.index(day)]:
        t._시세넣기(c, d)
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(job, "frozen_ids", lambda *_a, **_k: set())
        mp.setattr(cal, "local_today", lambda _m=None: date.fromisoformat(day))
        _줄, 기록 = job.run_step(c, market="KR", trade_date=cutoff, late=False, signal_rows=rows, settings=설정,
                               price_series={}, series_unknown={}, allocate=True,
                               now=datetime.fromisoformat(f"{day}T00:30:00+00:00"))  # fmt: skip
    finally:
        mp.undo()
    assert "투표 회사 헌장을 읽지 못함" in 기록["accounts"]["H"]["holding"]["error"]
    assert 기록["accounts"]["H"]["decisions"] == 0
    assert any(x["reason"] == "지주사 합의" for lg in rp.s.log if lg.day == day for x in lg.decisions)


def test_합성_창이_지주사·위원회_갈래를_지난다() -> None:
    """25.1921 — 시험이 헛돌지 않게: 세 계좌가 창 안에서 사고(더 산 로트 포함), H·E 는 팔고, 고르지 않은 안건 skip 이
    운영과 같은 사유로 나오고, H 의 회사 가중치가 표본으로 움직이며 표를 회의록과 직접 회의 두 길로 모두 읽는다.

    **지나지 않는 갈래**: D 는 창 안에 매도가 없다(기간초과까지 가지 않음). H 의 "점수 순위 밖(상위 5)" 은 두 곳 이상 추천이
    5종목을 넘는 날이 없어 나오지 않는다. 1910 과 같이 현금 모자람·섹터 상한·위험 점검 blocked·판 뒤 쉼도 없다(투자 비중
    최대 H 0.28·D 0.34·E 0.28)"""
    for variant, 산수, 매도사유 in (("core_h", 15, {"손절", "기간초과", "추적손절"}), ("core_d", 5, set()),
                                ("core_e", 10, {"손절", "목표도달"})):  # fmt: skip
        rp = t._리플레이(variant, "zero")
        assert sum(len(o.buys) for o in rp.주문.values()) >= 산수, variant
        assert {c for lg in rp.s.log for x in lg.sells.values() for c in x} == 매도사유, variant
        assert any(더삼 for 장 in rp.장부.values() for *_x, 더삼 in 장.values()), variant  # 더 산 로트(⑦·⑨·⑩ 자리)
        결정 = {(d["kind"], d["reason"]) for lg in rp.s.log for d in lg.decisions}
        assert ("buy", next(iter(_미리고른_사유[variant]))) in 결정, variant
        if variant == "core_h":
            assert ("skip", "지주사: 추천 회사 1곳") in 결정
        else:
            assert any(k == "skip" and r.startswith("위원회:") for k, r in 결정), 결정
    live = t._운영("core_h", "zero")
    출처 = {x for h in live.values() for x in ((h.record.get("holding") or {}).get("votes_from") or {}).items()}
    assert 출처 == {("A", "meeting"), ("C", "meeting"), ("D", "minutes"), ("E", "minutes")}, 출처
    가중 = {c: w for h in live.values() for c, w in ((h.record.get("holding") or {}).get("weights") or {}).items()
            if w != 1.0}  # fmt: skip
    assert set(가중) == {"A", "B", "C", "D", "E"}, 가중  # 결정 원장 성과(`fill_outcomes`)가 가중치를 움직였다


@pytest.mark.parametrize("variant", 변형들)
def test_비용_0_이면_장부·매도·결정·매수_비중이_허용_없이_같다(variant: str) -> None:
    """25.1921 — 수수료·세금 0 판. 1910 과 같은 L0~L3 이 허용 ③(2e-7) 안에서 같고, 그 아래 회의 단(지주사 가중치·표본·
    추천·고름, 위원회 안건·통과·가결 수)도 날마다 같다. 새 허용(⑩·⑨(다) 사유 이름)은 이 판에서 한 번도 쓰이지 않는다."""
    for 단 in (t._L0, t._L1, t._L2, t._L3, _회의_대조):
        틀림 = 단(variant, "zero")
        assert not 틀림, (단.__name__, 틀림[:5])
    assert t._관문(variant, "zero") <= 1e-4  # 운영 기록 `gate_day.gate` 의 소수 넷째 자리 반올림만
    aid = cs.variant_spec(variant).account
    assert not [k for k in t.베낌_0주 if k[0] == aid and k[1] == "zero"]


@pytest.mark.parametrize("variant", 변형들)
def test_비용_판도_이름_붙은_허용_안에서_같다(variant: str) -> None:
    """25.1921 — 운영 설정 수수료·시행일 거래세 판. L0(⑦·⑨)·L1(허용 없음)·L2(⑨(다) — 미리 고른 사유 이름)·L3(⑨(나))·
    관문(⑥)·회의 단(허용 없음)이 1910 의 상한 그대로 지난다.

    25.1957 뒤(엔진이 비용을 현금에서): ⑨(다) 뒤집힘·⑩ 0주 베낌 없음, 보유 비중 차 최대 H 1.2e-8·D 1.5e-8·E 1.9e-8(③ 크기),
    더 산 로트 원가 상대차 최대 H 1.5e-7. 아래는 예전 엔진의 2026-10-28 실측(기록으로 남김): 세 계좌 모두 2024-08-28 종목 5(든 종목 채우기)에서 ⑨(다) 뒤집힘 — 리플레이는 상한에 ③ 크기로 붙어
    "최소 주문 단위 미만"·"종목 비중 상한 도달", 운영은 ⑨ 만큼 아래라 아주 작은 매수. 다음 날(08-29) H·E 의 엔진이 그 종목을
    아주 조금 더 샀는데 비용 장부 축소가 더 커 엔진 수량이 운영보다 1.27e-5(비중) 적었다 → ⑩ 0주 베낌. 보유 비중 차 최대
    H 4.9e-5·D 2.9e-6·E 5.0e-5(⑨ 2.5e-4 안), 더 산 로트 원가 상대차 최대 H 3.9e-7(⑦ 1e-4 안), 관문 수익률 차 최대 H 0.022·
    E 0.022%p(⑥ 0.05 안)"""
    for 단 in (t._L0, t._L1, t._L2, t._L3, _회의_대조):
        틀림 = 단(variant, "fees")
        assert not 틀림, (단.__name__, 틀림[:5])
    assert t._관문(variant, "fees") <= t.ALLOW_GATE_RETURN_PCTP
    # 25.1957 — 엔진이 비용을 현금에서 낸 뒤 ⑨(다) 뒤집힘(예전 2024-08-28 종목 5)·⑩ 0주 베낌(예전 08-29 종목 5)이 없다
    본 = {(d, sid) for (v, d), 쌍 in t.견딘날.items() if v == variant for sid in 쌍}
    assert 본 == set(), 본
    aid = cs.variant_spec(variant).account
    assert not [k for k in t.베낌_0주 if k[0] == aid and k[1] == "fees"]
