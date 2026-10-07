/**
 * 무응답 감시 테스트 (docs/health.md 6장, Step 17).
 *
 * 완료 기준 (design.md Step 17):
 *   - 한도 80% 를 넘긴 API 가 경고색으로 보인다
 *   - 배치를 일부러 실패시키면 화면과 텔레그램에 드러난다
 *   - 워크플로를 꺼 두면 무응답 알림이 온다
 *
 * 판단은 순수 함수라 손으로 만든 값으로 전부 확인한다. 질의는 실제 마이그레이션에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import {
  CALENDAR_LOW_SESSIONS, HEALTH_ALERT_INSERT, LAST_SUCCESS, RAN_STATUSES, SESSIONS_AROUND, STUCK_AFTER_HOURS,
  SUCCESS_WINDOW_HOURS, TODAY_HEALTH_ALERTS, WATCHES, countsForToday, dueAlerts, isStuck, localDate, sessionOn,
  findLastSuccess, sinceText, usageRatio, usageTone, type LastSuccess, type Market, type Session,
} from "@/lib/health";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

/**
 * 국내 개장 09:00 KST = 00:00 UTC, 미국 개장 09:30 ET = 13:30 UTC(서머타임).
 *
 * **미래 세션을 넉넉히 둔다.** `dueAlerts` 는 달력이 `CALENDAR_LOW_SESSIONS` 이하로
 * 남으면 미리 경고하므로(25.104), 마감 판단을 보는 테스트가 달력 경고까지 받으면
 * 무엇을 보고 있는지 흐려진다. 달력 자체는 아래 "거래일 달력" 에서 따로 본다.
 */
const SESSIONS: Session[] = [
  { market: "KR", date: "2026-09-18", open_utc: "2026-09-18T00:00:00Z" },
  { market: "KR", date: "2026-09-21", open_utc: "2026-09-21T00:00:00Z" },
  { market: "KR", date: "2026-09-22", open_utc: "2026-09-22T00:00:00Z" },
  { market: "KR", date: "2026-09-23", open_utc: "2026-09-23T00:00:00Z" },
  { market: "KR", date: "2026-09-24", open_utc: "2026-09-24T00:00:00Z" },
  { market: "KR", date: "2026-09-25", open_utc: "2026-09-25T00:00:00Z" },
  { market: "US", date: "2026-09-17", open_utc: "2026-09-17T13:30:00Z" },
  { market: "US", date: "2026-09-18", open_utc: "2026-09-18T13:30:00Z" },
  { market: "US", date: "2026-09-21", open_utc: "2026-09-21T13:30:00Z" },
  { market: "US", date: "2026-09-22", open_utc: "2026-09-22T13:30:00Z" },
  { market: "US", date: "2026-09-23", open_utc: "2026-09-23T13:30:00Z" },
  { market: "US", date: "2026-09-24", open_utc: "2026-09-24T13:30:00Z" },
  { market: "US", date: "2026-09-25", open_utc: "2026-09-25T13:30:00Z" },
];

/** 달력이 다 마른 경우. 마지막 세션이 09-21 이라 09-22 에는 미래 세션이 하나도 없다 */
const DRAINED: Session[] = SESSIONS.filter((s) => s.date <= "2026-09-21");

/** 달력만 보려고 만드는 세션 목록. 개장 시각은 판단에 안 쓰이므로 00:00Z 로 둔다 */
function calendar(market: Market, dates: string[]): Session[] {
  return dates.map((date) => ({ market, date, open_utc: `${date}T00:00:00Z` }));
}

