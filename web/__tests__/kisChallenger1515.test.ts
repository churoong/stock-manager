/**
 * KIS모의투자 도전자 고르기 (docs/paper.md 15.10, docs/infra.md 25.1515).
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { ACCOUNTS_SQL, CHALLENGER_KEYS, CHALLENGER_READ, CHALLENGER_UPSERT, CHAMPION, SEPARATE_IDS, challengerOf, challengerSchema } from "@/lib/kisChallenger";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
const py = readFileSync(join(process.cwd(), "..", "batch", "jobs", "broker_mirror.py"), "utf8");

describe("도전자 설정", () => {
  it("배치와 같은 이름 — 증권사 칸·챔피언·설정 키", () => {
    expect(py).toContain(`CHALLENGER = Slot("${CHALLENGER_KEYS.KR}", "${CHALLENGER_KEYS.US}", kv.CHALLENGER`);
    expect(/^MIRROR_ACCOUNT = "([A-Z0-9]+)"/m.exec(py)?.[1]).toBe(CHAMPION);
    expect(py).toContain('db.get_setting(client, "kis_challenger", None)');
  });

  it("고르기 꼴 — 챔피언·이상한 id 는 막고 null 은 끄기", () => {
    expect(challengerSchema.safeParse({ account: "C" }).success).toBe(true);
    expect(challengerSchema.safeParse({ account: "R1" }).success).toBe(true);
    expect(challengerSchema.safeParse({ account: null }).success).toBe(true);
    expect(challengerSchema.safeParse({ account: CHAMPION }).success).toBe(false);
    expect(challengerSchema.safeParse({ account: "c; DROP" }).success).toBe(false);
    expect(challengerSchema.safeParse({}).success).toBe(false);
  });

  it("따로 재는 실험 계좌(단타·스윙)는 고를 수 없다 — 정의처와 같은 목록(25.1879)", () => {
    const 정의 = readFileSync(join(process.cwd(), "..", "batch", "services", "paper.py"), "utf8");
    const 목록 = /^SEPARATE_IDS = \(([^)]*)\)/m.exec(정의)?.[1] ?? "";
    expect([...목록.matchAll(/"([A-Z0-9]+)"/g)].map((m) => m[1])).toEqual([...SEPARATE_IDS]);
    for (const a of SEPARATE_IDS) expect(challengerSchema.safeParse({ account: a }).success).toBe(false);
    const route = readFileSync(join(process.cwd(), "app", "api", "kis", "challenger", "route.ts"), "utf8");
    expect(route).toContain("!SEPARATE_IDS.includes(a.id)"); // 고르기 목록에서도 뺀다
  });

  it("쓰고 읽기 — 배치가 읽는 {account} 꼴", () => {
    const db = new DatabaseSync(":memory:");
    for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
    expect(db.prepare(CHALLENGER_READ).all()).toEqual([]);
    db.prepare(CHALLENGER_UPSERT).run(JSON.stringify({ account: "C", at: "t" }), "t");
    db.prepare(CHALLENGER_UPSERT).run(JSON.stringify({ account: "D", at: "t2" }), "t2");
    const v = (db.prepare(CHALLENGER_READ).all() as Array<{ value: string }>)[0].value;
    expect(challengerOf(v)).toBe("D");
    expect(challengerOf(JSON.stringify({ account: null }))).toBeNull();
    expect(challengerOf("{")).toBeNull();
    expect(challengerOf(null)).toBeNull();
    expect((db.prepare(ACCOUNTS_SQL).all() as Array<{ id: string }>).some((a) => a.id === CHAMPION)).toBe(true);
  });
});
