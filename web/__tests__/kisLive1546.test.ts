/**
 * KIS모의투자 "지금 조회" — 잔고는 받았는데 곁 조회(미국 해외 현금·총자산, 오늘 체결)만 실패해도 받은 보유는 보인다
 * (docs/paper.md 15.7, docs/infra.md 25.1546). 화면 안내는 시장마다.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { liveRequests, parseUsLive } from "@/lib/kisVts";

const 잔고 = { rt_cd: "0", output1: [{ ovrs_pdno: "AAPL", ovrs_item_name: "애플", ovrs_cblc_qty: "3", pchs_avg_pric: "200", now_pric2: "210", ovrs_stck_evlu_amt: "630", frcr_evlu_pfls_amt: "30", evlu_pfls_rt: "5" }] };

describe("곁 조회 실패를 견딘다 (25.1546)", () => {
  it("잔고는 꼭 받아야 하고, 곁 조회만 optional", () => {
    const us = liveRequests("US", { cano: "12345678", prdt: "01" }, "2026-10-19");
    expect(us.map((q) => q.optional ?? null)).toEqual([null, null, null, "해외 현금·총자산", "오늘 체결"]);
    const kr = liveRequests("KR", { cano: "12345678", prdt: "01" }, "2026-10-19");
    expect(kr.map((q) => q.optional ?? null)).toEqual([null, "오늘 체결"]);
  });

  it("체결기준현재잔고·체결이 없어도 보유는 그대로, 해외 현금·총자산은 모름", () => {
    const v = parseUsLive([잔고, { rt_cd: "0", output1: [] }, { rt_cd: "0", output1: [] }], null, null, "t");
    expect(v.holdings.map((h) => [h.code, h.qty, h.value])).toEqual([["AAPL", 3, 630]]);
    expect(v.account.cash_krw).toBeNull();
    expect(v.account.assets_krw).toBeNull();
    expect(v.executions).toEqual([]);
  });

  it("경로는 optional 실패를 빠짐으로 적고 계속, 화면 안내는 시장별 상태", () => {
    const route = readFileSync(join(process.cwd(), "app", "api", "paper", "kis", "live", "route.ts"), "utf8");
    expect(route).toContain("if (코드 && q.optional)");
    expect(route).toContain("missing: 빠짐");
    const panel = readFileSync(join(process.cwd(), "components", "KisPanel.tsx"), "utf8");
    expect(panel).toContain("liveNote[market]");
  });
});
