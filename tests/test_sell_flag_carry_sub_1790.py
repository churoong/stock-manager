"""매도 플래그를 이을 때 오늘 판정한 속 근거는 뺀다 — 흑자로 돌아 풀린 적자 전환이 점수 없는 날 남지 않게
(docs/infra.md 25.1790, 결함 루프)."""

from __future__ import annotations

import inspect
from datetime import date

from batch.jobs import sell_flags as job
from batch.services import sell_flags as sf

적자 = {"label": sf.FIN_LOSS_LABEL, "display": "FY2024 −5 (전년 +10)"}
점수 = {"label": "종합 점수 하락", "display": "70 → 45"}


def test_오늘_재무를_봤으면_적자_전환은_잇지_않는다() -> None:
    h = sf.HoldingInput(stock_id=1, name="가", horizon="long", first_buy_date="2025-01-02", currency="KRW", cost=1000.0,
                        market_value=1000.0, price_date="2026-03-19", op_income_latest=8.0, op_income_prev=-5.0)
    assert "재무악화" in sf.unjudged(h, date(2026, 3, 20))  # 점수를 못 봐 재무악화는 "판정 못 함"
    assert sf.carry_criteria(h, "재무악화", [적자]) == []  # 그래도 적자 전환은 판정했다 — 풀림
    assert sf.carry_criteria(h, "재무악화", [적자, 점수]) == [점수]  # 점수 하락 근거만 잇는다
    모름 = sf.HoldingInput(**{**h.__dict__, "op_income_latest": None})
    assert sf.carry_criteria(모름, "재무악화", [적자, 점수]) == [적자, 점수]  # 재무도 못 봤으면 그대로
    assert sf.carry_criteria(h, "감성급락", [적자]) == [적자]  # 다른 사유는 그대로


def test_문장에서도_적자_전환_조각을_뺀다() -> None:
    글 = "삼성: FY2024 영업이익 적자 전환, 종합 점수 70→45점. 보유 이유가 유지되는지 점검하세요"
    assert sf.without_fin_loss(글) == "삼성: 종합 점수 70→45점. 보유 이유가 유지되는지 점검하세요"


def test_잇는_곳이_속_근거를_거른다() -> None:
    원문 = inspect.getsource(job.run)
    assert "sf.carry_criteria(보유별[sid], 사유, 어제근거)" in 원문 and "if 어제근거 and not 남김:" in 원문
    assert "글 = sf.without_fin_loss(글)" in 원문


def _실행(monkeypatch, c, h: sf.HoldingInput, day: date) -> None:  # noqa: ANN001
    monkeypatch.setattr(job, "load_holdings", lambda client, today=None: ([h], []))
    job.run(day)


def test_실행에서_풀리고_일부만_이어진다(monkeypatch) -> None:  # noqa: ANN001
    """`job.run` 을 끝까지 — 적자 전환만이던 재무악화는 흑자가 된 날(점수 묵음) 풀리고, 점수 하락이 함께였으면 그 근거만
    잇는다(25.1792, 25.1790 교차검증 [권고]: 소스 글 대조뿐이라 연결부를 깨도 통과했다)"""
    import json

    from batch.core import db
    from batch.jobs import portfolio as pf_job
    from tests.test_sell_flag_unjudged_1277 import _C

    c = _C()
    monkeypatch.setattr(db, "apply_migrations", lambda client: None)
    monkeypatch.setattr(job, "TursoClient", lambda: c)
    monkeypatch.setattr(pf_job, "쓰는_중", lambda cl: False)
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
                   " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')")  # fmt: skip
    기본 = dict(stock_id=1, name="에이", horizon="long", first_buy_date="2025-01-02", currency="KRW", cost=1000.0,
              market_value=1000.0, price_date="2026-03-19", fiscal_year_latest=2025, fin_report_date="2026-03-10")
    # 첫날: 적자 전환(+10 → −5) + 점수 하락(70 → 45, 신선)
    _실행(monkeypatch, c, sf.HoldingInput(**기본, op_income_latest=-5.0, op_income_prev=10.0, score_now=45.0,
                                          score_now_date="2026-03-19", score_at_buy=70.0,
                                          score_at_buy_date="2025-01-02"), date(2026, 3, 20))  # fmt: skip
    (자료,), = c.conn.execute("SELECT rationale_data FROM sell_flags WHERE is_active = 1").fetchall()
    assert {x["label"] for x in json.loads(자료)["criteria"]} == {sf.FIN_LOSS_LABEL, "종합 점수 하락"}
    # 이튿날: 새 사업보고서로 흑자(−5 → +8), 점수는 없음 — 적자 전환은 판정해 빠지고 점수 하락만 잇는다
    _실행(monkeypatch, c, sf.HoldingInput(**기본, op_income_latest=8.0, op_income_prev=-5.0), date(2026, 3, 21))
    (자료, 글), = c.conn.execute("SELECT rationale_data, rationale_text FROM sell_flags WHERE is_active = 1").fetchall()
    assert [x["label"] for x in json.loads(자료)["criteria"]] == ["종합 점수 하락"]
    assert sf.FIN_LOSS_LABEL not in 글 and "오늘 판정할 자료가 없어" in 글
    # 적자 전환만이던 경우: 흑자가 된 날 풀린다(resolved_at)
    c.conn.execute("DELETE FROM sell_flags")
    _실행(monkeypatch, c, sf.HoldingInput(**기본, op_income_latest=-5.0, op_income_prev=10.0, score_now=70.0,
                                          score_now_date="2026-03-19", score_at_buy=70.0,
                                          score_at_buy_date="2025-01-02"), date(2026, 3, 20))  # fmt: skip
    _실행(monkeypatch, c, sf.HoldingInput(**기본, op_income_latest=8.0, op_income_prev=-5.0), date(2026, 3, 21))
    assert c.conn.execute("SELECT COUNT(*) FROM sell_flags WHERE is_active = 1").fetchone()[0] == 0
    assert c.conn.execute("SELECT COUNT(*) FROM sell_flags WHERE resolved_at IS NOT NULL").fetchone()[0] == 1
