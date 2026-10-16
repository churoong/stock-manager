"""KIS모의투자 실행 품질 · 괴리 알림 (docs/paper.md 15.8, docs/desk.md 4장, docs/infra.md 25.1504·25.1507)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import broker_mirror as job
from batch.jobs import daily
from batch.notify import formatter
from batch.services import broker_mirror as bm
from batch.services import desk
from tests.test_kis_vts_1475 import _at, _Sess, _줄, c  # noqa: F401 — c 는 픽스처
from tests.test_paper_job_1445 import Client


def test_kept_pct_is_virtual_fill_realisation() -> None:
    """둘 다 참 2 · 가상만 참 1 → 가상이 찼다고 한 셋 가운데 KIS 도 찬 것 둘 = 66.67%"""
    s = bm.compare([
        _줄("buy", "filled", "filled", fq=3, avg=10_100.0, ip=10_000.0),
        _줄("sell", "filled", "filled", fq=3, avg=19_800.0, ip=20_000.0),
        _줄("buy", "unfilled", "filled", ip=9_800.0),
        _줄("buy", "partial", "expired", fq=1, avg=9_700.0),
    ])  # fmt: skip
    assert s["kept_pct"] == pytest.approx(200 / 3)
    assert bm.compare([])["kept_pct"] is None


def _요약(n: int, match: float | None, *, kept: float | None = 95.0, buy: float | None = None, sell: float | None = None,
         internal_only: int = 5, kis_only: int = 1, both: int = 25) -> dict:  # fmt: skip
    return {"days": 28, "n": n, "match_pct": match, "kept_pct": kept, "both": both, "internal_only": internal_only,
            "kis_only": kis_only, "slip_buy": {"n": n if buy is not None else 0, "avg": buy},
            "slip_sell": {"n": n if sell is not None else 0, "avg": sell}}  # fmt: skip


def test_diverge_active_hysteresis_and_thin_samples() -> None:
    assert bm.diverge_active(_요약(30, 79.9), set()) == {"match"}  # 켜는 문턱 아래
    assert bm.diverge_active(_요약(30, 80.0), set()) == set()  # 문턱 그 값은 넘은 것이 아니다
    # 켜진 칸은 꺼지는 문턱(80 + 5)까지 돌아와야 끈다 — 79.9 → 80.0 → 79.9 로 두 번 울리지 않게(검토 5)
    assert bm.diverge_active(_요약(30, 82.0), {"match"}) == {"match"}
    assert bm.diverge_active(_요약(30, 85.0), {"match"}) == set()
    # 표본이 모자라면 어제 상태 그대로 — 미국처럼 표본이 20 둘레를 오가도 깜빡이지 않는다
    assert bm.diverge_active(_요약(19, 99.0), {"match"}) == {"match"}
    assert bm.diverge_active(_요약(19, 10.0), set()) == set()
    # 실현율 — 가상이 찼다고 한 표본(둘 다 참 + 가상만 참)으로 판단(검토 2)
    assert bm.diverge_active(_요약(30, 90.0, kept=84.0), set()) == {"kept"}
    assert bm.diverge_active(_요약(30, 90.0, kept=84.0, both=10, internal_only=5), set()) == set()  # 가상 참 15 < 20
    assert bm.diverge_active(_요약(30, 90.0, kept=88.0), {"kept"}) == {"kept"}  # 90 까지 돌아와야
    # 가격 차이 — 0.5 위로 켜고 0.4 이하로 끈다
    assert bm.diverge_active(_요약(30, 90.0, buy=0.51), set()) == {"slip_buy"}
    assert bm.diverge_active(_요약(30, 90.0, buy=0.45), {"slip_buy"}) == {"slip_buy"}
    assert bm.diverge_active(_요약(30, 90.0, buy=0.4), {"slip_buy"}) == set()


def test_diverge_text_says_which_way() -> None:
    낙관 = bm.diverge(_요약(30, 70.0, internal_only=8, kis_only=1), ["match"], "KR")
    assert 낙관 is not None and 낙관.startswith("KIS모의투자 국내가 가상모의투자와 벌어졌습니다(지난 28일, 주문 30)")
    assert "체결 일치 70% — 문턱 80%. 가상만 참 8 · KIS만 참 1: 가상만 참이 더 많음 — 가상 체결 판정이 낙관" in 낙관
    비관 = bm.diverge(_요약(30, 70.0, internal_only=1, kis_only=8), ["match"], "US")
    assert 비관 is not None and 비관.startswith("KIS모의투자 미국이") and "가상 체결 판정이 비관" in 비관
    글 = bm.diverge(_요약(30, 90.0, kept=80.0, buy=0.8), ["kept", "slip_buy"], "KR")
    assert 글 is not None and "실현율 80%" in 글 and "매수 가격 차이 평균 +0.80% (n=30) — 문턱 +0.5%" in 글
    assert bm.diverge(_요약(30, 70.0), [], "KR") is None


def test_alert_until_sent_and_not_twice(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811 — 픽스처
    """켜졌는데 안 보낸 칸만 알린다. 보냄 표시 전에 죽으면(검토 1) 다음 실행이 다시, 보낸 뒤 같은 날 다시 돌리면(검토 4) 안 간다"""
    monkeypatch.setattr(job.bm, "compare", lambda _rows: _요약(30, 60.0))
    첫 = job.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 첫["diverge_keys"] == ["match"] and "체결 일치 60%" in 첫["diverge"]
    # 보냄 표시를 못 했다(발송 실패·실행이 죽음) — 다음 실행이 다시 알린다
    둘 = job.run(c, "KR", _at("08:40", "2026-10-19"), session=_Sess(), sleep=lambda _x: None,
                clock=lambda: _at("08:40", "2026-10-19"))  # fmt: skip
    assert 둘["diverge_keys"] == ["match"]
    job.mark_diverge_sent(c, "KR", ["match"], "2026-10-19")
    셋 = job.run(c, "KR", _at("09:00", "2026-10-19"), session=_Sess(), sleep=lambda _x: None,
                clock=lambda: _at("09:00", "2026-10-19"))  # fmt: skip
    assert "diverge" not in 셋  # 같은 날 다시 돌려도 두 번 가지 않는다
    # 회복하면 보냄 표시를 지우고, 다시 넘으면 다시 알린다
    monkeypatch.setattr(job.bm, "compare", lambda _rows: _요약(30, 90.0))
    job.run(c, "KR", _at("08:40", "2026-10-20"), session=_Sess(), sleep=lambda _x: None, clock=lambda: _at("08:40", "2026-10-20"))
    assert db.get_setting(c, "kis_diverge", None)["kis_vts"] == {"active": [], "sent": {}}
    monkeypatch.setattr(job.bm, "compare", lambda _rows: _요약(30, 60.0))
    넷 = job.run(c, "KR", _at("08:40", "2026-10-21"), session=_Sess(), sleep=lambda _x: None,
                clock=lambda: _at("08:40", "2026-10-21"))  # fmt: skip
    assert 넷["diverge_keys"] == ["match"]


def test_alert_wiring() -> None:
    # 괴리는 `_kis_알림문` 이 아니라 따로(보낸 뒤 표시) — 키 일부만·단계 실패만 여기
    assert daily._kis_알림문({"diverge": "벌어짐"}) is None
    assert daily._kis_알림문({"error": "x"}) == "KIS모의투자 단계 실패: x"
    확인 = desk.checks(halted=False, paper=None, broker={"diverge": "벌어짐", "diverge_error": "ValueError"}, recon_bad=[],
                     warnings=0)  # fmt: skip
    assert 확인[0] == {"level": "high", "text": "벌어짐"}
    assert "KIS 괴리 판정 실패(ValueError)" in 확인[1]["text"]
    # KIS 알림에는 "리포트는 웹에" 줄을 붙이지 않는다(검토 7)
    글 = formatter.failure_alert(market="KR", job_name="daily_kr", error_text="벌어짐", last_success=None,
                                title="KIS모의투자 괴리", report_note=False)  # fmt: skip
    assert "리포트" not in 글 and "KIS모의투자 괴리" in 글


def test_notify_failure_reports_whether_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.notify import telegram

    monkeypatch.setattr(daily.db, "last_successful_run", lambda *_a: None)
    보낸 = []
    monkeypatch.setattr(telegram, "send", lambda m: 보낸.append(m))
    assert daily._notify_failure(Client(), "KR", "daily_kr", "x", dry_run=False, title="KIS모의투자 괴리") is True

    def 실패(_m: str) -> None:
        raise RuntimeError("망")

    monkeypatch.setattr(telegram, "send", 실패)
    assert daily._notify_failure(Client(), "KR", "daily_kr", "x", dry_run=False, title="KIS모의투자 괴리") is False
