"""밴드 표기 — 실제로 덮은 기간 (docs/infra.md 25.1368, 24회차 P1)."""

from __future__ import annotations

from batch.services import signals as sg
from tests.test_signals import long_input


def test_표본이_창을_채우면_3년_아니면_실제_기간() -> None:
    assert sg.band_span(750) == "3년"
    assert sg.band_span(720) == "3년"  # 95% 이상
    assert sg.band_span(412) == "1.6년"
    assert sg.band_span(250) == "1.0년"


def test_판정표가_실제_기간을_쓴다() -> None:
    짧은 = sg.Band(p20=0.8, p30=0.9, p50=1.0, p80=1.3, sample=412)
    inp = long_input(band=짧은, pbr_now=0.85)
    글 = str(sg.judgement("long", inp, sg.long_term(inp)[1]))
    assert "1.6년 30% 분위" in 글 and "3년 30% 분위" not in 글
