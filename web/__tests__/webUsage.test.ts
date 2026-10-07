import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { USAGE_ADD } from "@/lib/apiUsage";
import {
  TURSO_READS_NAME,
  USAGE_ADD_MONTH,
  noteTursoReads,
  peekTursoPending,
  resetTursoPending,
  D1_DAILY_READ_LIMIT,
  D1_DAILY_WRITE_LIMIT,
  D1_READS_NAME,
  D1_WRITES_NAME,
  READ_FLUSH_ROWS,
  WRITE_FLUSH_ROWS,
  noteD1Usage,
  peekPending,
  resetPending,
  usageStatements,
  utcToday,
} from "@/lib/webUsage";

/**
 * 웹이 D1 에 쓰고 훑은 행을 센다 (docs/infra.md 25.124).
 *
 * **있었던 일.** 배치는 응답의 `rows_written`·`rows_read` 를 처음부터 모아 `api_usage` 에
 * 적었는데 웹은 한 번도 세지 않았다. 둘이 **같은 DB 의 같은 하루 예산**을 쓴다. 그래서
 * `remaining_d1_daily_writes()` 가 실제보다 넉넉한 수를 돌려주고, 따라잡기가 그 수로
 * "오늘 이만큼 더 받아도 된다" 를 정한다 — 25.20 에서 한 번 넘겼다.
 *
 * 여기서 지키는 것은 넷이다.
 *   1. 쓰기는 **매번** 적는다 (적게 보이는 카운터는 없느니만 못하다)
 *   2. 읽기는 문턱까지 모은다 (매번 적으면 쓰기가 읽기를 따라 는다)
 *   3. 적는 왕복이 쓴 행을 **또 세러 들어가지 않는다** (끝이 없다)
 *   4. 못 적었으면 **되돌려 놓는다** (버리면 카운터가 적게 보인다)
 */

const 어느날 = new Date("2026-09-22T23:40:00.000Z");

beforeEach(() => resetPending());

describe("언제 적나", () => {
  it("쓰기는 한 행만 있어도 바로 적는다", async () => {
    const 보낸것: unknown[][] = [];
    await noteD1Usage(1, 0, 어느날, async (sts) => 보낸것.push(sts));

    expect(WRITE_FLUSH_ROWS).toBe(1);
    expect(보낸것).toHaveLength(1);
    expect(peekPending()).toEqual({ writes: 0, reads: 0 });
  });

  it("읽기는 문턱까지 모은다", async () => {
    const 보낸것: unknown[][] = [];
    await noteD1Usage(0, READ_FLUSH_ROWS - 1, 어느날, async (sts) => 보낸것.push(sts));

    expect(보낸것).toHaveLength(0);
    expect(peekPending().reads).toBe(READ_FLUSH_ROWS - 1);
  });

  it("문턱을 넘으면 모아 둔 것을 한 번에 적는다", async () => {
    const 보낸것: Array<Array<{ args: unknown[] }>> = [];
    const 보내기 = async (sts: Array<{ args: unknown[] }>) => {
      보낸것.push(sts);
    };
    await noteD1Usage(0, 6_000, 어느날, 보내기 as never);
    await noteD1Usage(0, 6_000, 어느날, 보내기 as never);

    expect(보낸것).toHaveLength(1);
    expect(보낸것[0][0].args[2]).toBe(12_000);
    expect(peekPending().reads).toBe(0);
  });
});

describe("적는 일이 세는 일을 망치지 않는다", () => {
  it("적는 왕복이 쓴 행은 다음 번으로 넘긴다", async () => {
    // 카운터를 적는 그 쓰기도 예산을 쓴다. 또 세러 들어가면 끝이 없고, 버리면 덜 센다
    let 깊이 = 0;
    const 보내기 = async () => {
      깊이 += 1;
      expect(깊이).toBeLessThan(3);
      // 카운터 문장 자신이 2행을 썼다고 알려 온다
      await noteD1Usage(2, 0, 어느날, 보내기);
    };

    await noteD1Usage(5, 0, 어느날, 보내기);

    expect(깊이).toBe(1);
    expect(peekPending().writes).toBe(2);
  });

  it("못 적었으면 되돌려 놓는다", async () => {
    await noteD1Usage(7, 0, 어느날, async () => {
      throw new Error("D1 실패");
    });

    expect(peekPending().writes).toBe(7);
  });

  it("예외를 올리지 않는다", async () => {
    await expect(
      noteD1Usage(1, 0, 어느날, async () => {
        throw new Error("D1 실패");
      }),
    ).resolves.toBeUndefined();
  });
});

