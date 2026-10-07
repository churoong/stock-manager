import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 미국 ETF 의 대체 종가는 야후 previousClose — 판정일(UTC 실행일)의 **전 거래일** 종가다. 판정일을 종가 날짜처럼 적어 하루 늦게 읽혔다
// (docs/infra.md 25.921, 25.911·25.917 감사에서 남긴 것)
describe("미국 ETF 대체 종가 표기 (25.921)", () => {
  for (const f of ["components/EtfList.tsx", "components/SatelliteList.tsx"]) {
    it(`${f} 가 미국은 '판정 전 거래일 종가' 라 적는다`, () => {
      expect(readFileSync(f, "utf-8")).toContain('row.country === "US" ? "판정 전 거래일 종가" : "판정 때 종가"');
    });
  }
});
