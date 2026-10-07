"""국내 수정주가 테스트 (docs/adjust.md).

손으로 계산할 수 있는 예로 고정한다. 여기가 틀리면 모멘텀·리스크·백테스트가
조용히 전부 틀어진다.
"""

from __future__ import annotations

import pytest

from batch.services import adjust as adj


def day(date: str, close: float, change_pct: float | None) -> adj.Day:
    return adj.Day(date=date, close=close, change_pct=change_pct)


class Test계수:
    def test_기업행위가_없으면_1이다(self) -> None:
        # 10,000 → 10,500 이고 거래소도 +5% 라고 했다. 조정할 것이 없다
        assert adj.factor_of(10_000, 10_500, 5.0) == pytest.approx(1.0)

    def test_1대10_액면분할(self) -> None:
        # 종가는 10분의 1이 됐는데 거래소는 -2% 라고 한다 (기준가 1,000 대비 980)
        f = adj.factor_of(10_000, 980, -2.0)
        assert f == pytest.approx(0.1)
        assert adj.is_action(f)

    def test_무상감자는_계수가_1보다_크다(self) -> None:
        # 5:1 감자. 종가가 5배로 뛰지만 거래소는 -2% 로 본다
        f = adj.factor_of(1_000, 4_900, -2.0)
        assert f == pytest.approx(5.0)
        assert adj.is_action(f)

    def test_반올림_오차는_기업행위가_아니다(self) -> None:
        # 등락률이 소수 둘째 자리에서 잘려 계수가 1 에서 아주 조금 벗어난다
        f = adj.factor_of(10_000, 10_037, 0.37)
        assert not adj.is_action(f)

    def test_모르면_조정하지_않는다(self) -> None:
        # 등락률이 없으면 1. 없던 기업행위를 만들어 내지 않는다
        assert adj.factor_of(10_000, 1_000, None) == 1.0
        assert adj.factor_of(0, 1_000, 5.0) == 1.0
        assert adj.factor_of(10_000, 0, 5.0) == 1.0

    def test_말이_안_되는_값은_무시한다(self) -> None:
        assert adj.factor_of(10_000, 5_000, -100.0) == 1.0  # 등락률 -100%
        assert adj.factor_of(10_000, 1, 0.0) == 1.0  # 계수 0.0001 — 상한 밖


class Test수정종가:
    def test_분할_이전_가격이_끌려_내려온다(self) -> None:
        days = [
            day("2026-01-02", 10_000, 1.0),
            day("2026-01-05", 10_000, 0.0),
            day("2026-01-06", 980, -2.0),  # 1:10 분할
            day("2026-01-07", 1_000, 2.04),
        ]
        out = adj.adjust(days)
        prices = {a.date: a.adj_close for a in out}
        # 분할 전 10,000원은 분할 후 기준으로 1,000원이다
        assert prices["2026-01-02"] == pytest.approx(1_000, rel=1e-6)
        assert prices["2026-01-05"] == pytest.approx(1_000, rel=1e-6)
        # 분할 당일과 그 뒤는 그대로
        assert prices["2026-01-06"] == pytest.approx(980)
        assert prices["2026-01-07"] == pytest.approx(1_000)

    def test_수정종가로_보면_수익률이_정상이다(self) -> None:
        days = [day("2026-01-05", 10_000, 0.0), day("2026-01-06", 980, -2.0)]
        out = adj.adjust(days)
        before, after = out[0].adj_close, out[1].adj_close
        # 원자료로 보면 -90.2%, 수정주가로 보면 거래소가 말한 -2%
        assert round(980 / 10_000 - 1, 3) == -0.902
        assert (after / before - 1) == pytest.approx(-0.02, abs=1e-9)

    def test_기업행위가_없으면_원자료와_같다(self) -> None:
        days = [day("2026-01-02", 100, 1.0), day("2026-01-05", 103, 3.0), day("2026-01-06", 101, -1.94)]
        out = adj.adjust(days)
        assert [a.adj_close for a in out] == pytest.approx([100, 103, 101])
        assert all(a.factor == 1.0 for a in out)

    def test_분할이_두_번이면_곱해진다(self) -> None:
        days = [
            day("2026-01-02", 10_000, 0.0),
            day("2026-01-05", 1_000, 0.0),  # 1:10
            day("2026-01-06", 100, 0.0),  # 다시 1:10
        ]
        out = adj.adjust(days)
        assert out[0].adj_close == pytest.approx(100, rel=1e-6)
        assert out[1].adj_close == pytest.approx(100, rel=1e-6)
        assert out[2].adj_close == pytest.approx(100)

    def test_빈_입력과_한_줄(self) -> None:
        assert adj.adjust([]) == []
        one = adj.adjust([day("2026-01-02", 500, 1.0)])
        assert one[0].adj_close == 500 and one[0].factor == 1.0

    def test_기업행위_목록에_날짜와_계수가_남는다(self) -> None:
        days = [day("2026-01-02", 10_000, 0.0), day("2026-01-05", 980, -2.0)]
        assert adj.actions(days) == [("2026-01-05", pytest.approx(0.1))]  # 0.098 / 0.98


