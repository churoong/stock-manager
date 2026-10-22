"""25.1654 — KIS모의투자 괴리 알림은 고장 알림의 "원인" 머리를 쓰지 않는다 (25.1631 감사 [권고])."""

import inspect
import re

from batch.jobs import daily
from batch.notify import formatter


def test_괴리는_내용_머리_실패는_원인_머리() -> None:
    괴리 = formatter.failure_alert(market="KR", job_name="daily_kr", error_text="KIS모의투자 국내가 …", last_success=None,
                                 title="KIS모의투자 괴리", report_note=False, head="내용")  # fmt: skip
    assert "\n내용\n" in 괴리 and "원인" not in 괴리
    실패 = formatter.failure_alert(market="KR", job_name="daily_kr", error_text="x", last_success=None)
    assert "\n원인\n" in 실패


def test_일일_배치가_괴리를_내용_머리로_보낸다() -> None:
    본문 = inspect.getsource(daily)
    # 줄바꿈·들여쓰기에 기대지 않게(25.1654 교차검증 [참고])
    assert re.search(r'title="KIS모의투자 괴리",\s*report_note=False,\s*head="내용"\)', 본문)
