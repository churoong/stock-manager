"""25.1656 — 리포트 머리의 기업행위 멈춤 줄도 경계(모든 계좌)와 이어 멈춤(든 계좌만)을 나눠 센다 (25.1653 과 같은 결함)."""

import inspect

from batch.jobs import paper as job
from batch.services import paper as pp


def test_나눠_센다() -> None:
    assert pp.frozen_line({1, 2, 3}, {3}) == (
        "가상모의투자 멈춘 종목 — 기업행위 경계 2개(모든 계좌) · 이어 멈춤 1개(경계 전부터 든 계좌만)"
        " — 멈춘 계좌는 원가로 평가(docs/paper.md 2.3)")
    assert "기업행위 경계" not in pp.frozen_line({3}, {3}) and "이어 멈춤 1개" in pp.frozen_line({3}, {3})
    assert "이어 멈춤" not in pp.frozen_line({1, 2}, set())


def test_실행이_이_줄을_쓴다() -> None:
    assert 'pp.frozen_line(frozen, set(기록.get("action_sticky") or []))' in inspect.getsource(job.run_step)
