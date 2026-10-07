/**
 * 상태 화면 수동 실행 (docs/health.md 4장). 이벤트 이름이 워크플로와 맞는지 파일로 대조한다 —
 * 이름이 틀려도 GitHub 은 204 를 주므로 운영에서는 조용히 아무 일도 안 일어난다.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JOBS, dispatchJob, findJob } from "@/lib/dispatch";
import { WATCHES } from "@/lib/health";

const WF = join(process.cwd(), "..", ".github", "workflows");

describe("배치 목록", () => {
  it("모든 이벤트 이름이 어느 워크플로의 types 에 있다", () => {
    // 목록을 박지 않고 폴더 전체를 본다. 새 작업(turso-return)을 붙일 때 목록 고치기를 잊지 않게
    const all = readdirSync(WF)
      .filter((f) => f.endsWith(".yml"))
      .map((f) => readFileSync(join(WF, f), "utf-8"))
      .join("\n");
    for (const job of JOBS) expect(all, job.event).toContain(`types: [${job.event}]`);
  });

  it("일일 배치는 force 를 실어 보내고 워크플로가 client_payload 에서 읽는다", () => {
    for (const key of ["daily_kr", "daily_us"]) {
      expect(findJob(key)?.payload).toEqual({ force: true });
      const yml = readFileSync(join(WF, `${findJob(key)!.event}.yml`), "utf-8");
      expect(yml).toContain("github.event.client_payload.force");
    }
    expect(findJob("scores")?.payload).toBeUndefined();
    expect(findJob("rm -rf")).toBeNull();
  });

  it("감시 대상마다 예약 시각 글이 있고 워크플로 cron 과 맞는다", () => {
    for (const w of WATCHES) expect(w.schedule.length).toBeGreaterThan(5);
    // 국내 08:27 KST = 23:27 UTC (daily-kr.yml), 감성 07:40 KST = 22:40 UTC
    expect(readFileSync(join(WF, "daily-kr.yml"), "utf-8")).toContain('cron: "27 23 * * 0-4"');
    expect(WATCHES.find((w) => w.job === "daily_kr")?.schedule).toContain("08:27");
    expect(readFileSync(join(WF, "sentiment-kr.yml"), "utf-8")).toContain('cron: "40 22 * * 0-4"');
    expect(WATCHES.find((w) => w.job === "sentiment")?.schedule).toContain("07:40");
  });
});

describe("깨우기", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("토큰이 없으면 부르지 않고 이유를 준다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "");
    const fake = vi.fn();
    const r = await dispatchJob(findJob("scores")!, fake as unknown as typeof fetch);
    expect(r.dispatched).toBe(false);
    expect(fake).not.toHaveBeenCalled();
  });

  it("repository_dispatch 로 이벤트와 payload 를 보낸다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "t");
    vi.stubEnv("GH_REPO", "o/r");
    const calls: Array<{ url: string; body: string }> = [];
    const fake = async (url: string, init: { body: string }) => {
      calls.push({ url, body: init.body });
      return { status: 204 };
    };
    const r = await dispatchJob(findJob("daily_kr")!, fake as unknown as typeof fetch);
    expect(r.dispatched).toBe(true);
    expect(calls[0].url).toBe("https://api.github.com/repos/o/r/dispatches");
    expect(JSON.parse(calls[0].body)).toEqual({ event_type: "daily-kr", client_payload: { force: true } });
  });

  it("204 가 아니면 이유를 돌려준다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "t");
    vi.stubEnv("GH_REPO", "o/r");
    const fake = async () => ({ status: 403 });
    const r = await dispatchJob(findJob("scores")!, fake as unknown as typeof fetch);
    expect(r.dispatched).toBe(false);
    expect(r.reason).toContain("403");
  });
});
