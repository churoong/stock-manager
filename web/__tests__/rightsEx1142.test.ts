import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** 유상증자 권리락 뒤 겹친 관심 종목의 관심 목표가를 쓰지 않는다 (docs/infra.md 25.1142) */
describe("rights_ex", () => {
  it("장중 경로가 action_guard 와 함께 rights_ex 도 관심가를 막는 사유로 본다", () => {
    const src = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf-8");
    expect(src).toContain('t.reasons.includes("action_guard") || t.reasons.includes("rights_ex")');
  });
});
