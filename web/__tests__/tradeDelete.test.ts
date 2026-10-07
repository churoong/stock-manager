import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BALANCE_AT_SELLS, TRADES_FOR_HELD, deleteShortfallWarnings, laterOversell, oversellWarnings } from "@/lib/portfolio";

/**
 * **지우는 쪽에는 문이 없었다** (docs/infra.md 25.150).
 *
 * 넣을 때는 막는다 — `POST /api/trades` 가 "그날까지 보유 N주보다 많이 팔 수 없습니다"
 * 로 거절한다. 그런데 **매수를 지울 때는 아무것도 안 봤다.** 지우면 그 뒤 매도가
 * 그대로 남아 같은 상태가 된다. 25.0 「그물이 한 방향만 본다」.
 *
 * 게다가 `[id]` 경로의 설명문이 **"고치기는 지우고 다시 넣는다"** 고 권한다 —
 * 잘못 넣은 매수를 고치려면 반드시 이 길을 지난다. 드문 길이 아니다.
 *
 * 배치는 이 상태를 견딘다(`match_fifo` 가 보유분까지만 짝짓고 경고를 남긴다).
 * 다만 그 경고는 **다음 재계산이 끝나야** 보인다 — Actions 가 멈춰 있으면 영영.
 */

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')`);
});

function 매매(id: number, side: string, date: string, qty: number) {
  db.exec(`INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
    VALUES (${id}, 1, '${side}', '${date}', 1000, ${qty}, 'KRW', 1, 'none', 't', 't')`);
}

// 경로가 쓰는 판정 — 배치와 같은 규칙으로 지워서 새로 늘어나는 모자람만 (25.795)
function 지우면(id: number): string[] {
  const rows = db.prepare(TRADES_FOR_HELD).all(1) as Array<{ id: number; side: string; trade_date: string; quantity: number }>;
  return deleteShortfallWarnings(rows, id);
}

