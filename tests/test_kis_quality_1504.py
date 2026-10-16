"""KIS모의투자 실행 품질 · 괴리 알림 (docs/paper.md 15.8, docs/desk.md 4장, docs/infra.md 25.1504)."""

from __future__ import annotations

import json

import pytest

from batch.jobs import broker_mirror as job
from batch.jobs import daily
from batch.services import broker_mirror as bm
from batch.services import desk
from tests.test_kis_vts_1475 import _at, _Sess, _줄, c  # noqa: F401 — c 는 픽스처
from tests.test_paper_job_1445 import Client


def test_kept_pct_is_virtual_fill_realisation() -> None:
    """둘 다 참 2 · 가상만 참 1 → 가상이 찼다고 한 셋 가운데 KIS 도 찬 것 둘 = 66.67%"""
    s = bm.compare([
        _줄("buy", "filled", "filled", fq=3, avg=10_100.0, ip=10_000.0),
        _줄("sell", "filled", "filled", fq=3, avg=19_800.0, ip=20_000.0),
        _줄("buy", "unfilled", "filled", ip=9_800.0),
        _줄("buy", "partial", "expired", fq=1, avg=9_700.0),
    ])  # fmt: skip
    assert s["kept_pct"] == pytest.approx(200 / 3)
    assert bm.compare([])["kept_pct"] is None


def _요약(n: int, match: float | None, buy: float | None = None, sell: float | None = None) -> dict:
    return {"days": 28, "n": n, "match_pct": match, "internal_only": 5, "kis_only": 1,
            "slip_buy": {"n": n if buy is not None else 0, "avg": buy},
            "slip_sell": {"n": n if sell is not None else 0, "avg": sell}}  # fmt: skip


def test_diverge_fires_on_the_first_day_only() -> None:
    낮음 = _요약(30, 70.0)
    글 = bm.diverge(낮음, _요약(30, 85.0), "KR")
    assert 글 is not None and 글.startswith("KIS모의투자 국내이 가상모의투자와 벌어졌습니다(지난 28일, 주문 30)")
    assert "체결 일치 70% (전날 85%) — 문턱 80%. 가상만 참 5 · KIS만 참 1" in 글
    assert bm.diverge(낮음, _요약(30, 75.0), "KR") is None  # 어제도 넘어 있었다 — 날마다 울리지 않는다
    assert bm.diverge(낮음, None, "US") is not None  # 첫 기록
    assert bm.diverge(낮음, _요약(10, 50.0), "KR") is not None  # 어제는 표본이 모자라 판단 전
    assert bm.diverge(_요약(19, 10.0), None, "KR") is None  # 표본 모자람
    assert bm.diverge(_요약(30, 80.0), None, "KR") is None  # 문턱 그 값은 넘은 것이 아니다


def test_diverge_slippage_crossing() -> None:
    글 = bm.diverge(_요약(30, 90.0, buy=0.8, sell=0.1), _요약(30, 90.0, buy=0.4, sell=0.1), "US")
    assert 글 is not None and "매수 가격 차이 평균 +0.80% (n=30) — 문턱 +0.5%" in 글 and "매도" not in 글
    assert bm.diverge(_요약(30, 90.0, buy=0.8), _요약(30, 90.0, buy=0.9), "US") is None


def test_diverge_reaches_alert_and_desk() -> None:
    assert daily._kis_알림문({"diverge": "벌어짐"}) == "벌어짐"
    assert daily._kis_알림문({"sent": {"accepted": 1}}) is None
    확인 = desk.checks(halted=False, paper=None, broker={"diverge": "벌어짐"}, recon_bad=[], warnings=0)
    assert 확인 == [{"level": "high", "text": "벌어짐"}]


def test_job_reads_previous_day_and_records_diverge(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """전날 줄은 문턱 위(일치 90%), 오늘 비교가 문턱 아래면 `기록["diverge"]` — 일일 배치가 그 글로 알린다"""
    c.conn.execute(
        "INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source, fetched_at,"
        " summary_json) VALUES ('kis_vts', '2026-10-15', 'B', '[]', '{}', 'kis_vts', 't', ?)",
        [json.dumps(_요약(30, 90.0))],
    )  # fmt: skip
    낮게 = {**_요약(30, 60.0), "both": 10, "neither": 8, "partial": 0, "rejected": 0, "unknown": 0, "cleanup": 0,
           "reject_reasons": {}, "kept_pct": 50.0}  # fmt: skip
    monkeypatch.setattr(job.bm, "compare", lambda _rows: 낮게)
    기록 = job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 기록["compare"]["kept_pct"] == 50.0
    assert "체결 일치 60% (전날 90%)" in 기록["diverge"]
