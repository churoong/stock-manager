/**
 * 아예 안 깨워진 일일 배치를 무응답 감시가 한 번 깨운다 (docs/infra.md 25.2310·25.2311, docs/health.md 5장 예외).
 * 2026-11-23 정시 깨움 한 번이 오지 않았고 Actions 예비가 3시간 16분 늦었다.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { execute } = vi.hoisted(() => ({ execute: vi.fn() }));
vi.mock("@/lib/db", async (orig) => ({
  ...(await orig<typeof import("@/lib/db")>()),
  execute,
  batch: vi.fn(),
}));

import {
  ANY_RUN_SINCE_SQL,
  DAILY_KICK_JOBS,
  kickMissingDaily,
  kickNote,
  marketMidnightUtc,
} from "@/lib/dailyKick";
import { JOBS } from "@/lib/dispatch";
import type { Due } from "@/lib/health";

// 11-23(월) 국내 개장 09:00 KST = 00:00Z, 미국(겨울) 09:30 ET = 14:30Z
const 열림 = {
  KR: "2026-11-23T00:00:00.000Z",
  US: "2026-11-23T14:30:00.000Z",
} as const;

function 알림(
  job: string,
  kind: Due["kind"] = "missing",
  data?: Record<string, unknown>,
): Due {
  const market = job === "daily_us" ? "US" : "KR";
  return {
    job,
    market,
    kind,
    localDate: "2026-11-23",
    message: `[무응답] ${job}`,
    data: data ?? { session_open_utc: 열림[market], success_window_from: "x" },
  };
}

type 줄 = [string, string, string, Record<string, unknown> | null];

/** 실제 SQL 을 메모리 SQLite 에 — 표는 마이그레이션(0025 health_alerts)과 같은 열. 알림은 감시가 **저장한 뒤** 깨운다 */
function db(runs: 줄[] = [], 저장: Due[] = []) {
  const d = new DatabaseSync(":memory:");
  d.exec(`CREATE TABLE health_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, market TEXT NOT NULL,
    local_date TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT,
    UNIQUE (job, market, local_date, kind))`);
  d.exec(
    "CREATE TABLE batch_runs (id INTEGER PRIMARY KEY, job_name TEXT, started_at TEXT, status TEXT, step_log TEXT)",
  );
  for (const [job, at, status, log] of runs)
    d.prepare(
      "INSERT INTO batch_runs (job_name, started_at, status, step_log) VALUES (?, ?, ?, ?)",
    ).run(job, at, status, log === null ? "깨진 글" : JSON.stringify(log));
  for (const a of 저장)
    d.prepare(
      "INSERT INTO health_alerts (job, market, local_date, kind, message, data, created_at) VALUES (?, ?, ?, ?, ?, ?, 't')",
    ).run(
      a.job,
      a.market,
      a.localDate,
      a.kind,
      a.message,
      JSON.stringify(a.data),
    );
  execute.mockImplementation(async (sql: string, args: unknown[] = []) => {
    const st = d.prepare(sql);
    if (/^\s*SELECT/i.test(sql))
      return {
        columns: [],
        rows: st
          .all(...(args as never[]))
          .map((r) => Object.values(r as object)),
        affectedRows: 0,
      };
    return {
      columns: [],
      rows: [],
      affectedRows: Number(st.run(...(args as never[])).changes),
    };
  });
  const 행 = (job: string) =>
    d
      .prepare(
        "SELECT message, data FROM health_alerts WHERE job = ? AND kind = 'missing'",
      )
      .get(job) as { message: string; data: string } | undefined;
  return { 행 };
}

function 부르개() {
  const 부름: string[] = [];
  const f = async (job: { event: string; payload?: unknown }) => {
    부름.push(job.event);
    expect(job.payload).toBeUndefined(); // force 없이 — 예약 판단 그대로(화면 수동 실행과 다르다)
    return { dispatched: true };
  };
  return { 부름, f };
}

