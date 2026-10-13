// 복구 리허설 신선도 — 분기 1회라 한 번 거르면 노랑, 두 번이면 빨강 (docs/infra.md 25.1306, 운영 감사 재현)
import { describe, expect, it } from "vitest";
import { freshnessVerdict, RESTORE_DRILL_STALE_DAYS } from "@/lib/health";

describe("restore_drill", () => {
  it("분기 하나 거르면 노랑, 둘이면 빨강", () => {
    const now = new Date("2027-10-06T00:00:00Z");
    expect(freshnessVerdict("restore_drill", "2027-07-06", now, "turso").tone).toBe("ok");
    expect(freshnessVerdict("restore_drill", "2027-04-06", now, "turso").tone).toBe("warn");
    expect(freshnessVerdict("restore_drill", "2026-10-06", now, "turso").tone).toBe("bad");
    expect(RESTORE_DRILL_STALE_DAYS).toBeLessThan(120);
  });
});
