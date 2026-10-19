/**
 * 국내 KIS 시세의 기준가로 기업행위 첫날을 알아챈다 (docs/intraday.md 2장, docs/infra.md 25.1551).
 * 감사 재현: 무상 0.4주로 기준가 7,140원·현재가 7,100원, DB 전일 종가 10,000원 — 현재가 비율 0.71 이라 ±31% 안이어서
 * 첫날 가드가 걸리지 않았고, 조정 전 손절선 9,114원으로 거짓 "손절선 터치" 가 나갔다.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { KIS_BASE_ACTION_GAP, actionSuspect } from "@/lib/intraday";

describe("KIS 기준가 첫날 가드 (25.1551)", () => {
  it("기준가가 DB 전일 종가와 2% 넘게 어긋나면 첫날 의심", () => {
    expect(actionSuspect("KR", 7_100, 10_000, 7_140, true)).toBe(true);
    // 예전 길(KIS 가 아님)은 그대로 — 비율 0.71 은 ±31% 안
    expect(actionSuspect("KR", 7_100, 10_000, 7_140, false)).toBe(false);
  });

  it("평소(기준가 = 어제 종가)엔 의심하지 않는다, 미국은 보지 않는다", () => {
    expect(actionSuspect("KR", 9_500, 10_000, 10_000, true)).toBe(false);
    expect(actionSuspect("KR", 9_500, 10_000, 10_000 * (1 - KIS_BASE_ACTION_GAP / 2), true)).toBe(false);
    expect(actionSuspect("US", 95, 100, 90, true)).toBe(false);
  });

  it("장중 경로가 KIS 시세면 기준가 가드를 켠다", () => {
    const route = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf8");
    expect(route).toContain("quote.source === KIS_SOURCE)");
  });
});
