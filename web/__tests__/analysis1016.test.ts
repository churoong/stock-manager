/** 종목 분석 (docs/analysis.md, docs/infra.md 25.1016) — 화면은 배치 값을 읽기만 한다 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { HUB_LIMIT, HUB_SQL, VERDICT_LABEL, groupHub, type HubRow } from "@/lib/analysis";
import { loadSection, type Exec } from "@/lib/stockDetail";

const ROOT = join(process.cwd(), "..");

function memExec(): { db: DatabaseSync; exec: Exec } {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(ROOT, "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(ROOT, "migrations", f), "utf-8"));
  }
  const exec: Exec = async (sql, args = []) => db.prepare(sql).all(...(args as Array<string | number | null>)) as Array<Record<string, unknown>>;
  return { db, exec };
}

describe("종목 분석", () => {
  it("결론 이름이 배치와 같다", () => {
    const py = readFileSync(join(ROOT, "batch", "services", "verdict.py"), "utf-8");
    for (const [key, label] of Object.entries(VERDICT_LABEL)) expect(py).toContain(`"${key}": "${label}"`);
  });

  it("상세 화면 의견 섹션은 저장된 행을 읽고, 없으면 까닭을 말한다", async () => {
    const { db, exec } = memExec();
    db.exec(`INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at) VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 't', 't')`);
    const 없음 = await loadSection(exec, "verdict", 1);
    expect(없음.empty).toBe(true);
    db.exec(`INSERT INTO stock_verdicts VALUES (1, 'KR', 'waiting', '신호 대기 — 중기 신호까지 기준 1개 남음', '{"reasons":["r"],"against":[],"nearest":null}', '[{"label":"종합 점수","display":"72.5","threshold":"—","source":"scores","as_of":"2026-10-07"}]', '2026-10-07', '2026-10-07', '2026-10-08T00:00:00Z')`);
    const 있음 = await loadSection(exec, "verdict", 1) as unknown as { verdict: { detail: { reasons: string[] }; evidence: unknown[] } };
    expect(있음.verdict.detail.reasons).toEqual(["r"]);
    expect(있음.verdict.evidence).toHaveLength(1);
  });

  it("모아보기는 사람이 볼 결론만, 정해진 순서로", async () => {
    const { db, exec } = memExec();
    db.exec(`INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at) VALUES
      (1, 'A', 'KOSPI', 'KR', 'KRW', 't', 't'), (2, 'B', 'KOSPI', 'KR', 'KRW', 't', 't'), (3, 'C', 'KOSPI', 'KR', 'KRW', 't', 't')`);
    for (const [id, v] of [[1, "hold"], [2, "check_holding"], [3, "waiting"]] as const) {
      db.prepare(`INSERT INTO stock_verdicts VALUES (?, 'KR', ?, ?, '{}', '[]', NULL, NULL, 't')`).run(id, v, `${v} — x`);
    }
    const rows = (await exec(HUB_SQL)) as unknown as HubRow[];
    const groups = groupHub(rows);
    expect(groups.map((g) => g.key)).toEqual(["check_holding", "hold"]);
    expect(HUB_LIMIT).toBeGreaterThan(0);
  });
});
