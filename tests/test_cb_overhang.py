"""전환사채 잠재 물량 사실 줄 (docs/analysis.md 55장, docs/factors.md 12.20 후보 3, docs/infra.md 25.1192)."""

from __future__ import annotations

from datetime import date

from batch.core import db
from batch.jobs import disclosure_reaction as job
from batch.services import cb_overhang as cb
from tests.test_portfolio_job import MemClient

#: 10-12 탐침에서 본 모양 그대로(값은 지어낸 것)
응답 = [
    {"rcept_no": "20260301000111", "bd_fta": "10,000,000,000", "cv_prc": "12,000", "cvisstk_cnt": "833,333",
     "cvisstk_tisstk_vs": "5.23", "cvrqpd_bgd": "2027년 03월 05일", "cvrqpd_edd": "2031년 02월 05일",
     "act_mktprcfl_cvprc_lwtrsprc": "8,400"},
    {"rcept_no": "20260301000999", "bd_fta": "-", "cv_prc": "-", "cvisstk_tisstk_vs": "-", "cvrqpd_bgd": "-",
     "cvrqpd_edd": "-", "act_mktprcfl_cvprc_lwtrsprc": "-"},
]  # fmt: skip


def test_응답에서_그_접수번호_행만_읽는다() -> None:
    v = cb.parse_row(응답, "20260301000111")
    assert v == cb.CbIssue(1e10, 12000.0, 833333.0, 5.23, "2027-03-05", "2031-02-05", 8400.0)
    assert cb.parse_row(응답, "없는번호") is None
    빈 = cb.parse_row(응답, "20260301000999")
    assert 빈 is not None and 빈.refix_floor is None and 빈.claim_end is None  # "-" 는 모름


def test_제목은_발행결정만_고르고_정정은_뺀다() -> None:
    assert cb.is_cb_title("주요사항보고서(전환사채권발행결정)")
    assert not cb.is_cb_title("[기재정정]주요사항보고서(전환사채권발행결정)")
    assert not cb.is_cb_title("주요사항보고서(신주인수권부사채권발행결정)")


def _e(decided: str, end: str | None, pct: float | None = 5.0, status: str = "ok") -> dict:
    return {"receipt_no": decided, "decided_at": decided, "status": status, "face_amount": 1e10, "conv_price": 12000.0,
            "refix_floor": 8400.0, "shares_pct": pct, "claim_start": "2026-04-01", "claim_end": end}  # fmt: skip


def test_줄은_1년_안_청구기간_남은_것만_말하고_당시_기준을_밝힌다() -> None:
    오늘 = date(2026, 10, 12)
    줄 = cb.line([_e("2026-03-01", "2031-01-01"), _e("2026-05-01", "2030-01-01", 2.0)], 오늘)
    assert 줄 is not None
    assert 줄.startswith("전환사채 잠재 물량 2건, 합 발행주식의 7.0%: 2026-05-01 결정")  # 새 것부터
    assert "전환가 12,000원(조정 하한 8,400원)" in 줄 and "청구 시작 2026-04-01" in 줄
    assert "발행 결정 당시 기준" in 줄 and "점수·결론에 넣지 않음" in 줄
    # 1년 넘은 결정·청구 끝난 것·종료일 모름·no_row 는 싣지 않고, 남는 것이 없으면 아무 말도 하지 않는다
    assert cb.line([_e("2025-10-01", "2031-01-01"), _e("2026-03-01", "2026-10-11"), _e("2026-03-01", None),
                    _e("2026-03-01", "2031-01-01", status="no_row")], 오늘) is None  # fmt: skip


def _mem() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, dart_corp_code, source, fetched_at)"
              " VALUES (1, '000001', 'KOSDAQ', 'KR', 'KRW', 'active', '00111', 't', 't')")  # fmt: skip
    for no, title in (("20260301000111", "주요사항보고서(전환사채권발행결정)"),
                      ("20260302000222", "[기재정정]주요사항보고서(전환사채권발행결정)"),
                      ("20260303000333", "주요사항보고서(유상증자결정)"),
                      ("20260304000444", "주요사항보고서(전환사채권발행결정)")):  # fmt: skip
        c.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, url, is_material,"
                  " source, fetched_at) VALUES (1, '00111', ?, ?, ?, 'u', 1, 't', 't')",
                  [no, title, f"{no[:4]}-{no[4:6]}-{no[6:8]}"])  # fmt: skip
    return mem


