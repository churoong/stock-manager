"""위원회·지주사 감사 반영 (docs/company.md, docs/infra.md 25.1576) — H 가중치 기준 평균은 표본 가중,
주간 위원회 복기는 "가결이나 상위 N 밖" 을 부결로 세지 않는다."""

from __future__ import annotations

import json
from datetime import date, timedelta

from batch.services import company as co
from batch.services import holding as hold


def _기록(회사: str, n: int, 초과: float, 시작: int = 0) -> list[dict]:
    d0 = date(2026, 1, 1)
    return [{"decided_on": (d0 + timedelta(days=40 * (시작 + i))).isoformat(), "stock_id": 1000 + 시작 + i,
             "ret_20d": 초과, "bench_20d": 0.0, "detail_json": json.dumps({"holding": {"votes": {회사: 1.0}}})}
            for i in range(n)]  # fmt: skip


def test_표본_1건_회사의_운이_나머지를_하한으로_끌지_않는다() -> None:
    """A n=1 +50%p, 나머지 n=60 0%p — 단순 평균(12.5%p)이면 나머지가 0.25 하한까지 내려갔다"""
    rec = _기록("A", 1, 50.0) + _기록("C", 60, 0.0, 100) + _기록("D", 60, 0.0, 300) + _기록("E", 60, 0.0, 500)
    w, 성적 = hold.weights(rec, ["A", "C", "D", "E"])
    assert 성적["A"]["n"] == 1 and 성적["C"]["n"] == 60
    for v in ("C", "D", "E"):
        assert w[v] > 0.95, w
    평균 = 50.0 / 181
    assert w["A"] == round(1 + (1 / 31) * (50.0 - 평균) / hold.HOLDING_SCALE_PCT, 4)


def test_가결이나_상위_밖은_부결로_세지_않는다() -> None:
    결정 = [{"account_id": "D", "stock_id": 7, "decided_on": "2026-10-01", "kind": "skip",
             "reason": "위원회: 가결이나 표 순 상위 5 밖", "ret_20d": 11.0, "bench_20d": 0.0,
             "detail_json": json.dumps({"committee": {"minority": ["risk"]}})},
            {"account_id": "D", "stock_id": 8, "decided_on": "2026-10-01", "kind": "skip",
             "reason": "위원회: 표 0.83 < 문턱 1", "ret_20d": 11.0, "bench_20d": 0.0,
             "detail_json": json.dumps({"committee": {"minority": ["quant"]}})}]  # fmt: skip
    줄 = co.review_lines([], 결정, {7: "칠", 8: "팔"}, {"D": {"name": "장기보유투자사"}})
    본문 = "\n".join(줄)
    assert "칠" not in 본문  # 가결이었다 — 반대 소수를 "찬성" 으로 거꾸로 적지 않는다
    assert "팔: 부결했는데 +11.0%p" in 본문  # 진짜 부결은 그대로
    # 분모 — 일화만 보이지 않게 같은 행에서 다수 쪽이 맞은 수도 (25.1584)
    결정.append({"account_id": "D", "stock_id": 9, "decided_on": "2026-10-01", "kind": "skip", "reason": "위원회: 표 0.2 < 문턱 1",
                 "ret_20d": -3.0, "bench_20d": 0.0, "detail_json": json.dumps({"committee": {"minority": ["quant"]}})})
    결정.append({"account_id": "D", "stock_id": 10, "decided_on": "2026-10-01", "kind": "buy", "reason": "위원회 가결",
                 "ret_20d": 2.0, "bench_20d": 0.0, "detail_json": json.dumps({"committee": {"minority": []}})})
    본문 = "\n".join(co.review_lines([], 결정, {7: "칠", 8: "팔"}, {"D": {"name": "장기보유투자사"}}))
    # 7 은 "가결이나 상위 밖" — 가결 쪽(다수)이 맞았다(+11). 9 는 부결했는데 −3 — 다수가 맞음. 8 은 소수가 맞음 (25.1585)
    assert "성과 찬 결정 4 · 소수의견 있던 것 3 · 그 가운데 다수 쪽이 맞음 2 · 소수 쪽이 5%p 넘게 맞음 1" in 본문
    # 위험 점검에 막힌 가결(blocked)도 가결 쪽으로 센다
    막힘 = [{"account_id": "D", "stock_id": 11, "decided_on": "2026-10-01", "kind": "blocked", "reason": "위험 점검: 한 종목 상한",
             "ret_20d": 4.0, "bench_20d": 0.0, "detail_json": json.dumps({"committee": {"minority": ["risk"]}})}]
    assert "다수 쪽이 맞음 1 ·" in "\n".join(co.review_lines([], 막힘, {}, {"D": {"name": "장기보유투자사"}}))
    # 가결했는데 안 산 줄이 크게 밑돌면 소수(반대) 쪽이 옳았다 — 산 것만 보던 비대칭 (25.1586)
    막힘[0]["ret_20d"] = -9.0
    글 = "\n".join(co.review_lines([], 막힘, {11: "십일"}, {"D": {"name": "장기보유투자사"}}))
    assert "소수 쪽이 5%p 넘게 맞음 1" in 글 and "십일: 가결했는데(안 삼) -9.0%p — 소수의견(반대: 리스크관리부)이 옳았다" in 글
