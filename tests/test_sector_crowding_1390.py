"""1부 업종 쏠림 — 함께 사면 2부 섹터 상한에 걸릴 때만 한 줄 (docs/infra.md 25.1390, docs/factors.md 12.28 P6)."""

from __future__ import annotations

from batch.services import report_picks as rp
from tests.test_report_picks import row


def _종목(sid: int, sector: str | None, score: float) -> rp.SignalRow:
    return row(sid, score=score, sector=sector)


def test_종목_상한_곱하기_수가_섹터_상한을_넘을_때만_걸린다() -> None:
    넷 = [_종목(i, "반도체", 90 - i) for i in range(1, 5)] + [_종목(5, "은행", 80)]
    c = rp.sector_crowding(넷, 10.0, 30.0)
    assert (c["k"], c["sector"], c["trip"]) == (4, "반도체", True)  # 10% × 4 = 40% > 30%
    셋 = [_종목(i, "반도체", 90 - i) for i in range(1, 4)] + [_종목(4, "은행", 80), _종목(5, "화학", 79)]
    assert rp.sector_crowding(셋, 10.0, 30.0)["trip"] is False  # 10% × 3 = 30% 는 넘지 않는다
    # 문턱은 설정의 두 상한에서만 — 종목 상한 15% 면 셋도 걸린다
    assert rp.sector_crowding(셋, 15.0, 30.0)["trip"] is True


def test_업종_모름은_세지_않고_적는다() -> None:
    rows = [_종목(1, "반도체", 90), _종목(2, None, 89), _종목(3, "", 88), _종목(4, "반도체", 87)]
    c = rp.sector_crowding(rows, 20.0, 30.0)
    assert (c["k"], c["unknown"], c["trip"]) == (2, 2, True)
    줄 = rp.sector_crowding_line(c)
    assert 줄 is not None and "업종 모름 2종목 제외" in 줄 and "섹터 상한 30%" in 줄


def test_같은_종목의_여러_기간_신호는_한_번만_센다() -> None:
    rows = [_종목(1, "반도체", 90), row(1, horizon="short", score=90, sector="반도체"), _종목(2, "반도체", 89)]
    assert rp.sector_crowding(rows, 10.0, 30.0)["k"] == 2


def test_1부_끝에_한_줄_고른_종목은_그대로() -> None:
    넷 = [_종목(i, "반도체", 90 - i) for i in range(1, 5)] + [_종목(5, "은행", 80)]
    out = rp.compose(넷, 10_000_000, "2026-10-14", currency="KRW")
    assert "1부 4종목이 반도체 업종입니다" in out.text
    assert {r.stock_id for r in out.chosen} == {1, 2, 3, 4, 5}
    assert out.crowding is not None and out.crowding["trip"] is True
    안걸림 = rp.compose(넷[:3] + [_종목(5, "은행", 80)], 10_000_000, "2026-10-14", currency="KRW")
    assert "업종입니다" not in 안걸림.text
