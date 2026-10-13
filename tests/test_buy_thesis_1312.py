"""매수 근거가 아직 참인가 — 보유 사실 줄 (docs/infra.md 25.1312, 19회차 A4)."""

from __future__ import annotations

import json

from batch.core import db
from batch.jobs import verdicts as vj
from batch.services import buy_thesis as bt
from tests.test_portfolio_job import MemClient

그때 = [{"label": "성장 점수", "passed": True}, {"label": "퀄리티 점수", "passed": True},
        {"label": "매출 증가", "passed": True}, {"label": "영업이익 증가", "passed": True}]  # fmt: skip


def _오늘(**바꿈: object) -> list[dict]:
    기본 = {"성장 점수": (True, "74점"), "퀄리티 점수": (True, "61점"), "매출 증가": (True, "전년 대비 +5.0%"),
            "영업이익 증가": (False, "전년 대비 -3.2%")}  # fmt: skip
    기본.update(바꿈)  # type: ignore[arg-type]
    return [{"label": k, "passed": p, "display": d} for k, (p, d) in 기본.items()]


def test_유지와_탈락을_판정표_글자_그대로() -> None:
    r = bt.compare("mid", 그때, 3, _오늘(), 3)
    assert r is not None and len(r["kept"]) == 3 and r["broken"] == [("영업이익 증가", "전년 대비 -3.2%")]
    글 = bt.line("mid", "2026-09-10", "2026-10-13", r)
    assert 글.startswith("중기 매수 근거 4개 중 3개 유지 (산 날 신호 2026-09-10 → 오늘 판정표 2026-10-13)")
    assert "지금 탈락: 영업이익 증가(전년 대비 -3.2%)" in 글


def test_모름을_깨짐으로_적지_않는다() -> None:
    묵음 = [*_오늘(), {"label": "사업보고서 신선도", "passed": False, "display": "457일 넘음"}]
    r = bt.compare("mid", 그때, 3, 묵음, 3)
    assert r is not None and {lb for lb, _ in r["unknown"]} == {"매출 증가", "영업이익 증가"} and not r["broken"]
    r = bt.compare("mid", 그때, 3, _오늘(), 4)  # 계산 판이 다르면 점수 기준은 모름
    assert r is not None and {lb for lb, _ in r["unknown"]} == {"성장 점수", "퀄리티 점수"}
    r = bt.compare("mid", 그때, 3, None, None)  # 오늘 판정표 없음(유니버스 밖)
    assert r is not None and len(r["unknown"]) == 4 and not r["kept"]


def test_가격_기준과_단기는_견주지_않는다() -> None:
    assert bt.compare("short", [{"label": "추세 위"}], 3, [], 3) is None
    r = bt.compare("long", [{"label": "밸류 점수"}, {"label": "퀄리티 점수"}], 3, _오늘(), 3)
    assert r is not None and sum(len(v) for v in r.values()) == 1


def test_잡이_같은_기간_신호가_있을_때만_줄을_낸다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
              " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 't', 't')")  # fmt: skip
    for tid, horizon, snap in ((1, "mid", "2026-09-10"), (2, "long", "2026-08-01")):
        c.execute("INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                  " horizon, snapshot_as_of, created_at, updated_at)"
                  " VALUES (?, 1, 'buy', ?, 100, 1, 'KRW', 1, 'none', ?, ?, 't', 't')",
                  [tid, "2026-09-11" if tid == 1 else "2026-08-02", horizon, snap])  # fmt: skip
    c.execute("INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
              " tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at)"
              " VALUES (1, '2026-09-10', 'mid', '실적 모멘텀', 90, 100, 'KRW', '[]', 1, 't', ?, 3, 't')",
              [json.dumps({"criteria": 그때})])  # fmt: skip
    c.execute("INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json, calc_version,"
              " created_at) VALUES (1, '2026-10-13', 'mid', 0, 1, ?, 3, 't')", [json.dumps(_오늘())])  # fmt: skip
    경고: list[str] = []
    out = vj.load_buy_thesis(mem, [1], 경고)  # type: ignore[arg-type]
    assert not 경고 and "중기 매수 근거 4개 중 3개 유지" in out[1]["line"]
    assert [e["label"] for e in out[1]["evidence"]] == ["산 날의 신호", "오늘 판정표"]
    c.execute("DELETE FROM signals")  # 산 날 같은 기간 신호가 없으면 줄을 내지 않는다
    assert vj.load_buy_thesis(mem, [1], 경고) == {}  # type: ignore[arg-type]


def test_사실_줄에만_싣는다() -> None:
    """결론·반대 목소리·알림이 아니라 `detail.facts` 와 근거표에만 (검증 B 조건)."""
    import inspect

    src = inspect.getsource(vj.build_market)
    assert 'detail.setdefault("facts", []).append(매수근거[sid]["line"])' in src
    assert src.count("매수근거[") == 2
