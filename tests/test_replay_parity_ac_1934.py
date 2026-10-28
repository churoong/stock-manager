"""전략 깔때기 ⑤ 일치 시험 — 가치 A·모멘텀 C 의 매도·사기 전 점검 (docs/funnel.md 4장 ⑤, docs/infra.md 25.1934).

25.1910 의 틀(tests/test_replay_parity_1910.py — L0~L3)과 25.1921 의 회사 입력(tests/test_replay_parity_hde_1921.py — 헌장·조사
자료·지수, 회의 단)을 사기 전 점검 계좌(`core_a`·`core_c`, 운영 A·C)에 넓힌다. 비용 0 판만.

운영 쪽에 **운영이 가졌을 입력**을 더 넣는다(그날 `run_step` 바로 앞, cutoff 까지의 것만):
  - `financials`: 합성 재무 스냅샷 가운데 접수일 ≤ cutoff 인 것을 운영 표처럼 (종목, 사업연도, 보고서, 기준)마다 늦게 접수된 한
    줄로 — 운영 `sell_flags.enrich` 의 영업이익 적자 전환이 읽는다. 리플레이는 같은 스냅샷을 `cb.pit_fin_rows` 로 자른다(두 길)
  - `scores`: 리플레이 하루 재료의 그 주 종합 점수(`DayView.total_scores`)를 cutoff 날 행으로 — 운영 `SCORE_NOW_SQL`·점수 행
    (같은 잣대: 가중·판·감성 가중이 늘 같다). 허용 ⑧ 과 같은 뜻(같은 재료를 양쪽에)
  - `sentiment_scores`·`universe_members` 는 비운다 — 리플레이도 감성은 늘 빈 답(영구 차이), 유니버스를 자르지 않는 판이라
    점검 없음. 그 영구 차이는 리플레이와 실제 운영 자료 사이의 것이지 이 시험의 두 길 사이의 것이 아니다

합성 자료(1910 `_자료`)는 재무 스냅샷이 모두 모의 창 앞(2023-03-15 까지)이라 창 안에서 재무악화가 날 수 없다 — 이 파일에서만
FY2023 사업보고서를 창 안(2024-03-15)에 접수시키고 짝수 종목은 적자 전환으로 둔다(`_자료_ac`, 모듈 동안만 바꿔 끼운다 —
1910·1921 의 캐시와 섞이지 않게 변형 이름 `core_a`·`core_c` 만 쓴다).
"""

from __future__ import annotations

import copy
import json
from functools import cache

import pytest

from batch.services import core_backtest as cb
from batch.sources import dart
from tests import test_replay_parity_1910 as t
from tests import test_replay_parity_hde_1921 as hde

#: FY2023 사업보고서 접수일(모의 창 안) — 짝수 종목은 영업이익 적자 전환
_FY2023_접수 = "2024-03-15"
_원자료 = t._자료


@cache
def _자료_ac() -> tuple:
    universe, prices, turn, snaps, idx = _원자료()
    snaps = copy.deepcopy(snaps)
    for sid, rows in snaps.items():
        전 = max(rows, key=lambda r: r["fiscal_year"])
        값 = dict(전["values"])
        값["revenue"] *= 1.05
        값["operating_income"] = -0.3 * 값["operating_income"] if sid % 2 == 0 else 값["operating_income"] * 1.05
        값["net_income"] = 값["operating_income"] * 0.7
        rows.append({"as_of_date": _FY2023_접수, "fiscal_year": 2023, "receipt_no": f"2023{sid:04d}",
                     "consolidated": True, "values": 값})  # fmt: skip
    return universe, prices, turn, snaps, idx


@pytest.fixture(autouse=True, scope="module")
def _자료_바꿈():  # type: ignore[no-untyped-def]
    mp = pytest.MonkeyPatch()
    mp.setattr(t, "_자료", _자료_ac)
    yield
    mp.undo()


