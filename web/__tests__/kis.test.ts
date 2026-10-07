import { readFileSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 한국투자증권 KIS 국내 현재가 (web/lib/kis.ts, docs/infra.md 25.983).
 *
 * 토큰을 **하루 한 번만** 받는지(5분 크론마다 받으면 발급 1분 1회 제한·알림에 걸린다), 못 받은 종목을 야후로 넘기는지,
 * 응답을 야후와 같은 모양으로 옮기는지를 본다. 토큰 표는 `migrations/0045_api_tokens.sql` 을 메모리 DB 에 그대로 올린다.
 */

const 상태 = vi.hoisted(() => ({ db: null as InstanceType<typeof import("node:sqlite").DatabaseSync> | null }));

vi.mock("@/lib/db", () => {
  const run = (sql: string, args: unknown[] = []) => {
    const 문장 = 상태.db!.prepare(sql);
    if (/^\s*SELECT/i.test(sql)) {
      const 객체들 = 문장.all(...(args as never[])) as Record<string, unknown>[];
      const columns = 객체들.length ? Object.keys(객체들[0]) : [];
      return { columns, rows: 객체들.map((o) => columns.map((c) => o[c])), affectedRows: 0 };
    }
    return { columns: [], rows: [], affectedRows: Number(문장.run(...(args as never[])).changes) };
  };
  return {
    execute: async (sql: string, args: unknown[] = []) => run(sql, args),
    batch: async (stmts: Array<{ sql: string; args?: unknown[] }>) => stmts.map((s) => run(s.sql, s.args ?? [])),
    rowsToObjects: (rs: { columns: string[]; rows: unknown[][] }) =>
      rs.rows.map((row) => Object.fromEntries(rs.columns.map((c, i) => [c, row[i]]))),
  };
});

const { fetchKisQuotes, kisCode, parseKisPrice, tokenFresh, KIS_SOURCE, KIS_PER_SECOND } = await import("@/lib/kis");

const 지금 = new Date("2026-10-07T05:39:00Z");
const 정상 = (prpr: string) => ({
  rt_cd: "0",
  msg_cd: "MCA00000",
  output: { stck_prpr: prpr, prdy_vrss: "-1000", stck_sdpr: "272000", stck_hgpr: "273500", stck_lwpr: "269500", acml_vol: "12980705" },
});

let 토큰발급 = 0;
let 시세호출: string[] = [];

function 가짜fetch(시세: (code: string) => { status: number; body: unknown }) {
  return vi.fn(async (url: string) => {
    if (url.endsWith("/oauth2/tokenP")) {
      토큰발급 += 1;
      return new Response(JSON.stringify({ access_token: `tok${토큰발급}`, expires_in: 86400 }), { status: 200 });
    }
    const code = /FID_INPUT_ISCD=([0-9A-Z]{6})/.exec(url)![1];
    시세호출.push(code);
    const r = 시세(code);
    return new Response(JSON.stringify(r.body), { status: r.status });
  });
}

beforeEach(() => {
  상태.db = new DatabaseSync(":memory:");
  상태.db.exec(readFileSync(join(__dirname, "..", "..", "migrations", "0045_api_tokens.sql"), "utf-8"));
  process.env.KIS_APP_KEY = "k";
  process.env.KIS_APP_SECRET = "s";
  토큰발급 = 0;
  시세호출 = [];
});
afterEach(() => vi.unstubAllGlobals());

describe("모양 맞추기", () => {
  it("국내 심볼만 6자리 코드로 바꾼다", () => {
    expect(kisCode("005930.KS")).toBe("005930");
    expect(kisCode("0091P0.KQ")).toBe("0091P0");
    expect(kisCode("AAPL")).toBeNull();
  });

  it("응답을 야후와 같은 시세로 옮기고 출처를 붙인다 — 전일 종가는 기준가", () => {
    const q = parseKisPrice("005930.KS", 정상("271000"), 지금)!;
    expect(q).toMatchObject({ price: 271000, previous_close: 272000, day_high: 273500, day_low: 269500, volume: 12980705, source: KIS_SOURCE });
    expect(q.time).toBe(지금.toISOString());
  });

  it("기준가가 없으면 현재가 − 전일 대비", () => {
    const body = 정상("271000");
    body.output.stck_sdpr = "0";
    expect(parseKisPrice("005930.KS", body, 지금)!.previous_close).toBe(272000);
  });

  it("오류 응답·0원은 시세가 아니다 (25.721 과 같은 규칙)", () => {
    expect(parseKisPrice("005930.KS", { rt_cd: "1", output: {} }, 지금)).toBeNull();
    expect(parseKisPrice("005930.KS", 정상("0"), 지금)).toBeNull();
  });

  it("토큰은 만료 1시간 넘게 남았을 때만 그대로 쓴다", () => {
    expect(tokenFresh(new Date(지금.getTime() + 2 * 3600_000).toISOString(), 지금)).toBe(true);
    expect(tokenFresh(new Date(지금.getTime() + 30 * 60_000).toISOString(), 지금)).toBe(false);
    expect(tokenFresh(null, 지금)).toBe(false);
  });
});

describe("부르기", () => {
  it("토큰은 하루 한 번 — 두 번째 호출은 DB 의 토큰을 쓴다", async () => {
    vi.stubGlobal("fetch", 가짜fetch(() => ({ status: 200, body: 정상("271000") })));
    const 첫 = await fetchKisQuotes(["005930.KS"], 지금);
    const 둘 = await fetchKisQuotes(["005930.KS"], new Date(지금.getTime() + 5 * 60_000));
    expect(토큰발급).toBe(1);
    expect(첫.calls).toBe(2);
    expect(둘.calls).toBe(1);
    expect(둘.quotes["005930.KS"].price).toBe(271000);
  });

  it("못 받은 종목은 야후로 넘긴다 — 오류 코드만 남긴다", async () => {
    vi.stubGlobal("fetch", 가짜fetch((code) => (code === "000660" ? { status: 500, body: {} } : { status: 200, body: 정상("271000") })));
    const r = await fetchKisQuotes(["005930.KS", "000660.KS"], 지금);
    expect(Object.keys(r.quotes)).toEqual(["005930.KS"]);
    expect(r.failed).toEqual(["000660.KS"]);
    expect(r.error).toBe("KIS HTTP 500");
  });

  it("표가 없으면(마이그레이션 전) KIS 를 건너뛰고 전부 야후로", async () => {
    상태.db = new DatabaseSync(":memory:");
    vi.stubGlobal("fetch", 가짜fetch(() => ({ status: 200, body: 정상("271000") })));
    const r = await fetchKisQuotes(["005930.KS", "000660.KS"], 지금);
    expect(r.failed).toEqual(["005930.KS", "000660.KS"]);
    expect(r.error).toMatch(/표 없음/);
    expect(토큰발급).toBe(0);
  });

  it("토큰을 못 받으면 전부 야후로", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error_code: "EGW00133" }), { status: 403 })));
    const r = await fetchKisQuotes(["005930.KS"], 지금);
    expect(r.failed).toEqual(["005930.KS"]);
    expect(r.error).toBe("KIS 토큰 HTTP 403 EGW00133");
  });

  it("초당 묶음을 넘지 않게 나눠 부른다", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    try {
      vi.stubGlobal("fetch", 가짜fetch(() => ({ status: 200, body: 정상("1000") })));
      const 심볼 = Array.from({ length: KIS_PER_SECOND + 1 }, (_, i) => `${String(i).padStart(6, "0")}.KS`);
      const 약속 = fetchKisQuotes(심볼, 지금);
      await vi.advanceTimersByTimeAsync(0);
      expect(시세호출.length).toBe(KIS_PER_SECOND);
      await vi.advanceTimersByTimeAsync(1_000);
      const r = await 약속;
      expect(Object.keys(r.quotes).length).toBe(KIS_PER_SECOND + 1);
    } finally {
      vi.useRealTimers();
    }
  });
});