def test_발행결정마다_한_번만_부르고_없는_행은_다시_부르지_않는다(monkeypatch) -> None:
    mem = _mem()
    불림: list[tuple[str, str]] = []

    def 가짜(corp: str, day: str):
        불림.append((corp, day))
        return 응답, None

    monkeypatch.setattr(job.dd, "fetch_cb_decision", 가짜)
    결과 = job.collect_cb(mem, date(2026, 10, 12))  # type: ignore[arg-type]
    assert 결과["calls"] == 2 and 결과["ok"] == 1 and 결과["no_row"] == 1  # 정정·유상증자는 부르지 않는다
    assert sorted(불림) == [("00111", "20260301"), ("00111", "20260304")]
    줄 = mem.conn.execute("SELECT status, shares_pct, claim_start FROM cb_issuances ORDER BY receipt_no").fetchall()
    assert 줄 == [("ok", 5.23, "2027-03-05"), ("no_row", None, None)]
    불림.clear()
    assert job.collect_cb(mem, date(2026, 10, 12))["calls"] == 0 and 불림 == []  # type: ignore[arg-type]


def test_호출_오류면_적지_않고_멈춘다(monkeypatch) -> None:
    mem = _mem()
    monkeypatch.setattr(job.dd, "fetch_cb_decision", lambda corp, day: ([], "상태 020: 한도"))
    결과 = job.collect_cb(mem, date(2026, 10, 12))  # type: ignore[arg-type]
    assert 결과["error"] and 결과["calls"] == 1
    assert mem.conn.execute("SELECT COUNT(*) FROM cb_issuances").fetchone()[0] == 0  # 다음 실행이 다시 본다


def test_종목_분석_사실_줄과_근거표에_실린다() -> None:
    import json

    from batch.jobs import verdicts as vj

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    f = {"value": 50, "quality": 50, "growth": 50, "momentum": 50, "risk": 50}
    for sid in (1, 2):
        c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
                  " VALUES (?, ?, 'KOSDAQ', 'KR', ?, 'KRW', 't', 't')", [sid, f"00000{sid}", f"종목{sid}"])  # fmt: skip
        c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                  " weights_json, rank_in_market, calc_version, created_at) VALUES (?, '2026-10-07', 60, ?, 0, '{}', 1,"
                  " 11, 't')", [sid, json.dumps(f)])  # fmt: skip
    c.execute("INSERT INTO cb_issuances (receipt_no, stock_id, decided_at, status, face_amount, conv_price, conv_shares,"
              " shares_pct, claim_start, claim_end, refix_floor, source, fetched_at) VALUES ('20260301000111', 1,"
              " '2026-03-01', 'ok', 1e10, 12000, 833333, 5.23, '2027-03-05', '2031-02-05', 8400, 'dart_opendart', 't')")  # fmt: skip
    warnings: list[str] = []
    mem.batch(vj.build_market(mem, "KR", date(2026, 10, 8), warnings))  # type: ignore[arg-type]
    assert warnings == []
    d1 = json.loads(c.execute("SELECT detail_json FROM stock_verdicts WHERE stock_id = 1").fetchone()[0])
    assert any(x.startswith("전환사채 잠재 물량 1건: 2026-03-01 결정 권면 100억원") for x in d1["facts"])
    근거 = json.loads(c.execute("SELECT evidence_json FROM stock_verdicts WHERE stock_id = 1").fetchone()[0])
    assert any(r["label"] == "전환사채 발행결정" and "20260301000111" in r["source"] for r in 근거)
    d2 = json.loads(c.execute("SELECT detail_json FROM stock_verdicts WHERE stock_id = 2").fetchone()[0])
    assert "facts" not in d2  # 없는 종목은 아무 말도 하지 않는다
