import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DESK_ACCOUNTS, pendingAccounts, pendingText, type DeskLeaderboard } from "@/lib/desk";

// 25.2175 — 순위표(배치 운용 요약)는 평가 기록이 있는 계좌만 담아, 새로 연 계좌(국내ETF·미국ETF)는 첫 평가 전까지 운용 화면에
// 아예 안 보였다(2026-11-06 사용자 "안보이잖아"). 계좌 표를 함께 읽어 순위표 밖 계좌를 아래에 붙인다
describe("순위표 밖 계좌", () => {
  const lb = { window_start: "2026-10-16", rows: [{ id: "A" }, { id: "S" }] } as unknown as DeskLeaderboard;
  const 계좌 = [
    { id: "A", created_at: "2026-10-15T00:00:00+00:00" },
    { id: "S", created_at: "2026-10-29T00:00:00+00:00" },
    { id: "X", created_at: "2026-11-09T00:00:00+00:00" },
    { id: "Y", created_at: "2026-11-09T00:00:00+00:00" },
  ];

  it("순위표에 없는 계좌만, 개설 전이면 그 날짜부터", () => {
    const 밖 = pendingAccounts(lb, 계좌, "2026-11-06");
    expect(밖).toEqual([
      { id: "X", opens: "2026-11-09", before: true },
      { id: "Y", opens: "2026-11-09", before: true },
    ]);
    expect(pendingText(밖[0])).toBe("개설 전 · 2026-11-09부터");
  });

  it("개설일이 지났는데 아직 평가 전이면 첫 평가 전", () => {
    const [x] = pendingAccounts(lb, 계좌, "2026-11-09");
    expect(x.before).toBe(false);
    expect(pendingText(x)).toBe("첫 평가 전 · 2026-11-09 개설");
    expect(pendingText({ id: "Z", opens: null, before: false })).toBe("첫 평가 전");
  });

  it("순위표가 없으면 모든 계좌, 모두 있으면 빈 목록", () => {
    expect(pendingAccounts(null, 계좌, "2026-11-06").map((p) => p.id)).toEqual(["A", "S", "X", "Y"]);
    const 다 = { window_start: null, rows: 계좌.map((a) => ({ id: a.id })) } as unknown as DeskLeaderboard;
    expect(pendingAccounts(다, 계좌, "2026-11-06")).toEqual([]);
  });

  it("운용 경로가 계좌 표를 읽는다 — 표가 없을 때만 빈 목록(다른 실패는 삼키지 않음)", () => {
    expect(DESK_ACCOUNTS).toBe("SELECT id, created_at FROM paper_accounts ORDER BY id");
    const route = readFileSync(join(process.cwd(), "app", "api", "desk", "route.ts"), "utf8");
    expect(route).toContain("execute(DESK_ACCOUNTS)");
    expect(route).toContain("execute(DESK_ACCOUNTS).catch(ifMissingTable(null))");
    const view = readFileSync(join(process.cwd(), "components", "DeskView.tsx"), "utf8");
    expect(view).toContain("pendingAccounts(lb, accounts, kstToday())");
  });
});
