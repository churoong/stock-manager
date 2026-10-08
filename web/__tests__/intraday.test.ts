/**
 * 장중 모니터링 테스트 (docs/intraday.md, Step 14).
 *
 * 완료 기준 (design.md):
 *   - 같은 트리거 중복 발송이 DB 제약으로 차단된다
 *   - 장 밖 호출이 즉시 끝난다 / 토큰 없는 호출이 거부된다
 *   - 장중 실행이 scores 를 수정하지 않는다
 *   - 조용시간에는 저장만 하고, 해제 뒤 한 통으로 묶어 보낸다
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// 장중 감시가 켜졌을 때의 동작을 본다 — 운영 기본값은 꺼짐 (docs/infra.md 25.1026)
vi.mock("@/lib/intradaySwitch", () => ({ INTRADAY_ENABLED: true, INTRADAY_OFF_NOTE: "꺼짐" }));
import {
  activeSession,
  bundleMessage,
  evaluate,
  inQuietHours,
  isSessionQuote,
  parseSpark,
  tokenMatches,
  type Quote,
  type Target,
} from "@/lib/intraday";
import { localDate } from "@/lib/market";

const ROOT = join(process.cwd(), "..");

function target(extra: Partial<Target> = {}): Target {
  return {
    stock_id: 1, yahoo_symbol: "005930.KS", name: "삼성전자", currency: "KRW", reasons: ["holding"],
    buy_zone_low: null, buy_zone_high: null, target_price: null, stop_price: null, prev_close: 250_000,
    avg_volume_20d: 10_000_000, ...extra,
  };
}

function quote(extra: Partial<Quote> = {}): Quote {
  return {
    symbol: "005930.KS", price: 252_500, time: "2026-09-17T05:00:00.000Z", previous_close: 253_500,
    day_high: 259_000, day_low: 251_500, volume: 11_992_911, ...extra,
  };
}

const TH = { spike_pct: 5, volume_multiple: 3 };

describe("트리거", () => {
  it("아무것도 없으면 조용하다", () => {
    expect(evaluate(target(), quote(), TH)).toEqual([]);
  });

  it("분할 매수 다음 차수 가격에 저가가 닿으면 알린다 (25.999)", () => {
    const t = target({ next_tranche_price: 251_500, next_tranche_step: 2 });
    const [hit] = evaluate(t, quote(), TH);
    expect(hit.trigger).toBe("tranche");
    expect(hit.message).toContain("분할 매수 2차");
    expect(evaluate(target({ next_tranche_price: 251_000, next_tranche_step: 2 }), quote(), TH)).toEqual([]);
    expect(evaluate(target({ next_tranche_price: 251_500, next_tranche_step: null }), quote(), TH)).toEqual([]);
  });

  it("권장 매수 구간 진입", () => {
    const hits = evaluate(target({ buy_zone_low: 250_000, buy_zone_high: 255_000 }), quote(), TH);
    expect(hits.map((h) => h.trigger)).toEqual(["buy_zone"]);
  });

  it("관심 종목 목표 매수가", () => {
    expect(evaluate(target(), quote(), TH, 253_000).map((h) => h.trigger)).toEqual(["watch_price"]);
    expect(evaluate(target(), quote(), TH, 252_000)).toEqual([]);
  });

  it("관심 목표가는 매수 구간과 다른 트리거다 — 한쪽이 나간 날 다른 쪽을 묻지 않는다 (docs/infra.md 25.408)", () => {
    const hits = evaluate(target({ buy_zone_low: 250_000, buy_zone_high: 255_000 }), quote(), TH, 253_000);
    expect(hits.map((h) => h.trigger)).toEqual(["buy_zone", "watch_price"]);
  });

  it("목표가·손절선은 당일 고가·저가로 터치를 본다", () => {
    const hits = evaluate(target({ target_price: 258_000, stop_price: 252_000 }), quote(), TH);
    expect(hits.map((h) => h.trigger)).toEqual(["target", "stop"]);
  });

  it("급등락은 전일 종가 대비 설정 %", () => {
    expect(evaluate(target(), quote({ price: 266_175 }), TH).map((h) => h.trigger)).toEqual(["spike_up"]);
    expect(evaluate(target(), quote({ price: 240_825 }), TH).map((h) => h.trigger)).toEqual(["spike_down"]);
    expect(evaluate(target(), quote({ price: 266_000 }), TH)).toEqual([]);
  });

  it("거래량은 20일 평균의 N배 초과", () => {
    expect(evaluate(target(), quote({ volume: 30_000_001 }), TH).map((h) => h.trigger)).toEqual(["volume"]);
    expect(evaluate(target(), quote({ volume: 30_000_000 }), TH)).toEqual([]);
  });

  it("판정에 쓴 시세 시각을 남긴다", () => {
    const [hit] = evaluate(target({ stop_price: 260_000 }), quote(), TH);
    expect(hit.data.quote_time).toBe("2026-09-17T05:00:00.000Z");
  });
});

describe("시세가 이번 장의 것인가 (25.196)", () => {
  const q = (time: string) => ({ symbol: "X", price: 1, time, previous_close: null, day_high: null, day_low: null, volume: null });

  it("국내: 어제 15:30 시세는 오늘 장이 아니다", () => {
    expect(isSessionQuote(q("2026-09-21T06:30:00.000Z"), "KR", "2026-09-22")).toBe(false);
    expect(isSessionQuote(q("2026-09-22T00:05:00.000Z"), "KR", "2026-09-22")).toBe(true); // 09:05 KST
  });

  it("미국: 현지 날짜로 본다 — UTC 날짜로 보면 장 끝 무렵이 다음 날이 된다", () => {
    // 2026-09-22 20:30 EDT = 09-23 00:30Z. UTC 날짜로 세면 다음 날이 된다. 정규장(+유예 30분, 25.724)은
    // 20:30Z 에 끝나 실제로 넘어가지는 않지만, 잣대가 **현지 날짜**라는 것을 고정한다
    expect(isSessionQuote(q("2026-09-23T00:30:00.000Z"), "US", "2026-09-22")).toBe(true);
    expect(isSessionQuote(q("2026-09-21T19:59:00.000Z"), "US", "2026-09-22")).toBe(false);
  });

  it("시각을 못 읽으면 판정하지 않는다", () => {
    expect(isSessionQuote(q("엉터리"), "KR", "2026-09-22")).toBe(false);
  });
});

describe("야후 spark 응답", () => {
  it("실제 응답 모양에서 시세를 꺼낸다 (2026-09-17 실측 필드)", () => {
    const payload = {
      spark: {
        result: [
          {
            symbol: "005930.KS",
            response: [{ meta: { regularMarketPrice: 252500, regularMarketTime: 1789626612, previousClose: 253500,
              regularMarketDayHigh: 259000, regularMarketDayLow: 251500, regularMarketVolume: 11992911 } }],
          },
          { symbol: "BAD", response: [{ meta: {} }] },
        ],
      },
    };
    const quotes = parseSpark(payload);
    expect(Object.keys(quotes)).toEqual(["005930.KS"]);
    expect(quotes["005930.KS"].time).toBe("2026-09-17T06:30:12.000Z");
    expect(parseSpark({ spark: { result: null } })).toEqual({});
    // 25.721 — 0 이하 현재가는 버리고, 0 인 저가·고가·전일 종가는 모름(null)으로 본다. 거래량 0 은 남긴다
    const 영 = parseSpark({
      spark: {
        result: [
          { symbol: "A.KS", response: [{ meta: { regularMarketPrice: 0, regularMarketTime: 1_758_000_000 } }] },
          {
            symbol: "B.KS",
            response: [{ meta: { regularMarketPrice: 70500, regularMarketTime: 1_758_000_000, regularMarketDayLow: 0, regularMarketVolume: 0 } }],
          },
        ],
      },
    });
    expect(영["A.KS"]).toBeUndefined();
    expect(영["B.KS"].day_low).toBeNull();
    expect(영["B.KS"].volume).toBe(0);
  });
});

describe("세션·조용시간·토큰", () => {
  const sessions = [{ date: "2026-09-17", open_utc: "2026-09-17T00:00:00+00:00", close_utc: "2026-09-17T06:30:00+00:00" }];

  it("장 밖이면 세션이 없다 (폐장 뒤 30분까지는 지연 시세를 본다, 25.724)", () => {
    expect(activeSession(sessions, new Date("2026-09-16T23:59:00Z"))).toBeNull();
    expect(activeSession(sessions, new Date("2026-09-17T03:00:00Z"))?.date).toBe("2026-09-17");
    expect(activeSession(sessions, new Date("2026-09-17T07:00:00Z"))?.date).toBe("2026-09-17");
    // 15:55 호출이 몇 초 늦게 닿아도 장중이다 — 25분이던 때는 장 밖이라 종가 단일가가 빠졌다
    expect(activeSession(sessions, new Date("2026-09-17T06:55:02Z"))?.date).toBe("2026-09-17");
    expect(activeSession(sessions, new Date("2026-09-17T07:01:00Z"))).toBeNull();
    expect(activeSession([], new Date("2026-09-17T03:00:00Z"))).toBeNull();
  });

  it("조용시간은 한국 시각, 자정을 넘는 구간도", () => {
    const q = { enabled: true, start: "00:00", end: "07:00" };
    expect(inQuietHours(q, new Date("2026-09-16T15:30:00Z"))).toBe(true); // KST 00:30
    expect(inQuietHours(q, new Date("2026-09-16T22:00:00Z"))).toBe(false); // KST 07:00
    expect(inQuietHours({ enabled: true, start: "23:00", end: "07:00" }, new Date("2026-09-16T14:30:00Z"))).toBe(true);
    expect(inQuietHours({ ...q, enabled: false }, new Date("2026-09-16T15:30:00Z"))).toBe(false);
  });

  it("토큰은 정확히 같아야", () => {
    expect(tokenMatches("abc", "abc")).toBe(true);
    expect(tokenMatches("abd", "abc")).toBe(false);
    expect(tokenMatches(null, "abc")).toBe(false);
    expect(tokenMatches("abc", undefined)).toBe(false);
  });

  it("하루 기준은 시장 현지 날짜", () => {
    const now = new Date("2026-09-17T02:00:00Z"); // KST 11시, 뉴욕 16일 22시
    expect(localDate("KR", now)).toBe("2026-09-17");
    expect(localDate("US", now)).toBe("2026-09-16");
  });

  it("밀린 알림은 한 통으로", () => {
    const text = bundleMessage(
      [
        { market: "US", message: "A: 급락", created_at: "2026-09-16T15:00:00Z" },
        { market: "US", message: "B: 손절선", created_at: "2026-09-16T16:00:00Z" },
      ],
      new Date("2026-09-16T22:00:00Z"),
    );
    expect(text.split("\n")[0]).toBe("장중 알림 2건 (07:00 KST, 미국은 지연 시세)");
    // 국내만이면 지연이라 하지 않는다 — KIS 실시간 (25.986)
    expect(bundleMessage([{ market: "KR", message: "C: 급등", created_at: "2026-09-16T01:00:00Z" }], new Date("2026-09-16T01:05:00Z")).split("\n")[0]).toBe("장중 알림 1건 (10:05 KST)");
    expect(text).toContain("A: 급락");
    expect(text).toContain("자동 매매는 없습니다");
  });
});

describe("DB 제약", () => {
  it("같은 종목·트리거·날짜는 두 번 들어가지 않는다", () => {
    const db = new DatabaseSync(":memory:");
    for (const file of readdirSync(join(ROOT, "migrations")).filter((f) => f.endsWith(".sql")).sort()) {
      db.exec(readFileSync(join(ROOT, "migrations", file), "utf-8"));
    }
    db.exec(`INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)
             VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')`);
    const insert = db.prepare(`INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at)
      VALUES (1, 'KR', ?, ?, 'm', '{}', 't') ON CONFLICT (stock_id, trigger_type, trade_date) DO NOTHING`);
    expect(insert.run("2026-09-17", "stop").changes).toBe(1);
    expect(insert.run("2026-09-17", "stop").changes).toBe(0);
    expect(insert.run("2026-09-17", "spike_down").changes).toBe(1);
    expect(insert.run("2026-09-18", "stop").changes).toBe(1);
  });
});

describe("장중 경로는 alerts 말고 쓰지 않는다", () => {
  it("쓰기 문장의 대상 표는 alerts 와 호출 기록뿐", () => {
    // 호출 기록은 2026-09-21 에 web/lib/heartbeat.ts 로 모았다(docs/infra.md 25.60).
    // 경로가 부르는 파일을 다 훑지 않으면 이 검사가 조용히 헐거워진다.
    //
    // **`api_usage` 는 2026-09-22 에 일부러 넣었다** (25.105). 이 경로가 DART 를 직접
    // 부르면서 한도 카운터에는 한 번도 안 세고 있었다 — CLAUDE.md 비용 규칙 위반이다.
    // `cron_heartbeats` 와 같은 갈래다: **판단 자료가 아니라 우리가 뭘 했는지의 기록**.
    // 점수·신호·매매·보유는 여전히 못 쓴다. 이 목록이 늘어날 때는 그 이유를 여기 적는다
    const files = [
      join(ROOT, "web", "app", "api", "cron", "intraday", "route.ts"),
      join(ROOT, "web", "lib", "intraday.ts"),
      join(ROOT, "web", "lib", "heartbeat.ts"),
      join(ROOT, "web", "lib", "apiUsage.ts"),
      join(ROOT, "web", "lib", "limits.ts"),
    ];
    const written = new Set<string>();
    for (const file of files) {
      const text = readFileSync(file, "utf-8");
      // "ON CONFLICT … DO UPDATE SET" 의 UPDATE 는 표 이름이 아니라서 뺀다
      for (const m of text.matchAll(/\b(?:INSERT\s+INTO|(?<!DO\s)UPDATE|DELETE\s+FROM)\s+([a-z_]+)/gi)) {
        written.add(m[1].toLowerCase());
      }
    }
    expect([...written].sort()).toEqual(["alerts", "api_usage", "cron_heartbeats"]);
  });
});

describe("경로: 토큰·장 밖", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.doUnmock("@/lib/db");
  });

  it("토큰이 없으면 401, 시크릿이 설정되지 않았으면 503 (기록도 남기지 않는다)", async () => {
    vi.stubEnv("CRON_SECRET", "s3cret");
    const executed: string[] = [];
    vi.doMock("@/lib/db", () => ({
      execute: async (sql: string) => {
        executed.push(sql);
        return { columns: [], rows: [], affectedRows: 0 };
      },
      batch: async () => [],
      rowsToObjects: () => [],
      quotaReason: () => null,
    }));
    const { GET } = await import("@/app/api/cron/intraday/route");
    expect((await GET(new Request("https://x/api/cron/intraday?market=KR"))).status).toBe(401);
    vi.stubEnv("CRON_SECRET", "");
    expect((await GET(new Request("https://x/api/cron/intraday?market=KR"))).status).toBe(503);
    expect(executed).toEqual([]);
  });

  it("장 밖이면 시세를 받지 않고 끝난다", async () => {
    vi.stubEnv("CRON_SECRET", "s3cret");
    const executed: string[] = [];
    vi.doMock("@/lib/db", () => ({
      execute: async (sql: string) => {
        executed.push(sql);
        return { columns: [], rows: [], affectedRows: 0 };
      },
      batch: async () => [],
      rowsToObjects: () => [],
      quotaReason: () => null,
    }));
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { GET } = await import("@/app/api/cron/intraday/route");
    const res = await GET(new Request("https://x/api/cron/intraday?market=KR", { headers: { "x-cron-secret": "s3cret" } }));
    const body = await res.json();
    expect(res.status).toBe(200);
    // **무엇 때문에 건너뛰었는지까지 본다.** `toBeTruthy()` 로는 두 사유를 구별하지 못하는데,
    // 둘은 뜻이 정반대다 — "장 밖" 은 정상이고, "세션 정보 없음" 은 **거래일 달력이 비어 있다**는
    // 뜻이라 사람이 일일 배치를 봐야 한다. 가지를 바꿔 달아도 예전 단언은 통과했다 (2026-09-21)
    expect(body.skipped).toBe("세션 정보 없음(일일 배치 확인)");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(executed.some((sql) => /monitor_targets/.test(sql))).toBe(false);
    expect(executed.some((sql) => /INSERT INTO cron_heartbeats/.test(sql))).toBe(true);
    fetchSpy.mockRestore();
  });
});

describe("경로: 표가 아직 없을 때", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.doUnmock("@/lib/db");
    vi.resetModules();
  });

  it("500 대신 할 일을 알려 준다", async () => {
    vi.resetModules();
    vi.stubEnv("CRON_SECRET", "s3cret");
    vi.doMock("@/lib/db", () => ({
      execute: async (sql: string) => {
        if (/alerts|market_sessions/.test(sql)) throw new Error("SQL 실패: no such table: alerts");
        return { columns: [], rows: [], affectedRows: 0 };
      },
      batch: async () => [],
      rowsToObjects: () => [],
      quotaReason: () => null,
    }));
    const { GET } = await import("@/app/api/cron/intraday/route");
    const res = await GET(new Request("https://x/api/cron/intraday?market=KR", { headers: { "x-cron-secret": "s3cret" } }));
    expect(res.status).toBe(200);
    expect((await res.json()).skipped).toContain("마이그레이션");
  });
});

/**
 * 공시 확인이 DART 한도를 센다 (2026-09-22, docs/infra.md 25.105).
 *
 * 이 경로는 보유 종목마다 DART 를 직접 부르는데, 2026-09-22 까지 그 호출을 **한도
 * 카운터에 한 번도 세지 않았다.** 배치만 세고 있었으니 `/status` 의 게이지는 실제보다
 * 적게 보였고, 배치는 이미 웹이 쓴 여유를 제 것으로 알았다.
 */