function success(job: string, tradeDate: string, finishedAt: string): LastSuccess {
  return { job_name: job, market: null, trade_date: tradeDate, finished_at: finishedAt };
}

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`
    INSERT INTO market_sessions (market, date, open_utc, close_utc, source)
    VALUES ('KR', '2026-09-18', '2026-09-18T00:00:00Z', '2026-09-18T06:30:00Z', 'test'),
           ('US', '2026-09-17', '2026-09-17T13:30:00Z', '2026-09-17T20:00:00Z', 'test');
    INSERT INTO batch_runs (job_name, market, trade_date, trigger_source, started_at, finished_at, status)
    VALUES ('daily_kr', 'KR', '2026-09-17', 'schedule', '2026-09-16T23:27:00Z', '2026-09-16T23:33:00Z', 'success'),
           ('daily_kr', 'KR', '2026-09-16', 'schedule', '2026-09-15T23:27:00Z', '2026-09-15T23:31:00Z', 'success'),
           ('daily_kr', 'KR', '2026-09-18', 'schedule', '2026-09-17T23:27:00Z', NULL, 'running'),
           ('sentiment', 'KR', '2026-09-17', 'schedule', '2026-09-16T22:40:00Z', '2026-09-16T22:52:00Z', 'success'),
           -- 운영에서 본 모양: 경고를 안고 partial 로 끝났지만 리포트는 보냈다
           ('daily_us', 'US', '2026-09-16', 'schedule', '2026-09-17T12:27:00Z', '2026-09-17T12:35:00Z', 'partial'),
           -- 수동 시험 실행. 리포트를 보내지 않으므로 "돌았다" 로 세지 않는다
           ('daily_us', 'US', '2026-09-16', 'manual', '2026-09-17T13:00:00Z', '2026-09-17T13:02:00Z', 'dryrun');
    INSERT INTO api_usage (api_name, window_type, window_start, call_count, limit_value, warn_at_pct, state, updated_at)
    VALUES ('dart', 'day', '2026-09-17', 16000, 20000, 80, 'warn', 't');
  `);
});

