import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { MIN_SECRET_LENGTH, secretWeakDetail } from "@/lib/intraday";

/** 짧은 CRON_SECRET 을 아무 말 없이 받았다 (docs/infra.md 25.727) */
describe("크론 비밀 길이", () => {
  it("짧으면 표시를 더하고 길면 더하지 않는다", () => {
    expect(MIN_SECRET_LENGTH).toBe(32);
    expect(secretWeakDetail("short")).toHaveProperty("secret_weak");
    expect(secretWeakDetail(undefined)).toHaveProperty("secret_weak");
    expect(secretWeakDetail("x".repeat(32))).toEqual({});
  });

  it("장중·장 밖·오류 호출 기록 모두에 붙인다 (25.733, 교차검증: 장 밖 기록이 표시를 지웠다)", () => {
    const 원본 = readFileSync(join(__dirname, "..", "app", "api", "cron", "intraday", "route.ts"), "utf8");
    const 기록수 = (원본.match(/recordHeartbeat\("intraday"/g) ?? []).length;
    expect(기록수).toBeGreaterThanOrEqual(3);
    expect((원본.match(/\.\.\.secretWeakDetail\(process\.env\.CRON_SECRET\)/g) ?? []).length).toBe(기록수);
  });
});
