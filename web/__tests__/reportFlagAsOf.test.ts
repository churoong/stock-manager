import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { flagAsOf } from "@/lib/reports";

describe("리포트의 매도 플래그 판정일 (docs/infra.md 25.342)", () => {
  it("실린 판정일을 쓴다", () => {
    expect(flagAsOf({ as_of_date: "2026-09-28" })).toBe("2026-09-28");
  });

  it("옛 리포트처럼 판정일이 없으면 모른다고 한다", () => {
    expect(flagAsOf({})).toBeNull();
  });

  it("화면이 리포트 거래일을 판정일로 넘기지 않는다", () => {
    const src = readFileSync("components/ReportView.tsx", "utf8");
    expect(src).not.toContain("asOf={report.trade_date}");
    expect(src).toContain("asOf={flagAsOf(item.payload)}");
  });
});
