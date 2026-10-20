"""결정 단위 선정 증거 — 묶음 결정일은 달력 날짜로, 짝 차이도 사흘 문턱, 추가 매수는 새 선정이 아니다
(docs/paper.md 10.1, docs/infra.md 25.1570 — 운용 감사 [권고] 1·2, [확인필요] 6)."""

from __future__ import annotations

import json
from datetime import date, timedelta

from batch.services import paper as pp


def _결정(market: str, day: str, kind: str, ret: float, sid: int, held: bool | None = None) -> dict:
    det = {} if held is None else {"held": held}
    return {"market": market, "decided_on": day, "kind": kind, "reason": "2부 배분" if kind == "buy" else "점수 부족",
            "ret_20d": ret, "bench_20d": 0.0, "stock_id": sid, "detail_json": json.dumps(det)}  # fmt: skip


def _하루(market: str, day: str, held: bool | None = None) -> list[dict]:
    # 산 것 하나(수익 5) + 안 산 새 후보 풀(수익 0~9)
    return [_결정(market, day, "buy", 5.0, 1, held)] + [
        _결정(market, day, "skip", float(r), 100 + r) for r in range(pp.EVIDENCE_MIN_POOL)]


def _묶음_첫날(k: int) -> date:
    return date.fromordinal((date(2026, 1, 1).toordinal() // pp.EVIDENCE_BLOCK_DAYS + k) * pp.EVIDENCE_BLOCK_DAYS)


def test_두_시장_같은_날은_결정일_하나() -> None:
    """묶음마다 달력 이틀(국내·미국) — 예전엔 넷으로 세 사흘 문턱을 넘었다"""
    결정 = []
    for k in range(6):
        for i in range(2):
            d = (_묶음_첫날(k) + timedelta(days=i)).isoformat()
            결정 += _하루("KR", d) + _하루("US", d)
    e = pp.selection_evidence(결정)
    assert e["n_blocks"] == 0 and e["excess_blocks"] == {}
    # 사흘이면 묶음이 선다
    for k in range(6):
        d = (_묶음_첫날(k) + timedelta(days=2)).isoformat()
        결정 += _하루("KR", d)
    e = pp.selection_evidence(결정)
    assert e["n_blocks"] == 6 and len(e["excess_blocks"]) == 6


def test_짝_차이_하루치_묶음은_빠진다() -> None:
    결정 = []
    for k in range(3):
        for i in range(3):
            결정 += _하루("KR", (_묶음_첫날(k) + timedelta(days=i)).isoformat())
    결정 += _하루("KR", _묶음_첫날(3).isoformat())  # 하루치 끝 묶음
    assert len(pp.selection_evidence(결정)["excess_blocks"]) == 3


def test_추가_매수는_새_선정이_아니다() -> None:
    결정 = []
    for i in range(3):
        결정 += _하루("KR", (_묶음_첫날(0) + timedelta(days=i)).isoformat(), held=True)
    assert pp.selection_evidence(결정)["days"] == 0
    # 표시가 없는 옛 행·새로 산 것은 그대로 센다
    결정 = []
    for i, h in enumerate((None, False, None)):
        결정 += _하루("KR", (_묶음_첫날(0) + timedelta(days=i)).isoformat(), held=h)
    assert pp.selection_evidence(결정)["days"] == 3
    # 핵심 H 하루 기록 꼴(`detail` dict)도 읽는다
    assert pp._추가매수({"detail": {"held": True}}) and not pp._추가매수({"detail": {}})


def test_배치가_매수_결정에_보유_여부를_적는다(monkeypatch) -> None:  # noqa: ANN001
    from batch.jobs import paper as job
    from batch.services import report_picks
    from tests.test_paper_accounts_1458 import _하루  # noqa: F401
    from tests.test_paper_job_1445 import Client

    cl = Client(accounts=("B",))
    cl.conn.execute("INSERT INTO paper_cash_flows (flow_date, amount_krw, kind, created_at, request_key)"
                    " VALUES ('2026-10-14', 1000000, 'deposit', '2026-10-14T00:00:00+00:00', 'k1')")  # fmt: skip
    cl.conn.execute("INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
                    " VALUES (1, '2026-10-14', 10000, 10000, 10000, 10000, 1000, 'KRW', 't', 't')")  # fmt: skip
    monkeypatch.setattr(report_picks, "with_criteria", lambda rows: rows)
    monkeypatch.setattr(job, "frozen_ids", lambda *_a, **_k: set())
    _하루(cl, monkeypatch, "2026-10-14")
    det = [json.loads(r[0]) for r in cl.q("SELECT detail_json FROM paper_decisions WHERE kind = 'buy'")]
    assert det and all(d.get("held") is False for d in det)


def test_핵심_H_관문_잣대도_추가_매수를_뺀다() -> None:
    """`gate_evidence` 가 다시 만든 줄에 detail 이 없어 운영과 같은 잣대가 아니었다 (25.1572, 25.1570 반박 [권고] 2)"""
    from types import SimpleNamespace

    from batch.services import core_evidence as ce

    날 = [(date(2026, 1, 5) + timedelta(days=i)).isoformat() for i in range(80)]
    prices = {sid: {d: 100.0 + (sid % 7) * i for i, d in enumerate(날)} for sid in range(1, 12)}

    def 기록(held: bool) -> list:
        logs = []
        for i in range(0, 40):
            d = 날[i]
            결정 = [{"kind": "buy", "stock_id": 1, "reason": "지주사 합의", "ref_date": d, "detail": {"held": held}}]
            결정 += [{"kind": "skip", "stock_id": s, "reason": "점수 부족", "ref_date": d} for s in range(2, 12)]
            logs.append(SimpleNamespace(day=d, cutoff=d, decisions=결정))
        return logs

    assert ce.gate_evidence(기록(True), ce.Closes(prices))["days"] == 0
    assert ce.gate_evidence(기록(False), ce.Closes(prices))["days"] > 0