describe("무엇을 적나", () => {
  it("배치와 같은 이름·같은 한도로 적는다", () => {
    const sts = usageStatements({ writes: 3, reads: 4 }, 어느날);
    expect(sts.map((s) => s.args[0])).toEqual([D1_WRITES_NAME, D1_READS_NAME]);
    expect(sts[0].args[3]).toBe(D1_DAILY_WRITE_LIMIT);
    expect(sts[1].args[3]).toBe(D1_DAILY_READ_LIMIT);
  });

  it("0 인 쪽은 적지 않는다", () => {
    // 0 을 적으면 쓰기 한 번을 공짜로 버린다
    expect(usageStatements({ writes: 0, reads: 9 }, 어느날).map((s) => s.args[0])).toEqual([D1_READS_NAME]);
    expect(usageStatements({ writes: 9, reads: 0 }, 어느날).map((s) => s.args[0])).toEqual([D1_WRITES_NAME]);
  });

  it("한국 날짜가 아니라 UTC 날짜로 센다", () => {
    // D1 한도는 자정 UTC 에 풀린다. 23:40 UTC 는 한국에서 다음 날 08:40 이다
    expect(utcToday(어느날)).toBe("2026-09-22");
    expect(usageStatements({ writes: 1, reads: 0 }, 어느날)[0].args[1]).toBe("2026-09-22");
  });

  it("모아 둔 몫만으로 ok 라고 적지 않는다", () => {
    // 더한 뒤의 수를 모르므로 "괜찮다" 고 말할 수 없다. 모르는 것을 ok 라고 부르지 않는다
    const sts = usageStatements({ writes: 3, reads: 0 }, 어느날);
    expect(sts[0].args[5]).toBe("unknown");
  });

  it("모아 둔 몫만으로도 한도를 넘었으면 그렇게 적는다", () => {
    const sts = usageStatements({ writes: D1_DAILY_WRITE_LIMIT, reads: 0 }, 어느날);
    expect(sts[0].args[5]).toBe("blocked");
  });
});

describe("d1Batch 가 실제로 센다", () => {
  /**
   * **부르는 곳이 없으면 모듈 하나를 더 만든 것일 뿐이다.** 위 검사들은 전부
   * `noteD1Usage` 를 직접 불러서 본다 — `d1.ts` 에서 그 호출을 지워도 하나도 안 깨진다.
   * 25.123 에서 `FREE_DB_BYTES` 가 정확히 그 상태였다(정의만 있고 부르는 곳이 없었다).
   * 여기서는 **가짜 그물(fetch)** 을 깔고 `d1Batch` 를 불러 카운터 문장이 나가는지 본다.
   */
  it("한 번 다녀오면 카운터 문장이 뒤따라 나간다", async () => {
    const { vi } = await import("vitest");
    const 보낸본문: Array<Record<string, unknown>> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        보낸본문.push(JSON.parse(String(init?.body ?? "{}")));
        return new Response(
          JSON.stringify({ success: true, result: [{ success: true, results: [], meta: { rows_written: 4, rows_read: 9 } }] }),
          { status: 200 },
        );
      }),
    );
    for (const [k, v] of Object.entries({ D1_ACCOUNT_ID: "a", D1_DATABASE_ID: "b", D1_API_TOKEN: "c" })) {
      vi.stubEnv(k, v);
    }
    resetPending();

    const { d1Batch } = await import("@/lib/d1");
    await d1Batch([{ sql: "SELECT 1" }]);

    // 첫 왕복은 본 질의, 두 번째가 카운터다
    expect(보낸본문).toHaveLength(2);
    const 카운터 = 보낸본문[1];
    const 문장들 = JSON.stringify(카운터);
    expect(문장들).toContain("api_usage");
    expect(문장들).toContain(D1_WRITES_NAME);
    expect(문장들).toContain(D1_READS_NAME);

    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    resetPending();
  });
});

