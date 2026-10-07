import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { isLatestReport } from "@/lib/reports";

describe("가장 새 리포트면 날짜를 골라도 빠진 리포트를 센다 (docs/infra.md 25.343)", () => {
  const history = [{ trade_date: "2026-09-25" }, { trade_date: "2026-09-24" }];

  it("목록의 첫 날짜와 같으면 가장 새 것", () => {
    expect(isLatestReport({ trade_date: "2026-09-25" }, history)).toBe(true);
  });

  it("옛 날짜는 아니다", () => {
    expect(isLatestReport({ trade_date: "2026-09-24" }, history)).toBe(false);
    expect(isLatestReport(null, history)).toBe(false);
    expect(isLatestReport({ trade_date: "2026-09-25" }, [])).toBe(false);
  });

  it("경로가 날짜 유무만으로 가르지 않는다", () => {
    const src = readFileSync("app/api/reports/route.ts", "utf8");
    expect(src).toContain("isLatestReport(report, history)");
  });
});
