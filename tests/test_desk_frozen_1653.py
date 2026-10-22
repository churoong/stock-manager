"""25.1653 — 운용 화면 확인할 일: 기업행위 멈춤을 경계(모든 계좌)와 이어 멈춤(경계 전부터 든 계좌만)으로 나눠 센다."""

from batch.services import desk


def _글(paper: dict) -> list[str]:
    return [c["text"] for c in desk.checks(halted=False, paper=paper, broker=None, recon_bad=[], warnings=0)]


def test_경계와_이어_멈춤을_나눈다() -> None:
    assert _글({"frozen": [1, 2, 3], "action_sticky": [3]}) == [
        "기업행위 경계로 멈춘 종목 2개 — 모든 계좌가 사고팔지 않습니다 · 경계 전부터 든 계좌만 이어 멈춘 종목 1개"]
    assert _글({"frozen": [3], "action_sticky": [3]}) == ["경계 전부터 든 계좌만 이어 멈춘 종목 1개"]
    assert _글({"frozen": [1, 2]}) == ["기업행위 경계로 멈춘 종목 2개 — 모든 계좌가 사고팔지 않습니다"]
    assert _글({"frozen": []}) == []
