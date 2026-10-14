"""증권사 성적표 — 같은 종목 반복 의견의 창 겹침을 묶음으로 세기만 (docs/infra.md 25.1361, 23회차 P5)."""

from __future__ import annotations

from batch.services import broker_stats as bs
from tests.test_broker_stats_995 import DAYS, _bars


def test_같은_종목_60일_안_반복은_한_묶음() -> None:
    n = len(DAYS)
    closes = [100.0] * n
    # 같은 종목에 열흘 간격 의견 10개 — 모두 익지만 창이 겹친다
    ops = [bs.Opinion(1, "KOSPI", DAYS[k * 10], "가증권", 150.0) for k in range(10)]
    묶음: dict[str, bs.BrokerStat] = {}
    rows = bs.compute(ops, {1: _bars(closes)}, {"KOSPI": _bars(closes)}, 묶음)
    assert rows[0]["n_fwd"] == 10
    c = 묶음["가증권"].clusters()
    assert c["n_fwd_clusters"] == 2  # 기준 거래일 1 과, 그보다 60 거래일 넘게 뒤인 첫 의견(71)
    요약 = bs.cluster_summary(묶음, gate=5)
    assert 요약["fwd_gate_drop"] == 1  # 10 건이라 문(5)을 넘지만 묶음 2 개는 밑돈다
    # 성적·문은 그대로 — 행에 묶음 칸이 없다(테이블 열 그대로)
    assert "n_fwd_clusters" not in rows[0]


def test_다른_종목이면_따로_센다() -> None:
    assert bs._clusters([(1, 0), (2, 0), (1, 59), (1, 60)], 60) == 3