describe("경로: 공시 확인과 DART 한도", () => {
  const SESSION = { date: "2026-09-22", open_utc: "2026-09-22T00:00:00Z", close_utc: "2026-09-22T06:30:00Z" };

  function rs(columns: string[], rows: unknown[][]) {
    return { columns, rows, affectedRows: rows.length };
  }

  /** SQL 글자를 보고 답을 고르는 가짜 DB. 실제 스키마가 아니라 **경로의 순서**를 본다 */
  function makeDb(opts: { alerted?: number[]; usage?: Array<unknown> } = {}) {
    const executed: Array<{ sql: string; args: unknown[] }> = [];
    const execute = async (sql: string, args: unknown[] = []) => {
      executed.push({ sql, args });
      if (/FROM market_sessions/.test(sql)) return rs(["date", "open_utc", "close_utc"], [Object.values(SESSION)]);
      if (/FROM monitor_targets/.test(sql)) {
        return rs(
          ["stock_id", "yahoo_symbol", "name", "currency", "reasons", "buy_zone_low", "buy_zone_high",
            "target_price", "stop_price", "prev_close", "avg_volume_20d", "dart_corp_code"],
          [[1, "005930.KS", "삼성전자", "KRW", "[\"holding\"]", null, null, null, null, 100, 1000, "00126380"],
            [2, "000660.KS", "SK하이닉스", "KRW", "[\"holding\"]", null, null, null, null, 100, 1000, "00164779"]],
        );
      }
      if (/trigger_type = 'disclosure'/.test(sql)) return rs(["stock_id"], (opts.alerted ?? []).map((id) => [id]));
      if (/FROM api_usage/.test(sql)) return rs(["call_count", "limit_value", "warn_at_pct", "state"], opts.usage ? [opts.usage] : []);
      return rs([], []);
    };
    return { executed, execute };
  }

  function stubFetch(dart: (corp: string) => unknown) {
    const calls: string[] = [];
    return {
      calls,
      spy: vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        const body = url.includes("opendart.fss.or.kr")
          ? dart(new URL(url).searchParams.get("corp_code") ?? "")
          : { spark: { result: [] } }; // 시세는 비워 둔다 — 여기서 보는 것은 공시다
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    };
  }

  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-22T02:00:00Z")); // 국내 장중 11시
    vi.stubEnv("CRON_SECRET", "s3cret");
    vi.stubEnv("DART_API_KEY", "k");
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.doUnmock("@/lib/db");
    vi.restoreAllMocks();
  });

  async function run(db: ReturnType<typeof makeDb>, batched: Array<{ sql: string; args: unknown[] }> = []) {
    vi.doMock("@/lib/db", async (orig) => ({
      ...(await (orig as () => Promise<Record<string, unknown>>)()),
      execute: db.execute,
      batch: async (items: Array<{ sql: string; args: unknown[] }>) => {
        batched.push(...items);
        return items.map(() => ({ ...rs([], []), affectedRows: 1 }));
      },
    }));
    const { GET } = await import("@/app/api/cron/intraday/route");
    const res = await GET(new Request("https://x/api/cron/intraday?market=KR", { headers: { "x-cron-secret": "s3cret" } }));
    return res.json();
  }

  /** 시세만 주고 DART 는 "공시 없음" 인 fetch. 삼성전자 한 종목, 전일 종가 100 에서 120(+20%) */
  function stubSpark(regularMarketTime: number) {
    return vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("opendart.fss.or.kr")
        ? { status: "013" }
        : { spark: { result: [{ symbol: "005930.KS", response: [{ meta: {
            regularMarketPrice: 120, regularMarketTime, previousClose: 100,
            regularMarketDayHigh: 121, regularMarketDayLow: 99, regularMarketVolume: 10 } }] }] } };
      return new Response(JSON.stringify(body), { status: 200 });
    });
  }

  it("지난 장의 시세로는 알리지 않는다 — 어제의 급등이 오늘 알림 자리를 먹는다 (25.196)", async () => {
    // 2026-09-21 15:30 KST. 개장 뒤 지연 시세가 아직 어제 것을 주는 모양
    stubSpark(Date.parse("2026-09-21T06:30:00Z") / 1000);
    const batched: Array<{ sql: string; args: unknown[] }> = [];
    const db = makeDb();
    const body = await run(db, batched);
    expect(batched.filter((b) => /INSERT INTO alerts/.test(b.sql))).toEqual([]);
    expect(body.hits).toBe(0);
    // 조용히 버리지 않는다 — 몇 종목을 왜 건너뛰었는지 호출 기록에 남는다
    const beat = db.executed.find((e) => /cron_heartbeats/.test(e.sql));
    expect(JSON.stringify(beat?.args)).toContain("stale_quotes");
  });

  it("시세를 아예 못 받은 종목도 호출 기록에 이름을 남긴다 (25.345)", async () => {
    stubFetch(() => ({ status: "013" })); // 시세 응답이 비었다
    const db = makeDb();
    const body = await run(db);
    expect(body.missing_quotes).toContain("005930.KS");
    const beat = db.executed.find((e) => /cron_heartbeats/.test(e.sql));
    expect(JSON.stringify(beat?.args)).toContain("missing_quotes");
  });

  it("오늘 장의 시세면 그대로 알린다 — 거르개가 전부를 막지 않는다", async () => {
    // 같은 값, 시각만 오늘 10:55 KST. 미끼가 무는지(급등 +20%) 확인한다
    stubSpark(Date.parse("2026-09-22T01:55:00Z") / 1000);
    const batched: Array<{ sql: string; args: unknown[] }> = [];
    const body = await run(makeDb(), batched);
    const inserted = batched.filter((b) => /INSERT INTO alerts/.test(b.sql)).map((b) => b.args[3]);
    expect(inserted).toContain("spike_up");
    expect(body.hits).toBeGreaterThan(0);
  });

  it("부른 횟수를 api_usage 에 더한다 — 배치와 같은 이름·같은 행", async () => {
    const db = makeDb();
    const f = stubFetch(() => ({ status: "013" })); // 오늘 공시 없음
    const body = await run(db);
    expect(f.calls.filter((u) => u.includes("opendart")).length).toBe(2); // 읽어 냈는지 먼저 센다
    const add = db.executed.find((e) => /INSERT INTO api_usage/.test(e.sql));
    expect(add).toBeTruthy();
    expect(add!.args[0]).toBe("dart_opendart"); // 25.68 — 이름이 갈리면 한도 판정이 쪼개진다
    expect(add!.args[2]).toBe(2); // 실제로 나간 횟수
    expect(body.errors).toEqual([]);
  });

  it("오늘 이미 알린 종목은 다시 묻지 않는다", async () => {
    // 하루 한 번은 alerts 의 UNIQUE 가 막으므로 다시 불러도 **결과가 버려진다**.
    // 장 한 번에 종목당 78번씩 태우던 한도다
    const db = makeDb({ alerted: [1] });
    const f = stubFetch(() => ({ status: "013" }));
    await run(db);
    const dart = f.calls.filter((u) => u.includes("opendart"));
    expect(dart).toHaveLength(1);
    expect(dart[0]).toContain("00164779"); // 남은 한 종목만
  });

  it("전부 알렸으면 DART 도 카운터도 건드리지 않는다", async () => {
    const db = makeDb({ alerted: [1, 2] });
    const f = stubFetch(() => ({ status: "013" }));
    await run(db);
    expect(f.calls.filter((u) => u.includes("opendart"))).toHaveLength(0);
    expect(db.executed.some((e) => /INSERT INTO api_usage/.test(e.sql))).toBe(false);
  });

  it("한도에 걸려 있으면 **부르기 전에** 멈추고 사유를 남긴다", async () => {
    // CLAUDE.md 비용 규칙: 100% 에서 중단. 셈이 모자라도 적어 둔 blocked 를 믿는다
    const db = makeDb({ usage: [3, 20_000, 80, "blocked"] });
    const f = stubFetch(() => ({ status: "000", list: [{ report_nm: "x", rcept_no: "1" }] }));
    const body = await run(db);
    expect(f.calls.filter((u) => u.includes("opendart"))).toHaveLength(0);
    expect(body.errors.join()).toContain("DART 일일 한도");
  });

  it("DART 가 '요청 제한' 이라 답하면 멈추고 blocked 로 적는다 — 공시 없음과 구별한다", async () => {
    // 예전에는 status 가 000 이 아니면 조용히 넘어가서, **막힌 것이 "오늘 공시 없음" 과
    // 똑같이** 보였다. 25.0 의 「실패가 성공으로 보인다」
    const db = makeDb();
    const f = stubFetch(() => ({ status: "020", message: "요청 제한을 초과하였습니다" }));
    const body = await run(db);
    expect(f.calls.filter((u) => u.includes("opendart"))).toHaveLength(1); // 첫 응답에서 멈춘다
    expect(body.errors.join()).toContain("요청 제한(020)");
    const add = db.executed.find((e) => /INSERT INTO api_usage/.test(e.sql))!;
    expect(add.args[2]).toBe(1); // 실제로 나간 한 번은 센다
    expect(add.args[5]).toBe("blocked");
  });

  it("알 수 없는 응답도 조용히 넘기지 않는다", async () => {
    const db = makeDb();
    const f = stubFetch(() => ({ status: "100", message: "필드의 부적절한 값" }));
    const body = await run(db);
    expect(f.calls.filter((u) => u.includes("opendart"))).toHaveLength(2); // 멈추지는 않는다
    expect(body.errors.join()).toContain("DART 100");
  });
});
