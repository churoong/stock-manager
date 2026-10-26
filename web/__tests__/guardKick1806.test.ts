/**
 * 웹 장중 경로가 멈춘 KIS모의투자 장중 손절·익절 감시를 깨운다 (docs/infra.md 25.1806).
 * 2026-10-26 kis-guard 의 Actions 예비 예약 넷이 하루 내내 한 번도 오지 않았다 — 일일 배치의 `guard_start` 가 못 깨우는 날의 예비.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { execute } = vi.hoisted(() => ({ execute: vi.fn() }));
vi.mock("@/lib/db", async (orig) => ({
  ...(await orig<typeof import("@/lib/db")>()),
  execute,
}));

import { GUARD_JOB, GUARD_KICK_GAP_MS, GUARD_RECENT_SQL, GUARD_SILENT_MS, kickGuardIfSilent, kickWindow } from "@/lib/guardKick";
import { LIVE_THROTTLE_SQL } from "@/lib/kisVts";

// 10-27(화) 국내 정규장 09:00~15:30 KST
const 장 = { open_utc: "2026-10-27T00:00:00Z", close_utc: "2026-10-27T06:30:00Z" };
const at = (hm: string) => new Date(`2026-10-27T${hm}:00Z`);

/** 실제 SQL 을 메모리 SQLite 에 돌린다 — 질의가 표와 맞는지까지 */
function db() {
  const d = new DatabaseSync(":memory:");
  d.exec("CREATE TABLE batch_runs (id INTEGER PRIMARY KEY, job_name TEXT, market TEXT, started_at TEXT, status TEXT)");
  d.exec("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)");
  execute.mockImplementation(async (sql: string, args: unknown[] = []) => {
    const st = d.prepare(sql);
    if (/^\s*SELECT/i.test(sql)) {
      const rows = st.all(...(args as never[]));
      return { columns: [], rows: rows.map((r) => Object.values(r as object)), affectedRows: 0 };
    }
    const r = st.run(...(args as never[]));
    return { columns: [], rows: [], affectedRows: Number(r.changes) };
  });
  return d;
}

describe("멈춘 장중 감시 깨우기", () => {
  beforeEach(() => {
    execute.mockReset();
  });

  it("창: 개장 + 25분 ~ 마감 − 25분", () => {
    expect(kickWindow(장, at("00:24"))).toBe(false); // 09:24 — 첫 회차·아침 깨우기가 자리 잡을 시간
    expect(kickWindow(장, at("00:25"))).toBe(true);
    expect(kickWindow(장, at("06:05"))).toBe(true); // 15:05
    expect(kickWindow(장, at("06:06"))).toBe(false);
    expect(kickWindow({ open_utc: "?", close_utc: "?" }, at("03:00"))).toBe(false);
  });

  it("최근 25분에 감시 기록이 있으면 그대로, 없으면 깨우고 30분은 다시 깨우지 않는다", async () => {
    const d = db();
    const 깨움 = vi.fn(async () => ({ dispatched: true }));
    d.prepare("INSERT INTO batch_runs (job_name, market, started_at, status) VALUES ('intraday_guard', 'KR', ?, 'success')").run(
      "2026-10-27T01:50:00Z",
    );
    expect(await kickGuardIfSilent(장, at("02:10"), 깨움)).toBe("alive"); // 20분 전 기록
    expect(await kickGuardIfSilent(장, at("02:20"), 깨움)).toBe("kicked"); // 30분 비었다
    expect(깨움).toHaveBeenCalledWith(GUARD_JOB);
    expect(await kickGuardIfSilent(장, at("02:25"), 깨움)).toBe("throttled"); // 러너가 뜨는 동안 또 깨우지 않는다
    expect(await kickGuardIfSilent(장, at("02:51"), 깨움)).toBe("kicked"); // 30분 지나도 여전히 없으면 다시
    expect(깨움).toHaveBeenCalledTimes(2);
    // 다른 작업의 기록은 세지 않는다
    d.prepare("INSERT INTO batch_runs (job_name, market, started_at, status) VALUES ('daily_kr', 'KR', ?, 'success')").run("2026-10-27T03:30:00Z");
    expect(await kickGuardIfSilent(장, at("03:35"), 깨움)).toBe("kicked");
    expect(GUARD_SILENT_MS).toBe(25 * 60_000);
    expect(GUARD_KICK_GAP_MS).toBe(30 * 60_000);
  });

  it("창 밖이면 DB 도 보지 않는다, 못 깨우면 까닭", async () => {
    db();
    expect(await kickGuardIfSilent(장, at("23:50"), vi.fn())).toBe("not_window");
    expect(execute).not.toHaveBeenCalled();
    expect(await kickGuardIfSilent(장, at("03:00"), async () => ({ dispatched: false, reason: "GH_DISPATCH_TOKEN 없음" }))).toBe(
      "failed:GH_DISPATCH_TOKEN 없음",
    );
  });

  it("이벤트 이름이 워크플로 repository_dispatch types 와 같고, 그 이벤트는 반복 실행이 된다", () => {
    const 글 = readFileSync(join(process.cwd(), "..", ".github", "workflows", "kis-guard.yml"), "utf8");
    expect(글).toMatch(new RegExp(`repository_dispatch:\\s*\\n\\s*types: \\[${GUARD_JOB.event}\\]`));
    // 손 실행(workflow_dispatch)에서 loop 를 끈 것만 한 번 — repository_dispatch 는 반복
    expect(글).toContain('[ "${{ github.event_name }}" = "workflow_dispatch" ] && [ "${{ inputs.loop }}" != "true" ]');
    expect(GUARD_RECENT_SQL).toContain("job_name = 'intraday_guard'");
    expect(LIVE_THROTTLE_SQL).toContain("WHERE settings.updated_at < ?");
  });

  it("국내 장중 호출이 깨우기를 부른다 — 장 밖 되돌아가기 뒤, 시세 일 앞", () => {
    const route = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf8");
    const i = route.indexOf("kickGuardIfSilent(session, now)");
    expect(i).toBeGreaterThan(route.indexOf('const skipped = sessions.some'));
    expect(i).toBeLessThan(route.indexOf("const thresholds = settings.alert_thresholds"));
    expect(route.slice(i - 80, i)).toContain('market === "KR"');
  });
});