describe("적는 일이 읽기까지 비싸게 만들지 않는다", () => {
  /**
   * **내가 만든 고장이다** (docs/infra.md 25.133).
   *
   * 카운터를 적는 문장도 행을 쓰고, 그 몫은 다음 번으로 넘어온다(위 「적는 왕복이 쓴 행은
   * 다음 번으로 넘긴다」). 그런데 문턱을 **모아 둔 수**로만 봤더니, 첫 쓰기 뒤에는
   * `pending.writes` 가 영영 0 이 되지 않아 **읽기만 하는 질의까지 매번 카운터 왕복을
   * 하나 더** 만들었다. 화면 한 번에 D1 왕복이 두 배가 된다.
   *
   * 위의 검사 열두 개가 하나도 못 잡았다. 전부 `resetPending()` 에서 **깨끗하게 시작**해
   * 한 번만 불렀기 때문이다 — "쓰고 나서 읽는다" 는 **이어지는** 상황을 아무도 안 봤다.
   */
  it("쓰기 한 번 뒤에 읽기만 하면 카운터 왕복이 없다", async () => {
    const 왕복: number[] = [];
    const 보내기 = async () => {
      왕복.push(1);
      // 실제 D1 은 카운터 문장이 쓴 행 수를 돌려준다. 그 몫이 다음 번으로 넘어온다
      await noteD1Usage(2, 0, 어느날, 보내기);
    };

    await noteD1Usage(3, 0, 어느날, 보내기); // 진짜 쓰기 한 번
    expect(왕복).toHaveLength(1);
    expect(peekPending().writes).toBe(2); // 카운터 자신의 몫이 넘어왔다

    for (let i = 0; i < 6; i++) await noteD1Usage(0, 500, 어느날, 보내기); // 화면 조회 여섯 번

    expect(왕복, "읽기만 했는데 카운터를 적었다").toHaveLength(1);
    expect(peekPending()).toEqual({ writes: 2, reads: 3_000 });
  });

  it("넘어온 몫은 버리지 않고 다음 쓰기에 얹는다", async () => {
    const 보낸것: Array<Array<{ args: unknown[] }>> = [];
    let 깊이 = 0;
    const 보내기 = async (sts: Array<{ args: unknown[] }>) => {
      보낸것.push(sts);
      if (++깊이 < 3) await noteD1Usage(2, 0, 어느날, 보내기 as never);
    };

    await noteD1Usage(3, 0, 어느날, 보내기 as never); // 3행 → 적는다
    await noteD1Usage(5, 0, 어느날, 보내기 as never); // 5행 + 넘어온 2행 = 7

    expect(보낸것).toHaveLength(2);
    expect(보낸것[0][0].args[2]).toBe(3);
    expect(보낸것[1][0].args[2], "넘어온 2행을 잃었다").toBe(7);
  });

  it("읽기는 문턱을 넘으면 쓰기가 없어도 적는다", async () => {
    const 왕복: number[] = [];
    const 보내기 = async () => {
      왕복.push(1);
    };

    await noteD1Usage(0, READ_FLUSH_ROWS, 어느날, 보내기);

    expect(왕복).toHaveLength(1);
  });
});

describe("웹의 Turso 읽기도 월 카운터에 더한다 (docs/infra.md 25.891)", () => {
  /**
   * 예전에는 "웹의 Turso 경로는 훑은 행 수를 주지 않는다" 며 세지 않았는데, 같은 경로를 쓰는 배치는
   * `result.rows_read` 로 이달 5,545만 행을 세고 있었다. 웹 몫이 빠져 월 5억 게이지가 배치만 봤다.
   */
  it("문턱 아래는 모았다가, 넘으면 turso_reads 의 그달 행에 한 번 적는다", async () => {
    resetTursoPending();
    const sent: Array<{ sql: string; args: unknown[] }> = [];
    const send = async (sts: Array<{ sql: string; args: unknown[] }>) => {
      sent.push(...sts);
    };
    const now = new Date("2026-10-02T06:00:00Z");
    await noteTursoReads(READ_FLUSH_ROWS - 1, now, send);
    expect(sent).toHaveLength(0);
    await noteTursoReads(5, now, send);
    expect(sent).toHaveLength(1);
    expect(sent[0].sql).toContain("'month'");
    expect(sent[0].args.slice(0, 3)).toEqual([TURSO_READS_NAME, "2026-10", READ_FLUSH_ROWS + 4]);
    expect(peekTursoPending()).toBe(0);
  });

  it("못 적으면 되돌려 놓는다 — 버리면 실제보다 적게 보인다", async () => {
    resetTursoPending();
    await noteTursoReads(READ_FLUSH_ROWS, new Date(), async () => {
      throw new Error("x");
    });
    expect(peekTursoPending()).toBe(READ_FLUSH_ROWS);
  });

  it("배치와 같은 이름·창을 쓴다", () => {
    expect(TURSO_READS_NAME).toBe("turso_reads");
    expect(USAGE_ADD_MONTH).not.toBe(USAGE_ADD);
    expect(USAGE_ADD_MONTH).toContain("VALUES (?, 'month',");
  });

  it("Turso 응답의 rows_read 를 읽는다", () => {
    const src = readFileSync(join(process.cwd(), "lib/db.ts"), "utf-8");
    expect(src).toMatch(/raw\.rows_read \?\? raw\.stat\?\.rows_read/);
    expect(src).toContain("noteTursoReads(read");
  });
});
