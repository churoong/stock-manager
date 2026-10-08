/** "지금 분석" (docs/analysis.md 8장, docs/infra.md 25.1018) — 관심 종목에 넣고 작업을 깨운다. 웹은 계산하지 않는다 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { ANALYZE_EVENT, REQUEST_LOCK_MINUTES, requestAnalysis, requestInFlight, type AnalysisRequest } from "@/lib/analysis";
import { TRIGGER_LABEL, TRIGGER_ORDER } from "@/lib/alerts";
import type { DispatchJob } from "@/lib/dispatch";
import { loadSection } from "@/lib/stockDetail";

const ROOT = join(process.cwd(), "..");

function mem() {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(ROOT, "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(ROOT, "migrations", f), "utf-8"));
  }
  db.exec(`INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at, asset_type) VALUES
    (1, '111111', 'KOSDAQ', 'KR', 'KRW', 't', 't', 'stock'), (2, '069500', 'KOSPI', 'KR', 'KRW', 't', 't', 'etf')`);
  const exec = async (sql: string, args: Array<string | number | null> = []) =>
    db.prepare(sql).all(...args) as Array<Record<string, unknown>>;
  return { db, exec };
}

describe("지금 분석", () => {
  it("워크플로가 이 이벤트를 받고, 종목 번호는 환경변수로만 넘긴다", () => {
    const yml = readFileSync(join(ROOT, ".github", "workflows", "analyze-stock.yml"), "utf-8");
    expect(yml).toContain(`types: [${ANALYZE_EVENT}]`);
    expect(yml).toContain("STOCK_ID: ${{ github.event.client_payload.stock_id || inputs.stock_id }}");
    expect(yml).not.toMatch(/run:[^\n]*\$\{\{\s*github\.event\.client_payload/);
  });

  it("관심 종목에 넣고 깨운다 — 이미 있는 관심 설정은 건드리지 않고, 도는 중이면 다시 깨우지 않는다", async () => {
    const { db, exec } = mem();
    db.exec(`INSERT INTO watchlist (stock_id, added_at, target_buy_price, alert_enabled) VALUES (1, 'x', 1234, 0)`);
    const 깨운: DispatchJob[] = [];
    const dispatch = async (job: DispatchJob) => (깨운.push(job), { dispatched: true });
    const now = new Date("2026-10-08T03:00:00Z");
    expect(await requestAnalysis(exec, 1, now, dispatch)).toEqual({ ok: true, state: "dispatched" });
    expect(깨운.map((j) => [j.event, j.payload])).toEqual([[ANALYZE_EVENT, { stock_id: "1" }]]);
    expect(db.prepare("SELECT target_buy_price, alert_enabled FROM watchlist WHERE stock_id = 1").get()).toEqual({ target_buy_price: 1234, alert_enabled: 0 });
    expect(db.prepare("SELECT status FROM analysis_requests").get()).toEqual({ status: "requested" });
    // 5분 뒤 다시 누름 → 깨우지 않는다
    expect(await requestAnalysis(exec, 1, new Date(now.getTime() + 5 * 60_000), dispatch)).toEqual({ ok: true, state: "in_flight" });
    expect(깨운).toHaveLength(1);
    // 잠금 시간을 넘기면 멈춘 것으로 보고 다시 깨운다
    expect((await requestAnalysis(exec, 1, new Date(now.getTime() + REQUEST_LOCK_MINUTES * 60_000), dispatch)).ok).toBe(true);
    expect(깨운).toHaveLength(2);
  });

  it("관심에 없던 종목은 넣고, ETF·없는 종목은 거절한다", async () => {
    const { db, exec } = mem();
    const dispatch = async () => ({ dispatched: true });
    await requestAnalysis(exec, 1, new Date(), dispatch);
    expect(db.prepare("SELECT alert_enabled FROM watchlist WHERE stock_id = 1").get()).toEqual({ alert_enabled: 1 });
    expect(await requestAnalysis(exec, 2, new Date(), dispatch)).toMatchObject({ ok: false, status: 400 });
    expect(await requestAnalysis(exec, 99, new Date(), dispatch)).toMatchObject({ ok: false, status: 400 });
  });

  it("깨우지 못하면 요청을 실패로 닫는다 — 관심 종목에는 남는다", async () => {
    const { db, exec } = mem();
    const out = await requestAnalysis(exec, 1, new Date(), async () => ({ dispatched: false, reason: "토큰 없음" }));
    expect(out).toMatchObject({ ok: false, status: 502 });
    expect(db.prepare("SELECT status, note FROM analysis_requests").get()).toEqual({ status: "failed", note: "토큰 없음" });
    expect(db.prepare("SELECT COUNT(*) AS n FROM watchlist").get()).toEqual({ n: 1 });
    // 실패한 요청은 잠그지 않는다
    expect(requestInFlight(db.prepare("SELECT * FROM analysis_requests").get() as unknown as AnalysisRequest, new Date())).toBe(false);
  });

  it("상세 화면 의견 섹션은 요청 진행을 함께 준다(의견이 없어도)", async () => {
    const { db, exec } = mem();
    db.exec(`INSERT INTO analysis_requests VALUES (1, '2026-10-08T03:00:00Z', 'running', NULL, NULL)`);
    const s = await loadSection(exec, "verdict", 1);
    expect(s.empty).toBe(true);
    expect((s as unknown as { request: { status: string } }).request.status).toBe("running");
  });

  it("알림 트리거 이름이 배치와 같다", () => {
    const py = readFileSync(join(ROOT, "batch", "jobs", "analyze_extra.py"), "utf-8");
    expect(py).toContain("'analysis'");
    expect(TRIGGER_LABEL.analysis).toBe("분석 완료");
    expect(TRIGGER_ORDER).toContain("analysis");
  });
});
