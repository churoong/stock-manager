"""KorFinASC 채점기의 순수한 부분 (batch/jobs/sentiment.py).

**왜 이 테스트가 생겼나.** 기존 감성 테스트는 `korfinasc_scorer` 를 통째로 가짜로 바꾼다.
그래서 **채점기 내부는 한 번도 검증된 적이 없었다** — 모델을 받아야 도는 코드라 손대지
않았던 것이다. 그런데 2026-09-18 에 `채점·집계` 단계가 실패했고(원인 미확정, infra 25.19),
바로 이 검증 안 된 자리에 조용히 망가질 수 있는 줄이 둘 있었다.

1. `labels[i]` — 허깅페이스 설정은 JSON 이라 `id2label` 키가 **문자열**로 올 수 있다.
   그러면 모델을 다 받아 놓고 채점 직전에 `KeyError` 로 터진다
2. `named.get("positive", 0.0)` — 라벨 이름이 다른 모델을 물리면 **모든 기사가 0 점**이 된다.
   터지지 않고 조용히 죽는다. 매일 도는데 아무도 눈치채지 못한다 — 더 나쁘다

모델을 받지 않는다. 확률 한 줄과 라벨 표만 본다.
"""

from __future__ import annotations

import pytest

from batch.jobs.sentiment import label_map, score_from_probs


class Test라벨_표:
    def test_정수_키를_그대로_받는다(self) -> None:
        assert label_map({0: "positive", 1: "negative"}) == {0: "positive", 1: "negative"}

    def test_문자열_키를_정수로_고친다(self) -> None:
        """허깅페이스 설정이 JSON 이라 이렇게 올 수 있다. 이것이 KeyError 의 원인이었다."""
        assert label_map({"0": "positive", "1": "negative"}) == {0: "positive", 1: "negative"}

    def test_이름을_소문자로_맞춘다(self) -> None:
        assert label_map({"0": "Positive", "1": "NEGATIVE"}) == {0: "positive", 1: "negative"}


class Test점수:
    labels = {0: "negative", 1: "neutral", 2: "positive"}

    def test_긍정에서_부정을_뺀다(self) -> None:
        점수, 확률 = score_from_probs([0.1, 0.2, 0.7], self.labels)
        assert 점수 == pytest.approx(0.6)
        assert 확률 == {"negative": 0.1, "neutral": 0.2, "positive": 0.7}

    def test_부정이_크면_음수다(self) -> None:
        점수, _ = score_from_probs([0.8, 0.1, 0.1], self.labels)
        assert 점수 == pytest.approx(-0.7)

    def test_중립뿐이면_0_에_가깝다(self) -> None:
        점수, _ = score_from_probs([0.05, 0.9, 0.05], self.labels)
        assert 점수 == pytest.approx(0.0)

    def test_범위를_벗어나지_않는다(self) -> None:
        """기사 점수는 −1~+1 이다 (CLAUDE.md 뉴스 감성 규칙)."""
        for row in ([1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.34, 0.33, 0.33]):
            점수, _ = score_from_probs(row, self.labels)
            assert -1.0 <= 점수 <= 1.0

    def test_문자열_키로_만든_표로도_돈다(self) -> None:
        """실제 경로는 label_map 을 거친다. 둘을 이어서 본다."""
        점수, _ = score_from_probs([0.1, 0.2, 0.7], label_map({"0": "NEGATIVE", "1": "Neutral", "2": "Positive"}))
        assert 점수 == pytest.approx(0.6)


class Test조용히_죽지_않는다:
    def test_라벨_이름이_다르면_터진다(self) -> None:
        """예전에는 모든 기사가 0 점이 됐다. 센티먼트 축이 죽은 채로 매일 도는 것이 가장 나쁘다."""
        with pytest.raises(ValueError, match="positive/negative"):
            score_from_probs([0.3, 0.7], {0: "label_0", 1: "label_1"})

    def test_한쪽만_있어도_돈다(self) -> None:
        """긍정만 있는 이진 모델은 받아 준다 — 규칙이 분명하다."""
        점수, _ = score_from_probs([0.3, 0.7], {0: "neutral", 1: "positive"})
        assert 점수 == pytest.approx(0.7)

    def test_라벨_수가_모자라면_터진다(self) -> None:
        """확률은 셋인데 라벨이 둘이면 조용히 넘어가면 안 된다."""
        with pytest.raises(KeyError):
            score_from_probs([0.2, 0.3, 0.5], {0: "negative", 1: "positive"})
