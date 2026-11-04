/**
 * 감시 목록이 묵으면 기업행위 첫날 가드가 prices 최신 종가와 KIS 기준가를 견준다 (docs/infra.md 25.2135, 11-04 결함 회차 [권고]).
 * 감사 재현: 목록 prev_close 10,000(3거래일 전), 어제 종가 9,600 = KIS 기준가, 현재가 9,200, 보유 손절 9,300 —
 * |9,600/10,000 − 1| = 4% > 2% 라 첫날 의심으로 손절을 지웠다. 묵은 날수만큼 날마다 빠졌다.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { actionBaseClose, actionSuspect } from "@/lib/intraday";

describe("묵은 목록의 기준가 가드 (25.2135)", () => {
  it("묵은 목록이면 prices 최신 종가로 견줘 진짜 손절을 지우지 않는다", () => {
    expect(actionSuspect("KR", 9_200, 10_000, 9_600, true)).toBe(true); // 예전 — 목록 값으로 견줌
    expect(actionSuspect("KR", 9_200, actionBaseClose(10_000, 9_600, false), 9_600, true)).toBe(false);
  });

  it("묵은 목록에서도 진짜 권리락 첫날은 여전히 잡는다", () => {
    // 어제 종가 10,000(최신), 기준가 7,140 — 무상증자 첫날
    expect(actionSuspect("KR", 7_100, actionBaseClose(10_500, 10_000, false), 7_140, true)).toBe(true);
  });

  it("이번 장 목록이거나 최신 종가를 모르면 목록 값 그대로", () => {
    expect(actionBaseClose(10_000, 9_600, true)).toBe(10_000);
    expect(actionBaseClose(10_000, undefined, false)).toBe(10_000);
    expect(actionBaseClose(10_000, 0, false)).toBe(10_000);
    expect(actionBaseClose(null, null, false)).toBeNull();
  });

  it("장중 경로가 묵은 날만 최신 종가를 읽어 가드에 넘긴다", () => {
    const route = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf8");
    expect(route).toContain("if (!이번장 && market === \"KR\" && targets.length)");
    expect(route).toContain("actionBaseClose(target.prev_close, 최신종가.get(target.stock_id), 이번장)");
  });
});
