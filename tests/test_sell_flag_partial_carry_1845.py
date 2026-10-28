"""재무악화가 오늘 일부 근거로만 다시 서면, 판정하지 못한 어제 근거를 잇는다 — 확인한 플래그가 다음 날 NEW 로 다시
알리지 않게 (docs/infra.md 25.1845, 결함 루프 재현).

적자 전환은 점수와 상관없이 서므로 점수를 못 본 날(잣대 바뀜·점수 없음·묵음)에도 재무악화가 적자 전환 하나로 새로 섰다
— 통째 잇기(25.1277)를 타지 않아 그날 행에 "점수 하락" 이 빠졌고, 다음 날 점수 하락이 돌아오자 새 속 사유로 보여 [확인] 이
풀리고 NEW 로 다시 나갔다.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from batch.services import sell_flags as sf
from tests.test_sell_flag_carry_sub_1790 import _실행

기본 = dict(stock_id=1, name="에이", horizon="long", first_buy_date="2025-01-02", currency="KRW", cost=1000.0,
          market_value=1000.0, price_date="2026-03-19", fiscal_year_latest=2025, fin_report_date="2026-03-10",
          op_income_latest=-5.0, op_income_prev=10.0)  # fmt: skip
점수봄 = dict(score_now_date="2026-03-19", score_at_buy=70.0, score_at_buy_date="2025-01-02")


def _장부(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    from batch.core import db
    from batch.jobs import portfolio as pf_job
    from batch.jobs import sell_flags as job
    from tests.test_sell_flag_unjudged_1277 import _C

    c = _C()
    monkeypatch.setattr(db, "apply_migrations", lambda client: None)
    monkeypatch.setattr(job, "TursoClient", lambda: c)
    monkeypatch.setattr(pf_job, "쓰는_중", lambda cl: False)
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
                   " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')")  # fmt: skip
    return c, job


def test_잣대가_바뀐_날을_끼어도_확인이_이어진다(monkeypatch: pytest.MonkeyPatch) -> None:
    c, job = _장부(monkeypatch)
    # 1일차: 적자 전환 + 점수 하락(70 → 45). 사용자가 [확인]
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-03-19T23:27:00+00:00")
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=45.0, **점수봄), date(2026, 3, 20))
    c.conn.execute("UPDATE sell_flags SET dismissed_at = '2026-03-20T01:00:00+00:00'")
    # 2일차: 계산 판이 바뀌어 같은 잣대 기준점이 없다 — 점수 하락을 판정하지 못함. 적자 전환은 그대로 선다
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-03-20T23:27:00+00:00")
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=44.0, score_now_date="2026-03-20",
                                          score_scale_unknown=True), date(2026, 3, 21))  # fmt: skip
    (자료, 글, 확인), = c.conn.execute(
        "SELECT rationale_data, rationale_text, dismissed_at FROM sell_flags WHERE is_active = 1").fetchall()
    자료 = json.loads(자료)
    assert {x["label"] for x in 자료["criteria"]} == {sf.FIN_LOSS_LABEL, "종합 점수 하락"}  # 점수 하락을 이었다
    assert 자료["carried_since"] == "2026-03-20" and "일부 근거(종합 점수 하락)는 오늘 판정할 자료가 없어" in 글
    assert 확인 == "2026-03-20T01:00:00+00:00"
    # 3일차: 점수를 다시 판정, 하락 그대로 — 예전엔 여기서 확인이 풀리고 NEW 였다
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-03-21T23:27:00+00:00")
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=44.0, **{**점수봄, "score_now_date": "2026-03-21"}),
          date(2026, 3, 22))  # fmt: skip
    (자료, 확인, 만든), = c.conn.execute(
        "SELECT rationale_data, dismissed_at, created_at FROM sell_flags WHERE is_active = 1").fetchall()
    assert (확인, 만든) == ("2026-03-20T01:00:00+00:00", "2026-03-19T23:27:00+00:00")
    assert "carried_since" not in json.loads(자료)  # 다 판정한 날은 잇지 않은 새 근거


def test_잇는_끝이_지나면_오늘_근거만(monkeypatch: pytest.MonkeyPatch) -> None:
    """판정일에서 `MAX_CARRY_DAYS` 가 지나면 판정 못 한 근거를 더 잇지 않는다(통째 잇기와 같은 끝)"""
    c, job = _장부(monkeypatch)
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=45.0, **점수봄), date(2026, 3, 20))
    늦은날 = date(2026, 3, 20 + job.MAX_CARRY_DAYS + 1)
    _실행(monkeypatch, c, sf.HoldingInput(**기본), 늦은날)  # 점수 없음
    (자료,), = c.conn.execute("SELECT rationale_data FROM sell_flags WHERE is_active = 1").fetchall()
    assert [x["label"] for x in json.loads(자료)["criteria"]] == [sf.FIN_LOSS_LABEL]


def test_다음_날_통째로_이어도_문장에_사유가_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1847 (25.1845 교차검증 [권고]) — 일부 잇기가 근거표에만 점수 하락을 붙여, 다음 날 흑자로 돌고 점수도 못 본 날
    통째 잇기가 적자 조각을 빼면 "에이: . 보유 이유가 …" 처럼 사유 없는 문장이 리포트에 나갈 뻔했다"""
    c, job = _장부(monkeypatch)
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=45.0, **점수봄), date(2026, 3, 20))
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=44.0, score_now_date="2026-03-20",
                                          score_scale_unknown=True), date(2026, 3, 21))  # fmt: skip
    (글,), = c.conn.execute("SELECT rationale_text FROM sell_flags WHERE is_active = 1").fetchall()
    assert "※ 일부 근거(종합 점수 하락)는 오늘 판정할 자료가 없어" in 글
    흑자 = {**기본, "op_income_latest": 5.0}
    _실행(monkeypatch, c, sf.HoldingInput(**흑자, score_now=44.0, score_now_date="2026-03-21",
                                          score_scale_unknown=True), date(2026, 3, 22))  # fmt: skip
    (글, 자료), = c.conn.execute("SELECT rationale_text, rationale_data FROM sell_flags WHERE is_active = 1").fetchall()
    assert [x["label"] for x in json.loads(자료)["criteria"]] == ["종합 점수 하락"]
    assert ": ." not in 글 and "에이: 종합 점수 하락." in 글
    assert "일부 근거" not in 글 and 글.count("오늘 판정할 자료가 없어") == 1