describe("매수를 지우면 남은 매도가 어떻게 되나", () => {
  it("판 적이 있는 매수를 지우면 말한다", () => {
    매매(1, "buy", "2026-01-05", 100);
    매매(2, "sell", "2026-03-10", 100);

    const 말 = 지우면(1);
    expect(말).toHaveLength(1);
    expect(말[0]).toContain("2026-03-10");
    expect(말[0]).toContain("100주 많아집니다");
    expect(말[0]).toContain("함께 정리하세요");
  });

  it("아직 안 판 매수는 조용하다", () => {
    매매(1, "buy", "2026-01-05", 100);

    expect(지우면(1)).toEqual([]);
  });

  it("남은 매수로 덮이면 조용하다", () => {
    // 100주를 두 번 샀고 100주만 팔았다. 하나를 지워도 모자라지 않는다
    매매(1, "buy", "2026-01-05", 100);
    매매(2, "buy", "2026-01-06", 100);
    매매(3, "sell", "2026-03-10", 100);

    expect(지우면(1)).toEqual([]);
  });

  it("끝은 맞는데 중간이 모자란 것도 잡는다", () => {
    // **합계만 보면 0 이라 멀쩡해 보인다.** 그런데 3월 매도 시점에는 살 것이 없다
    매매(1, "buy", "2026-01-05", 100);
    매매(2, "sell", "2026-03-10", 100);
    매매(3, "buy", "2026-05-01", 100);

    const 말 = 지우면(1);
    expect(말, "합계만 세면 놓친다").toHaveLength(1);
    expect(말[0]).toContain("2026-03-10");
  });

  it("모자란 날이 여럿이면 몇 날인지 함께 말한다", () => {
    매매(1, "buy", "2026-01-05", 100);
    매매(2, "sell", "2026-03-10", 50);
    매매(3, "sell", "2026-04-10", 50);

    expect(지우면(1)[0]).toContain("그런 매도 2건");
  });

  it("다른 종목의 매도는 세지 않는다", () => {
    db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
      VALUES (2, '000660', 'KOSPI', 'KR', '다른종목', 'KRW', 'active', 't', 't')`);
    매매(1, "buy", "2026-01-05", 100);
    db.exec(`INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)
      VALUES (2, 2, 'sell', '2026-03-10', 1000, 100, 'KRW', 1, 'none', 't', 't')`);

    expect(지우면(1)).toEqual([]);
  });
});

describe("판정 함수만 따로", () => {
  it("빈 목록이면 아무 말도 안 한다", () => {
    expect(oversellWarnings([])).toEqual([]);
  });

  it("0 은 모자란 것이 아니다", () => {
    // 부동소수 찌꺼기로 -1e-12 가 나와도 경고하지 않는다
    expect(oversellWarnings([{ d: "2026-01-01", balance: 0 }])).toEqual([]);
    expect(oversellWarnings([{ d: "2026-01-01", balance: -1e-12 }])).toEqual([]);
  });
});

describe("경로와 화면이 그 판정을 쓴다", () => {
  it("DELETE 가 지우기 **전에** 본다", async () => {
    const 글 = readFileSync(join(process.cwd(), "app", "api", "trades", "[id]", "route.ts"), "utf-8");

    expect(글).toContain("TRADES_FOR_HELD");
    expect(글).toContain("deleteShortfallWarnings(");
    // 지운 뒤에 세면 그 매수가 이미 없어 늘 모자라 보인다
    expect(글.indexOf("deleteShortfallWarnings(")).toBeLessThan(글.indexOf("DELETE FROM trades"));
  });

  it("매도를 지울 때는 세지 않는다", () => {
    // 매도를 지우면 보유가 늘어난다. 모자랄 일이 없고, 괜히 질의만 하나 는다
    const 글 = readFileSync(join(process.cwd(), "app", "api", "trades", "[id]", "route.ts"), "utf-8");
    expect(글).toContain('대상.side === "buy"');
  });

  it("화면이 그 말을 띄운다", () => {
    const 글 = readFileSync(join(process.cwd(), "components", "PortfolioView.tsx"), "utf-8");
    expect(글).toContain("j.warnings?.length");
  });
});

describe("과거 날짜로 매도를 끼워 넣으면 (25.201)", () => {
  /** 넣으려는 매도가 그 뒤 매도를 넘기는가. 경로가 부르는 그대로 — 아무것도 빼지 않는다(id -1) */
  function 넣으면(date: string, qty: number) {
    const rows = db.prepare(BALANCE_AT_SELLS).all(-1, 1) as Array<{ d: string; balance: number }>;
    return laterOversell(rows, date, qty);
  }

  it("그날은 보유가 넉넉해도 뒤 매도가 넘치면 막는다 — 25.150 과 같은 상태를 넣을 때 만든다", () => {
    매매(1, "buy", "2026-09-01", 10);
    매매(2, "sell", "2026-09-10", 10);

    // 9/5 에는 10주를 들고 있다. "그날까지" 만 보면 통과한다
    const held = (db.prepare("SELECT SUM(CASE side WHEN 'buy' THEN quantity ELSE -quantity END) AS h FROM trades WHERE trade_date <= '2026-09-05'").get() as { h: number }).h;
    expect(held).toBe(10);
    expect(넣으면("2026-09-05", 5)).toEqual({ d: "2026-09-10", short: 5 });
  });

  it("뒤 매도가 있어도 여유 안이면 통과한다 — 거르개가 모든 매도를 막지 않는다", () => {
    매매(1, "buy", "2026-09-01", 10);
    매매(2, "sell", "2026-09-10", 4);

    expect(넣으면("2026-09-05", 6)).toBeNull();
    expect(넣으면("2026-09-05", 7)).toEqual({ d: "2026-09-10", short: 1 });
  });

  it("뒤에 매도가 없으면 그날 보유 검사에 맡긴다", () => {
    매매(1, "buy", "2026-09-01", 10);
    매매(2, "sell", "2026-09-03", 2); // 앞 날짜 매도는 여기서 보지 않는다 — 그날 보유 검사가 센다

    expect(넣으면("2026-09-05", 8)).toBeNull();
  });

  describe("경로를 실제로 돌린다", () => {
    // 글자로 "부른다" 를 보면 부르는 자리가 죽어 있어도 통과한다 (25.199 의 교훈).
    // 가짜 DB 대신 **실제 스키마의 SQLite** 에 질의를 그대로 흘린다
    beforeEach(() => {
      vi.resetModules();
      vi.doMock("@/lib/db", async (orig) => {
        const 진짜 = await (orig as () => Promise<Record<string, unknown>>)();
        const 돌리기 = async (sql: string, args: unknown[] = []) => {
          const stmt = db.prepare(sql);
          if (!/^\s*(SELECT|WITH)/i.test(sql)) {
            const r = stmt.run(...(args as never[]));
            return { columns: [], rows: [], affectedRows: Number(r.changes) };
          }
          const rows = stmt.all(...(args as never[])) as Array<Record<string, unknown>>;
          const columns = rows[0] ? Object.keys(rows[0]) : stmt.columns().map((c) => c.name);
          return { columns, rows: rows.map((r) => columns.map((c) => r[c])), affectedRows: 0 };
        };
        return {
          ...진짜,
          execute: 돌리기,
          batch: async (items: Array<{ sql: string; args: unknown[] }>) => Promise.all(items.map((i) => 돌리기(i.sql, i.args))),
        };
      });
      vi.doMock("@/lib/portfolio", async (orig) => ({
        ...(await (orig as () => Promise<Record<string, unknown>>)()),
        requestRecalc: async () => "skipped",
      }));
    });
    afterEach(() => {
      vi.doUnmock("@/lib/db");
      vi.doUnmock("@/lib/portfolio");
    });

    async function 매도_넣기(date: string, qty: number) {
      const { POST } = await import("@/app/api/trades/route");
      const res = await POST(new Request("https://x/api/trades", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stock_id: 1, side: "sell", trade_date: date, price: 1000, quantity: qty }),
      }));
      return { status: res.status, body: (await res.json()) as { errors?: string[] } };
    }

    it("뒤 매도를 넘기는 매도는 400 이고 들어가지 않는다", async () => {
      매매(1, "buy", "2026-09-01", 10);
      매매(2, "sell", "2026-09-10", 10);

      const { status, body } = await 매도_넣기("2026-09-05", 5);

      expect(status).toBe(400);
      expect(body.errors?.[0]).toContain("2026-09-10 매도가 보유보다 5주 많아집니다");
      expect((db.prepare("SELECT COUNT(*) AS n FROM trades").get() as { n: number }).n).toBe(2);
    });

    it("여유 안이면 들어간다 — 미끼가 무는지", async () => {
      매매(1, "buy", "2026-09-01", 10);
      매매(2, "sell", "2026-09-10", 4);

      const { status } = await 매도_넣기("2026-09-05", 6);

      expect(status).toBe(200);
      expect((db.prepare("SELECT COUNT(*) AS n FROM trades").get() as { n: number }).n).toBe(3);
    });
  });
});
