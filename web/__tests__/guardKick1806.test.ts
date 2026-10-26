/**
 * 웹 장중 경로가 멈춘 KIS모의투자 장중 손절·익절 감시를 깨운다 (docs/infra.md 25.1806·25.1807).
 * 2026-10-26 kis-guard 의 Actions 예비 예약 넷이 하루 내내 한 번도 오지 않았다 — 일일 배치의 `guard_start` 가 못 깨우는 날의 예비.
 * 장중 감시 스위치(`INTRADAY_ENABLED`, 25.1026)가 꺼져 있어도 돌아야 한다(25.1806 교차검증 [필수]).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { execute } = vi.hoisted(() => ({ execute: vi.fn() }));
vi.mock("@/lib/db", async (orig) => ({ ...(await orig<typeof import("@/lib/db")>()), execute, batch: vi.fn() }));

import { GUARD_JOB, GUARD_KICK_GAP_MS, GUARD_SILENT_MS, clockMayKick, kickGuardIfSilent, kickWindow } from "@/lib/guardKick";

// 10-27(화) 국내 정규장 09:00~15:30 KST
const 장 = { open_utc: "2026-10-27T00:00:00Z", close_utc: "2026-10-27T06:30:00Z" };
const at = (hm: string, day = "2026-10-27") => new Date(`${day}T${hm}:00Z`);

/** 실제 SQL 을 메모리 SQLite 에 돌린다 — 질의가 표와 맞는지까지. 부른 SQL 을 모은다 */
function db(sessions = true) {
  const d = new DatabaseSync(":memory:");
  d.exec("CREATE TABLE batch_runs (id INTEGER PRIMARY KEY, job_name TEXT, market TEXT, started_at TEXT, status TEXT)");
  d.exec("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)");
  d.exec("CREATE TABLE market_sessions (market TEXT, date TEXT, open_utc TEXT, close_utc TEXT)");
  if (sessions) d.prepare("INSERT INTO market_sessions VALUES ('KR', '2026-10-27', ?, ?)").run(장.open_utc, 장.close_utc);
  const 부른: string[] = [];
  execute.mockImplementation(async (sql: string, args: unknown[] = []) => {
    부른.push(sql);
    const st = d.prepare(sql);
    if (/^\s*SELECT/i.test(sql)) {
      const rows = st.all(...(args as never[]));
      return { columns: [], rows: rows.map((r) => Object.values(r as object)), affectedRows: 0 };
    }
    const r = st.run(...(args as never[]));
    return { columns: [], rows: [], affectedRows: Number(r.changes) };
  });
  return { d, 부른 };
}

