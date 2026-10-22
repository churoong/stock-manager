/**
 * 25.1642 — 도전자를 바꾸면 같은 증권사 키에 앞 계좌 줄이 남아 실행 품질 선이 두 계좌를 이었다. 지금 계좌 줄만 남긴다.
 */

import { describe, expect, it } from "vitest";
import { currentAccountRows, type QualityRow } from "@/lib/kisQuality";

const 줄 = (
  as_of: string,
  account_id: string | null,
  match_pct: number,
): QualityRow => ({
  as_of,
  account_id,
  n: 25,
  match_pct,
  kept_pct: 90,
  slip_buy: null,
  slip_sell: null,
  rejected: 0,
});

describe("지금 베끼는 계좌의 줄만", () => {
  it("도전자를 E 에서 D 로 바꾸면 D 줄만", () => {
    const rows = [
      줄("2026-10-01", "E", 70),
      줄("2026-10-02", "E", 71),
      줄("2026-10-05", "D", 95),
      줄("2026-10-06", "D", 96),
    ];
    expect(
      currentAccountRows(rows).map((r) => [r.as_of, r.account_id]),
    ).toEqual([
      ["2026-10-05", "D"],
      ["2026-10-06", "D"],
    ]);
  });
  it("E→D→E 로 되돌리면 마지막 E 구간만 — D 를 가로지르지 않는다", () => {
    const rows = [
      줄("2026-10-01", "E", 70),
      줄("2026-10-05", "D", 95),
      줄("2026-10-09", "E", 72),
      줄("2026-10-10", "E", 73),
    ];
    expect(currentAccountRows(rows).map((r) => r.as_of)).toEqual([
      "2026-10-09",
      "2026-10-10",
    ]);
  });
  it("한 계좌뿐이면 그대로 · 빈 목록 · 계좌 없는 옛 줄은 거르지 않는다", () => {
    const rows = [줄("2026-10-01", "H", 80), 줄("2026-10-02", "H", 81)];
    expect(currentAccountRows(rows)).toEqual(rows);
    expect(currentAccountRows([])).toEqual([]);
    const 옛 = [줄("2026-10-01", "H", 80), 줄("2026-10-02", null, 81)];
    expect(currentAccountRows(옛)).toEqual(옛);
  });
});
