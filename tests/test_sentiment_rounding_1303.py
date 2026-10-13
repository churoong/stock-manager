"""감성급락 문장의 정수·소수 한 자리도 웹 toFixed 규칙 (docs/infra.md 25.1303, 감성 감사 #2)."""

from __future__ import annotations

from datetime import date

from batch.services import sell_flags as sf


def test_감성급락_문장의_반올림() -> None:
    필드 = sf.HoldingInput.__dataclass_fields__
    기본 = dict(stock_id=1, name="에이", horizon="long", first_buy_date="2026-07-01", cost=1000.0, market_value=1000.0,
              price_date="2026-10-14", sentiment_now=-60.0, sentiment_delta_7d=-42.5, sentiment_negative_7d=6,
              sentiment_tracked_7d=True, sentiment_date="2026-10-14", sentiment_ref_date="2026-10-14")  # fmt: skip
    h = sf.HoldingInput(**{k: v for k, v in 기본.items() if k in 필드},
                        **{k: None for k in 필드 if k not in 기본 and 필드[k].default is 필드[k].default_factory})  # type: ignore[arg-type]  # fmt: skip
    [f] = [f for f in sf.evaluate(h, date(2026, 10, 15), sf.Rules() if hasattr(sf, "Rules") else None)
           if f.reason_code == "감성급락"]  # fmt: skip
    assert "7일 사이 -43점" in f.rationale_text  # 파이썬 서식이면 -42(짝수)