describe("멈춘 장중 감시 깨우기", () => {
  beforeEach(() => {
    execute.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("시계 거름(평일 KST 09:20~15:10)·세션 창(개장 + 25분 ~ 마감 − 25분)", () => {
    expect(clockMayKick(at("00:19"))).toBe(false); // 09:19
    expect(clockMayKick(at("00:20"))).toBe(true);
    expect(clockMayKick(at("06:10"))).toBe(true); // 15:10
    expect(clockMayKick(at("06:11"))).toBe(false);
    expect(clockMayKick(at("03:00", "2026-10-25"))).toBe(false); // 일요일
    expect(kickWindow(장, at("00:24"))).toBe(false);
    expect(kickWindow(장, at("00:25"))).toBe(true);
    expect(kickWindow(장, at("06:05"))).toBe(true);
    expect(kickWindow(장, at("06:06"))).toBe(false);
    expect(kickWindow({ open_utc: "?", close_utc: "?" }, at("03:00"))).toBe(false);
  });

  it("최근 25분에 감시 기록이 있으면 그대로, 없으면 깨우고 30분은 다시 깨우지 않는다", async () => {
    const { d } = db();
    const 깨움 = vi.fn(async () => ({ dispatched: true }));
    // 배치는 파이썬 isoformat(+00:00, 마이크로초)로 적는다
    d.prepare("INSERT INTO batch_runs (job_name, market, started_at, status) VALUES ('intraday_guard', 'KR', ?, 'success')").run(
      "2026-10-27T01:50:00.123456+00:00",
    );
    expect(await kickGuardIfSilent(at("02:10"), 깨움)).toBe("alive"); // 20분 전 기록
    expect(await kickGuardIfSilent(at("02:20"), 깨움)).toBe("kicked"); // 30분 비었다
    expect(깨움).toHaveBeenCalledWith(GUARD_JOB);
    const 행 = d.prepare("SELECT value FROM settings WHERE key = 'kis_guard_kick_at'").get() as { value: string };
    expect(JSON.parse(행.value)).toEqual({ at: "2026-10-27T02:20:00.000Z", result: "kicked" });
    expect(await kickGuardIfSilent(at("02:25"), 깨움)).toBe("throttled"); // 러너가 뜨는 동안 또 깨우지 않는다
    expect(await kickGuardIfSilent(at("02:51"), 깨움)).toBe("kicked"); // 30분 지나도 여전히 없으면 다시
    expect(깨움).toHaveBeenCalledTimes(2);
    // 다른 작업의 기록은 세지 않는다
    d.prepare("INSERT INTO batch_runs (job_name, market, started_at, status) VALUES ('daily_kr', 'KR', ?, 'success')").run("2026-10-27T03:30:00Z");
    expect(await kickGuardIfSilent(at("03:35"), 깨움)).toBe("kicked");
    expect(GUARD_SILENT_MS).toBe(25 * 60_000);
    expect(GUARD_KICK_GAP_MS).toBe(30 * 60_000);
  });

  it("시계 밖이면 DB 를 읽지 않는다, 휴장일·창 밖은 읽기만, 실패는 던지지 않고 까닭", async () => {
    const { 부른 } = db();
    expect(await kickGuardIfSilent(at("23:50", "2026-10-26"), vi.fn())).toBe("off_clock");
    expect(execute).not.toHaveBeenCalled();
    expect(await kickGuardIfSilent(at("00:21"), vi.fn())).toBe("not_window"); // 09:21 — 세션 행 하나 읽고 끝
    expect(부른).toHaveLength(1);
    db(false);
    expect(await kickGuardIfSilent(at("03:00"), vi.fn())).toBe("no_session"); // 휴장일
    db();
    expect(await kickGuardIfSilent(at("03:00"), async () => ({ dispatched: false, reason: "GH_DISPATCH_TOKEN 없음" }))).toBe(
      "failed:GH_DISPATCH_TOKEN 없음",
    );
    execute.mockImplementation(async () => {
      throw new Error("DB 끊김");
    });
    expect(await kickGuardIfSilent(at("03:40"), vi.fn())).toBe("failed:DB 끊김");
  });

  it("장중 감시 스위치가 꺼져 있어도 국내 장중 호출이 실제로 깨운다 — 미국 호출·밤 호출은 DB 를 읽지 않는다", async () => {
    process.env.CRON_SECRET = "s".repeat(40);
    process.env.GH_DISPATCH_TOKEN = "t";
    process.env.GH_REPO = "o/r";
    const 보냄 = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", 보냄);
    const { INTRADAY_ENABLED } = await import("@/lib/intradaySwitch");
    expect(INTRADAY_ENABLED).toBe(false);
    const { GET } = await import("@/app/api/cron/intraday/route");
    const 부르기 = (m: string) => GET(new Request(`http://x/api/cron/intraday?market=${m}`, { headers: { "x-cron-secret": "s".repeat(40) } }));
    const { 부른 } = db();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(at("03:00")); // 12:00 KST, 감시 기록 없음
    const body = await (await 부르기("KR")).json();
    expect(body.skipped).toContain("장중 감시 꺼짐");
    expect(body.guard_kick).toBe("kicked");
    expect(보냄).toHaveBeenCalledTimes(1);
    const [url, init] = 보냄.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.github.com/repos/o/r/dispatches");
    expect(JSON.parse(String(init.body)).event_type).toBe("kis-guard");
    // 꺼진 동안 읽고 쓰는 것은 깨우기 몫(세션·감시 기록·막기 행)뿐
    expect(부른.every((s) => /market_sessions|batch_runs|settings/.test(s))).toBe(true);
    execute.mockClear();
    expect((await (await 부르기("US")).json()).guard_kick).toBeUndefined();
    expect(execute).not.toHaveBeenCalled();
    vi.setSystemTime(at("12:00")); // 21:00 KST
    expect((await (await 부르기("KR")).json()).guard_kick).toBe("off_clock");
    expect(execute).not.toHaveBeenCalled();
  });

  it("이벤트 이름이 워크플로 repository_dispatch types 와 같고, 그 이벤트는 반복 실행이 된다", () => {
    const 글 = readFileSync(join(process.cwd(), "..", ".github", "workflows", "kis-guard.yml"), "utf8");
    expect(글).toMatch(new RegExp(`repository_dispatch:\\s*\\n\\s*types: \\[${GUARD_JOB.event}\\]`));
    expect(글).toContain('[ "${{ github.event_name }}" = "workflow_dispatch" ] && [ "${{ inputs.loop }}" != "true" ]');
    // 장중 감시가 켜졌을 때 호출 기록에도 남긴다
    const route = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf8");
    expect(route).toContain("{ guard_kick: 깨움 }");
  });
});
