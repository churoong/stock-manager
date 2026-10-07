"""D1 따라잡기 결과를 텔레그램으로 알린다 (docs/infra.md 25.8). `d1-catchup.yml` 의 마지막 단계가 부른다.

왜 있나: 따라잡기는 사람 없이 매일 09:05 KST 에 돈다. **실패해도 알림이 없어**(2026-09-18 발견) Actions
로그를 열어 봐야 알았다. 폰으로 진행과 멈춤을 보게 한다.

첫 며칠은 하루 쓰기 한도를 과거 시세 채우기에 써서 08:27 아침 리포트가 건너뛸 수 있다(25.14). 그런 날은
**오늘 신호도 싣는다.** 근거 문장은 신호 배치가 저장한 `rationale_text` 그대로다 — 여기서 숫자를 만들지 않는다.

이 알림이 따라잡기를 실패로 만들면 안 된다. 읽기가 실패하면 아는 것만 싣고, 보내기가 실패하면 로그만 남긴다.

실행
  python scripts/catchup_report.py --status success     # 워크플로가 job.status 와 STEPS_JSON 을 넘긴다
  python scripts/catchup_report.py --dry-run            # 보내지 않고 출력만
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import db  # noqa: E402
from batch.services import criteria as crit  # noqa: E402

#: 워크플로 단계 id → 사람이 읽는 이름 (d1-catchup.yml 과 같은 순서)
STEP_NAMES = {
    "check": "0. 지금 D1 을 쓰는가",
    "universe": "1. 유니버스",
    "financials": "2. 재무",
    "sectors": "3. 업종",
    "backfill": "4. 과거 시세",
    "metrics": "5. 성과 지표",
    "scores": "6. 점수",
    "signals": "7. 신호",
    "targets": "8. 장중 감시·뉴스 후보",
}
#: 종합 점수에 필요한 거래일 (docs/infra.md 25.8). 모멘텀은 6개월, 리스크는 성과 지표 1년 창(200일 이상)
MOMENTUM_DAYS = 126
RISK_DAYS = 200
#: 아침 리포트가 없는 날 싣는 신호 수. 전부는 앱의 "오늘의 추천" 에 있다
TOP_SIGNALS = 5
HORIZON_KO = {"short": "단기", "mid": "중기", "long": "장기"}
KST = timedelta(hours=9)


@dataclass
class Facts:
    """D1 에서 읽은 것. 못 읽은 항목은 None 으로 둔다 — 지어내지 않는다."""

    universe: int | None = None
    price_days: int | None = None
    price_first: str | None = None
    price_last: str | None = None
    financial_companies: int | None = None
    sectors: int | None = None
    metrics_1y: int | None = None
    scores_as_of: str | None = None
    scored: int | None = None
    scores_total: int | None = None
    signals_as_of: str | None = None
    #: 신호를 **못 읽었으면** 그 이유. `None` 이면 정말 0건이다 (docs/infra.md 25.164)
    signals_read_error: str | None = None
    signals_by_horizon: dict[str, int] = field(default_factory=dict)
    top_signals: list[dict[str, Any]] = field(default_factory=list)
    #: 근거표(`rationale_data.criteria`)가 없어 싣지 않은 종목 수 (docs/infra.md 25.752)
    no_evidence: int = 0
    #: 근거표가 있어 실을 수 있는 종목 수 — "외 N종목" 의 밑 (25.755, 교차검증: 행 수로 세어 부풀었다)
    eligible_stocks: int = 0
    morning_report_sent: bool | None = None
    #: 아침 리포트가 아직 없지만 국내 일일 배치 예비 실행(09:35 KST, infra 25.870)이 남았다 — 대신 싣지 않는다 (25.871)
    late_report_pending: bool = False
    d1_writes_today: int | None = None
    d1_reads_today: int | None = None
    #: 최근 따라잡기가 실제로 저장한 논리 행 수 (최신 순). **속도를 재는 재료다** (docs/infra.md 25.85)
    recent_rows: list[int] = field(default_factory=list)


def morning_report_due(day: Any) -> bool:
    """그날 국내 아침 리포트가 나가야 하는 날인가 — 국내 거래일 (docs/infra.md 25.375).

    달력을 못 읽으면 나가야 한다고 본다(알림을 막는 쪽보다 한 번 더 보내는 쪽이 낫다).
    """
    try:
        from batch.core import calendar as cal

        return cal.is_session("KR", day)
    except Exception:  # noqa: BLE001 — 달력 고장으로 알림을 막지 않는다
        return True


def failed_steps(steps_json: str | None) -> list[str]:
    """`toJSON(steps)` 에서 실패한 단계 이름. 모양: {"backfill": {"outcome": "failure", ...}, ...}"""
    if not steps_json:
        return []
    try:
        steps = json.loads(steps_json)
    except ValueError:
        return []
    return [STEP_NAMES.get(step_id, step_id) for step_id, info in steps.items()
            if isinstance(info, dict) and info.get("outcome") == "failure"]  # fmt: skip


def quota_skips(log_text: str) -> list[str]:
    """`catchup.log` 에서 DB 한도로 건너뛴 단계의 사유 (`entry.guard` 가 찍는 "건너뜀: …" 줄, 25.875).

    한도로 건너뛴 단계는 종료 코드 0 이라 `STEPS_JSON` 으로는 "성공" 이다
    이걸 따로 읽지 않으면 알림이 "✅ 끝" 만 말했다.
    """
    return [줄.split("건너뜀:", 1)[1].strip() for 줄 in log_text.splitlines()
            if 줄.startswith("건너뜀:") and "한도" in 줄]  # fmt: skip


def deferred_steps(steps_json: str | None) -> list[str]:
    """읽기 문이 내일로 미룬 단계 (`outputs.deferred == "true"`, scripts/d1_read_gate.py, 25.847).

    미룬 단계는 성공(`exit 0`)으로 끝난다 — 이걸 따로 읽지 않으면 알림이 "✅ 끝" 만 말해,
    점수·신호가 하루 묵은 것을 모른다.
    """
    if not steps_json:
        return []
    try:
        steps = json.loads(steps_json)
    except ValueError:
        return []
    return [STEP_NAMES.get(step_id, step_id) for step_id, info in steps.items()
            if isinstance(info, dict) and (info.get("outputs") or {}).get("deferred") == "true"]  # fmt: skip


def _one(client, sql: str, args: list[Any] | None = None) -> tuple | None:
    try:
        rows = client.execute(sql, args or []).rows
    except Exception:  # noqa: BLE001 — 알림이 따라잡기를 막으면 안 된다
        return None
    return tuple(rows[0]) if rows else None


def _criteria(raw: Any) -> list[Any]:
    """신호 `rationale_data` 의 근거표 기준. 깨졌거나 비면 빈 목록 (`report_picks.parse_json` 과 같은 뜻)."""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return []
    crit = data.get("criteria") if isinstance(data, dict) else None
    return crit if isinstance(crit, list) else []


def collect(client, now: datetime) -> Facts:
    facts = Facts()
    row = _one(
        client,
        "SELECT COUNT(*) FROM universe_members u JOIN stocks s ON s.id = u.stock_id"
        f" WHERE u.included = 1 AND s.country = 'KR' AND u.snapshot_date = {db.latest_snapshot_sql()}",
        ["KR"],
    )
    facts.universe = row[0] if row else None

    row = _one(
        client,
        # 날짜 색인을 한 칸씩 건너뛰며 센다 (docs/infra.md 25.866) — `COUNT(DISTINCT p.date)` 는 시세 표 전체(약 110만
        # 행)를 읽었다
        "WITH RECURSIVE d(x) AS (SELECT (SELECT MIN(date) FROM prices)"
        " UNION ALL SELECT (SELECT MIN(date) FROM prices WHERE date > d.x) FROM d WHERE d.x IS NOT NULL)"
        " SELECT COUNT(*), MIN(x), MAX(x) FROM d WHERE x IS NOT NULL AND EXISTS (SELECT 1 FROM prices p"
        "   CROSS JOIN stocks s WHERE p.date = d.x AND s.id = p.stock_id AND s.country = 'KR')",
    )
    if row:
        facts.price_days, facts.price_first, facts.price_last = row

    row = _one(
        client,
        "SELECT COUNT(DISTINCT f.stock_id) FROM financials f JOIN stocks s ON s.id = f.stock_id"
        " WHERE s.country = 'KR' AND f.period_type = 'A'",
    )
    facts.financial_companies = row[0] if row else None

    row = _one(client, "SELECT COUNT(*) FROM stocks WHERE country = 'KR' AND sector IS NOT NULL")
    facts.sectors = row[0] if row else None

    row = _one(
        client,
        # **기준일을 나라 안에서 잡는다** (2026-09-22, docs/infra.md 25.112).
        # 전체에서 잡으면 미국 지표가 더 최신인 날 이 수가 **0** 이 되고, 되살아나는 날의
        # 자동 점검(25.80)이 "국내 지표가 안 만들어졌다" 고 **거짓으로 실패를 알린다.**
        # 바로 아래 신호 질의는 이미 나라 안에서 잡고 있었다 — 같은 파일에서 갈렸다
        "SELECT COUNT(DISTINCT pm.stock_id) FROM performance_metrics pm JOIN stocks s ON s.id = pm.stock_id"
        " WHERE s.country = 'KR' AND pm.\"window\" = '1Y' AND pm.mdd IS NOT NULL"
        " AND pm.as_of_date = (SELECT MAX(pm2.as_of_date) FROM performance_metrics pm2"
        "   JOIN stocks s2 ON s2.id = pm2.stock_id WHERE s2.country = 'KR')",
    )
    facts.metrics_1y = row[0] if row else None

    row = _one(
        client,
        "SELECT sc.as_of_date, SUM(sc.total_score IS NOT NULL), COUNT(*)"
        " FROM scores sc JOIN stocks s ON s.id = sc.stock_id WHERE s.country = 'KR'"
        " GROUP BY sc.as_of_date ORDER BY sc.as_of_date DESC LIMIT 1",
    )
    if row:
        facts.scores_as_of, facts.scored, facts.scores_total = row

    try:
        # 기준일은 **마지막으로 계산한 날** — 그날 걸린 신호가 0건이면 MAX 는 더 옛날이라 옛 신호를 지금 것처럼 보냈다.
        # 제외·폐지 종목은 뺀다 — 추천 화면·아침 리포트와 같은 잣대 (25.337·25.802·25.827, 교차검증)
        계산일 = db.last_signal_calc_date(client, "KR", None) or "0000-00-00"
        signal_rows = client.execute(
            "SELECT sg.as_of_date, sg.horizon, st.name_ko, st.ticker, sg.buy_zone_low, sg.buy_zone_high,"
            " sg.currency, sg.rationale_text, sg.rationale_data"
            " FROM signals sg JOIN stocks st ON st.id = sg.stock_id WHERE st.country = 'KR' AND st.status = 'active'"
            " AND sg.as_of_date = ?"
            # 그날의 가장 새 판만 — 옛 판 행까지 세면 신호 수가 부푼다 (docs/infra.md 25.423·25.464)
            " AND sg.calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id"
            "   WHERE s3.country = 'KR' AND c.as_of_date = ?)"
            # **점수 순**이다 (25.489, 텔레그램 감사). `sg.id` 는 적재 순서라 점수와 무관해,
            # 아침 리포트가 냈을 상위와 다른 종목이 "대신 싣는 추천" 으로 나갔다. 그날 점수가 없으면 뒤로
            " ORDER BY (SELECT sc.total_score FROM scores sc WHERE sc.stock_id = sg.stock_id"
            "   AND sc.as_of_date = sg.as_of_date ORDER BY sc.calc_version DESC LIMIT 1) DESC NULLS LAST, sg.id",
            [계산일, 계산일],
        ).dicts()
    except Exception as e:  # noqa: BLE001 — 표가 없는 DB 는 정상, 나머지는 말한다
        signal_rows = []
        # **"신호 0건" 과 "못 읽었다" 는 다른 말이다** (docs/infra.md 25.164).
        # 따라잡기 알림에 0건이 찍히면 사람은 "아직 조건이 안 찼구나" 로 읽는다
        if not db.표가_없나(e):
            facts.signals_read_error = str(e)
    if signal_rows:
        facts.signals_as_of = signal_rows[0]["as_of_date"]
        for signal in signal_rows:
            facts.signals_by_horizon[signal["horizon"]] = facts.signals_by_horizon.get(signal["horizon"], 0) + 1
        # **근거표를 만들 수 없는 추천은 싣지 않는다** (CLAUDE.md 절대 규칙, docs/infra.md 25.752 리포트 감사). 25.489
        # 는
        # `report_picks.compose` 에만 걸어, 아침 리포트가 안 나간 날의 "대신 싣는 추천" 은 근거표가 빈 신호도 그대로
        # 나갔다.
        # 규칙은 `report_picks.with_criteria` 와 같다 — 한 기간이라도 근거표가 비면 종목 전체를 뺀다
        # 웹 거르개와 같은 판정(`criteria.usable`) — 비었는지만 보면 웹이 못 그리는 근거표도 실렸다 (25.822, 교차검증)
        근거없음 = {s["ticker"] for s in signal_rows if not crit.usable(_criteria(s.get("rationale_data")))}
        facts.no_evidence = len(근거없음)
        facts.eligible_stocks = len({s["ticker"] for s in signal_rows} - 근거없음)
        # 한 종목이 단기·중기·장기 세 칸을 다 차지하지 않게 종목마다 한 줄 (25.489)
        본: set[Any] = set()
        for signal in signal_rows:
            if signal["ticker"] in 본 or signal["ticker"] in 근거없음:
                continue
            본.add(signal["ticker"])
            facts.top_signals.append(signal)
            if len(facts.top_signals) >= TOP_SIGNALS:
                break

    # 오늘(한국 날짜) 아침 리포트가 나갔는가. 일일 배치는 00:00 KST 이후에 시작한 것만 본다
    kst_midnight = (now + KST).replace(hour=0, minute=0, second=0, microsecond=0) - KST
    # **오늘 한 번이라도 성공했는가**를 본다 (docs/infra.md 25.433). 예전에는 마지막 한 줄만 봤는데, 일일 배치는
    # 시각 판정을 성공 여부보다 먼저 해서 늦게 도착한 예비 cron(실측 2~4시간 지연)이 **성공 뒤에 skipped 줄**을 남긴다.
    # 그러면 성공한 날인데도 "아침 리포트가 나가지 않아 오늘 신호를 싣습니다" 가 거의 매 거래일 나갔다
    row = _one(
        client,
        "SELECT COUNT(*) FROM batch_runs WHERE job_name = 'daily_kr' AND started_at >= ?"
        " AND status IN ('success', 'partial')",
        [kst_midnight.isoformat()],
    )
    # **거래일이 아니면 모른다(None)로 둔다** (docs/infra.md 25.375). 이 워크플로는 매일 도는데 예전에는 오늘
    # `daily_kr` 이 성공했는지만 봐서, 토요일·휴장일(아침 배치가 원래 안 돈다)마다 "아침 리포트가 나가지 않아
    # 오늘 신호를 싣습니다" 로 금요일 신호를 보냈다
    오늘 = (now + KST).date()
    if not morning_report_due(오늘):
        facts.morning_report_sent = None
    else:
        facts.morning_report_sent = bool(row and int(row[0] or 0) > 0)
        # 예비 실행 전이면(평일 00:35 UTC 전 + Actions 지연 여유로 장 마감 06:30 UTC 전) 늦은 리포트가 곧 온다 —
        # 여기서 신호를 대신 실으면 같은 아침에 추천이 두 번 가고 날짜가 다를 수 있었다 (25.871, 감사)
        if facts.morning_report_sent is False and now.astimezone(UTC).weekday() < 5 and now.astimezone(UTC).hour < 6:
            facts.late_report_pending = True
            facts.morning_report_sent = None

    # **모르면 None 이다.** 예전에는 remaining_d1_daily_writes() 를 빼서 썼는데, 그 함수는
    # 못 읽어도 "한도 전체" 를 돌려주므로 결과가 늘 0 이 됐다 — "오늘 0행 썼다" 로 보이지만
    # 실제로는 모른다는 뜻이었다 (Facts 의 약속을 어긴다)
    facts.d1_writes_today = db.d1_writes_today(client)
    # 읽기도 같이 본다 (25.122). 2026-09-18 에 계정을 막은 것은 쓰기가 아니라 읽기였다
    facts.d1_reads_today = db.d1_reads_today(client)

    # 따라잡기 속도의 재료 (docs/infra.md 25.85). 성공·부분성공만 본다 —
    # 건너뛴 실행의 0 을 섞으면 속도가 **실제보다 느리게** 나와 "언제쯤" 이 늘어진다
    try:
        rows = client.execute(
            "SELECT step_log FROM batch_runs WHERE job_name = 'backfill_kr'"
            " AND status IN ('success', 'partial') AND step_log IS NOT NULL"
            " ORDER BY id DESC LIMIT 10"
        ).rows
    except Exception:  # noqa: BLE001 — 알림이 따라잡기를 막으면 안 된다
        rows = []
    for (기록,) in rows:
        try:
            값 = json.loads(str(기록)).get("rows")
        except ValueError:
            continue
        if isinstance(값, int) and 값 > 0:
            facts.recent_rows.append(값)
    return facts


#: 속도를 낼 때 볼 최근 실행 수. 하루는 들쭉날쭉하고(재무·업종이 예산을 먼저 쓰는 날) 너무 많으면 낡는다
RATE_RUNS = 3


def 회당_거래일(rows_by_run: list[int], universe: int | None) -> float | None:
    """따라잡기 한 번이 실제로 받는 거래일 수. **못 재면 None 이다 — 지어내지 않는다.**

    `rows` 는 논리 행이고 한 거래일에 유니버스 종목 수만큼 들어온다. 그래서
    `행 ÷ 종목 수` 가 곧 거래일이다. D1 이 세는 행(인덱스 포함, 배수 3)이 아니라
    **저장한 논리 행**이므로 배수를 다시 곱하면 안 된다 (docs/infra.md 25.20 의 반대 실수).
    """
    if not universe or universe <= 0:
        return None
    쓸것 = [r for r in rows_by_run[:RATE_RUNS] if r and r > 0]
    if not 쓸것:
        return None
    return sum(쓸것) / len(쓸것) / universe


def 며칠_뒤(모자란_거래일: int, 회당: float | None) -> str:
    """따라잡기는 하루 한 번 도니 **남은 실행 횟수가 곧 날 수**다."""
    if 모자란_거래일 <= 0:
        return ""
    if not 회당 or 회당 <= 0:
        return " (속도를 재지 못해 언제일지 모릅니다)"
    return f" · 이 속도면 약 {math.ceil(모자란_거래일 / 회당)}일 뒤"


def 모자란_재료(facts: Facts) -> list[str]:
    """시세 말고 **아직 비어 있는** 재료 (docs/infra.md 25.85).

    처음에는 위 줄을 "추천까지 N거래일" 이라고 적었다. **그것은 단정이다** — 시세가 차도
    재무나 업종이 비면 팩터가 둘 이상 없어 종합 점수가 나오지 않는다(docs/factors.md).
    2026-09-19 D1 실측이 정확히 그 꼴이었다: 시세 88거래일, 재무 0, 업종 0.
    시세만 세어 날짜를 말하면 그날 추천이 나올 것처럼 읽힌다.

    **모르는 것(None)은 빈 것으로 치지 않는다** — 못 읽은 것과 없는 것은 다르다(25.74).
    """
    빈것 = []
    if facts.financial_companies == 0:
        빈것.append("재무")
    if facts.sectors == 0:
        빈것.append("업종")
    return 빈것


def _n(value: int | None) -> str:
    return "?" if value is None else f"{value:,}"


def _ready(days: int | None, need: int) -> str:
    if days is None:
        return "?"
    return "✓" if days >= need else f"✗ {need - days}일 남음"


def compose(
    facts: Facts, status: str, failed: list[str], now: datetime, deferred: list[str] | None = None,
    quota: list[str] | None = None, db_unread: bool = False,
) -> str:
    """보낼 문장. 순수 함수 — 테스트가 이것을 본다."""
    stamp = (now + KST).strftime("%m-%d %H:%M")
    ok = status == "success" and not failed and not quota
    if quota:
        머리 = f"⛔ DB 한도로 {len(quota)}단계 건너뜀"
    else:
        머리 = "✅ 끝" if ok else f"❌ 멈춤: {', '.join(failed) or status}"
    head = f"📦 D1 따라잡기 (국내) — {stamp} KST {머리}"
    lines = [head]
    if quota:
        # 읽기 한도면 UTC 자정(09:00 KST)까지 웹·로그인·장중 감시가 함께 막힌다 (25.843·25.875)
        lines.append(f"{quota[0]} — 그때까지 웹 화면·장중 감시도 DB 를 읽지 못합니다")
    if db_unread:
        lines.append("DB 를 읽지 못해 아래 숫자는 모릅니다(?) — 0 이 아닙니다")
    if not ok and not quota:
        lines.append("한도에 걸렸거나 오류입니다. 내일 09:05 에 남은 것부터 이어서 합니다 (Actions → D1 따라잡기 로그)")
    if deferred:
        lines.append(f"⏸ 읽기 한도를 지키려고 내일로 미룬 단계: {', '.join(deferred)} (infra 25.844·25.847)")

    span = f" ({facts.price_first} ~ {facts.price_last})" if facts.price_first else ""
    lines.append(f"유니버스 {_n(facts.universe)}종목 · 시세 {_n(facts.price_days)}거래일{span}")
    lines.append(
        f"재무(사업보고서) {_n(facts.financial_companies)}종목 · 업종 {_n(facts.sectors)}종목"
        f" · 성과지표(1년) {_n(facts.metrics_1y)}종목"
    )
    by = facts.signals_by_horizon
    signal_total = sum(by.values())
    as_of = f" ({facts.scores_as_of})" if facts.scores_as_of else ""
    counts = f"단기 {by.get('short', 0)} · 중기 {by.get('mid', 0)} · 장기 {by.get('long', 0)}"
    신호칸 = (
        "신호 읽지 못함" if facts.signals_read_error or db_unread else f"신호 {signal_total}건 ({counts})"
    )
    lines.append(f"점수 {_n(facts.scored)}/{_n(facts.scores_total)}종목{as_of} · {신호칸}")
    if facts.signals_read_error:
        lines.append(f"  (신호 조회 실패: {facts.signals_read_error})")
    회당 = 회당_거래일(facts.recent_rows, facts.universe)
    lines.append(
        f"추천 조건: 모멘텀 {MOMENTUM_DAYS}거래일 {_ready(facts.price_days, MOMENTUM_DAYS)}"
        f" · 리스크 {RISK_DAYS}거래일 {_ready(facts.price_days, RISK_DAYS)}"
    )
    # **언제쯤 추천이 나오나.** 거래일 수만 적으면 사람은 그것을 날짜로 옮기지 못한다
    # (docs/infra.md 25.85). 리스크가 마지막 문턱이라 그것만 적는다
    if facts.price_days is not None and facts.price_days < RISK_DAYS:
        남은 = RISK_DAYS - facts.price_days
        속도 = f" (따라잡기 한 번에 약 {회당:.0f}거래일)" if 회당 else ""
        lines.append(f"시세가 차기까지 {남은}거래일{며칠_뒤(남은, 회당)}{속도}")
        if 모자란_재료(facts):
            lines.append(f"  다만 시세만으로는 안 됩니다 — {', '.join(모자란_재료(facts))}도 있어야 합니다")
    if facts.d1_writes_today is not None:
        lines.append(f"오늘 D1 쓰기 {facts.d1_writes_today:,} / {db.D1_DAILY_WRITE_LIMIT:,}행")
    if facts.d1_reads_today is not None:
        lines.append(f"오늘 D1 읽기 {facts.d1_reads_today:,} / {db.D1_DAILY_READ_LIMIT:,}행")

    # 근거표가 없어 모두 뺀 날도 **뺐다고 말한다** (25.755, 교차검증 — 블록 전체가 생략되어 경고까지 사라졌다)
    if facts.late_report_pending:
        lines += ["", "아침 리포트가 아직 없습니다 — 09:35 예비 실행이 늦은 리포트를 냅니다 (infra 25.870)"]
    if facts.morning_report_sent is False and (facts.top_signals or facts.no_evidence):
        # 모두 뺀 날에 "싣습니다" 라 하면 앞뒤가 맞지 않는다 (25.757, 교차검증)
        머리 = (
            "아침 리포트가 나가지 않아 오늘 신호를 싣습니다"
            if facts.top_signals
            else "아침 리포트가 나가지 않았지만 실을 신호가 없습니다"
        )
        lines += ["", f"{머리} ({facts.signals_as_of} 기준)"]
        for signal in facts.top_signals:
            unit = "원" if signal["currency"] == "KRW" else f" {signal['currency']}"
            name = signal["name_ko"] or signal["ticker"]
            horizon = HORIZON_KO.get(signal["horizon"], signal["horizon"])
            zone = f"{signal['buy_zone_low']:,.0f}~{signal['buy_zone_high']:,.0f}{unit}"
            lines.append(f"· {name}({signal['ticker']}) {horizon} 매수 구간 {zone}")
            lines.append(f"  {signal['rationale_text']}")
        # 종목 수로 센다 — 신호 행 수(기간별)로 세면 근거 없는 종목·한 종목의 여러 기간까지 "외 N건" 에 들어갔다
        # (25.755)
        if facts.eligible_stocks > len(facts.top_signals):
            lines.append(f"… 외 {facts.eligible_stocks - len(facts.top_signals)}종목")
        if facts.no_evidence:
            lines.append(f"⚠️ 근거표를 만들 수 없는 {facts.no_evidence}종목은 싣지 않았습니다")
        if facts.top_signals:
            lines.append("근거표는 앱의 '오늘의 추천' 에서 펼쳐 봅니다")

    lines += ["", "미국은 Turso 로 돌아갈 때까지 쉽니다 (docs/infra.md 25.14)"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="D1 따라잡기 결과 알림")
    parser.add_argument("--status", default="success", help="워크플로의 job.status")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log", default="catchup.log", help="한도로 건너뛴 단계를 찾을 로그 (25.875)")
    args = parser.parse_args()

    now = datetime.now(UTC)
    failed = failed_steps(os.environ.get("STEPS_JSON"))
    try:
        from batch.core.client import TursoClient

        with TursoClient() as client:
            facts = collect(client, now)
    except Exception as exc:  # noqa: BLE001 — 읽지 못해도 멈췄다는 것은 알려야 한다
        print(f"DB 를 읽지 못했습니다: {exc}")
        facts = Facts()
        못읽음 = True
    else:
        못읽음 = False
    try:
        로그 = Path(args.log).read_text(encoding="utf-8")
    except OSError:
        로그 = ""

    text = compose(facts, args.status, failed, now, deferred_steps(os.environ.get("STEPS_JSON")),
                   quota=quota_skips(로그), db_unread=못읽음)  # fmt: skip
    print(text)
    if args.dry_run:
        return 0
    try:
        from batch.notify import telegram

        telegram.send(text)
    except Exception as exc:  # noqa: BLE001
        print(f"텔레그램 알림 실패: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
