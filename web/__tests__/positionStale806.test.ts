/**
 * 종목 상세의 보유가 재계산 전인지 (docs/infra.md 25.806, 종목 상세 감사).
 * 매매를 넣고 1~2분 동안 "내 보유" 가 옛 값을 지금 값처럼 보였고, 평균 단가 선도 옛 값으로 그렸다.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { overlayNote, positionStale } from "@/lib/stockDetail";

const 가격 = (v: unknown) => String(v);

describe("보유 재계산 전 판정", () => {
  it("요약의 지문과 지금 지문이 다르면 늦은 것", () => {
    expect(positionStale([{ trades_version: "t3:a|d0:" }], [{ version: "t4:b|d0:" }])).toBe(true);
    expect(positionStale([{ trades_version: "t4:b|d0:" }], [{ version: "t4:b|d0:" }])).toBe(false);
  });

  it("요약이 아직 없으면 기록이 있을 때만 늦은 것 — 배치와 같은 지문 모양", () => {
    expect(positionStale([], [{ version: "t0:|d0:" }])).toBe(false);
    expect(positionStale([], [{ version: "t1:x|d0:" }])).toBe(true);
    // batch/jobs/portfolio.trades_version 과 모양이 같다
    const 배치 = readFileSync("../batch/jobs/portfolio.py", "utf-8");
    expect(배치).toContain('f"t{t[0]}:{t[1]}|d{d[0]}:{d[1]}"');
  });

  it("못 읽으면 모른다(null) — 늦었다고도 아니라고도 하지 않는다", () => {
    expect(positionStale(null, [{ version: "t1:x|d0:" }])).toBeNull();
    expect(positionStale([{ trades_version: "x" }], null)).toBeNull();
  });
});

describe("겹쳐 그린 선 문장", () => {
  it("보유를 못 읽었으면 '보유하지 않은 종목' 이라 하지 않는다", () => {
    const 글 = overlayNote([], "ok", "2026-09-25", null, "KRW", 가격, "unread");
    expect(글).not.toContain("보유하지 않은");
    expect(글).toContain("읽지 못해");
  });

  it("재계산 전이면 그 사실을 말한다 — 선이 있을 때도", () => {
    expect(overlayNote([], "ok", "2026-09-25", null, "KRW", 가격, "stale")).toContain("다시 계산하는 중");
    expect(overlayNote([{ price: 100, label: "목표", kind: "target" }], "ok", null, null, "KRW", 가격, "stale"))
      .toContain("평균 단가 선을 그리지 않았습니다");
  });

  it("화면은 재계산 전·못 읽은 보유로 평균 단가 선을 그리지 않는다", () => {
    const 화면 = readFileSync("components/StockDetail.tsx", "utf-8");
    expect(화면).toContain('position={보유상태 === "ok" ? position : null}');
    expect(화면).toContain("stale={positionStale === true}");
  });
});

describe("재계산이 멈췄을 때 (25.809, 교차검증)", () => {
  it("마지막 재계산이 실패했으면 '1~2분' 대신 그 까닭을 싣는다", async () => {
    const { DatabaseSync } = await import("node:sqlite");
    const { readdirSync } = await import("node:fs");
    const { join } = await import("node:path");
    const { loadOverview } = await import("@/lib/stockDetail");
    const 폴더 = join(process.cwd(), "..", "migrations");
    const db = new DatabaseSync(":memory:");
    for (const f of readdirSync(폴더).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(폴더, f), "utf-8"));
    db.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at)
      VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't');
      INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (1, 'buy', '2026-09-30', 60000, 10, 'KRW', 1, 'krw', '2026-09-30T09:00:00.000Z', '2026-09-30T09:00:00.000Z');
      INSERT INTO batch_runs (job_name, market, status, started_at, error_text)
      VALUES ('portfolio', NULL, 'failed', '2026-09-30T09:01:00.000Z', '환율 0행');
    `);
    const exec = async (sql: string, args: Array<string | number | null> = []) => db.prepare(sql).all(...args) as Record<string, unknown>[];
    const o = await loadOverview(exec, 1);
    expect(o?.position_stale).toBe(true);
    expect(o?.position_stuck).toContain("재계산이 실패했습니다: 환율 0행");
    const 화면 = readFileSync("components/StockDetail.tsx", "utf-8");
    expect(화면).toContain('{positionStuck ?? "매매 기록이 바뀌어');
  });
});
