/**
 * 포트폴리오에 쓰는 설정이 바뀌면 저장이 재계산을 깨운다 (docs/infra.md 25.629, 감사).
 * 예전에는 매매 저장만 깨워, 수수료·세율·목표 등을 바꿔도 다음 일일 배치 전까지 옛 계산이 "지금 설정" 처럼 보였다.
 */
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

const 저장된 = new Map<string, string>();
vi.mock("@/lib/db", () => ({
  execute: vi.fn(async () => ({ rows: [...저장된.entries()].map(([k, v]) => [k, v]) })),
  batch: vi.fn(async () => []),
}));
const 깨움 = vi.fn(async () => ({ dispatched: true }));
vi.mock("@/lib/portfolio", async (orig) => ({ ...(await orig<typeof import("@/lib/portfolio")>()), requestRecalc: () => 깨움() }));

describe("설정 저장과 포트폴리오 재계산", () => {
  beforeEach(() => {
    저장된.clear();
    깨움.mockClear();
  });

  async function 저장(값: unknown): Promise<Record<string, unknown>> {
    const { PUT } = await import("@/app/api/settings/route");
    const res = await PUT(new Request("https://x/api/settings", { method: "PUT", body: JSON.stringify(값) }) as never);
    return (await res.json()) as Record<string, unknown>;
  }

  it("포트폴리오에 쓰는 칸이 바뀌면 깨우고, 그대로면 깨우지 않는다", async () => {
    const { DEFAULT_SETTINGS } = await import("@/lib/settings");
    const { PORTFOLIO_SETTING_KEYS } = await import("@/lib/portfolio");
    for (const k of PORTFOLIO_SETTING_KEYS) 저장된.set(k, JSON.stringify(DEFAULT_SETTINGS[k as keyof typeof DEFAULT_SETTINGS]));
    await 저장(DEFAULT_SETTINGS);
    expect(깨움).not.toHaveBeenCalled();
    await 저장({ ...DEFAULT_SETTINGS, max_weight_per_sector: 25 });
    expect(깨움).toHaveBeenCalledTimes(1);
  });

  it("재계산이 읽는 키 목록이 배치가 읽는 키와 같다", async () => {
    const { PORTFOLIO_SETTING_KEYS } = await import("@/lib/portfolio");
    const 배치 = readFileSync("../batch/jobs/portfolio.py", "utf8");
    const 읽는키 = new Set([...배치.matchAll(/get_setting(?:_in_range)?\(\s*client,\s*"([a-z_]+)"/g)].map((m) => m[1]));
    expect([...PORTFOLIO_SETTING_KEYS].sort()).toEqual([...읽는키].sort());
  });
});

describe("뉴스 수집 스위치는 배치와 같은 규칙으로 읽는다 (25.629)", () => {
  it("범위 밖·모양이 틀리면 기본값, 0 만 끔", async () => {
    const { newsTopN, DEFAULT_SETTINGS } = await import("@/lib/settings");
    const 기본 = DEFAULT_SETTINGS.sentiment_target_top_n;
    expect(newsTopN("150")).toBe(150);
    expect(newsTopN("0")).toBe(0);
    for (const 값 of ["-5", '"100"', "true", "0.5", "깨짐", undefined]) expect(newsTopN(값)).toBe(기본);
  });
});