describe("마감 판단", () => {
  it("휴장일에는 아무것도 기대하지 않는다", () => {
    // 2026-09-19(토)는 세션이 없다. 달력은 25일까지 있으므로 달력 알림도 없다
    const now = new Date("2026-09-19T05:00:00Z"); // 토 14시 KST
    expect(dueAlerts(now, SESSIONS, [], [])).toEqual([]);
  });

  it("마감 전에는 조용하다", () => {
    // 국내 개장 09:00 KST, 국내 일일 배치 마감은 +20분(09:20 = 00:20 UTC)
    const now = new Date("2026-09-18T00:10:00Z");
    const kr = dueAlerts(now, SESSIONS, [], [], WATCHES.filter((w) => w.job === "daily_kr"));
    expect(kr).toEqual([]);
  });

  it("마감이 지났는데 오늘 성공이 없으면 알린다", () => {
    const now = new Date("2026-09-18T00:30:00Z"); // 09:30 KST
    const due = dueAlerts(now, SESSIONS, [success("daily_kr", "2026-09-17", "2026-09-16T23:33:00Z")], [],
      WATCHES.filter((w) => w.job === "daily_kr"));
    expect(due).toHaveLength(1);
    expect(due[0].kind).toBe("missing");
    expect(due[0].localDate).toBe("2026-09-18");
    // 마지막 성공 시각이 문구에 들어간다 — 얼마나 오래 멈췄는지가 판단에 필요하다
    expect(due[0].message).toContain("2026-09-17 08:33 (한국 시각)");
    // 마감도 한국 시각으로 적는다. 기계용 UTC 는 data 에 (docs/infra.md 25.297)
    expect(due[0].message).toContain("2026-09-18 09:20 (한국 시각) 까지");
    expect(due[0].message).not.toContain("T00:20:00");
    expect(due[0].message).toContain("국내 일일 배치");
    expect(due[0].data.due_utc).toBe("2026-09-18T00:20:00.000Z");
  });

  it("오늘 성공했으면 마감이 지나도 조용하다", () => {
    const now = new Date("2026-09-18T05:00:00Z");
    const due = dueAlerts(now, SESSIONS, [success("daily_kr", "2026-09-17", "2026-09-18T00:05:00Z")], [],
      WATCHES.filter((w) => w.job === "daily_kr"));
    expect(due).toEqual([]);
  });

  it("성공 판단은 trade_date 가 아니라 끝난 시각으로 한다", () => {
    // 장 시작 전 배치라 trade_date 는 **직전 거래일**이다(batch/core/calendar.decide).
    // 오늘 날짜와 비교하면 성공한 날에도 매일 무응답 알림이 간다 — 한 번 그렇게 짰다가 고쳤다
    const session = SESSIONS[0]; // KR 2026-09-18 개장 00:00Z
    const 오늘실행 = success("daily_kr", "2026-09-17", "2026-09-17T23:33:00Z"); // 08:33 KST 오늘
    const 어제실행 = success("daily_kr", "2026-09-16", "2026-09-16T23:33:00Z");
    expect(countsForToday(오늘실행, session)).toBe(true);
    expect(countsForToday(어제실행, session)).toBe(false);
    expect(countsForToday(null, session)).toBe(false);
    expect(SUCCESS_WINDOW_HOURS).toBe(12);
    // 전날 밤 22:00 KST 에 손으로 다시 돌린 성공은 오늘 것이 아니다 (docs/infra.md 25.376)
    const 전날밤 = success("daily_kr", "2026-09-17", "2026-09-17T13:00:00Z"); // 22:00 KST 9/17
    expect(countsForToday(전날밤, session)).toBe(false);

    const now = new Date("2026-09-18T00:30:00Z"); // 마감(00:20Z) 이후
    const watch = WATCHES.filter((w) => w.job === "daily_kr");
    expect(dueAlerts(now, SESSIONS, [오늘실행], [], watch)).toEqual([]);
    expect(dueAlerts(now, SESSIONS, [어제실행], [], watch)).toHaveLength(1);
  });

  it("감성은 개장 30분 전이 마감이다 (일일 배치의 입력이라서)", () => {
    const watch = WATCHES.filter((w) => w.job === "sentiment");
    expect(dueAlerts(new Date("2026-09-17T23:20:00Z"), SESSIONS, [], [], watch)).toEqual([]); // 08:20 KST
    const late = dueAlerts(new Date("2026-09-17T23:40:00Z"), SESSIONS, [], [], watch); // 08:40 KST
    expect(late).toHaveLength(1);
    expect(late[0].job).toBe("sentiment");
  });

  it("미국은 개장 시각이 마감이고 현지 날짜로 센다", () => {
    const watch = WATCHES.filter((w) => w.job === "daily_us");
    expect(dueAlerts(new Date("2026-09-17T13:00:00Z"), SESSIONS, [], [], watch)).toEqual([]);
    const late = dueAlerts(new Date("2026-09-17T14:00:00Z"), SESSIONS, [], [], watch);
    expect(late).toHaveLength(1);
    expect(late[0].localDate).toBe("2026-09-17");
  });

  it("이미 보낸 것은 다시 내지 않는다", () => {
    const now = new Date("2026-09-18T00:30:00Z");
    const sent = [{ job: "daily_kr", market: "KR", local_date: "2026-09-18", kind: "missing" }];
    expect(dueAlerts(now, SESSIONS, [], sent, WATCHES.filter((w) => w.job === "daily_kr"))).toEqual([]);
  });

  it("미래 세션이 바닥나면 달력 알림", () => {
    const now = new Date("2026-09-22T05:00:00Z"); // DRAINED 의 마지막 세션(09-21) 다음
    const due = dueAlerts(now, DRAINED, [], []);
    expect(due.map((d) => `${d.job}:${d.market}`)).toEqual(["calendar:KR", "calendar:US"]);
    expect(due[0].message).toContain("market_sessions");
  });

  it("D1 임시 운영 중에는 미국을 보지 않는다 — 돌리지 않는 배치를 없다고 알리면 거짓 알림이다", () => {
    // 2026-09-18: D1 에는 미국 데이터가 없어 미국 일일 배치를 쉰다 (docs/infra.md 25.14)
    const late = new Date("2026-09-17T14:00:00Z"); // 미국 마감(개장) 지남
    const onlyKr = dueAlerts(late, SESSIONS, [], [], WATCHES.filter((w) => w.job === "daily_us"), ["KR"]);
    expect(onlyKr).toEqual([]);
    const dry = dueAlerts(new Date("2026-09-22T05:00:00Z"), DRAINED, [], [], WATCHES, ["KR"]);
    expect(dry.map((d) => `${d.job}:${d.market}`)).toEqual(["calendar:KR"]); // 국내 달력은 그대로 본다
  });

  it("현지 날짜는 시장마다 다르다", () => {
    const now = new Date("2026-09-18T02:00:00Z"); // 한국 11시, 미국 전날 밤
    expect(localDate("KR", now)).toBe("2026-09-18");
    expect(localDate("US", now)).toBe("2026-09-17");
    expect(sessionOn(SESSIONS, "KR", "2026-09-18")?.open_utc).toBe("2026-09-18T00:00:00Z");
    expect(sessionOn(SESSIONS, "KR", "2026-09-19")).toBeNull();
  });
});

