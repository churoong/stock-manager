/**
 * 아예 안 깨워진 일일 배치를 무응답 감시가 한 번 깨운다 (docs/infra.md 25.2310, docs/health.md 5장 예외).
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
} from "@/lib/dailyKick";
import { JOBS } from "@/lib/dispatch";
import type { Due } from "@/lib/health";

const 창 = "2026-11-22T12:00:00.000Z"; // 11-23 개장(00:00Z) − 12시간

function 알림(
  job: string,
  kind: Due["kind"] = "missing",
  data: Record<string, unknown> = { success_window_from: 창 },
): Due {
  return {
    job,
    market: job === "daily_us" ? "US" : "KR",
    kind,
    localDate: "2026-11-23",
    message: `[무응답] ${job}`,
    data,
  };
}

/** 실제 SQL 을 메모리 SQLite 에 — 질의가 표와 맞는지까지 */
function db(runs: Array<[string, string]> = []) {
  const d = new DatabaseSync(":memory:");
  d.exec(
    "CREATE TABLE batch_runs (id INTEGER PRIMARY KEY, job_name TEXT, started_at TEXT, status TEXT)",
  );
  for (const [job, at] of runs)
    d.prepare(
      "INSERT INTO batch_runs (job_name, started_at, status) VALUES (?, ?, 'skipped')",
    ).run(job, at);
  execute.mockImplementation(async (sql: string, args: unknown[] = []) => ({
    columns: [],
    rows: d
      .prepare(sql)
      .all(...(args as never[]))
      .map((r) => Object.values(r as object)),
    affectedRows: 0,
  }));
}

describe("아예 안 깨워진 일일 배치 깨우기", () => {
  // 블록으로 — 화살표가 mockReset() 의 돌려준 값(목 자체)을 내면 vitest 가 그것을 뒷정리 훅으로 불러 엉뚱한 호출이 생긴다
  beforeEach(() => {
    execute.mockReset();
  });

  it("그날 실행 기록이 한 줄도 없는 일일 배치의 [무응답] 만 깨운다 — force 없이", async () => {
    db();
    const 부름: string[] = [];
    const 결과 = await kickMissingDaily(
      [
        알림("daily_kr"),
        알림("sentiment"),
        알림("calendar", "calendar"),
        알림("daily_us", "calendar-low"),
      ],
      async (job) => {
        부름.push(job.event);
        expect(job.payload).toBeUndefined(); // 예약 판단 그대로 — 화면 수동 실행(force)과 다르다
        return { dispatched: true };
      },
    );
    expect(부름).toEqual(["daily-kr"]);
    expect(결과[0].data.kick).toBe("kicked");
    expect(결과[0].message).toContain(
      "웹 감시가 일일 배치를 한 번 다시 깨웠습니다",
    );
    expect(결과[0].data.success_window_from).toBe(창); // 원래 칸은 그대로
    // 나머지는 손대지 않는다
    expect(결과.slice(1).map((x) => [x.message, "kick" in x.data])).toEqual([
      ["[무응답] sentiment", false],
      ["[무응답] calendar", false],
      ["[무응답] daily_us", false],
    ]);
  });

  it("창 안에 실행 기록(실패·건너뜀·도는 중)이 있으면 깨우지 않는다 — 돌았는데 안 된 것은 사람이 본다", async () => {
    db([
      ["daily_kr", "2026-11-22T23:27:05+00:00"], // 창 안 — 건너뛴 실행(실패만 보면 놓친다)
      ["daily_us", "2026-11-22T11:59:00+00:00"], // 창 밖(전날)
    ]);
    const 부름: string[] = [];
    const 결과 = await kickMissingDaily(
      [알림("daily_kr"), 알림("daily_us")],
      async (job) => {
        부름.push(job.event);
        return { dispatched: true };
      },
    );
    expect(부름).toEqual(["daily-us"]);
    expect(결과[0].data.kick).toBe("ran");
    expect(결과[0].message).toContain("다시 깨우지 않았습니다");
    expect(결과[1].data.kick).toBe("kicked");
  });

  it("깨우기 실패·DB 실패는 던지지 않고 글로 — 알림 발송을 막지 않는다", async () => {
    db();
    const 실패 = await kickMissingDaily([알림("daily_kr")], async () => ({
      dispatched: false,
      reason: "토큰 없음",
    }));
    expect(실패[0].data.kick).toBe("failed:토큰 없음");
    expect(실패[0].message).toContain("다시 깨우지 못했습니다: 토큰 없음");
    execute.mockRejectedValue(new Error("SQLITE_BUSY"));
    const 깨짐 = await kickMissingDaily([알림("daily_kr")], async () => {
      throw new Error("부르면 안 된다");
    });
    expect(깨짐[0].data.kick).toBe("failed:SQLITE_BUSY");
  });

  it("창의 시작을 모르면 깨우지 않는다", async () => {
    db();
    const 결과 = await kickMissingDaily(
      [알림("daily_kr", "missing", {})],
      async () => {
        throw new Error("부르면 안 된다");
      },
    );
    expect("kick" in 결과[0].data).toBe(false);
  });

  it("이벤트 이름은 워크플로·화면 수동 실행과 같다 · 질의는 색인 열만", () => {
    for (const [key, job] of Object.entries(DAILY_KICK_JOBS)) {
      expect(job.event).toBe(JOBS.find((j) => j.key === key)?.event);
      const 흐름 = readFileSync(
        join(process.cwd(), "..", ".github", "workflows", `${job.event}.yml`),
        "utf8",
      );
      expect(흐름).toMatch(new RegExp(`types:\\s*\\[${job.event}\\]`));
      // 같은 동시 실행 그룹 — 예비 예약과 겹쳐도 줄을 선다
      expect(흐름).toMatch(new RegExp(`group:\\s*${job.event}`));
    }
    expect(ANY_RUN_SINCE_SQL).toContain("job_name = ? AND started_at >= ?");
    expect(kickNote("kicked")).not.toBe(kickNote("ran"));
  });

  it("무응답 경로가 알림을 저장하기 전에 부른다", () => {
    const 경로 = readFileSync(
      join(process.cwd(), "app", "api", "cron", "health", "route.ts"),
      "utf8",
    );
    const 깨움 = 경로.indexOf("const due = await kickMissingDaily(판정);");
    expect(깨움).toBeGreaterThan(0);
    expect(경로.indexOf("HEALTH_ALERT_INSERT", 깨움)).toBeGreaterThan(깨움);
  });
});
