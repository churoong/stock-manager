import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { accountsLine, parseAccounts } from "@/lib/etfAccounts";

/** 위성 ETF 의 계좌 칸 — 배치가 적어 두고 화면이 안 보였다 (docs/infra.md 25.1381) */
describe("위성 계좌 줄", () => {
  it("가능 여부와 안내를 한 줄로", () => {
    const raw = JSON.stringify({
      accounts: {
        pension: { eligible: true, priority: null, reason: null, note: null },
        irp: { eligible: true, priority: null, reason: null, note: "위험자산 — 주식형 합계 70% 이하" },
        taxable: { eligible: true, priority: null, reason: null, note: null },
      },
    });
    expect(accountsLine(parseAccounts(raw))).toBe("연금저축 가능 · IRP 가능(위험자산 — 주식형 합계 70% 이하) · 일반계좌 가능");
    expect(accountsLine(parseAccounts(JSON.stringify({ criteria: [] })))).toBeNull();
  });

  it("위성 카드가 그 줄을 그린다", () => {
    expect(readFileSync("components/SatelliteList.tsx", "utf8")).toContain("accountsLine(parseAccounts(row.rationale_data))");
  });
});