/**
 * 거래일 달력이 마르는 것을 **마르기 전에** 본다 (2026-09-22, docs/infra.md 25.104).
 *
 * `market_sessions` 는 일일 배치가 14일치를 채운다. 배치가 멈추면 하루에 한 거래일씩
 * 줄고, 다 마르는 순간 장중 감시가 조용히 멈춘다(`/api/cron/intraday` 가 "세션 정보 없음").
 * 예전 규칙은 `future.length > 0` 이면 넘어갔다 — **다 마른 날에야** 울렸다.
 * 그때는 이미 장중 감시가 멈춘 뒤다. 열흘치 여유가 있는데 하루도 쓰지 않았다.
 */
describe("거래일 달력", () => {
  const 지금 = new Date("2026-09-22T05:00:00Z"); // 국내 09-22 14시

  /** 달력만 본다 — 감시 대상을 비워 무응답 판단이 섞이지 않게 한다 */
  function 달력알림(dates: string[], existing: Parameters<typeof dueAlerts>[3] = []) {
    return dueAlerts(지금, calendar("KR", dates), [], existing, [], ["KR"]);
  }

  it("문턱은 3거래일이고 근거가 코드에 있다", () => {
    // 14일 창이 덮는 거래일은 국내 5~11일·미국 8~11일이다(실측). 3 이면 정상일 때는 절대 안 울린다
    expect(CALENDAR_LOW_SESSIONS).toBe(3);
    const lib = readFileSync(join(process.cwd(), "lib", "health.ts"), "utf-8");
    expect(lib).toContain("국내 **5~11일**");
    expect(lib).toContain("미국 **8~11일**");
    // 문서와 코드가 어긋난 채로 두지 않는다 (CLAUDE.md 기록 규칙)
    const doc = readFileSync(join(process.cwd(), "..", "docs", "health.md"), "utf-8");
    expect(doc).toContain(`CALENDAR_LOW_SESSIONS`);
    expect(doc).toContain(`(=${CALENDAR_LOW_SESSIONS})`);
  });

  it("딱 3거래일 남으면 미리 알린다 — 아직 멈추지 않았을 때다", () => {
    const due = 달력알림(["2026-09-22", "2026-09-23", "2026-09-24"]);
    expect(due).toHaveLength(1);
    expect(due[0].kind).toBe("calendar-low");
    expect(due[0].job).toBe("calendar");
    expect(due[0].localDate).toBe("2026-09-22");
    // 며칠 남았고 언제까지인지가 문구에 있어야 사람이 급한지 안 급한지 안다
    expect(due[0].message).toContain("[주의]");
    expect(due[0].message).toContain("3거래일");
    expect(due[0].message).toContain("2026-09-24");
    expect(due[0].data.future_sessions).toBe(3);
  });

  it("4거래일 남으면 조용하다 — 정상 운영에서 울리면 아무도 안 본다", () => {
    expect(달력알림(["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"])).toEqual([]);
  });

  it("오늘 세션도 남은 것으로 센다", () => {
    // 오늘 장이 아직 안 끝났으면 오늘도 감시할 날이다. `date >= today`
    const due = 달력알림(["2026-09-22"]);
    expect(due[0].data.future_sessions).toBe(1);
    expect(due[0].kind).toBe("calendar-low");
  });

  it("지난 세션만 남으면 [무응답] 으로 올라간다 — 이미 멈춘 것이다", () => {
    const due = 달력알림(["2026-09-18", "2026-09-21"]);
    expect(due).toHaveLength(1);
    expect(due[0].kind).toBe("calendar");
    expect(due[0].message).toContain("[무응답]");
    expect(due[0].message).toContain("장중 감시가 지금 멈춰");
    expect(due[0].data.future_sessions).toBe(0);
  });

  it("미리 알림과 멈춤 알림은 서로를 막지 않는다", () => {
    // 같은 job·market·날짜라 종류가 하나였다면, 미리 보낸 [주의] 가 [무응답] 을 삼킨다
    const 주의보냄 = [{ job: "calendar", market: "KR", local_date: "2026-09-22", kind: "calendar-low" }];
    expect(달력알림(["2026-09-22", "2026-09-23"], 주의보냄)).toEqual([]);
    const 멈춤 = 달력알림(["2026-09-21"], 주의보냄);
    expect(멈춤).toHaveLength(1);
    expect(멈춤[0].kind).toBe("calendar");
  });

  it("시장마다 따로 센다", () => {
    const sessions = [...calendar("KR", ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]),
      ...calendar("US", ["2026-09-22", "2026-09-23"])];
    const due = dueAlerts(지금, sessions, [], [], [], ["KR", "US"]);
    expect(due.map((d) => `${d.market}:${d.kind}`)).toEqual(["US:calendar-low"]);
  });
});

