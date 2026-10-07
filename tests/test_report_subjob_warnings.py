"""하위 작업(포트폴리오·매도 플래그)의 경고가 리포트로 올라온다, 매도 플래그 판정일 (docs/infra.md 25.492)."""

from __future__ import annotations

import json

import pytest

from batch.jobs import daily
from batch.notify import report_sections as rs


def test_하위_작업의_마지막_기록_경고를_올린다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    for job, log in (("portfolio", {"warnings": ["종가 11일 넘게 묵음: 005930", "환율 7일 넘게 묵음", "a", "b"]}),
                     ("sell_flags", {"flags": 0, "warnings": ["감성을 못 읽어 감성급락 판정을 건너뜀"]})):  # fmt: skip
        db.conn.execute(
            "INSERT INTO batch_runs (job_name, market, status, started_at, step_log)"
            " VALUES (?, 'KR', 'partial', '2026-09-28T00:00:01+00:00', ?)",
            [job, json.dumps(log, ensure_ascii=False)],
        )

    class 문맥(type(db)):  # type: ignore[misc, valid-type]
        def __enter__(self):  # noqa: ANN204
            return db

        def __exit__(self, *a: object) -> None: ...

    monkeypatch.setattr(daily, "TursoClient", lambda: 문맥.__new__(문맥))
    got = daily._하위_경고(["portfolio", "sell_flags"], "2026-09-28T00:00:00+00:00")
    assert got[:3] == ["portfolio: 종가 11일 넘게 묵음: 005930", "portfolio: 환율 7일 넘게 묵음", "portfolio: a"]
    assert "portfolio: … 외 1건 (배치 기록 참고)" in got
    assert "sell_flags: 감성을 못 읽어 감성급락 판정을 건너뜀" in got


def _문맥(monkeypatch: pytest.MonkeyPatch, db: object) -> None:
    class 문맥:
        def __enter__(self):  # noqa: ANN204
            return db

        def __exit__(self, *a: object) -> None: ...

    monkeypatch.setattr(daily, "TursoClient", 문맥)


def test_이번에_열리지_않은_옛_기록은_읽지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """작업이 기록을 열기 전에 죽으면 어제 경고가 오늘 것으로 실렸다 (docs/infra.md 25.496, 교차검증)."""
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    db.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at, step_log)"
        " VALUES ('sell_flags', NULL, 'success', '2026-09-27T12:00:00+00:00', ?)",
        [json.dumps({"warnings": ["어제 경고"]}, ensure_ascii=False)],
    )
    _문맥(monkeypatch, db)
    assert daily._하위_경고(["sell_flags"], "2026-09-28T00:00:00+00:00") == []


def test_외_N건은_잘리기_전_건수로(monkeypatch: pytest.MonkeyPatch) -> None:
    """기록에는 앞 10건만 남는다 — 25건이면 "외 22건" 이다 (25.496, 교차검증)."""
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    db.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at, step_log)"
        " VALUES ('portfolio', NULL, 'partial', '2026-09-28T00:00:01+00:00', ?)",
        [json.dumps({"warnings": [f"w{i}" for i in range(10)], "warning_count": 25})],
    )
    _문맥(monkeypatch, db)
    got = daily._하위_경고(["portfolio"], "2026-09-28T00:00:00+00:00")
    assert got[-1] == "portfolio: … 외 22건 (배치 기록 참고)"


def test_경고를_못_읽어도_리포트는_나가고_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    def 터짐() -> None:
        raise RuntimeError("DB 한도")

    monkeypatch.setattr(daily, "TursoClient", 터짐)
    assert daily._하위_경고(["portfolio"], "") == ["하위 작업 경고를 읽지 못했습니다: DB 한도"]


def test_매도_플래그_머리에_판정일() -> None:
    줄 = rs.render_sell_flags([{"level": "red", "rationale": "손절선 아래", "as_of_date": "2026-09-25"}])
    assert 줄[0] == "매도 플래그 (자동 매도 없음, 판정일 2026-09-25)"


def test_달러_리포트의_미국_보유는_종목_통화_평가액_그대로() -> None:
    """원화 평가액(당일 환율)을 전일 환율로 나누면 달러 평가가 틀어진다 (docs/infra.md 25.494, 텔레그램 감사)."""
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    db.종목(1, "US")
    db.종목(2, "KR")
    for sid, 통화, 평가, 평가원 in ((1, "USD", 1000.0, 1_400_000.0), (2, "KRW", 700_000.0, 700_000.0)):
        db.conn.execute(
            "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
            " market_value, market_value_krw, updated_at) VALUES (?, 1, ?, 1, 1, 1, 1, '2026-01-02', ?, ?, 't')",
            [sid, 통화, 평가, 평가원],
        )
    got = {h.stock_id: h.value for h in daily.load_holdings(db, "USD", 1350.0)}  # type: ignore[arg-type]
    assert got[1] == pytest.approx(1000.0)  # 1,400,000 ÷ 1,350 = 1,037 이 아니다
    assert got[2] == pytest.approx(700_000.0 / 1350.0)  # 국내 보유는 리포트 환율로