def _판단전(c, rp: t.리플레이, day: str, cutoff: str) -> None:  # type: ignore[no-untyped-def]
    """조사 자료 성과 지표(1921) + cutoff 까지 접수된 재무 + 그 주 종합 점수."""
    hde._판단전(c, rp, day, cutoff)
    for sid, rows in t._자료()[3].items():
        아는 = {}
        for r in sorted(rows, key=lambda r: (r["as_of_date"], r["receipt_no"])):
            if r["as_of_date"] <= cutoff:
                아는[r["fiscal_year"]] = r
        for fy, r in 아는.items():
            v = r["values"]
            c.conn.execute(
                "INSERT OR REPLACE INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated,"
                " report_date, receipt_no, currency, unit, revenue, operating_income, net_income, total_equity,"
                " source, fetched_at, period_days) VALUES (?, ?, ?, 'A', 1, ?, ?, 'KRW', 'KRW', ?, ?, ?, ?, 't', 't',"
                " 365)", [sid, fy, dart.ANNUAL_REPORT_CODE, r["as_of_date"], r["receipt_no"], v["revenue"],
                          v["operating_income"], v["net_income"], v["total_equity"]])  # fmt: skip
    dv = rp.하루[day]
    무게 = json.dumps(cb.EQUAL_WEIGHTS, sort_keys=True)
    for sid, 총 in sorted(dv.total_scores.items()):
        if 총 is None:
            continue
        c.conn.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_score,"
            " sentiment_weight_used, weights_json, calc_version, created_at) VALUES (?, ?, ?, ?, NULL, 0.0, ?, 1, 't')",
            [sid, cutoff, 총, json.dumps(dv.factor_scores.get(sid) or {}), 무게])  # fmt: skip


_입력 = t.운영입력(db=hde._db_회사, 판단전=_판단전, 시세후=hde._지수넣기)
t.운영입력들.update({"core_a": _입력, "core_c": _입력})

변형들 = ["core_a", "core_c"]


def test_합성_창이_사기_전_점검_갈래를_지난다() -> None:
    """시험이 헛돌지 않게 — 창 안에서 두 계좌가 사고, FY2023 적자 전환이 접수된 뒤 재무악화(든 종목 매도 또는 사기 전 점검
    skip)가 실제로 나온다. 접수 전 결정에는 재무악화가 없다.

    2026-10-28 실측(비용 0): A 매수 16·매도 재무악화 1(03-18 종목 18 — cutoff = 접수일)·손절 1·목표도달 1, 사기 전 점검 skip 3
    (종목 22). C 매수 21·매도 재무악화 2(03-18 종목 18·22)·손절 5, skip 12(종목 12 여덟 번·22 네 번). 감성급락·점검은 0(자료 없음)"""
    기대 = {"core_a": ({("2024-03-18", 18)}, {22: 3}), "core_c": ({("2024-03-18", 18), ("2024-03-18", 22)}, {12: 8, 22: 4})}
    for variant in 변형들:
        rp = t._리플레이(variant, "zero")
        assert sum(len(o.buys) for o in rp.주문.values()) >= 15, variant
        판 = {(lg.day, sid) for lg in rp.s.log for sid, x in lg.sells.items() if "재무악화" in x}
        skip: dict[int, int] = {}
        for lg in rp.s.log:
            for d in lg.decisions:
                if d["reason"].startswith("사면 바로 매도 규칙에 걸림"):
                    assert d["reason"] == "사면 바로 매도 규칙에 걸림: 재무악화" and lg.cutoff >= _FY2023_접수, d
                    skip[d["stock_id"]] = skip.get(d["stock_id"], 0) + 1
        assert (판, skip) == 기대[variant], (variant, 판, skip)
        assert all(lg.cutoff >= _FY2023_접수 for lg in rp.s.log for x in lg.sells.values() if "재무악화" in x)
        assert not {c for lg in rp.s.log for x in lg.sells.values() for c in x} & {"감성급락", "점검"}


@pytest.mark.parametrize("variant", 변형들)
def test_비용_0_이면_장부·매도·결정·매수_비중이_허용_없이_같다(variant: str) -> None:
    """25.1934 — 수수료·세금 0 판. 1910 의 L0~L3 이 허용 ③(2e-7) 안에서, 1921 의 회의 단이 허용 없이 같다 — 운영 `run_step` 의
    `sell_decisions`(enrich 보강 → 재무악화)·`prebuy_sell_flags` 와 리플레이의 시점 창이 날마다 같은 매도·skip 을 낸다."""
    for 단 in (t._L0, t._L1, t._L2, t._L3, hde._회의_대조):
        틀림 = 단(variant, "zero")
        assert not 틀림, (단.__name__, 틀림[:5])
    assert t._관문(variant, "zero") <= 1e-4
