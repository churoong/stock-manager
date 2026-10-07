/** 리포트 화면이 기간을 날 키("short")로 보였다 (docs/infra.md 25.927). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { horizonShort } from "@/lib/recommend";

describe("horizonShort", () => {
  it("키를 한국어 한 낱말로", () => {
    expect([horizonShort("short"), horizonShort("mid"), horizonShort("long")]).toEqual(["단기", "중기", "장기"]);
  });

  it("모르는 값과 빈 값은 지어내지 않는다", () => {
    expect(horizonShort("weird")).toBe("weird");
    expect(horizonShort(undefined)).toBe("");
  });

  it("리포트 화면이 날 키를 그대로 찍지 않는다", () => {
    const src = readFileSync(join(process.cwd(), "components", "ReportView.tsx"), "utf-8");
    expect(src).not.toContain("String(item.payload.horizon");
    expect(src).toContain("horizonShort(item.payload.horizon)");
  });
});