def _NEW(처음_걸린_날: str, 처음_시각: str, 발송: str | None, 경고: str = "[]", 앞_발송: str | None = None) -> bool:
    from batch.jobs import sell_flags
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    db.종목(1, "KR")
    db.conn.execute(
        "INSERT INTO sell_flags (stock_id, as_of_date, level, reason_code, rationale_text, rationale_data,"
        " first_seen_date, is_active, created_at) VALUES (1, '2026-09-28', 'red', 'stop', '손절', '{}', ?, 1, ?)",
        [처음_걸린_날, 처음_시각],
    )
    if 앞_발송:
        db.conn.execute(
            "INSERT INTO daily_reports (market, trade_date, status, generated_at, sent_at, summary_text, warnings_json)"
            " VALUES ('KR', '2026-09-22', 'success', 't', ?, 't', '[]')",
            [앞_발송],
        )
    if 발송:
        db.conn.execute(
            "INSERT INTO daily_reports (market, trade_date, status, generated_at, sent_at, summary_text, warnings_json)"
            " VALUES ('KR', '2026-09-25', 'success', 't', ?, 't', ?)",
            [발송, 경고],
        )
    마지막 = daily._last_report_sent_at(db, "KR")  # type: ignore[arg-type]
    return sell_flags.active_flags_for_report(db, "KR", "2026-09-28", 마지막)[0]["new"]  # type: ignore[arg-type]


def test_NEW_는_이_나라_리포트가_아직_보여_주지_않은_것() -> None:
    """저녁 미국 배치에서 처음 걸린 국내 플래그도 다음 날 국내 리포트에서 NEW (docs/infra.md 25.495, 텔레그램 감사)."""
    # 9/26(금) 08:30 KST 발송 뒤 9/27 에 처음 걸림 → 9/28 NEW
    assert _NEW("2026-09-27", "2026-09-27T12:30:00+00:00", "2026-09-25T23:30:00+00:00") is True
    # 발송 기록이 없으면 예전 규칙(처음 걸린 날 == 오늘)
    assert _NEW("2026-09-27", "2026-09-27T12:30:00+00:00", None) is False


def test_아침_발송_뒤_같은_날_걸린_것도_NEW() -> None:
    """9/24 08:30 KST 발송, 같은 날 21:30 KST 미국 배치에서 처음 걸림 → 9/25 국내 리포트에서 NEW.
    날짜로 견주면 '9/24 > 9/24' 가 거짓이라 빠졌다 (docs/infra.md 25.501, 교차검증)."""
    assert _NEW("2026-09-24", "2026-09-24T12:30:00+00:00", "2026-09-23T23:30:00+00:00") is True
    # 아침 배치가 걸고(08:27) 그 리포트가 보냈으면(08:30) 다음 날은 NEW 가 아니다
    assert _NEW("2026-09-24", "2026-09-23T23:27:00+00:00", "2026-09-23T23:30:00+00:00") is False



def test_일부만_나간_리포트는_보여_준_것으로_치지_않는다() -> None:
    """매도 플래그는 글 끝이라 잘린 뒷부분이었을 수 있다 (docs/infra.md 25.512, 교차검증)."""
    잘림 = json.dumps([f"텔레그램 리포트 3조각 가운데 1{daily.PARTIAL_SEND_MARK} — 나머지는 웹"], ensure_ascii=False)
    # 9/24 아침 일부 발송, 그 전 온전한 발송은 9/22. 9/23 에 걸린 플래그는 아직 못 보여 준 것이다
    assert _NEW("2026-09-23", "2026-09-23T01:00:00+00:00", "2026-09-23T23:30:00+00:00", 잘림,
                앞_발송="2026-09-21T23:30:00+00:00") is True  # fmt: skip
    assert _NEW("2026-09-23", "2026-09-23T01:00:00+00:00", "2026-09-23T23:30:00+00:00",
                앞_발송="2026-09-21T23:30:00+00:00") is False  # fmt: skip


def test_신호를_건너뛴_날은_까닭을_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """run 이 0 을 돌려줘 리포트가 까닭 없이 "오늘 계산분 없음" 만 적었다 (docs/infra.md 25.547, 교차검증)."""
    from tests.test_signal_outcomes_version import 메모리DB

    db = 메모리DB()
    db.conn.execute(
        "INSERT INTO batch_runs (job_name, market, status, started_at, step_log)"
        " VALUES ('signals', 'KR', 'skipped', '2026-09-28T00:00:01+00:00', ?)",
        [json.dumps({"reason": "2026-09-25 종가가 없는 종목이 60/100 입니다"}, ensure_ascii=False)],
    )
    _문맥(monkeypatch, db)
    assert daily._건너뛴_까닭("signals", "2026-09-28T00:00:00+00:00") == (
        "신호를 계산하지 않았습니다 — 2026-09-25 종가가 없는 종목이 60/100 입니다. 앞서 계산된 신호를 그대로 씁니다(기준일은 추천 머리에)"
    )
    assert daily._건너뛴_까닭("signals", "2026-09-29T00:00:00+00:00") is None
    import inspect

    assert "_건너뛴_까닭(signals.JOB_NAME, 시작)" in inspect.getsource(daily)
