import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { freshDisclosures } from "@/lib/intraday";

const 목록 = [
  { rcept_no: "20260928000100", report_nm: "오늘 공시" },
  { rcept_no: "20260925000900", report_nm: "금요일 장 마감 뒤" },
  { rcept_no: "20260925000300", report_nm: "금요일 장중 — 이미 알림" },
];

describe("장 마감 뒤 공시도 다음 거래일에 알린다 (docs/infra.md 25.344)", () => {
  it("전 거래일에 알린 것보다 뒤의 것과 오늘 것만 남긴다", () => {
    const 남은 = freshDisclosures(목록, "20260928", "20260925000300").map(
      (d) => d.report_nm,
    );
    expect(남은).toEqual(["오늘 공시", "금요일 장 마감 뒤"]);
  });

  it("전 거래일에 알림이 없었으면 그날 것도 모두 새 것", () => {
    expect(freshDisclosures(목록, "20260928", null)).toHaveLength(3);
  });

  it("경로가 전 거래일부터 묻는다", () => {
    const src = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(src).toContain("bgn_de=${부터}&end_de=${ymd}");
    expect(src).toContain("freshDisclosures(");
  });
});
