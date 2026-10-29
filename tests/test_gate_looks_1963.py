"""25.1963 — 실전 전환 관문의 판정 시점 고정(126·189·252 평가)·Pocock 문턱 (docs/desk.md 6장, docs/funnel.md 3장 "2 → 3").

- 결정 단위 하한(선정·가상 대조)은 평균 − t(1 − `GATE_POCOCK_ALPHA`, 묶음 − 1) × 표준오차
- 통과는 판정 시점에 닿은 날의 판정(`looks`)으로만 — 그 사이 날의 칸은 진도, 한 번 통과하면 그 판에서 남는다
- 판정은 앞선 운용 요약의 관문에서 같은 계좌·같은 판일 때만 이어받는다
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from batch.jobs import desk as job
from batch.services import desk
from batch.services import paper as pp
from tests.test_desk_gate_1508 import _날
from tests.test_paper_job_1445 import Client

좋은증거 = {"lower": 58.0, "n_blocks": 8, "mean": 62.0, "se": 1.0,
          "control": {"lower": 57.0, "n_blocks": 8, "mean": 61.0, "se": 1.0}}  # fmt: skip


def _관문(n: int, prev: dict[str, Any] | None = None, version: int | None = None, 좋음: bool = True) -> dict[str, Any]:
    날 = _날(n)
    h = [(d, 1 + 0.001 * i + (0.002 if i % 2 else 0)) for i, d in enumerate(날)]
    r = [(d, 1 + (0.01 if i % 2 else -0.01)) for i, d in enumerate(날)]
    지수 = [(날[0], 1000.0, 1000.0, 1000.0), (날[-1], 1000.0, 1000.0, 1000.0)]
    return desk.gate({"H": h, "R1": r, "R2": r}, "H", version=version, recon_bad_days=0 if 좋음 else 1, batch_failed=0,
                     kis={"kept_pct": 95.0, "n": 200}, evidence=좋은증거, index=지수, prev=prev)  # fmt: skip


def test_pocock_하한_손계산() -> None:
    assert desk.pocock_lower({"mean": 60.0, "se": 2.0, "n_blocks": 6}) == pytest.approx(
        60.0 - pp.t_quantile(1 - 0.0221, 5) * 2.0)
    # 95% 하한보다 낮다(더 보수적)
    assert desk.pocock_lower({"mean": 60.0, "se": 2.0, "n_blocks": 6}) < 60.0 - pp.t_quantile(0.95, 5) * 2.0
    assert desk.pocock_lower({"mean": 60.0, "se": 2.0, "n_blocks": 3}) is None  # 95% 하한도 없는 자리
    assert desk.pocock_lower({"mean": 60.0, "n_blocks": 6}) is None and desk.pocock_lower({}) is None


def test_판정_시점_전에는_통과가_없다() -> None:
    g = _관문(120)
    assert g["looks"] == [] and not g["passed"] and g["next_look"] == 126


def test_첫_판정_시점에_닿은_날_판정하고_다음_시점을_적는다() -> None:
    g = _관문(130)
    assert all(i["ok"] for i in g["items"])
    assert g["looks"] == [{"n": 126, "at": 130, "date": _날(130)[-1], "passed": True, "failed": []}]
    assert g["passed"] and g["next_look"] == 189


def test_못_넘은_판정은_그_사이_날_칸이_좋아도_통과가_아니다() -> None:
    못 = _관문(130, 좋음=False)
    assert not 못["passed"] and 못["looks"][0]["failed"] == ["incidents"]
    뒤 = _관문(150, prev=못)  # 칸은 모두 맞지만 판정 시점이 아니다
    assert all(i["ok"] for i in 뒤["items"]) and not 뒤["passed"] and 뒤["looks"] == 못["looks"] and 뒤["next_look"] == 189
    다음 = _관문(190, prev=뒤)
    assert [x["n"] for x in 다음["looks"]] == [126, 189] and 다음["passed"] and 다음["next_look"] == 252


def test_한_번_통과하면_그_판에서는_남는다() -> None:
    g = _관문(130)
    뒤 = _관문(140, prev=g, 좋음=False)
    assert not all(i["ok"] for i in 뒤["items"]) and 뒤["passed"]


def test_판이_바뀌면_처음부터() -> None:
    g = _관문(130, version=1)
    새판 = _관문(130, prev=g, version=2, 좋음=False)
    assert [x["n"] for x in 새판["looks"]] == [126] and not 새판["passed"]
    # 다른 계좌도 이어받지 않는다 — 판은 같게(둘 다 None) 두어 계좌 비교만 잰다(25.1967, 교차검증 [권고])
    같은판 = _관문(130)
    assert _관문(130, prev=같은판, 좋음=False)["passed"] is True
    assert _관문(130, prev={**같은판, "account": "B"}, 좋음=False)["passed"] is False


def test_여러_시점을_건너뛰었으면_닿은_날_모두_판정() -> None:
    g = _관문(260)
    assert [(x["n"], x["at"]) for x in g["looks"]] == [(126, 260), (189, 260), (252, 260)] and g["next_look"] is None


def test_상황실이_오늘_이하_가장_늦은_요약의_관문을_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    앞 = {"account": "B", "version": None, "looks": [{"n": 126, "at": 126, "date": "2026-10-15", "passed": False,
                                                     "failed": ["kis"]}]}  # fmt: skip
    for as_of, g in (("2026-10-16", 앞), ("2026-10-20", {"account": "B", "looks": []})):  # 뒤 날은 넘기지 않는다
        c.conn.execute("INSERT INTO paper_desk (as_of, summary_json, updated_at) VALUES (?, ?, 't')",
                       [as_of, json.dumps({"gate": g, "markets": {}})])  # fmt: skip
    본: list[Any] = []
    원래 = desk.gate

    def 엿봄(*a: Any, **k: Any) -> dict[str, Any]:
        본.append(k.get("prev"))
        return 원래(*a, **k)

    monkeypatch.setattr(job.desk, "gate", 엿봄)
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    assert 본 == [앞]  # as_of 10-17(KST) 이하 가장 늦은 줄(10-16)


def test_주간_요약_준비도에_판정_시점_한_줄() -> None:
    g = _관문(130, 좋음=False)
    줄 = desk.readiness_lines(g, [])
    assert "  판정 시점(126·189·252평가에만 판정): 126 못 넘음 · 다음 189평가" in 줄
    assert desk.gate_looks_line({"items": [], "passed": False}) is None  # 판정 시점 기록 없는 옛 관문
    assert desk.gate_looks_line({**_관문(260, 좋음=False)}) == (
        "판정 시점(126·189·252평가에만 판정): 126 못 넘음 · 189 못 넘음 · 252 못 넘음 · 판정 시점 끝 — 새 판 등록 전엔 통과 없음")


def test_판정_시점_날_재료_없음은_못_넘음() -> None:
    """25.1972 (25.1968 교차검증 [권고]) — 판정 시점(126) 날 결정 단위 증거가 비었으면(성과 분해 실패 등) 그 칸은
    모름이고 판정은 못 넘음이다(desk.md 6장 — 보수적 쪽, 시점 하나를 태운다)"""
    날 = _날(130)
    h = [(d, 1 + 0.001 * i + (0.002 if i % 2 else 0)) for i, d in enumerate(날)]
    r = [(d, 1 + (0.01 if i % 2 else -0.01)) for i, d in enumerate(날)]
    지수 = [(날[0], 1000.0, 1000.0, 1000.0), (날[-1], 1000.0, 1000.0, 1000.0)]
    g = desk.gate({"H": h, "R1": r, "R2": r}, "H", recon_bad_days=0, batch_failed=0,
                  kis={"kept_pct": 95.0, "n": 200}, evidence=None, index=지수)  # fmt: skip
    칸 = {i["key"]: i for i in g["items"]}
    assert 칸["selection"]["now"] is None and 칸["control"]["now"] is None
    assert g["looks"][0]["n"] == 126 and not g["looks"][0]["passed"] and not g["passed"]
    assert {"selection", "control"} <= set(g["looks"][0]["failed"])
