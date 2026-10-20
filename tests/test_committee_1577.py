"""25.1576 교차검증 권고 반영 (docs/company.md 5장, docs/infra.md 25.1577).

지키는 것
- 보충 구간에 상세(`cb_issuances`)를 아직 못 받은 전환사채 결정이 있으면 그 종목 공시부는 "자료 없음" — 이미 아는 전환사채가
  문턱 이상이면 반대는 그대로 선다
- 보충은 덮개 끝 **날**부터 읽는다(금요일 18:17 뒤 공시) · 겹치는 사건은 두 번 넣지 않는다
- 보충한 사건의 근거표 출처는 공시 표도 가리킨다
- 지주사 기록은 지금 판만 · 실행 품질 플래그는 발생마다 따로
"""

from __future__ import annotations

import json

from batch.jobs import company as cjob
from batch.jobs import paper as job
from batch.jobs import weekly_summary as ws
from batch.services import company as co
from tests.test_company_dossier_1463 import _행, c, 기준  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def _덮개(c: Client) -> None:  # noqa: F811
    x = c.conn.execute
    x("DELETE FROM governance_events WHERE day = '2026-05-01'")
    x("DELETE FROM cb_issuances")
    x("UPDATE governance_events SET covered_to = '2026-10-12'")
    for day in ("2026-10-13", "2026-10-14", "2026-10-15"):
        x("INSERT INTO disclosure_coverage (kind, day, rows, calls, source, fetched_at) VALUES ('I', ?, 1, 1, 't', 't')",
          [day])  # fmt: skip


def _공시(c: Client, no: str, title: str, day: str) -> None:  # noqa: F811
    c.conn.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, source, fetched_at)"
                   " VALUES (1, '00126380', ?, ?, ?, 't', 't')", [no, title, day])  # fmt: skip


def test_상세_전_전환사채_결정이면_자료_없음(c: Client) -> None:  # noqa: F811
    _덮개(c)
    _공시(c, "cb1", "주요사항보고서(전환사채권발행결정)", "2026-10-14")
    [d] = cjob.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert not d.gov_covered and co.governance(d).note.startswith("자료 없음")  # type: ignore[union-attr]
    # 금요일에 상세를 받으면 다시 덮는다
    c.conn.execute("INSERT INTO cb_issuances (receipt_no, stock_id, decided_at, status, shares_pct, source, fetched_at)"
                   " VALUES ('cb1', 1, '2026-10-14', 'ok', 1.0, 't', 't')")  # fmt: skip
    [d] = cjob.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert d.gov_covered
    # 정정 공시는 결정이 아니다
    c.conn.execute("DELETE FROM cb_issuances")
    c.conn.execute("UPDATE disclosures SET title = '[기재정정]주요사항보고서(전환사채권발행결정)'")
    [d] = cjob.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert d.gov_covered


def test_이미_아는_전환사채가_문턱_이상이면_반대는_그대로(c: Client) -> None:  # noqa: F811
    _덮개(c)
    c.conn.execute("INSERT INTO cb_issuances (receipt_no, stock_id, decided_at, status, shares_pct, source, fetched_at)"
                   " VALUES ('old', 1, '2026-08-01', 'ok', ?, 't', 't')", [co.CB_PCT + 1])  # fmt: skip
    _공시(c, "cb2", "주요사항보고서(전환사채권발행결정)", "2026-10-14")
    [d] = cjob.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    op = co.governance(d)
    assert d.gov_covered and op.stance == -1 and op.conviction == co.CB_CONVICTION


def test_덮개_끝_날의_공시도_보충하고_출처를_가리킨다(c: Client) -> None:  # noqa: F811
    _덮개(c)
    _공시(c, "f1", "횡령ㆍ배임혐의발생", "2026-10-12")  # 금요일 저녁 — 주간 표가 지어진 뒤
    [d] = cjob.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert d.gov_events == [("2026-10-12", "fraud")] and d.gov_source == "governance_events·disclosures"
    [기준행] = co.governance(d).evidence
    assert 기준행["source"] == "governance_events·disclosures"
    # 주간 표에 이미 있는 같은 사건은 두 번 넣지 않고 출처도 주간 표 그대로
    c.conn.execute("INSERT INTO governance_events (receipt_no, stock_id, day, type, title, covered_from, covered_to,"
                   " source, computed_at) VALUES ('f1', 1, '2026-10-12', 'fraud', 't', '2025-01-01', '2026-10-12',"
                   " 't', 't')")  # fmt: skip
    [d] = cjob.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert d.gov_events == [("2026-10-12", "fraud")] and d.gov_source == "governance_events"


def test_지주사_기록은_지금_판만() -> None:
    m = Client()
    for sid, v in ((1, 1), (3, 2)):
        m.conn.execute(
            "INSERT INTO paper_decisions (account_id, market, stock_id, decided_on, kind, reason, ret_20d, bench_20d,"
            " detail_json, rule_version, created_at) VALUES ('B', 'KR', ?, '2026-10-01', 'buy', 't', 5, 0, ?, ?, 't')",
            [sid, json.dumps({"holding": {"votes": {"A": 1.0}}}), v],
        )
    rows = m.execute(job.HOLDING_RECORDS_SQL, ["B", "KR", "2026-01-01", 2]).dicts()
    assert [r["stock_id"] for r in rows] == [3]


def test_실행_품질_플래그는_발생마다_따로() -> None:
    m = Client()
    for day, first in (("2026-05-01", "2026-05-01"), ("2026-05-02", "2026-05-01"), ("2026-09-08", "2026-09-08")):
        m.conn.execute(
            "INSERT INTO sell_flags (stock_id, as_of_date, level, reason_code, rationale_text, rationale_data, is_active,"
            " first_seen_date, created_at) VALUES (1, ?, 'red', '손절', 't', '{}', 1, ?, 't')", [day, first],
        )
    rows = sorted(m.execute(ws.EXEC_FLAGS_SQL, ["2026-01-01"]).dicts(), key=lambda r: r["first_seen"])
    assert [(r["first_seen"], r["last_seen"]) for r in rows] == [("2026-05-01", "2026-05-02"), ("2026-09-08", "2026-09-08")]


