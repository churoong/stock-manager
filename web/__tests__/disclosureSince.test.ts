import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import {
  DISCLOSURE_LOOKBACK_SESSIONS,
  disclosureBoundary,
  freshDisclosures,
} from "@/lib/intraday";

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

  it("경로가 앞 거래일 둘째 날부터 묻고, 그 창의 알림 전부를 경계로 쓴다", () => {
    const src = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(src).toContain("bgn_de=${부터}&end_de=${ymd}");
    expect(src).toContain("freshDisclosures(");
    expect(src).toContain("DISCLOSURE_LOOKBACK_SESSIONS");
    expect(src).toContain("disclosureBoundary(");
    expect(DISCLOSURE_LOOKBACK_SESSIONS).toBeGreaterThanOrEqual(2);
  });
});

describe("하루짜리 DART 점검이 장 마감 뒤 공시를 삼키지 않는다 (docs/infra.md 25.1075)", () => {
  // 수(10-07) 장 마감 뒤 공시 → 목(10-08) 하루 종일 DART 점검(상태 800, 알림 없음) → 금(10-09) 장중
  const 목록 = [
    { rcept_no: "20261007000900", report_nm: "수요일 장 마감 뒤" },
    { rcept_no: "20261007000300", report_nm: "수요일 장중 — 수요일에 알림" },
  ];
  const 알림들 = [
    { stock_id: 1, data: JSON.stringify({ rcept_no: "20261007000300" }) },
    { stock_id: 2, data: JSON.stringify({ rcept_no: "20261006000500" }) },
    { stock_id: 2, data: JSON.stringify({ rcept_no: "20261007000100" }) },
    { stock_id: 3, data: "{깨짐" },
  ];

  it("창 안 알림 가운데 종목마다 가장 늦은 접수번호가 경계다", () => {
    const 경계 = disclosureBoundary(알림들);
    expect(경계.get(1)).toBe("20261007000300");
    expect(경계.get(2)).toBe("20261007000100");
    expect(경계.has(3)).toBe(false);
  });

  it("점검 다음 날에도 수요일 장 마감 뒤 공시가 새 것으로 남고, 이미 알린 것은 빠진다", () => {
    const 남은 = freshDisclosures(
      목록,
      "20261009",
      disclosureBoundary(알림들).get(1) ?? null,
    ).map((d) => d.report_nm);
    expect(남은).toEqual(["수요일 장 마감 뒤"]);
  });
});
