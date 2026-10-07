import { NextResponse } from "next/server";
import { batch as batchExecute, currentBackend, execute, rowsToObjects } from "@/lib/db";
import {
  API_USAGE, CRON_HEARTBEATS, FRESHNESS, LAST_SUCCESS, RECENT_BATCH_RUNS, RECENT_HEALTH_ALERTS,
  ADJUST_QUEUE, SESSIONS_AROUND, WATCHES, countsForToday, findLastSuccess, localDate, monthWindow, pickUsageRows, sessionOn,
  type AdjustQueue, type LastSuccess, type Session,
} from "@/lib/health";

/**
 * 시스템 상태 화면 데이터 (docs/health.md 4장).
 *
 * 읽기만 한다. 감시 판단(무엇이 늦었나)은 화면이 아니라 감시 경로가 하고, 여기서는
 * 그 결과와 원재료(마지막 성공·오늘 마감)를 함께 보여 준다.
 *
 * 표가 없을 수 있어(마이그레이션 전) 신선도 질의는 하나씩 따로 부르고 실패는 넘긴다.
 * 하나가 없다고 화면 전체가 비면 무엇이 문제인지 알 수 없다.
 */
export async function GET() {
  const now = new Date();
  const from = new Date(now.getTime() - 36 * 3_600_000).toISOString().slice(0, 10);

  try {
    // **못 읽은 것을 "없다" 로 만들지 않는다** (docs/infra.md 25.163).
    // 25.74 에서 `/api/alerts` 에는 이것을 했는데 **정작 이 화면은 안 했다.**
    // 여기가 "크론이 부르고 있나" 를 답하는 자리라, 못 읽고 "호출 기록 없음" 이라
    // 적으면 사람이 cron-job.org 를 다시 등록하러 간다 — 멀쩡한 것을.
    const readErrors: Record<string, string> = {};
    const 읽되_이유를_남긴다 = (key: string) => (error: unknown) => {
      readErrors[key] = error instanceof Error ? error.message : `${key} 를 읽지 못했습니다`;
      return null;
    };

    const [runsRs, usageRs, alertsRs, cronRs, sessionsRs, lastRs, adjustRs] = await Promise.all([
      execute(RECENT_BATCH_RUNS),
      execute(API_USAGE, [from, monthWindow(now)]).catch(읽되_이유를_남긴다("usage")),
      execute(RECENT_HEALTH_ALERTS).catch(읽되_이유를_남긴다("health_alerts")),
      execute(CRON_HEARTBEATS).catch(읽되_이유를_남긴다("cron")),
      execute(SESSIONS_AROUND, [from]).catch(읽되_이유를_남긴다("sessions")),
      execute(LAST_SUCCESS),
      // 수정주가 재수집 대기열 (docs/infra.md 25.165). 비어 있는 것이 정상이라 화면은 조용하다
      execute(ADJUST_QUEUE).catch(읽되_이유를_남긴다("adjust_queue")),
    ]);

    const sessions = sessionsRs ? rowsToObjects<Session>(sessionsRs) : [];
    const lastSuccess = rowsToObjects<LastSuccess>(lastRs);

    // D1 임시 운영 중에는 미국 배치를 돌리지 않는다 — 무응답 감시(cron/health)는 미국을 빼는데 여기는 그대로 봐서
    // 날마다 거짓 빨간 "무응답" 과 "텔레그램으로도 알립니다" 가 떴다 (docs/infra.md 25.552, 감사 재현)
    const backend = await currentBackend();
    const watches = WATCHES.map((w) => {
      const paused = backend === "d1" && w.market === "US";
      const today = localDate(w.market, now);
      const session = sessionOn(sessions, w.market, today);
      // **달력이 오늘까지 오지 않으면 휴장이 아니라 모름이다** (docs/infra.md 25.552, 감사 재현). 배치가 며칠 멈춰
      // `market_sessions` 가 지난 금요일에서 끝나면, 거래일인 월요일이 "기대 없음 (휴장)" 회색으로 보였다 —
      // 가장 나쁜 때 이 화면만 정상처럼 보였다. 못 읽은 경우(`sessionsRs` 없음, 25.163)도 모름이다
      const calendarKnown = sessionsRs !== null && sessions.some((s) => s.market === w.market && s.date >= today);
      // **시장까지 맞춘다** (docs/infra.md 25.373). 무응답 감시(cron/health)는 `findLastSuccess` 로 시장을 가리는데
      // 여기는 작업 이름만 봐서, `sentiment` 의 미국 행으로 국내 감시를 "성공" 으로 그렸다 — 텔레그램은 무응답을 보내는데
      const last = findLastSuccess(lastSuccess, w);
      const due = session ? new Date(Date.parse(session.open_utc) + w.dueOffsetMinutes * 60_000).toISOString() : null;
      // trade_date 가 아니라 끝난 시각으로 본다. 장 시작 전 배치라 trade_date 는 직전 거래일이다
      const doneToday = session !== null && countsForToday(last, session);
      return {
        job: w.job,
        market: w.market,
        label: w.label,
        schedule: w.schedule,
        local_date: today,
        // null = 달력이 없어 모른다. 화면은 휴장이 아니라 경고로 그린다
        trading_day: calendarKnown ? session !== null : null,
        due_utc: due,
        overdue: !paused && due !== null && now.toISOString() > due && !doneToday,
        paused,
        done_today: doneToday,
        last_success_at: last?.finished_at ?? null,
        last_success_trade_date: last?.trade_date ?? null,
      };
    });

    // 신선도 질의를 **한 요청에 묶는다.** 하나씩 보내면 칸 수만큼 왕복한다
    // (2026-09-23: "아홉 개" 라고 적혀 있었는데 그 사이 스물여섯이 됐다. 수를 적지 않는다)
    // 표 하나가 없으면(마이그레이션 전) 묶음 전체가 실패하므로, 그때만 하나씩 다시 시도한다
    let freshness: Array<{ key: string; label: string; value: string | null }>;
    const 못읽은칸: string[] = [];
    // D1 에서 쉬는 칸은 **읽지 않는다** (docs/infra.md 25.857, 감사). 화면은 어차피 "쉬는 중" 이고, 미국 행이 없는 D1 에서 이 질의들은
    // 국내 행을 끝까지 걸어 NULL 을 돌려줬다 — 상태 화면 한 번에 약 325만 행, 하루 읽기 한도(500만)의 3분의 2
    const 쉬는칸 = (f: (typeof FRESHNESS)[number]) => backend === "d1" && f.restsOnD1 === true;
    const 읽을칸 = FRESHNESS.filter((f) => !쉬는칸(f));
    const 값 = new Map<string, string | null>();
    try {
      const results = await batchExecute(읽을칸.map((f) => ({ sql: f.sql })));
      읽을칸.forEach((f, i) => 값.set(f.key, rowsToObjects<{ v: string | null }>(results[i])[0]?.v ?? null));
      freshness = FRESHNESS.map((f) => ({ key: f.key, label: f.label, value: 값.get(f.key) ?? null }));
    } catch {
      freshness = [];
      for (const f of FRESHNESS) {
        if (쉬는칸(f)) {
          freshness.push({ key: f.key, label: f.label, value: null });
          continue;
        }
        try {
          const rs = await execute(f.sql);
          freshness.push({ key: f.key, label: f.label, value: rowsToObjects<{ v: string | null }>(rs)[0]?.v ?? null });
        } catch {
          // 못 읽은 칸도 `null` 이라 "없음" 과 같아 보인다. **몇 칸인지는 말한다** (25.163)
          못읽은칸.push(f.label);
          freshness.push({ key: f.key, label: f.label, value: null });
        }
      }
    }

    return NextResponse.json({
      now: now.toISOString(),
      // 신선도 판정에 쓴다 — D1 에서 쉬는 칸은 낡은 것이 정상이다 (docs/infra.md 25.140)
      db_backend: backend,
      watches,
      runs: rowsToObjects(runsRs),
      // (API, 창)마다 가장 새 창 하나, 지금 창인지 표시 (25.593)
      usage: usageRs ? pickUsageRows(rowsToObjects<{ api_name: string; window_type: string; window_start: string }>(usageRs), now) : [],
      health_alerts: alertsRs ? rowsToObjects(alertsRs) : [],
      cron: cronRs ? rowsToObjects(cronRs) : [],
      adjust_queue: adjustRs ? (rowsToObjects<AdjustQueue>(adjustRs)[0] ?? null) : null,
      freshness,
      // 빈 칸이 "아직 없음" 인지 "못 읽음" 인지 화면이 가릴 수 있게 함께 준다 (25.163)
      read_errors: {
        ...readErrors,
        ...(못읽은칸.length
          ? { freshness: `신선도 ${못읽은칸.length}칸을 읽지 못했습니다: ${못읽은칸.slice(0, 3).join(", ")}` }
          : {}),
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}
