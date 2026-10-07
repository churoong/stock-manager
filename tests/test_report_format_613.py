"""텔레그램 리포트 서식 가장자리 (docs/infra.md 25.613, 감사)."""

from __future__ import annotations

from batch import config
from batch.notify import formatter, report_sections, telegram


def test_돈_서식() -> None:
    for f in (formatter._money, report_sections._money):
        assert f(-1234.5, "USD") == "-$1,234.50"
        assert f(-0.4, "KRW") == "0원"
        assert f(float("nan"), "KRW") == "-"
        assert f(1234.5, "USD") == "$1,234.50"


def test_퍼센트는_반올림한_뒤_부호를_정한다() -> None:
    assert report_sections._pct(-0.04) == "0.0%"
    assert report_sections._pct(float("nan")) == "-"
    assert report_sections._pct(-12.34) == "-12.3%"
    assert formatter._change(-0.001) == "+0.00%"


def test_긴_경고는_잘렸다고_말한다() -> None:
    긴 = "가" * 1000
    줄 = formatter._한_줄(긴)
    assert len(줄) < 400 and "잘림" in 줄
    assert formatter._한_줄("짧다") == "짧다"


def test_실패_원인이_잘리면_표시한다() -> None:
    import inspect

    assert "잘림, 배치 기록 참고" in inspect.getsource(formatter)


def test_고지는_본문_중간에_있어도_끝에_붙인다(monkeypatch) -> None:
    monkeypatch.setattr(config, "TELEGRAM_DISCLAIMER", True)  # 켰을 때의 규칙 — 기본은 꺼짐(25.878)
    본문 = f"경고: 누가 '{config.DISCLAIMER}' 를 인용했다\n1부 ..."
    assert telegram.append_disclaimer(본문).rstrip().endswith(config.DISCLAIMER)
    끝 = f"본문\n\n{config.DISCLAIMER}"
    assert telegram.append_disclaimer(끝) == 끝


def test_리포트의_경고_줄이_잘린다() -> None:
    from datetime import UTC, datetime

    글 = formatter.daily_report(market="KR", trade_date="2026-09-25", rows=[], started_at=datetime(2026, 9, 25, tzinfo=UTC),
                               warnings=["예외: " + "가" * 5000])  # fmt: skip
    assert max(len(줄) for 줄 in 글.split("\n")) < 400
