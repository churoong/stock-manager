/** 텔레그램 질의응답 (docs/telegram-qa.md, docs/infra.md 25.1004) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResultSet, SqlValue } from "@/lib/db";
import { answer, parseQuery, type Exec } from "@/lib/telegramQa";

const ROOT = join(process.cwd(), "..");

function memExec(): { db: DatabaseSync; exec: Exec } {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(join(ROOT, "migrations")).filter((x) => x.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(ROOT, "migrations", f), "utf-8"));
  }
  const exec: Exec = async (sql: string, args: SqlValue[] = []) => {
    const st = db.prepare(sql);
    const rows = st.all(...(args as Array<string | number | null>)) as Array<Record<string, unknown>>;
    const columns = st.columns().map((c) => c.name);
    return { columns, rows: rows.map((r) => columns.map((c) => r[c])), affectedRows: 0 } satisfies ResultSet;
  };
  return { db, exec };
}

describe("질문 해석", () => {
  it("명령과 종목 이름을 가른다", () => {
    expect(parseQuery("/start")).toEqual({ kind: "help" });
    expect(parseQuery("/보유@mybot")).toEqual({ kind: "holdings" });
    expect(parseQuery("알림")).toEqual({ kind: "alerts" });
    expect(parseQuery(" 삼성전자 ")).toEqual({ kind: "stock", q: "삼성전자" });
    expect(parseQuery("/종목 AAPL")).toEqual({ kind: "stock", q: "AAPL" });
    expect(parseQuery("/모르는명령")).toEqual({ kind: "help" });
    expect(parseQuery("가".repeat(31))).toEqual({ kind: "help" });
    expect(parseQuery("   ")).toBeNull();
  });
});

describe("답 — 실제 스키마", () => {
  it("종목 하나면 점수·신호·보유·플래그를 DB 값 그대로", async () => {
    const { db, exec } = memExec();
    db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
             VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't'),
                    (2, '005935', 'KOSPI', 'KR', '삼성전자우', 'KRW', 'active', 't', 't')`);
    db.exec(`INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)
             VALUES (1, '2026-10-06', 1, 1, 1, 61000, 1, 'KRW', 't', 't')`);
    db.exec(`INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used, weights_json,
             rank_in_market, calc_version, created_at)
             VALUES (1, '2026-10-06', 72.5, '{"value":60,"quality":80,"growth":null,"momentum":70,"risk":75}', 0, '{}', 12, 1, 't')`);
    db.exec(`INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,
             unrealized_pnl_krw, updated_at) VALUES (1, 10, 'KRW', 55000, 1, 550000, 550000, '2026-01-02', 55000, 't')`);
    const text = await answer({ kind: "stock", q: "삼성전자" }, exec);
    expect(text).toContain("삼성전자 (005930)");
    expect(text).toContain("종가 61,000 (2026-10-06)");
    expect(text).toContain("종합 72.5점 · 시장 12위 (2026-10-06)");
    expect(text).toContain("밸류 60 · 퀄리티 80 · 성장 - · 모멘텀 70 · 리스크 75");
    expect(text).toContain("매수 신호: 최근 없음");
    expect(text).toContain("보유 10주 · 평균 55,000 · 평가손익률 10%");
  });

  it("여럿이 맞으면 고르게 하고, 없으면 그렇게 말한다", async () => {
    const { db, exec } = memExec();
    db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
             VALUES (1, '000001', 'KOSPI', 'KR', '한국전력기술', 'KRW', 'active', 't', 't'),
                    (2, '000002', 'KOSPI', 'KR', '한국전력공사', 'KRW', 'active', 't', 't')`);
    expect(await answer({ kind: "stock", q: "한국전력" }, exec)).toContain("여럿이 맞습니다");
    expect(await answer({ kind: "stock", q: "없는회사" }, exec)).toContain("맞는 종목이 없습니다");
    expect(await answer({ kind: "holdings" }, exec)).toBe("보유 종목이 없습니다");
    expect(await answer({ kind: "alerts" }, exec)).toBe("장중 알림 기록이 없습니다");
  });
});

describe("웹훅 — 비밀 머리글과 본인 대화방", () => {
  const sent: string[] = [];
  beforeEach(() => {
    vi.resetModules();
    sent.length = 0;
    vi.doMock("@/lib/telegram", () => ({ sendTelegram: async (t: string) => void sent.push(t) }));
    vi.doMock("@/lib/db", () => ({ execute: async () => ({ columns: [], rows: [], affectedRows: 0 }) }));
    vi.stubEnv("TELEGRAM_WEBHOOK_SECRET", "s".repeat(20));
    vi.stubEnv("TELEGRAM_CHAT_ID", "42");
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.doUnmock("@/lib/telegram");
    vi.doUnmock("@/lib/db");
  });

  function req(secret: string | null, chat: number, text = "/보유", type = "private") {
    return new Request("http://x/api/telegram/webhook", {
      method: "POST",
      headers: secret ? { "x-telegram-bot-api-secret-token": secret } : {},
      body: JSON.stringify({ message: { text, chat: { id: chat, type } } }),
    });
  }

  it("비밀값이 틀리면 401, 다른 대화방이면 답하지 않는다, 본인이면 답한다", async () => {
    const { POST } = await import("@/app/api/telegram/webhook/route");
    expect((await POST(req("wrong", 42))).status).toBe(401);
    expect((await POST(req(null, 42))).status).toBe(401);
    expect((await POST(req("s".repeat(20), 7))).status).toBe(200);
    expect(sent).toEqual([]);
    expect((await POST(req("s".repeat(20), 42))).status).toBe(200);
    expect(sent).toEqual(["보유 종목이 없습니다"]);
  });

  it("본인 대화방 번호여도 단체방이면 답하지 않는다 (25.1007)", async () => {
    const { POST } = await import("@/app/api/telegram/webhook/route");
    expect((await POST(req("s".repeat(20), 42, "/보유", "group"))).status).toBe(200);
    expect(sent).toEqual([]);
  });

  it("미리보기 배포에서는 켜지 않는다 (25.1007)", async () => {
    vi.stubEnv("TELEGRAM_BOT_TOKEN", "t");
    vi.stubEnv("VERCEL_ENV", "preview");
    const { POST } = await import("@/app/api/telegram/webhook-setup/route");
    const r = await POST(new Request("https://preview.example/api/telegram/webhook-setup", { method: "POST", body: JSON.stringify({ action: "on" }) }));
    expect(r.status).toBe(400);
    expect((await r.json()).error).toContain("운영 배포에서만");
  });

  it("비밀값 환경변수가 없으면 경로가 없는 것처럼 404", async () => {
    vi.stubEnv("TELEGRAM_WEBHOOK_SECRET", "");
    const { POST } = await import("@/app/api/telegram/webhook/route");
    expect((await POST(req("", 42))).status).toBe(404);
  });
});