describe("아예 안 깨워진 일일 배치 깨우기", () => {
  // 블록으로 — 화살표가 mockReset() 의 돌려준 값(목 자체)을 내면 vitest 가 그것을 뒷정리 훅으로 불러 엉뚱한 호출이 생긴다
  beforeEach(() => {
    execute.mockReset();
  });

  it("저장된 일일 배치 [무응답] 만 깨우고 결과를 그 행의 글·data 에 적는다", async () => {
    const 알림들 = [
      알림("daily_kr"),
      알림("sentiment"),
      알림("calendar", "calendar"),
    ];
    const { 행 } = db([], 알림들);
    const { 부름, f } = 부르개();
    const 결과 = await kickMissingDaily(알림들, f);
    expect(부름).toEqual(["daily-kr"]);
    expect(결과).toEqual([
      { job: "daily_kr", market: "KR", kick: "kicked", noted: true },
    ]);
    const r = 행("daily_kr")!;
    expect(r.message).toBe("[무응답] daily_kr" + kickNote("kicked"));
    expect(JSON.parse(r.data)).toMatchObject({
      kick: "kicked",
      session_open_utc: 열림.KR,
    });
    expect(행("sentiment")!.message).toBe("[무응답] sentiment"); // 손대지 않는다
  });

  it("전날 밤 실행(그 시장 자정 전)은 오늘 시도가 아니다 — [필수] 25.2311", async () => {
    // 국내 전날 22:00 KST 손 실행(성공 창 = 개장 − 12h = 전날 21:00 KST 안이지만 자정 전)
    const { 행 } = db(
      [["daily_kr", "2026-11-22T13:00:00+00:00", "success", {}]],
      [알림("daily_kr")],
    );
    const { 부름, f } = 부르개();
    await kickMissingDaily([알림("daily_kr")], f);
    expect(부름).toEqual(["daily-kr"]);
    expect(JSON.parse(행("daily_kr")!.data).kick).toBe("kicked");
  });

  it("예정된 건너뜀('아직 이릅니다'·'휴장일')·확인 실행은 시도가 아니다 — 겨울 미국 12:27Z [필수] 25.2311", async () => {
    const { 행 } = db(
      [
        [
          "daily_us",
          "2026-11-23T12:27:40+00:00",
          "skipped",
          {
            decision: "개장까지 123분 남아 아직 이릅니다. 다음 예약에 맡깁니다",
          },
        ],
        [
          "daily_us",
          "2026-11-23T12:40:00+00:00",
          "skipped",
          { decision: "휴장일 — x" },
        ],
        ["daily_us", "2026-11-23T12:50:00+00:00", "dryrun", {}],
      ],
      [알림("daily_us")],
    );
    const { 부름, f } = 부르개();
    await kickMissingDaily([알림("daily_us")], f);
    expect(부름).toEqual(["daily-us"]);
    expect(JSON.parse(행("daily_us")!.data).kick).toBe("kicked");
  });

  it("오늘 예정 밖 건너뜀·실패·도는 중이 있으면 깨우지 않고 그 상태를 적는다 · 깨진 step_log 도 질의를 깨지 않는다", async () => {
    const 경우: 줄[] = [
      [
        "daily_kr",
        "2026-11-22T23:27:05+00:00",
        "skipped",
        { decision: "한도로 건너뜀" },
      ],
      ["daily_kr", "2026-11-22T23:27:05+00:00", "failed", null],
      ["daily_kr", "2026-11-22T23:27:05+00:00", "running", {}],
      // 건너뜀이면서 기록이 깨진 줄 — `json_valid` 막음이 없으면 질의가 'malformed JSON' 으로 깨진다(25.2312)
      ["daily_kr", "2026-11-22T23:27:06+00:00", "skipped", null],
    ];
    for (const 한줄 of 경우) {
      const { 행 } = db([한줄], [알림("daily_kr")]);
      const { 부름, f } = 부르개();
      const 결과 = await kickMissingDaily([알림("daily_kr")], f);
      expect(부름).toEqual([]);
      expect(결과[0].kick).toBe(`ran:${한줄[2]}`);
      expect(행("daily_kr")!.message).toContain(
        `실행 기록이 있습니다(${한줄[2]})`,
      );
    }
  });

  it("저장되지 않았거나 다른 호출이 잡은 알림은 깨우지 않는다 — 하루 한 번 [권고] 25.2311", async () => {
    db([], []); // 저장이 안 됐다(쓰기 한도 등)
    const 하나 = 부르개();
    expect(await kickMissingDaily([알림("daily_kr")], 하나.f)).toEqual([]);
    expect(하나.부름).toEqual([]);
    db([], [알림("daily_kr")]);
    const 둘 = 부르개();
    await kickMissingDaily([알림("daily_kr")], 둘.f);
    await kickMissingDaily([알림("daily_kr")], 둘.f); // 겹친 호출 — 이미 잡혔다
    expect(둘.부름).toEqual(["daily-kr"]);
  });

  it("깨우는 동안 겹친 호출이 그 행을 발송용으로 잡으면 결과를 못 적은 것으로 남긴다 — 25.2312", async () => {
    const { 행 } = db([], [알림("daily_kr")]);
    const 결과 = await kickMissingDaily([알림("daily_kr")], async () => {
      await execute(
        "UPDATE health_alerts SET sent_at = 'claim:x' WHERE job = 'daily_kr'",
      );
      return { dispatched: true };
    });
    expect(결과).toEqual([
      { job: "daily_kr", market: "KR", kick: "kicked", noted: false },
    ]);
    expect(행("daily_kr")!.message).toBe("[무응답] daily_kr");
  });

  it("저장과 잡기 사이에 겹친 호출이 발송용으로 잡은 행도 깨운다 — 깨움을 잃지 않는다(25.2314)", async () => {
    db([], [알림("daily_kr")]);
    await execute(
      "UPDATE health_alerts SET sent_at = 'claim:2026-11-23T00:20:00Z' WHERE job = 'daily_kr'",
    );
    const { 부름, f } = 부르개();
    const 결과 = await kickMissingDaily([알림("daily_kr")], f);
    expect(부름).toEqual(["daily-kr"]);
    expect(결과[0]).toMatchObject({ kick: "kicked", noted: false }); // 결과 줄은 못 붙는다(발송 중)
  });

  it("발송된 행은 실제 SQL 로도 잡히지 않는다", async () => {
    const { 행 } = db([], [알림("daily_kr")]);
    await execute(
      "UPDATE health_alerts SET sent_at = '2026-11-23T00:20:00Z' WHERE job = 'daily_kr'",
    );
    const { 부름, f } = 부르개();
    expect(await kickMissingDaily([알림("daily_kr")], f)).toEqual([]);
    expect(부름).toEqual([]);
    expect(행("daily_kr")!.message).toBe("[무응답] daily_kr");
  });

  it("깨우기 실패·DB 실패는 던지지 않고 글로", async () => {
    const { 행 } = db([], [알림("daily_kr")]);
    const 결과 = await kickMissingDaily([알림("daily_kr")], async () => ({
      dispatched: false,
      reason: "토큰 없음",
    }));
    expect(결과[0].kick).toBe("failed:토큰 없음");
    expect(행("daily_kr")!.message).toContain(
      "다시 깨우지 못했습니다: 토큰 없음",
    );
    execute.mockRejectedValue(new Error("SQLITE_BUSY"));
    const 깨짐 = await kickMissingDaily([알림("daily_kr")], async () => {
      throw new Error("부르면 안 된다");
    });
    expect(깨짐[0].kick).toBe("failed:SQLITE_BUSY");
    expect(깨짐[0].noted).toBe(false); // 결과 쓰기도 실패했다 — 늘 참으로 두면 깨진다(25.2314)
  });

  it("개장 시각을 모르면 깨우지 않는다 · 그 시장 자정", async () => {
    db([], [알림("daily_kr", "missing", {})]);
    expect(
      await kickMissingDaily([알림("daily_kr", "missing", {})], async () => ({
        dispatched: true,
      })),
    ).toEqual([]);
    expect(marketMidnightUtc("KR", 열림.KR)).toBe("2026-11-22T15:00:00.000Z");
    expect(marketMidnightUtc("US", 열림.US)).toBe("2026-11-23T05:00:00.000Z"); // 겨울 EST
    expect(marketMidnightUtc("US", "2026-07-15T13:30:00.000Z")).toBe(
      "2026-07-15T04:00:00.000Z",
    ); // 여름 EDT
    expect(marketMidnightUtc("JP", 열림.KR)).toBeNull();
  });

  it("이벤트 이름은 워크플로·화면 수동 실행과 같다 · 예정된 건너뜀 글은 배치의 글과 같다", () => {
    for (const [key, job] of Object.entries(DAILY_KICK_JOBS)) {
      expect(job.event).toBe(JOBS.find((j) => j.key === key)?.event);
      const 흐름 = readFileSync(
        join(process.cwd(), "..", ".github", "workflows", `${job.event}.yml`),
        "utf8",
      );
      expect(흐름).toMatch(new RegExp(`types:\\s*\\[${job.event}\\]`));
      expect(흐름).toMatch(new RegExp(`group:\\s*${job.event}`)); // 같은 동시 실행 그룹 — 예비 예약과 겹쳐도 줄을 선다
    }
    const 달력 = readFileSync(
      join(process.cwd(), "..", "batch", "core", "calendar.py"),
      "utf8",
    );
    expect(달력).toContain("아직 이릅니다");
    const 주간 = readFileSync(
      join(process.cwd(), "..", "batch", "jobs", "weekly_summary.py"),
      "utf8",
    );
    expect(주간).toContain('_예정된_건너뜀 = ("휴장일", "아직 이릅니다")');
    expect(ANY_RUN_SINCE_SQL).toContain("job_name = ? AND started_at >= ?");
  });

  it("무응답 경로는 알림을 저장한 뒤, 발송 전에 깨운다", () => {
    const 경로 = readFileSync(
      join(process.cwd(), "app", "api", "cron", "health", "route.ts"),
      "utf8",
    );
    const 깨움 = 경로.indexOf("await kickMissingDaily(due)");
    expect(깨움).toBeGreaterThan(0);
    expect(경로.indexOf("sql: HEALTH_ALERT_INSERT")).toBeLessThan(깨움);
    expect(경로.indexOf("execute(PENDING_HEALTH_ALERTS")).toBeGreaterThan(깨움);
  });
});
