"""시세 고침 기록을 두 겹으로 — 몇 종목의 먼 과거 고침이 전 종목으로 번지지 않는다 (docs/infra.md 25.1537).

일일 배치는 오늘 시세를 수천 종목에 넣고 같은 실행에서 몇 종목의 수정주가를 몇 년 치 고친다. 예전 요약은 그것을
"전 종목, 가장 이른 날(2021-09-16)부터" 로 줄여, 시세 사본이 넓은 범위라며 날마다 비워졌다(10-13 부터) — 점수·신호가
날마다 Turso 에서 시세를 읽어 월 읽기 한도를 앞당겼다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from batch.core import db
from batch.core import price_replica as rep
from tests.test_price_replica import _같나, _원격, 지금


def _하루치(원격) -> None:
    """종목 2~8 은 오늘(08-30) 시세를 새로, 종목 1 은 08-01 부터 수정주가를 고친다(먼 과거)"""
    db.bulk_upsert_prices(원격, [(sid, "2026-08-30", None, None, None, 500.0 + sid, 500.0 + sid, 10, 100, "KRW", "t",
                                 "t", None) for sid in range(2, 9)])  # fmt: skip
    db.bulk_upsert_prices(원격, [(1, f"2026-08-{d:02d}", None, None, None, 1.0 + d, 1.0 + d, 10, 100, "KRW", "t", "t",
                                 None) for d in range(1, 31)])  # fmt: skip


def test_요약은_먼_과거_몇_종목만_따로(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "PRICES_TOUCHED_MAX_STOCKS", 2)
    db.note_prices_touched([(sid, "2026-08-30") for sid in range(2, 9)] + [(1, "2026-08-01")])
    assert db.prices_touched_summary() == {"all": True, "min_date": "2026-08-30", "stocks": {"1": "2026-08-01"}}
    # 상한 안이면 예전 그대로
    monkeypatch.setattr(db, "PRICES_TOUCHED_MAX_STOCKS", 300)
    assert db.prices_touched_summary()["all"] is False


def test_끝난_실행의_두_겹_기록으로_사본을_비우지_않고_맞춘다(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "PRICES_TOUCHED_MAX_STOCKS", 2)
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)  # 표본 검증이 대신 고치지 않게
    원격 = _원격(종목수=6)
    사본 = rep.open_replica(tmp_path / "prices.db")
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    번호 = db.start_batch_run(원격, job_name="daily_kr", market="KR", trade_date=None)  # type: ignore[arg-type]
    _하루치(원격)
    db.finish_batch_run(원격, 번호, status="success", step_log={"ok": 1})  # type: ignore[arg-type]
    원격.execute("UPDATE batch_runs SET finished_at = ? WHERE id = ?", [(지금 + timedelta(minutes=5)).isoformat(), 번호])
    # 08-01 은 지금(10-06)에서 66일 — 예전 요약이면 "전 종목 08-01 부터" 라 넓은 범위로 비웠다
    r = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(hours=1))
    assert r["usable"] and not r.get("cleared")
    assert _같나(원격, 사본)


def test_같은_실행의_두_겹_기록으로도(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """일일 배치가 점수·신호 직전에 맞출 때(`pending`, 25.1033)"""
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.setattr(db, "PRICES_TOUCHED_MAX_STOCKS", 2)
    monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)
    원격 = _원격(종목수=6)
    사본 = rep.open_replica(tmp_path / "prices.db")
    rep.sync(원격, 사본, backend="turso", allow_build=True, now=지금)
    _하루치(원격)
    r = rep.sync(원격, 사본, backend="turso", allow_build=False, now=지금 + timedelta(hours=1),
                 pending=db.prices_touched_summary())  # fmt: skip
    assert r["usable"] and not r.get("cleared")
    assert _같나(원격, 사본)