describe("질의", () => {
  it("마지막 성공은 성공한 실행만 본다 (running 은 세지 않는다)", () => {
    const rows = db.prepare(LAST_SUCCESS).all() as unknown as LastSuccess[];
    const kr = rows.find((r) => r.job_name === "daily_kr");
    expect(kr?.trade_date).toBe("2026-09-17");
    expect(rows.find((r) => r.job_name === "sentiment")?.trade_date).toBe("2026-09-17");
  });

  it("상관 서브쿼리를 쓰지 않는다 — 매시간 도는 질의라 기록이 쌓이면 읽기가 제곱으로 는다", () => {
    // docs/infra.md 24.4. 고치면 다시 느려지지 않게 모양 자체를 고정한다
    expect(LAST_SUCCESS).not.toMatch(/SELECT[^)]*FROM batch_runs[sS]*FROM batch_runs/i);
    expect(LAST_SUCCESS).toContain("GROUP BY job_name");
  });

  it("partial 도 '돌았다' 로 센다 (리포트는 나갔다). dryrun 은 세지 않는다", () => {
    const rows = db.prepare(LAST_SUCCESS).all() as unknown as LastSuccess[];
    const us = rows.find((r) => r.job_name === "daily_us");
    expect(us?.status).toBe("partial");
    expect(us?.finished_at).toBe("2026-09-17T12:35:00Z");

    // 미국 개장(13:30Z)이 마감인데 그 전에 partial 로 끝났으므로 조용하다
    const now = new Date("2026-09-17T14:00:00Z");
    const watch = WATCHES.filter((w) => w.job === "daily_us");
    expect(dueAlerts(now, SESSIONS, rows, [], watch)).toEqual([]);
  });

  it("끝나지 않고 굳은 실행은 '실행 중' 이 아니라 문제다", () => {
    const now = new Date("2026-09-17T12:00:00Z");
    expect(isStuck("running", "2026-09-17T11:30:00Z", now)).toBe(false); // 30분 전이면 아직 도는 중
    expect(isStuck("running", "2026-09-17T01:37:00Z", now)).toBe(true); // 10시간 전이면 굳었다
    expect(isStuck("success", "2026-09-16T01:00:00Z", now)).toBe(false);
    expect(STUCK_AFTER_HOURS).toBe(6);
  });

  it("세션 질의는 기준일 이후만 준다", () => {
    // 픽스처에는 KR 09-18, US 09-17 만 있다. 기준일이 09-18 이면 미국 것은 빠진다
    const rows = db.prepare(SESSIONS_AROUND).all("2026-09-18") as unknown as Session[];
    expect(rows.map((r) => `${r.market}:${r.date}`)).toEqual(["KR:2026-09-18"]);
    const wider = db.prepare(SESSIONS_AROUND).all("2026-09-17") as unknown as Session[];
    expect(wider.map((r) => `${r.market}:${r.date}`)).toEqual(["KR:2026-09-18", "US:2026-09-17"]);
  });

  it("같은 대상·날짜·종류는 DB 가 한 번만 받는다", () => {
    const args = ["daily_kr", "KR", "2026-09-18", "missing", "문구", "{}", "2026-09-18T00:30:00Z", null];
    db.prepare(HEALTH_ALERT_INSERT).run(...(args as never[]));
    db.prepare(HEALTH_ALERT_INSERT).run(...(args as never[]));
    const rows = db.prepare(TODAY_HEALTH_ALERTS).all("2026-09-18") as Array<{ job: string }>;
    expect(rows).toHaveLength(1);
    expect(rows[0].job).toBe("daily_kr");
  });

  it("마이그레이션이 감시 호출 기록 자리를 만들어 둔다", () => {
    const row = db.prepare("SELECT outcome FROM cron_heartbeats WHERE job = 'health'").get() as { outcome: string };
    expect(row.outcome).toBe("never");
  });
});

