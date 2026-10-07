/** 장기 적립 ETF 계좌별 보기 (docs/etf.md 11.1, docs/infra.md 25.966). 순위는 배치가 적은 그대로 모으기만 한다. */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { EtfRow } from "@/lib/etf";
import { ACCOUNTS, groupByAccount, parseAccounts } from "@/lib/etfAccounts";

function cell(eligible: boolean, priority: number | null, reason: string | null = null) {
  return { eligible, priority, reason, note: null };
}

function row(over: Partial<EtfRow>, accounts: unknown): EtfRow {
  return {
    etf_id: 1, symbol: "X", name: "X", country: "KR", bucket: "해외 주식", passed: 1, excluded_reason: null,
    score: 100, rank_in_category: 1, category_size: 1, rationale_text: "", as_of_date: "2026-10-01",
    category: "S&P 500", family: null, expense_ratio: null, total_assets: 1, inception_date: null,
    turnover_est: null, premium_abs_avg: null,
    rationale_data: accounts === undefined ? JSON.stringify({ criteria: [] }) : JSON.stringify({ accounts }),
    ...over,
  };
}

const 해외 = { pension: cell(true, 1, "연금 1"), irp: cell(true, 1), taxable: cell(true, 3) };
const 국내 = { pension: cell(true, 3), irp: cell(true, 3), taxable: cell(true, 1, "비과세") };
const 미국 = { pension: cell(false, null), irp: cell(false, null), taxable: cell(true, 2) };

describe("groupByAccount", () => {
  const rows = [
    row({ etf_id: 1, symbol: "360750", category: "S&P 500", rank_in_category: 1 }, 해외),
    row({ etf_id: 2, symbol: "379800", category: "S&P 500", rank_in_category: 2 }, 해외),
    row({ etf_id: 3, symbol: "069500", bucket: "국내 주식", category: "코스피 200" }, 국내),
    row({ etf_id: 4, symbol: "VOO", country: "US", bucket: "미국 주식", category: "Large Blend" }, 미국),
  ];

  it("연금저축은 국내 상장 해외 주식이 1순위이고 미국 상장은 뺀다", () => {
    const v = groupByAccount(rows, "pension");
    expect(v.tiers.map((t) => [t.priority, t.bucket])).toEqual([[1, "해외 주식"], [3, "국내 주식"]]);
    expect(v.tiers[0].rows.map((r) => r.symbol)).toEqual(["360750"]); // 같은 지수는 분류 1위만
    expect(v.tiers[0].reason).toBe("연금 1");
    expect(v.ineligible).toBe(1);
  });

  it("일반계좌는 국내 주식 → 미국 상장 → 국내 상장 해외 순", () => {
    const v = groupByAccount(rows, "taxable");
    expect(v.tiers.map((t) => `${t.priority}${t.country}${t.bucket}`)).toEqual(["1KR국내 주식", "2US미국 주식", "3KR해외 주식"]);
  });

  it("계좌 칸이 없는 옛 판정은 세기만 하고 지어내지 않는다", () => {
    const v = groupByAccount([row({}, undefined)], "pension");
    expect(v).toEqual({ tiers: [], ineligible: 0, missing: 1 });
    expect(parseAccounts(JSON.stringify({ accounts: { pension: {} } }))).toBeNull();
    expect(parseAccounts("{")).toBeNull();
  });
});

describe("배치와 맞춤", () => {
  it("계좌 키가 배치와 같다", () => {
    const src = readFileSync(join(process.cwd(), "..", "batch", "services", "etf_accounts.py"), "utf-8");
    expect(src).toContain(`ACCOUNTS = (${ACCOUNTS.map((a) => `"${a.key}"`).join(", ")})`);
  });

  it("장기 적립 탭이 계좌별 보기를 그린다", () => {
    const src = readFileSync(join(process.cwd(), "components", "EtfList.tsx"), "utf-8");
    expect(src).toContain("groupByAccount(picked, account)");
  });
});
