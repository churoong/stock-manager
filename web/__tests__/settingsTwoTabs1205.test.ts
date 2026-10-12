/**
 * 바뀐 칸만 쓰는 저장(25.1115)의 빈틈 (docs/infra.md 25.1205, 설정 감사).
 * ① 두 탭이 서로 다른 상한을 고쳐 "종목 20 · 섹터 15" 가 둘 다 저장됐다 ② 행이 없는 칸이 저장할 때마다 "바뀜"(목표 지연·재계산)
 * ③ 옛 모양 행을 "다른 곳에서 바꿈" 으로 읽었다
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const 저장된 = new Map<string, string>();
const 쓴것: Array<{ sql: string; args: unknown[] }> = [];
vi.mock("@/lib/db", () => ({
  execute: vi.fn(async () => ({ columns: ["key", "value"], rows: [...저장된.entries()].map(([k, v]) => [k, v]) })),
  rowsToObjects: (rs: { columns: string[]; rows: unknown[][] }) =>
    rs.rows.map((r) => Object.fromEntries(rs.columns.map((c, i) => [c, r[i]]))),
  batch: vi.fn(async (stmts: Array<{ sql: string; args: unknown[] }>) => {
    for (const s of stmts) {
      쓴것.push(s);
      저장된.set(String(s.args[0]), String(s.args[1]));
    }
    return [];
  }),
}));
const 깨움 = vi.fn(async () => ({ dispatched: true }));
vi.mock("@/lib/portfolio", async (orig) => ({ ...(await orig<typeof import("@/lib/portfolio")>()), requestRecalc: () => 깨움() }));

async function 저장(값: unknown): Promise<{ status: number; body: Record<string, unknown> }> {
  const { PUT } = await import("@/app/api/settings/route");
  const res = await PUT(new Request("https://x/api/settings", { method: "PUT", body: JSON.stringify(값) }) as never);
  return { status: res.status, body: (await res.json()) as Record<string, unknown> };
}

describe("설정 저장 — 쓰고 난 DB 의 모습으로 본다 (25.1205)", () => {
  beforeEach(() => {
    저장된.clear();
    쓴것.length = 0;
    깨움.mockClear();
  });

  it("다른 탭이 종목 상한을 올린 뒤 옛 탭이 섹터 상한을 내리면 막는다", async () => {
    const { DEFAULT_SETTINGS } = await import("@/lib/settings");
    const 처음 = { ...DEFAULT_SETTINGS, max_weight_per_stock: 10, max_weight_per_sector: 30 };
    // 탭 B: 종목 20 저장
    expect((await 저장({ ...처음, max_weight_per_stock: 20, _base: 처음 })).status).toBe(200);
    // 옛 탭 A: 섹터 15 저장 — 이 탭 화면에선 종목 10 이라 통과했었다
    const a = await 저장({ ...처음, max_weight_per_sector: 15, _base: 처음 });
    expect(a.status).toBe(409);
    expect(String((a.body.errors as string[])[0])).toContain("종목 비중 상한이 섹터 비중 상한보다 클 수 없습니다");
    expect(저장된.get("max_weight_per_sector")).toBeUndefined();
  });

  it("행이 없는 칸은 기본값과 견준다 — 관계없는 칸 저장이 목표 변경·재계산을 부르지 않는다", async () => {
    const { DEFAULT_SETTINGS } = await import("@/lib/settings");
    const base = { ...DEFAULT_SETTINGS };
    const 바꿈 = { ...base, quiet_hours: { ...base.quiet_hours, end: base.quiet_hours.end === "07:00" ? "08:00" : "07:00" } };
    const r = await 저장({ ...바꿈, _base: base });
    expect(r.status).toBe(200);
    expect(r.body.saved_keys).toEqual(["quiet_hours"]);
    expect(r.body.targets_changed).toBe(false);
    expect(깨움).not.toHaveBeenCalled();
  });

  it("목표를 실제로 바꾸면 그대로 알린다", async () => {
    const { DEFAULT_SETTINGS } = await import("@/lib/settings");
    const base = { ...DEFAULT_SETTINGS };
    const t = structuredClone(base.horizon_targets);
    t.short.target_pct = t.short.target_pct + 1;
    const r = await 저장({ ...base, horizon_targets: t, _base: base });
    expect(r.body.targets_changed).toBe(true);
    expect(깨움).toHaveBeenCalledTimes(1);
  });
});
