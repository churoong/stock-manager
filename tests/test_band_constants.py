"""밸류에이션 밴드의 숫자는 **한 곳에서만 온다** (docs/infra.md 25.108).

넷이 코드 곳곳에 글자로 박혀 있었다.

* 진입 분위 **30** — 상수 `BAND_ENTRY_PERCENTILE` 이 있는데 **판정은 `band.p30` 을 직접 봤다**
* 손절 분위 **20** — 상수가 아예 없었다
* 창 **750** — `services/valuation_band` · `jobs/signals` · `jobs/backtest` 에 따로 세 번
* 최소 표본 **250** — 기본 인자 · 다른 모듈의 상수 · 화면 글에 따로

가장 나쁜 것이 첫째다. `BAND_ENTRY_PERCENTILE` 을 20 으로 바꾸면 **동작은 그대로이고
근거표 글만 바뀐다.** 사용자는 "PBR ≤ 밴드 20% 분위" 라고 적힌 근거를 보는데 시스템은
30% 로 판정한 것이다 — CLAUDE.md 가 "사용자가 확인할 수 있어야 한다" 고 못 박은 자리다.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from batch.jobs import backtest as bt_job
from batch.jobs import signals as sg_job
from batch.services import signals as sg
from batch.services import valuation_band as vb

뿌리 = Path(__file__).resolve().parent.parent


class Test한_곳에서_온다:
    def test_창과_표본을_가져다_쓴다(self) -> None:
        assert sg_job.BAND_DAYS == sg.BAND_DAYS
        assert bt_job.LONG_BAND_DAYS == sg.BAND_DAYS
        assert vb.BAND_DAYS == sg.BAND_DAYS
        assert vb.MIN_SAMPLE == sg.BAND_MIN_SAMPLE

    def test_숫자를_다시_적지_않는다(self) -> None:
        """**값이 같은지가 아니라 다시 적었는지를 본다.**

        `BAND_DAYS = 750` 이 두 곳에 있으면 지금은 통과한다. 갈라지는 것은 나중이다.
        """
        다시적음 = []
        for 이름 in ("batch/jobs/signals.py", "batch/jobs/backtest.py", "batch/services/valuation_band.py"):
            본문 = (뿌리 / 이름).read_text(encoding="utf-8")
            이름들 = r"(?:\w*BAND_DAYS|MIN_SAMPLE|BAND_MIN_SAMPLE|\w*_PERCENTILE)"
            for m in re.finditer(rf"^\s*({이름들})\s*=\s*(\d+)\s*(?:#.*)?$", 본문, re.M):
                다시적음.append(f"{이름}: {m.group(1)} = {m.group(2)}")
        assert not 다시적음, (
            "밴드 숫자를 글자로 다시 적었다. 정의처는 batch/services/signals 다:\n  " + "\n  ".join(다시적음)
        )

    def test_표본_기본값도_상수에서_온다(self) -> None:
        기본 = inspect.signature(sg.build_band).parameters["min_sample"].default
        assert 기본 == sg.BAND_MIN_SAMPLE

    def test_기간_글이_창을_따라간다(self) -> None:
        """창을 바꾸면 "3년" 이라는 글도 따라 바뀌어야 한다."""
        assert sg.BAND_YEARS == sg.BAND_DAYS // 250 == 3


class Test판정이_상수를_본다:
    """**여기가 본론이다.** 상수를 바꿔도 판정이 안 바뀌면 상수가 아니라 장식이다."""

    @staticmethod
    def _본문(fn) -> str:
        """주석과 `data["band_pNN"] = …` 을 뺀 본문.

        주석에 `band.p30` 이라고 **적어 둔 설명**까지 물면 거짓 양성이다. 저장할 열 이름
        (`data["band_p20"] = inp.band.p20`)도 남아야 한다 — 그 열은 `signals` 표에 그대로 들어간다.
        막는 것은 **판정·계산에 값을 집어 오는 쪽**이다.
        """
        줄 = []
        for line in inspect.getsource(fn).splitlines():
            몸통 = line.split("#", 1)[0]
            if re.match(r'\s*data\["band_p\d+"\]\s*=', 몸통):
                continue
            줄.append(몸통)
        return "\n".join(줄)

    @pytest.mark.parametrize(
        "fn",
        [sg.long_term, sg.buy_zone, sg.judgement, sg.criteria, bt_job.long_entry],
        ids=["long_term", "buy_zone", "judgement", "criteria", "backtest.long_entry"],
    )
    def test_분위를_상수로_고른다(self, fn) -> None:
        본문 = self._본문(fn)
        # `data["band_p30"] = …` 처럼 **저장할 열 이름**은 남는다. 막는 것은 값을 집어 오는 쪽이다
        직접읽기 = re.findall(r"(?:band|inp\.band)\.p\d\d", 본문)
        assert not 직접읽기, (
            f"{fn.__name__} 이 분위를 글자로 집는다: {sorted(set(직접읽기))}.\n"
            "  `band.at(BAND_ENTRY_PERCENTILE)` 처럼 상수에서 고른다 — 안 그러면 상수를 바꿔도 안 따라온다"
        )

    def test_진입_분위를_바꾸면_판정이_따라_바뀐다(self, monkeypatch) -> None:
        """실제로 움직이는지 본다. 글자만 읽는 검사로는 모자라다."""
        band = sg.Band(p20=0.50, p30=0.80, p50=1.10, p80=1.60, sample=750)
        inp = sg.SignalInput(
            stock_id=1, ticker="A", name="가", market="KOSPI", closes=[100.0],
            factor_scores={"quality": 71.0, "value": 83.0}, pbr_now=0.60, band=band,
        )
        assert sg.long_term(inp)[0] is True  # 0.60 <= 0.80(30% 분위)

        monkeypatch.setattr(sg, "BAND_ENTRY_PERCENTILE", 20)
        assert sg.long_term(inp)[0] is False, "20% 분위(0.50) 아래가 아닌데 통과했다 — 상수가 안 먹는다"

    def test_손절_분위를_바꾸면_구간_하단이_따라_바뀐다(self, monkeypatch) -> None:
        # 진입 분위를 높게 잡아 상단이 현재가로 고정되게 한다 — 하단만 움직이는지 보려는 것이다
        band = sg.Band(p20=0.50, p30=0.55, p50=1.10, p80=1.60, sample=750)
        inp = sg.SignalInput(
            stock_id=1, ticker="A", name="가", market="KOSPI", closes=[100.0],
            factor_scores={"quality": 71.0, "value": 83.0}, pbr_now=0.60, band=band,
        )
        _, data = sg.long_term(inp)
        monkeypatch.setattr(sg, "BAND_ENTRY_PERCENTILE", 80)
        bps = 100.0 / 0.60

        기본_하단, 상단 = sg.buy_zone("long", inp, data)
        assert 기본_하단 == pytest.approx(0.50 * bps)
        assert 상단 == pytest.approx(100.0)

        monkeypatch.setattr(sg, "BAND_STOP_PERCENTILE", 30)
        바뀐_하단, _ = sg.buy_zone("long", inp, data)
        assert 바뀐_하단 == pytest.approx(0.55 * bps), "손절 분위를 바꿨는데 구간 하단이 그대로다"


# 문서(`docs/signals.md`)와 대 보는 일은 **`tests/test_doc_constants.py` 가 집이다.**
# 여기에도 두면 같은 진실이 두 벌이 되고, 한쪽만 고쳐질 때 갈라진다 (docs/infra.md 25.49).
# 그쪽 `Test장기_신호` 가 진입 분위·창·손절 분위를 본다.