describe("표시", () => {
  it("80% 부터 경고, 100% 부터 차단, 한도를 모르면 unknown", () => {
    expect(usageTone(usageRatio(16000, 20000), 80)).toBe("warn");
    expect(usageTone(usageRatio(15000, 20000), 80)).toBe("ok");
    expect(usageTone(usageRatio(20000, 20000), 80)).toBe("blocked");
    expect(usageTone(usageRatio(5, null), 80)).toBe("unknown");
    expect(usageRatio(5, 0)).toBeNull();
  });

  it("경과 시간은 사람이 읽는 단위로", () => {
    const now = new Date("2026-09-18T00:00:00Z");
    expect(sinceText("2026-09-17T23:30:00Z", now)).toBe("1시간 이내");
    expect(sinceText("2026-09-17T00:00:00Z", now)).toBe("24시간 전");
    expect(sinceText("2026-09-15T00:00:00Z", now)).toBe("3일 전");
    expect(sinceText(null, now)).toBe("기록 없음");
  });
});

describe("감시 경로", () => {
  it("문서에 적은 마감 오프셋과 코드가 같다", () => {
    const doc = readFileSync(join(process.cwd(), "..", "docs", "health.md"), "utf-8");
    expect(doc).toContain("개장 **+20분**");
    expect(doc).toContain("개장 **−30분**");
    expect(WATCHES.find((w) => w.job === "daily_kr")?.dueOffsetMinutes).toBe(20);
    expect(WATCHES.find((w) => w.job === "sentiment")?.dueOffsetMinutes).toBe(-30);
    expect(WATCHES.find((w) => w.job === "daily_us")?.dueOffsetMinutes).toBe(0);
  });

  it("감시 경로는 로그인 예외(크론)로 열려 있고 토큰으로 스스로를 지킨다", () => {
    const proxy = readFileSync(join(process.cwd(), "proxy.ts"), "utf-8");
    expect(proxy).toContain('const CRON_PREFIX = "/api/cron/"');
    const route = readFileSync(join(process.cwd(), "app", "api", "cron", "health", "route.ts"), "utf-8");
    expect(route).toContain("tokenMatches");
    expect(route).toContain("status: 401");
    // 쓰기 대상은 health_alerts 와 cron_heartbeats 뿐이다 (docs/health.md 1장).
    // 경로에 직접 쓴 문장과, 경로가 쓰는 lib 상수를 함께 본다
    const allowed = new Set(["health_alerts", "cron_heartbeats"]);
    const written = [
      ...[...route.matchAll(/INSERT INTO (\w+)/g)].map((m) => m[1]),
      ...[...route.matchAll(/UPDATE (\w+) SET/g)].map((m) => m[1]),
      ...[...HEALTH_ALERT_INSERT.matchAll(/INSERT INTO (\w+)/g)].map((m) => m[1]),
    ];
    expect(written.length).toBeGreaterThan(0);
    for (const table of written) expect(allowed.has(table)).toBe(true);
    expect(HEALTH_ALERT_INSERT).toContain("INSERT INTO health_alerts");
  });
});

/**
 * 같은 진실이 두 벌 있지 않은가 (2026-09-21, docs/infra.md 25.49).
 *
 * `RAN_STATUSES` 에는 **왜 `partial` 을 세는지**가 적혀 있다 — 2026-09-17 에 국내 일일
 * 배치가 경고를 안고 `partial` 로 끝나면서 리포트는 정상 발송했고, 그날 "무응답" 을
 * 보내면 거짓 알림이었다. 그런데 그 상수는 **아무도 쓰지 않았고**, 13줄 아래 SQL 이
 * `IN ('success', 'partial')` 을 글자로 다시 적고 있었다.
 *
 * 상수를 고쳐도 아무 일이 없고, SQL 을 고치면 상수가 거짓말을 한다. 이제 SQL 을 상수에서
 * 만든다 — 이 테스트는 그 연결이 끊기지 않았는지 본다.
 */