class Test구멍_위의_기업행위:
    """**계수 식은 "앞 행 = 바로 전 거래일" 을 전제한다** (docs/infra.md 25.132).

    수집이 하루 빠지면 그 전제가 깨진다. 계수가 기업행위가 아니라 **빠진 구간의 수익률**이
    되고, 그것이 2% 를 넘으면 기업행위로 오인된다. 수정계수는 **그 이전 전체**에 곱해지므로
    한 번 틀리면 그 종목의 역사가 통째로 몇 % 밀린다 — 화면에도 백테스트에도 조용히 들어간다.

    **여기서 계수를 바꾸지 않는다.** 국내 액면분할은 매매거래정지를 동반해 진짜 기업행위도
    구멍 위에 앉는다. 둘을 가르려면 한국거래소가 정지 종목의 행을 주는지 알아야 하는데
    확인되지 않았다 `[확인필요]`. 그래서 **표시만 한다.**
    """

    def test_앞_행과의_일수를_적어_둔다(self) -> None:
        days = [
            adj.Day("2026-09-01", 1000.0, None),
            adj.Day("2026-09-02", 1010.0, 1.0),
            adj.Day("2026-09-30", 1020.0, 0.99),
        ]
        결과 = adj.adjust(days)
        assert [a.gap_days for a in 결과] == [0, 1, 28]

    def test_이어진_날의_기업행위는_의심하지_않는다(self) -> None:
        # 1:2 분할. 앞 행이 바로 전 거래일이면 계수를 믿을 수 있다
        days = [
            adj.Day("2026-09-01", 1000.0, None),
            adj.Day("2026-09-02", 500.0, 0.0),
        ]
        결과 = adj.adjust(days)
        assert adj.is_action(결과[1].factor)
        assert not adj.is_suspect(결과[1])
        assert adj.suspect_actions(결과) == []

    def test_구멍을_건너뛴_기업행위는_의심한다(self) -> None:
        # 한 달이 비었다. 그 사이 시세가 반 토막 났는지 분할이 있었는지 알 수 없다
        days = [
            adj.Day("2026-08-01", 1000.0, None),
            adj.Day("2026-09-10", 500.0, 0.0),
        ]
        결과 = adj.adjust(days)
        assert adj.is_action(결과[1].factor)
        assert adj.is_suspect(결과[1]), "구멍 위인데 표시하지 않았다"
        assert [a.date for a in adj.suspect_actions(결과)] == ["2026-09-10"]

    def test_하루_구멍도_의심한다(self) -> None:
        """간격 2일(09-22 한 거래일 빠짐)은 11일 문턱에 안 걸려 그날 +10% 가 이력에서 지워졌다 (docs/infra.md 25.569)."""
        결과 = adj.adjust([adj.Day("2026-09-21", 10000.0, 0.0), adj.Day("2026-09-23", 11110.0, 1.0)])
        assert adj.is_suspect(결과[1])
        # 연휴(거래일이 빠지지 않음)는 날짜가 벌어져도 의심하지 않는다 — 추석 09-24~09-27 휴장 뒤
        연휴 = adj.adjust([adj.Day("2026-09-23", 1000.0, 0.0), adj.Day("2026-09-28", 500.0, 0.0)])
        assert not adj.is_suspect(연휴[1])

    def test_구멍이어도_기업행위가_아니면_조용하다(self) -> None:
        """거짓 경보를 쌓으면 사람이 경고를 안 읽게 된다. 긴 연휴는 늘 있다."""
        days = [
            adj.Day("2026-08-01", 1000.0, None),
            adj.Day("2026-09-10", 1000.0, 0.0),
        ]
        결과 = adj.adjust(days)
        assert adj.suspect_actions(결과) == []

    def test_문턱을_metrics_에서_가져다_쓴다(self) -> None:
        """**같은 사실을 두 번 재지 않는다** (25.0 「한 규칙이 두 곳에 있다」).

        최장 휴장 간격 11일은 `metrics` 가 `exchange_calendars` 로 실측한 값이다.
        여기서 다시 적으면 한쪽만 고쳐질 때 갈라진다.
        """
        from batch.services.metrics import MAX_SESSION_GAP_DAYS

        assert adj._최장_휴장_간격() == MAX_SESSION_GAP_DAYS

        # 그 값이 실제로 판정을 움직이는지 본다. 상수만 맞춰 두고 안 쓰면 장식이다 (25.108)
        # 앞 행 날짜를 모를 때(달력으로 셀 수 없을 때)의 물러난 규칙이다 (25.569)
        딱_맞음 = adj.Adjusted("2026-09-10", 1.0, 0.5, gap_days=MAX_SESSION_GAP_DAYS)
        하나_더 = adj.Adjusted("2026-09-10", 1.0, 0.5, gap_days=MAX_SESSION_GAP_DAYS + 1)
        assert not adj.is_suspect(딱_맞음)
        assert adj.is_suspect(하나_더)


def test_작업이_구멍을_세고_말한다() -> None:
    """**부르는 곳이 없으면 함수를 하나 더 만든 것일 뿐이다** (25.123 에서 배운 것)."""
    import ast
    from pathlib import Path

    본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "adjust_kr.py").read_text("utf-8")
    나무 = ast.parse(본문)
    부름 = [
        n
        for n in ast.walk(나무)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "suspect_actions"
    ]
    assert 부름, "adjust_kr 가 suspect_actions 를 안 부른다 — 구멍 위 판정이 조용히 지나간다"
    assert "stocks_with_suspect_action" in 본문, "실행 기록(step_log)에 남지 않으면 나중에 볼 수 없다"
