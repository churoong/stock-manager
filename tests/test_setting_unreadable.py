"""설정 행이 있는데 읽지 못하면 말한다 (docs/infra.md 25.778).

예전에는 JSON 이 깨졌거나 최상위가 null 인 설정이 "행 없음" 과 구별되지 않아, 배치가 기본 가중치·기본 목표/손절로
**말없이** 계산했다. 웹 설정 화면은 같은 행을 "읽지 못한 칸" 으로 띄운다.
"""

from __future__ import annotations

from batch.core import db
from batch.jobs import scores
from batch.services import trend
from tests.test_report_picks import SqliteClient


def _설정(c: SqliteClient, key: str, raw: str) -> None:
    c.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, 't')", [key, raw])


def test_행이_없으면_기본값이고_말하지_않는다() -> None:
    c = SqliteClient()
    못읽음: list[str] = []
    assert db.get_setting(c, "fees", {"a": 1}, 못읽음=못읽음) == {"a": 1}  # type: ignore[arg-type]
    assert 못읽음 == []


def test_깨진_JSON_과_null_은_기본값을_쓰되_말한다() -> None:
    c = SqliteClient()
    _설정(c, "fees", "{깨짐")
    _설정(c, "taxes", "null")
    못읽음: list[str] = []
    assert db.get_setting(c, "fees", {}, 못읽음=못읽음) == {}  # type: ignore[arg-type]
    assert db.get_setting(c, "taxes", {}, 못읽음=못읽음) == {}  # type: ignore[arg-type]
    assert len(못읽음) == 2
    assert "'fees'" in 못읽음[0] and "JSON" in 못읽음[0]
    assert "'taxes'" in 못읽음[1] and "null" in 못읽음[1]


def test_정상값은_그대로_말없이() -> None:
    c = SqliteClient()
    _설정(c, "fees", '{"kr_commission_pct": 0.015}')
    못읽음: list[str] = []
    assert db.get_setting(c, "fees", {}, 못읽음=못읽음) == {"kr_commission_pct": 0.015}  # type: ignore[arg-type]
    assert 못읽음 == []


def test_가중치_설정이_깨지면_점수_경고에_오른다() -> None:
    c = SqliteClient()
    _설정(c, "factor_weights", '{"value": 20, ')
    weights, _감성, 경고 = scores.load_weights(c)  # type: ignore[arg-type]
    assert weights == scores.DEFAULT_WEIGHTS
    assert any("factor_weights" in 줄 for 줄 in 경고), 경고


def test_추세_필터_설정이_null_이면_경고에_오른다() -> None:
    c = SqliteClient()
    _설정(c, trend.SETTING_KEY, "null")
    설정 = trend.load_settings(c)  # type: ignore[arg-type]
    assert 설정["enabled"] == trend.DEFAULT_ENABLED
    assert any("trend_filter" in 줄 for 줄 in 설정["warnings"]), 설정


def test_단일_값_설정도_읽지_못하면_경고로_돌려준다() -> None:
    """원화·비율 설정은 `get_setting_in_range` 로 읽는다 — 그 경고로 닿아야 한다 (25.782, 교차검증)."""
    c = SqliteClient()
    _설정(c, "total_investable_amount", "null")
    _설정(c, "sentiment_weight", "{깨짐")
    값, 말 = db.get_setting_in_range(c, "total_investable_amount", 0.0)  # type: ignore[arg-type]
    assert 값 == 0.0 and 말 and "total_investable_amount" in 말
    _가중치, _감성, 경고 = scores.load_weights(c)  # type: ignore[arg-type]
    assert any("sentiment_weight" in 줄 for 줄 in 경고), 경고


def test_단일_값_설정이_정상이면_경고가_없다() -> None:
    c = SqliteClient()
    _설정(c, "total_investable_amount", "5000000")
    assert db.get_setting_in_range(c, "total_investable_amount", 0.0) == (5_000_000, None)  # type: ignore[arg-type]
