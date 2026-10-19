/**
 * KIS모의투자 대 H 견주기 글 (docs/paper.md 15.7, docs/infra.md 25.1543) — 같은 창, 쉬는 해외 현금 환율 몫을 뺀 KIS 손익.
 * 값은 배치가 내고 화면은 글만 만든다.
 */

import { describe, expect, it } from "vitest";
import { type KisPerf, perfLines } from "@/lib/kisPaper";

const 바탕: KisPerf = {
  start: "2026-10-15", start_total: 1_100_000, last: "2026-10-19", last_total: 1_113_000, pnl_krw: 13_000, return_pct: 1.18, mdd_pct: 0, days: 3,
  h_pnl_krw: 3_000, h_return_pct: 1.0,
};

describe("KIS 견주기 글 (25.1543)", () => {
  it("같은 창 — KIS 는 환율 몫을 뺀 손익", () => {
    const t = perfLines({ ...바탕, h_from: "2026-10-15", h_to: "2026-10-16", kis_pnl_krw: 5_000, kis_cmp_pnl_krw: 3_000, fx_cash_krw: 2_000 });
    expect(t[1]).toBe(
      "견주기 2026-10-15 → 2026-10-16: KIS +3,000원(쉬는 해외 현금 환율 몫 +2,000원 뺌) · 가상모의투자 H +3,000원 (+1.00%) — 같은 주문을 베끼므로 손익 금액으로 견줍니다(끝은 미국 배치까지 끝난 날)",
    );
  });

  it("환율을 모르면 원래 손익에 섞였다고 밝힌다, 창이 없으면 아직 없다", () => {
    expect(perfLines({ ...바탕, h_from: "2026-10-15", h_to: "2026-10-16", kis_pnl_krw: 5_000, kis_cmp_pnl_krw: null, fx_cash_krw: null })[1]).toContain(
      "KIS +5,000원(환율 모름 — 해외 현금 환율 몫이 섞임)",
    );
    expect(perfLines({ ...바탕, h_pnl_krw: null, h_from: null, h_to: null })[1]).toContain("견주기: 아직 없습니다");
  });

  it("25.1543 전 줄은 예전 글", () => {
    expect(perfLines(바탕)[1]).toContain("같은 기간 가상모의투자 H 손익 +3,000원");
  });
});