def test_재무도_못_본_날_통째로_이어도_이은_근거_이름이_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1848 (25.1847 교차검증 [권고]) — 셋째 날 영업이익도 모르면 적자 조각이 그대로라 빈 사유 채우기를 타지 않고,
    일부 표시만 떼어 근거표의 "종합 점수 하락" 이 문장에서 사라졌다"""
    c, job = _장부(monkeypatch)
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=45.0, **점수봄), date(2026, 3, 20))
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=44.0, score_now_date="2026-03-20",
                                          score_scale_unknown=True), date(2026, 3, 21))  # fmt: skip
    모름 = {**기본, "op_income_latest": None}
    _실행(monkeypatch, c, sf.HoldingInput(**모름, score_now=44.0, score_now_date="2026-03-21",
                                          score_scale_unknown=True), date(2026, 3, 22))  # fmt: skip
    (글, 자료), = c.conn.execute("SELECT rationale_text, rationale_data FROM sell_flags WHERE is_active = 1").fetchall()
    assert {x["label"] for x in json.loads(자료)["criteria"]} == {sf.FIN_LOSS_LABEL, "종합 점수 하락"}
    assert "적자 전환" in 글 and "(종합 점수 하락 포함)" in 글 and 글.count("오늘 판정할 자료가 없어") == 1


def test_빈_사유_채우기는_정규식_특수문자에_안전하다() -> None:
    assert sf.full_carry_text("가: . 점검", ["a\\1"]) == "가: a\\1. 점검"


def test_넷째_날_흑자로_돌면_포함_표시를_다시_계산한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1849 (25.1848 교차검증 [참고]) — 셋째 날 "(종합 점수 하락 포함)" 뒤 넷째 날 흑자로 빈 사유를 그 이름으로 채우면
    이름이 두 번 찍혔다"""
    c, job = _장부(monkeypatch)
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=45.0, **점수봄), date(2026, 3, 20))
    for 날, 재무 in ((21, 기본), (22, {**기본, "op_income_latest": None}), (23, {**기본, "op_income_latest": 5.0})):
        _실행(monkeypatch, c, sf.HoldingInput(**재무, score_now=44.0, score_now_date=f"2026-03-{날 - 1}",
                                              score_scale_unknown=True), date(2026, 3, 날))  # fmt: skip
    (글,), = c.conn.execute("SELECT rationale_text FROM sell_flags WHERE is_active = 1").fetchall()
    assert 글.count("종합 점수 하락") == 1 and "에이: 종합 점수 하락." in 글 and "포함" not in 글
    assert 글.count("오늘 판정할 자료가 없어") == 1


def test_재무를_이틀_연속_못_봐도_포함_표시가_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1850 (25.1849 교차검증 [권고]) — 어제 "(… 포함)" 표시를 떼고 다시 계산하는 길에서 이름 거두기를 꺼도 시험이
    통과했다. 재무를 이틀 연속 모르면 넷째 날 문장에서 점수 하락이 사라졌다"""
    c, job = _장부(monkeypatch)
    _실행(monkeypatch, c, sf.HoldingInput(**기본, score_now=45.0, **점수봄), date(2026, 3, 20))
    모름 = {**기본, "op_income_latest": None}
    for 날, 재무 in ((21, 기본), (22, 모름), (23, 모름)):
        _실행(monkeypatch, c, sf.HoldingInput(**재무, score_now=44.0, score_now_date=f"2026-03-{날 - 1}",
                                              score_scale_unknown=True), date(2026, 3, 날))  # fmt: skip
    (글,), = c.conn.execute("SELECT rationale_text FROM sell_flags WHERE is_active = 1").fetchall()
    assert "(종합 점수 하락 포함)" in 글 and 글.count("오늘 판정할 자료가 없어") == 1


def test_재무악화_근거_이름에_괄호와_쉼표가_없다() -> None:
    """`full_carry_text` 가 문장의 괄호 표시에서 이름을 다시 거두는 제약(25.1849) — 근거 이름이 늘면 여기서 깨진다"""
    h = sf.HoldingInput(**기본, score_now=45.0, **점수봄)
    (f,) = [x for x in sf.evaluate(h, date(2026, 3, 20), {}) if x.reason_code == "재무악화"] or [None]
    assert f is not None
    for c in f.criteria:
        assert not set("(),") & set(str(c.get("label"))), c
