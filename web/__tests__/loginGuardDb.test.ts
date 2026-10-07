import { readFileSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 로그인 잠금이 **실제로 막는가** (web/lib/loginGuard.ts).
 *
 * **왜 이제서야 생겼나.** 2026-09-20 에 웹 덮임을 처음 재 봤더니(25.32 에서 `npm ci` 가
 * 9초면 된다는 것을 알고 나서) `loginGuard.ts` 가 **37%** 였다. 기존 테스트
 * (`loginGuard.test.ts`)는 주소 파싱과 **문턱의 산수**만 본다 — "5번/15분이면 1만 가지를
 * 훑는 데 500시간" 같은 계산이다. 정작 **막는 함수인 `checkGuard` 는 한 번도 불린 적이
 * 없었다.** 숫자가 옳다는 것은 증명돼 있고 문이 닫힌다는 것은 증명돼 있지 않았다.
 *
 * 이 자리가 왜 중요한가: `lib/auth.ts` 가 비밀번호 최소 길이를 4자로 낮추면서 근거로
 * 적은 것이 이 잠금이다 — "둘 중 하나만 있으면 안 된다". 잠금이 조용히 새면 네 자리
 * 숫자가 공개 주소에 그대로 걸린다.
 *
 * **진짜 SQL 을 돌린다.** `execute` 를 정해진 값 돌려주는 가짜로 바꾸면 창(window) 계산과
 * 주소 격리를 검증할 수 없다 — 그게 틀리기 쉬운 부분이다. 대신 `node:sqlite` 메모리 DB 에
 * `migrations/0003_login_attempts.sql` 을 그대로 올려 같은 질의를 돌린다.
 */

const 상태 = vi.hoisted(() => ({
  db: null as InstanceType<typeof import("node:sqlite").DatabaseSync> | null,
  오류: null as Error | null,
  /** 읽기(SELECT)만 실패 — 쓰기는 되고 읽기 한도만 넘긴 D1 (25.850) */
  읽기오류: null as Error | null,
}));

vi.mock("@/lib/db", () => ({
  execute: async (sql: string, args: unknown[] = []) => {
    if (상태.오류) throw 상태.오류;
    if (상태.읽기오류 && /^\s*SELECT/i.test(sql)) throw 상태.읽기오류;
    const 문장 = 상태.db!.prepare(sql);
    if (/^\s*SELECT/i.test(sql)) {
      const 객체들 = 문장.all(...(args as never[])) as Record<
        string,
        unknown
      >[];
      const columns = 객체들.length ? Object.keys(객체들[0]) : [];
      return {
        columns,
        rows: 객체들.map((o) => columns.map((c) => o[c])),
        affectedRows: 0,
      };
    }
    const 결과 = 문장.run(...(args as never[]));
    return { columns: [], rows: [], affectedRows: Number(결과.changes) };
  },
  rowsToObjects: (rs: { columns: string[]; rows: unknown[][] }) =>
    rs.rows.map((row) =>
      Object.fromEntries(rs.columns.map((c, i) => [c, row[i]])),
    ),
}));

const {
  GLOBAL_MAX_FAILURES,
  GLOBAL_WINDOW_MINUTES,
  PER_IP_MAX_FAILURES,
  PER_IP_WINDOW_MINUTES,
  alertGlobalLock,
  checkGuard,
  recordAttempt,
  reserveAttempt,
} = await import("@/lib/loginGuard");

const 나 = "내주소해시";
const 남 = "남의주소해시";

function 분전(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

/** 실패(또는 성공) 기록을 직접 심는다. 시각을 마음대로 놓아야 창을 시험할 수 있다 */
function 심기(ipHash: string, 몇건: number, 분: number, success = 0): void {
  for (let i = 0; i < 몇건; i += 1) {
    상태.db!.prepare(
      "INSERT INTO login_attempts (ip_hash, attempted_at, success) VALUES (?, ?, ?)",
    ).run(ipHash, 분전(분), success);
  }
}

function 실패수(ipHash?: string): number {
  const sql = ipHash
    ? "SELECT COUNT(*) AS n FROM login_attempts WHERE success = 0 AND ip_hash = ?"
    : "SELECT COUNT(*) AS n FROM login_attempts WHERE success = 0";
  const row = (
    ipHash ? 상태.db!.prepare(sql).get(ipHash) : 상태.db!.prepare(sql).get()
  ) as {
    n: number;
  };
  return row.n;
}

beforeEach(async () => {
  (await import("@/lib/loginGuard")).memoryReset(); // 잠금 기억(25.914)도 함께 비운다
  상태.오류 = null;
  상태.읽기오류 = null;
  상태.db = new DatabaseSync(":memory:");
  // 손으로 다시 적지 않는다. 마이그레이션이 바뀌면 이 테스트도 같이 바뀌어야 한다
  const 스키마 = readFileSync(
    join(process.cwd(), "..", "migrations", "0003_login_attempts.sql"),
    "utf-8",
  );
  상태.db.exec(스키마);
  // 잠금 알림이 "한 창에 한 번" 을 기억하는 곳. 여기서도 손으로 옮겨 적지 않는다
  const 초기 = readFileSync(
    join(process.cwd(), "..", "migrations", "0001_initial.sql"),
    "utf-8",
  );
  const settings표 = 초기.match(
    /CREATE TABLE IF NOT EXISTS settings \([^;]*\);/,
  )?.[0];
  expect(
    settings표,
    "0001_initial.sql 에서 settings 표를 찾지 못했다",
  ).toBeTruthy();
  상태.db.exec(settings표!);
});

afterEach(() => {
  상태.db?.close();
});

describe("막을 때 막는다", () => {
  it("기록이 없으면 통과한다", async () => {
    const 판정 = await checkGuard(나);

    expect(판정).toEqual({ allowed: true, recentFailures: 0 });
  });

  it(`실패 ${PER_IP_MAX_FAILURES - 1}건까지는 통과하고 횟수를 알려준다`, async () => {
    심기(나, PER_IP_MAX_FAILURES - 1, 1);

    // 횟수는 실패 지연(failureDelayMs)의 입력이다. 틀리면 지연이 안 늘어난다
    expect(await checkGuard(나)).toEqual({
      allowed: true,
      recentFailures: PER_IP_MAX_FAILURES - 1,
    });
  });

  it(`실패 ${PER_IP_MAX_FAILURES}건이면 그 주소를 막는다`, async () => {
    심기(나, PER_IP_MAX_FAILURES, 1);

    const 판정 = await checkGuard(나);

    expect(판정.allowed).toBe(false);
    expect(판정.allowed === false && 판정.reason).toBe("ip");
  });

  it("주소를 바꿔 가며 들어와도 전체 잠금이 막는다", async () => {
    // 주소마다 문턱 아래로 흩어 놓는다. 주소별 잠금만 있으면 전부 통과한다
    const 주소수 = Math.ceil(GLOBAL_MAX_FAILURES / (PER_IP_MAX_FAILURES - 1));
    for (let i = 0; i < 주소수; i += 1)
      심기(`주소${i}`, PER_IP_MAX_FAILURES - 1, 1);
    expect(실패수()).toBeGreaterThanOrEqual(GLOBAL_MAX_FAILURES);

    const 판정 = await checkGuard("아직깨끗한주소");

    expect(판정.allowed).toBe(false);
    expect(판정.allowed === false && 판정.reason).toBe("global");
  });

  it("둘 다 걸리면 전체 잠금이라고 말한다", async () => {
    // 사람에게 보내는 알림이 달라진다. "나만 잠김" 과 "누가 두드리고 있다" 는 다른 일이다
    심기(나, GLOBAL_MAX_FAILURES, 1);

    const 판정 = await checkGuard(나);

    expect(판정.allowed === false && 판정.reason).toBe("global");
  });

  it("언제 풀리는지 알려주고, 그 값이 창을 넘지 않는다", async () => {
    심기(나, PER_IP_MAX_FAILURES, 5); // 5분 전에 몰아서 실패했다

    const 판정 = await checkGuard(나);

    expect(판정.allowed).toBe(false);
    if (판정.allowed) return;
    // 가장 오래된 실패가 창에서 빠지면 풀린다 → 남은 10분 언저리
    expect(판정.retryAfterSeconds).toBeGreaterThan(0);
    expect(판정.retryAfterSeconds).toBeLessThanOrEqual(
      (PER_IP_WINDOW_MINUTES - 5 + 1) * 60,
    );
  });
});

describe("막지 말아야 할 때 막지 않는다", () => {
  it("창 밖의 실패는 세지 않는다", async () => {
    // 이게 틀리면 한 번 잠긴 사람이 영영 못 들어온다
    심기(나, PER_IP_MAX_FAILURES * 3, PER_IP_WINDOW_MINUTES + 1);

    expect((await checkGuard(나)).allowed).toBe(true);
  });

  it("성공한 시도는 실패로 세지 않는다", async () => {
    심기(나, PER_IP_MAX_FAILURES * 2, 1, 1);

    expect(await checkGuard(나)).toEqual({ allowed: true, recentFailures: 0 });
  });

  it("남이 틀린 것으로 내가 잠기지 않는다", async () => {
    심기(남, PER_IP_MAX_FAILURES, 1);

    // 주소별 잠금이 ip_hash 조건을 잃으면 여기서 걸린다
    expect(await checkGuard(나)).toEqual({ allowed: true, recentFailures: 0 });
  });
});

describe("DB 를 못 읽으면 통과시킨다 — 의도된 선택이다", () => {
  it("읽기가 실패해도 로그인을 아예 막지는 않는다", async () => {
    // **이것은 fail-open 이다.** 마이그레이션 전이나 DB 가 잠시 안 될 때 본인마저
    // 못 들어오는 것이 더 나쁘다고 보고 고른 쪽이다(lib/loginGuard.ts 주석).
    // 대가: DB 가 죽어 있는 동안에는 네 자리 비밀번호에 시도 제한이 없다.
    // 이 테스트는 그 선택이 **사고가 아니라 선택임**을 못 박는다. 바꾸려면 여기부터 고친다
    상태.오류 = new Error("no such table: login_attempts");

    expect(await checkGuard(나)).toEqual({ allowed: true, recentFailures: 0 });
  });

  it("기록이 실패해도 예외를 밖으로 던지지 않는다", async () => {
    상태.오류 = new Error("DB 없음");

    await expect(recordAttempt(나, false)).resolves.toBeUndefined();
  });
});

describe("기록", () => {
  it("실패를 남긴다", async () => {
    await recordAttempt(나, false);

    expect(실패수(나)).toBe(1);
  });

  it("들어오면 그 주소의 실패 기록을 지운다", async () => {
    심기(나, PER_IP_MAX_FAILURES, 1);
    expect((await checkGuard(나)).allowed).toBe(false);

    await recordAttempt(나, true);

    // 본인이 제대로 들어왔으면 다음 번에 깨끗한 상태에서 시작해야 한다
    expect((await checkGuard(나)).allowed).toBe(true);
    expect(실패수(나)).toBe(0);
  });

  it("들어와도 남의 실패 기록까지 지우지는 않는다", async () => {
    // 지우면 공격자가 본인 로그인 한 번으로 전체 잠금을 풀 수 있다
    심기(남, PER_IP_MAX_FAILURES, 1);

    await recordAttempt(나, true);

    expect(실패수(남)).toBe(PER_IP_MAX_FAILURES);
  });

  it("하루가 지난 기록은 치운다", async () => {
    심기(남, 3, 25 * 60);
    심기(남, 2, 60);

    await recordAttempt(나, false);

    expect(실패수(남)).toBe(2);
  });
});

describe("전체 잠금 알림", () => {
  const 원래fetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = 원래fetch;
    delete process.env.TELEGRAM_BOT_TOKEN;
    delete process.env.TELEGRAM_CHAT_ID;
  });

  it("설정이 없으면 아무 데도 부르지 않는다", async () => {
    const 부른것: string[] = [];
    globalThis.fetch = (async (url: string) => {
      부른것.push(url);
      return new Response("{}");
    }) as typeof fetch;

    await alertGlobalLock();

    expect(부른것).toEqual([]);
  });

  /** 보낸 본문들을 모은다 */
  function 받아적기(): string[] {
    const 본문들: string[] = [];
    process.env.TELEGRAM_BOT_TOKEN = "토큰";
    process.env.TELEGRAM_CHAT_ID = "1";
    globalThis.fetch = (async (_url: string, init: RequestInit) => {
      본문들.push(JSON.parse(String(init.body)).text);
      return new Response('{"ok":true}');
    }) as typeof fetch;
    return 본문들;
  }

  it("보내는 문장이 무슨 일인지와 할 일을 말하고, 끝에 고지가 붙는다", async () => {
    const 본문들 = 받아적기();

    await alertGlobalLock();

    const 본문 = 본문들[0];
    expect(본문).toContain("전체 잠금");
    expect(본문).toContain("비밀번호를 바꾸는 것을 권합니다");
    // 텔레그램에는 고지를 붙이지 않는다 (2026-10-02 사용자 지시, 25.878)
    expect(본문).not.toContain("투자 판단의 책임은 본인에게 있습니다.");
  });

  it("문턱 숫자를 문장에 박아 두지 않는다", async () => {
    // 상수를 바꿨는데 문장이 옛 숫자를 말하면 알림이 거짓말이 된다
    const 본문들 = 받아적기();

    await alertGlobalLock();

    expect(본문들[0]).toContain(String(GLOBAL_MAX_FAILURES));
    expect(본문들[0]).toContain(String(GLOBAL_WINDOW_MINUTES));
  });

  it("잠긴 동안 계속 두드려도 한 번만 보낸다", async () => {
    // 잠기면 시도를 기록하지 않으므로 뒤따르는 요청이 전부 같은 "잠금" 판정을 받는다.
    // 그대로 두면 공격자가 두드리는 수만큼 폰에 알림이 쏟아진다 — 알림이 공격을 돕는다
    const 본문들 = 받아적기();

    for (let i = 0; i < 50; i += 1) await alertGlobalLock();

    expect(본문들.length).toBe(1);
  });

  it("창이 지나면 다시 보낸다", async () => {
    // 한 번 보내고 영영 입을 닫으면, 이어지는 공격을 모르게 된다
    const 본문들 = 받아적기();
    const 지금 = new Date();

    await alertGlobalLock(지금);
    await alertGlobalLock(
      new Date(지금.getTime() + (GLOBAL_WINDOW_MINUTES - 1) * 60_000),
    );
    await alertGlobalLock(
      new Date(지금.getTime() + (GLOBAL_WINDOW_MINUTES + 1) * 60_000),
    );

    expect(본문들.length).toBe(2);
  });

  it("장부를 적지 못하면 보내는 쪽을 고른다", async () => {
    // settings 표가 없거나 DB 가 안 될 때. 조용한 쪽이 더 위험하다
    const 본문들 = 받아적기();
    상태.오류 = new Error("no such table: settings");

    const { memoryReset } = await import("@/lib/loginGuard");
    memoryReset();
    await alertGlobalLock();

    expect(본문들.length).toBe(1);
    memoryReset();
  });

  it("장부를 못 적는 날에도 한 창에 한 번만 보낸다 — 메모리 쿨다운 (25.846)", async () => {
    // 예전에는 DB 가 안 되면 늘 보내서, 메모리 전체 잠금 중 잠긴 요청마다 텔레그램이 나갔다
    const { memoryReset, MEMORY_GLOBAL_MAX_FAILURES } = await import("@/lib/loginGuard");
    memoryReset();
    const 본문들 = 받아적기();
    상태.오류 = new Error("D1 daily limit exceeded");
    const 지금 = new Date("2026-10-01T06:00:00Z");

    for (let i = 0; i < 20; i += 1) await alertGlobalLock(new Date(지금.getTime() + i * 1000), true);
    expect(본문들.length).toBe(1);
    // 걸린 기준을 말한다 — DB 기준(20)이 아니라 메모리 기준
    expect(String(본문들[0])).toContain(`${MEMORY_GLOBAL_MAX_FAILURES}번을 넘어`);
    expect(String(본문들[0])).toContain("메모리");

    await alertGlobalLock(new Date(지금.getTime() + GLOBAL_WINDOW_MINUTES * 60_000 + 1), true);
    expect(본문들.length).toBe(2);
    memoryReset();
  });

  it("텔레그램이 거절하면 장부를 되돌려 다음 잠금 요청이 다시 보낸다 (25.931)", async () => {
    process.env.TELEGRAM_BOT_TOKEN = "토큰";
    process.env.TELEGRAM_CHAT_ID = "1\n"; // 값 끝 줄바꿈 — 공용 발송기가 다듬는다
    const 받은: Array<{ chat_id: string }> = [];
    let 응답 = '{"ok":false,"description":"Too Many Requests"}';
    globalThis.fetch = (async (_url: string, init: RequestInit) => {
      받은.push(JSON.parse(String(init.body)));
      return new Response(응답, { status: 응답.includes("false") ? 429 : 200 });
    }) as typeof fetch;
    const 지금 = new Date("2026-10-04T01:00:00Z");

    await alertGlobalLock(지금);
    응답 = '{"ok":true}';
    await alertGlobalLock(new Date(지금.getTime() + 1000));
    await alertGlobalLock(new Date(지금.getTime() + 2000));

    expect(받은.length).toBe(2); // 거절 뒤 한 번 더, 성공 뒤에는 창이 닫힌다
    expect(받은.every((b) => b.chat_id === "1")).toBe(true);
  });

  it("텔레그램이 안 되어도 던지지 않는다", async () => {
    process.env.TELEGRAM_BOT_TOKEN = "토큰";
    process.env.TELEGRAM_CHAT_ID = "1";
    globalThis.fetch = (async () => {
      throw new Error("네트워크 없음");
    }) as typeof fetch;

    // 알림이 실패했다고 로그인 응답이 500 이 되면 안 된다
    await expect(alertGlobalLock()).resolves.toBeUndefined();
  });

  it("설정이 없으면 장부도 건드리지 않는다", async () => {
    // 보낼 수도 없는데 "보냈다" 고 적어 두면, 설정을 채운 뒤 첫 알림을 놓친다
    받아적기();
    delete process.env.TELEGRAM_BOT_TOKEN;

    await alertGlobalLock();

    const 남은것 = 상태
      .db!.prepare("SELECT COUNT(*) AS n FROM settings")
      .get() as { n: number };
    expect(남은것.n).toBe(0);
  });
});

