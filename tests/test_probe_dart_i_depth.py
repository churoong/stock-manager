"""DART 거래소공시 과거 깊이 탐침 (docs/infra.md 25.1674) — 해마다 건수·실적 공시 수·가장 이른 접수일, 호출 상한, 워크플로."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("probe_dart_i_depth", ROOT / "scripts" / "probe_dart_i_depth.py")
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def test_해마다_건수와_실적_공시_수() -> None:
    rows = [
        {"rcept_no": "20260130800001", "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)"},
        {"rcept_no": "20260115800002", "report_nm": "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"},  # 정정 — 실적 아님
        {"rcept_no": "20250130800003", "report_nm": "영업(잠정)실적(공정공시)"},
        {"rcept_no": "20250105800004", "report_nm": "연결재무제표기준영업실적등에대한전망(공정공시)"},  # 전망 — 실적 아님
        {"rcept_dt": "20190131", "report_nm": "매출액또는손익구조30%(대규모법인은15%)이상변경"},  # 접수번호 없으면 rcept_dt
        {"rcept_no": "", "report_nm": "날짜 없음"},
    ]
    해, 실적, 이른 = probe.summarize_rows(rows)
    assert 해 == {"2026": 2, "2025": 2, "2019": 1}
    assert 실적 == {"2026": 1, "2025": 1, "2019": 1}
    assert 이른 == "20190131"


def test_호출_상한에서_멈춘다(monkeypatch) -> None:  # noqa: ANN001
    부름: list[dict] = []

    class R:
        def json(self) -> dict:
            return {"status": "000", "list": [], "total_page": 1}

    monkeypatch.setattr(probe.requests, "get", lambda url, timeout, params: 부름.append(params) or R())
    c = probe.Caller("k", max_calls=2)
    assert c.page({}) and c.page({}) and c.page({}) is None
    assert len(부름) == 2 and all(p["pblntf_ty"] == "I" for p in 부름)


def test_표본일은_국내_거래일이다() -> None:
    """25.1680 — 평일만 고르면 2019-02-04(설 연휴 휴장)가 걸려 그해만 0건으로 읽혔다(교차검증 [권고])."""
    from datetime import date

    assert probe.sample_day(2019) == date(2019, 2, 7) and probe.sample_day(2026) == date(2026, 2, 3)


def test_회사는_해마다_첫_쪽으로_세고_이름을_찍는다(capsys) -> None:  # noqa: ANN001
    부름: list[dict] = []

    class C:
        def page(self, params: dict) -> dict:
            부름.append(params)
            return {"status": "000", "total_count": 7, "total_page": 1,
                    "list": [{"rcept_no": f"{params['bgn_de'][:4]}0130800001", "corp_name": "가나",
                              "report_nm": "영업(잠정)실적(공정공시)"}]}  # fmt: skip

    probe.corp_years(C(), "00126380", range(2020, 2023))
    assert [(p["bgn_de"], p["end_de"], p["page_no"]) for p in 부름] == [
        ("20200101", "20201231", 1), ("20210101", "20211231", 1), ("20220101", "20221231", 1)]
    out = capsys.readouterr().out
    assert "2021: status 000 전체 7건" in out and "실적 1" in out and "회사 이름 가나" in out


def test_워크플로는_손으로만_돌고_결과는_비공개로() -> None:
    src = (ROOT / ".github" / "workflows" / "probe-dart-i-depth.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in src and "schedule" not in src and "TURSO" not in src
    assert "ops_tee.py" in src and "publish_output.py" in src and "probe_dart_i_depth.py" in src
    적는, 올리는 = re.search(r"ops_tee\.py (\S+)", src), re.search(r"--file (\S+)", src)
    assert 적는 and 올리는 and 적는.group(1) == 올리는.group(1)
