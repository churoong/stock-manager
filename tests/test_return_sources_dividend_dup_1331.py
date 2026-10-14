"""상승의 출처의 "그 사이 배당" 이 같은 해 배당을 두세 번 더하지 않는다 (docs/infra.md 25.1331, 배당 감사 #1).

국내 사업보고서 하나가 당기·전기·전전기 배당을 함께 실어 한 회계연도가 최대 세 행(`report_year` 다름)이다."""

from __future__ import annotations

from datetime import date

from batch.services import event_history as ev
from tests.test_event_history_1063 import _날, _재무


def _출처(배당: list[dict]) -> dict | None:
    날 = _날(1300, date(2021, 1, 1))
    c = [100.0] * 1299 + [150.0]
    재무 = [_재무(2021, "11011", "2022-03-20", 0, ni=100), _재무(2024, "11011", "2025-03-20", 0, ni=120)]
    return ev.return_sources(재무, 배당, 날, c, 1.0)


def _행(fy: int, ry: int, total: float | None) -> dict:
    return {"fiscal_year": fy, "report_year": ry, "cash_dividend_total": total}


def test_한_해는_가장_늦은_보고서_한_행만() -> None:
    # 2022~2024 배당 10씩. 2022 는 세 보고서(2022·2023·2024), 2023 은 둘, 2024 는 하나에 실렸다
    배당 = [_행(2022, 2022, 10), _행(2022, 2023, 10), _행(2022, 2024, 10), _행(2023, 2023, 10), _행(2023, 2024, 10),
          _행(2024, 2024, 10)]  # fmt: skip
    assert _출처(배당)["dividends"] == 0.3  # 예전: 0.6 (6행 합)


def test_옛_보고서의_빈_값이_배당_몫을_지우지_않는다() -> None:
    배당 = [_행(2022, 2022, None), _행(2022, 2024, 10), _행(2023, 2024, 10), _행(2024, 2024, 10)]
    assert _출처(배당)["dividends"] == 0.3  # 예전: None — 옛 보고서 행 하나가 비어 통째로 빠졌다


def test_정정으로_값이_바뀌면_늦은_보고서() -> None:
    배당 = [_행(2022, 2022, 99), _행(2022, 2023, 10), _행(2023, 2023, 10), _행(2024, 2024, 10)]
    assert _출처(배당)["dividends"] == 0.3