describe("돌았다고 셀 상태", () => {
  it("SQL 이 상수에서 만들어진다", () => {
    for (const status of RAN_STATUSES) {
      expect(LAST_SUCCESS).toContain(`'${status}'`);
    }
  });

  it("세지 않는 상태는 SQL 에 없다", () => {
    // failed 는 배치가 스스로 알리고, dryrun·skipped 는 리포트를 내지 않는다
    for (const status of ["failed", "dryrun", "skipped", "running"]) {
      expect(LAST_SUCCESS).not.toContain(`'${status}'`);
    }
  });

  it("partial 을 센다", () => {
    // 이 한 줄이 2026-09-17 의 거짓 알림을 막는다. 빼면 경고를 안고 끝난 날마다 무응답 알림이 간다
    expect(RAN_STATUSES).toContain("partial");
  });
});

describe("감시가 시장을 가린다", () => {
  /**
   * **있었던 일** (docs/infra.md 25.128). `sentiment` 는 이름 하나로 두 시장에서 돈다 —
   * `daily_kr`·`daily_us` 가 각각 `sentiment.run(market)` 을 부르고 둘 다
   * `batch_runs(job_name='sentiment')` 를 남긴다.
   *
   * `LAST_SUCCESS` 가 `GROUP BY job_name` 이었고 `dueAlerts` 도 이름만 봤다. 그래서
   * **미국 감성의 성공이 국내 감성 감시를 잠재웠다.** 국내가 몇 주 죽어 있어도 조용하다.
   * 25.111·25.112 와 같은 모양인데, 하필 **다른 모든 고장을 알려 주는 장치** 안에 있었다.
   *
   * 지금은 미국이 쉬는 중이라(25.14) 잠들어 있지만, 25.14 는 "Turso 로 돌아가면 다시 돈다"
   * 고 적힌 **일시 정지**다. 돌아오는 날 아무도 이걸 기억하지 못한다.
   */
  const 감성감시 = WATCHES.filter((w) => w.job === "sentiment");
  // 달력 규칙(25.104)이 끼어들지 않게 넉넉히 채운다 — 여기서 보는 것은 무응답 규칙이다
  const KR세션: Session[] = calendar("KR", ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]);
  const 마감후 = new Date("2026-09-22T02:00:00Z"); // 개장 00:00Z, 감성 마감은 −30분

  function 성공(market: string | null, finishedAt: string): LastSuccess {
    return { job_name: "sentiment", market, trade_date: "2026-09-21", finished_at: finishedAt };
  }

  it("미국 감성의 성공은 국내 감성 감시를 잠재우지 못한다", () => {
    const due = dueAlerts(마감후, KR세션, [성공("US", "2026-09-22T01:00:00Z")], [], 감성감시, ["KR"]);
    expect(due.map((d) => d.job)).toEqual(["sentiment"]);
  });

  it("국내 감성이 돌았으면 조용하다", () => {
    const due = dueAlerts(마감후, KR세션, [성공("KR", "2026-09-22T01:00:00Z")], [], 감성감시, ["KR"]);
    expect(due).toEqual([]);
  });

  it("둘이 섞여 있어도 제 시장 것을 고른다", () => {
    const rows = [성공("US", "2026-09-22T01:00:00Z"), 성공("KR", "2026-09-22T01:30:00Z")];
    expect(findLastSuccess(rows, { job: "sentiment", market: "KR" })?.market).toBe("KR");
    expect(findLastSuccess(rows, { job: "sentiment", market: "US" })?.market).toBe("US");
  });

  it("시장을 안 남기는 작업은 그 기록을 쓴다", () => {
    // `portfolio`·`sell_flags` 처럼 나라가 섞인 작업이 나중에 감시 대상이 될 수 있다.
    // 그때 딱 맞는 기록이 없다고 매일 거짓 알림을 보내면 감시 자체를 못 믿게 된다
    const rows = [성공(null, "2026-09-22T01:00:00Z")];
    expect(findLastSuccess(rows, { job: "sentiment", market: "KR" })?.finished_at).toBe("2026-09-22T01:00:00Z");
  });

  it("질의가 시장까지 묶는다", () => {
    expect(LAST_SUCCESS).toContain("GROUP BY job_name, market");
  });
});