describe("동시 요청으로 시도 제한을 우회하지 못한다 (docs/infra.md 25.393)", () => {
  it("먼저 적고 세므로, 한꺼번에 들어온 요청은 문턱 넘게 통과하지 못한다", async () => {
    const 결과 = await Promise.all(
      Array.from({ length: 20 }, () => reserveAttempt(나)),
    );
    expect(결과.filter((v) => v.allowed).length).toBeLessThanOrEqual(
      PER_IP_MAX_FAILURES,
    );
    expect(실패수(나)).toBe(20); // 막힌 시도도 실패로 남는다
  });

  it("이미 문턱만큼 틀렸으면 들어오자마자 막는다", async () => {
    심기(나, PER_IP_MAX_FAILURES, 1);
    const v = await reserveAttempt(나);
    expect(v.allowed).toBe(false);
  });

  it("안내하는 대기 시간은 잠금이 실제로 풀리는 때다 — 가장 오래된 실패가 아니라 (docs/infra.md 25.596)", async () => {
    // 14분 전 1건 + 방금 몰린 5건: 가장 오래된 것이 빠지는 1분 뒤에도 5건이 남아 잠겨 있다
    심기(나, 1, 14);
    심기(나, PER_IP_MAX_FAILURES, 0);
    const v = await reserveAttempt(나);
    expect(v.allowed).toBe(false);
    expect(v.allowed === false && v.retryAfterSeconds).toBeGreaterThan((PER_IP_WINDOW_MINUTES - 1) * 60);
  });

  it("잠긴 뒤 하나씩 오는 요청은 실패로 적지 않는다 — 잠금이 스스로 늘지 않는다 (docs/infra.md 25.594)", async () => {
    심기(나, PER_IP_MAX_FAILURES, 1);
    for (let i = 0; i < 10; i += 1) expect((await reserveAttempt(나)).allowed).toBe(false);
    expect(실패수(나)).toBe(PER_IP_MAX_FAILURES);
  });

  it("표가 없으면 통과, 그 밖의 DB 실패는 메모리로 더 엄격하게 센다 (25.393 → 25.843)", async () => {
    const { memoryReset, MEMORY_PER_IP_MAX_FAILURES } = await import("@/lib/loginGuard");
    memoryReset();
    상태.오류 = new Error("no such table: login_attempts");
    expect((await reserveAttempt(나)).allowed).toBe(true);
    // D1 읽기 한도 날 — 예전에는 본인도 18시간 못 들어왔다. 이제 주소별 3번까지는 시도할 수 있고 그 뒤는 막는다
    상태.오류 = new Error("D1 daily limit exceeded");
    for (let i = 0; i < MEMORY_PER_IP_MAX_FAILURES; i++) expect((await reserveAttempt(나)).allowed).toBe(true);
    const v = await reserveAttempt(나);
    expect(v.allowed === false && v.reason).toBe("ip");
    expect((await checkGuard(나)).allowed).toBe(false); // 옛 판정 함수는 그대로(부르는 곳 없음)
    memoryReset();
  });

  it("쓰기는 되고 읽기만 실패해도 메모리로 세고, 맞히면 메모리 실패를 지운다 (25.843 경로, 25.850)", async () => {
    const { memoryReset, MEMORY_PER_IP_MAX_FAILURES } = await import("@/lib/loginGuard");
    memoryReset();
    상태.읽기오류 = new Error("D1 daily read limit exceeded");
    for (let i = 0; i < MEMORY_PER_IP_MAX_FAILURES; i++) expect((await reserveAttempt(나)).allowed).toBe(true);
    const v = await reserveAttempt(나);
    expect(v.allowed === false && v.reason).toBe("ip");
    expect(v.allowed === false && v.memory).toBe(true);
    // 쓰기는 됐다 — DB 에도 실패가 남는다(읽기가 돌아오면 원래 기준으로 센다)
    상태.읽기오류 = null;
    expect(실패수(나)).toBeGreaterThanOrEqual(MEMORY_PER_IP_MAX_FAILURES);
    상태.읽기오류 = new Error("D1 daily read limit exceeded");
    // 잠긴 뒤 두드리는 요청은 DB 에 쓰지 않는다 — 쓰기 한도를 태우지 않고, 읽기가 돌아온 아침 DB 잠금으로 이어지지 않는다 (25.851)
    상태.읽기오류 = null;
    const 잠긴뒤 = 실패수(나);
    상태.읽기오류 = new Error("D1 daily read limit exceeded");
    for (let i = 0; i < 10; i += 1) expect((await reserveAttempt(나)).allowed).toBe(false);
    상태.읽기오류 = null;
    expect(실패수(나)).toBe(잠긴뒤);
    상태.읽기오류 = new Error("D1 daily read limit exceeded");
    // 본인이 맞혔으면 메모리로 센 실패도 지운다 — 안 지우면 다음 로그인이 15분 막힌다
    await recordAttempt(나, true);
    expect((await reserveAttempt(나)).allowed).toBe(true);
    memoryReset();
  });
});


describe("잠긴 뒤 두드려도 DB 를 읽지 않는다 (25.914, 감사)", () => {
  it("DB 가 잠갔다고 판정한 주소는 그 시각까지 메모리로 돌려보낸다", async () => {
    심기(나, PER_IP_MAX_FAILURES, 1);
    const 첫 = await reserveAttempt(나);
    expect(첫).toMatchObject({ allowed: false, reason: "ip" });
    // 이제 DB 를 부르면 깨진다 — 그래도 같은 잠금이 나와야 한다(메모리 대체 판정이 아니라)
    상태.오류 = new Error("읽으면 안 된다");
    const 다음 = await reserveAttempt(나);
    expect(다음).toMatchObject({ allowed: false, reason: "ip" });
    expect((다음 as { memory?: boolean }).memory).toBeUndefined();
    // 다른 주소는 기억에 걸리지 않는다 — DB 로 가서(깨져) 메모리 대체 판정으로 통과한다
    expect((await reserveAttempt(남)).allowed).toBe(true);
  });
});
