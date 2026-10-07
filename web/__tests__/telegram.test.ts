import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_LEN, SAFE_LEN, sendTelegram, splitMessage } from "@/lib/telegram";
import { DISCLAIMER } from "@/lib/notice";
import { bundleMessage } from "@/lib/intraday";

/**
 * 텔레그램 발송 (웹 쪽) — 길이 제한 (docs/infra.md 25.58).
 *
 * **무엇이 잘못됐었나.** 2026-09-21 까지 웹 쪽 `sendTelegram` 은 길이를 보지 않았다.
 * 장중 알림은 밀린 것을 최대 50건 **한 통으로** 묶어 보내는데(`bundleMessage` +
 * `flushPending` 의 `LIMIT 50`), 미국 종목명이 섞이면 그 한 통이 5,600자를 넘어
 * 텔레그램이 `"message is too long"` 으로 거절한다. 경로는 **보낸 뒤에야** `sent_at` 을
 * 찍으므로 같은 50건이 대기열에 그대로 남아 5분마다 같은 실패를 되풀이한다 —
 * **스스로 낫지 않고**, 막힌 것이 텔레그램이라 막혔다고 알릴 수도 없다.
 *
 * 나누는 규칙은 `batch/notify/telegram.py` 에도 있다. 같은 표
 * (`tests/fixtures/telegram_split.json`)를 양쪽이 읽어 어긋나지 않게 한다.
 */

interface 사례 {
  이름: string;
  글: string;
  한도: number;
  조각: string[];
  줄보존: boolean;
}

const 답안 = JSON.parse(
  readFileSync(join(process.cwd(), "..", "tests", "fixtures", "telegram_split.json"), "utf-8"),
) as { 사례: 사례[] };
const 사례들 = 답안.사례;

describe("나누기 표 (batch/notify/telegram.split_message 와 같은 파일을 읽는다)", () => {
  it("표를 읽어 냈다", () => {
    // 읽기가 조용히 빈 목록을 내면 아래가 0번 돌고 전부 통과한다
    expect(사례들.length).toBeGreaterThanOrEqual(7);
    expect(new Set(사례들.map((r) => r.이름)).size).toBe(사례들.length);
  });

  it.each(사례들.map((r) => [r.이름, r] as const))("%s", (_이름, r) => {
    expect(splitMessage(r.글, r.한도)).toEqual(r.조각);
  });

  it.each(사례들.map((r) => [r.이름, r] as const))("%s — 글자를 잃지도 더하지도 않는다", (_이름, r) => {
    expect(splitMessage(r.글, r.한도).join("").replaceAll("\n", "")).toBe(r.글.replaceAll("\n", ""));
  });

  it.each(사례들.filter((r) => r.줄보존).map((r) => [r.이름, r] as const))(
    "%s — 이어 붙이면 원문이 된다",
    (_이름, r) => {
      expect(splitMessage(r.글, r.한도).join("\n")).toBe(r.글);
    },
  );

  it("표에 줄보존 사례와 아닌 사례가 둘 다 있다", () => {
    // 위 두 무리 중 한쪽이 비면 공짜로 통과한다
    expect(사례들.some((r) => r.줄보존)).toBe(true);
    expect(사례들.some((r) => !r.줄보존)).toBe(true);
  });
});

describe("길이 상수", () => {
  it("자르는 길이가 상한보다 넉넉히 작다", () => {
    // 상한을 공식 문서로 확인하지 못했다 [확인필요] — 여유가 없으면 여유가 아니다
    expect(SAFE_LEN).toBeLessThan(MAX_LEN);
    expect(MAX_LEN - SAFE_LEN).toBeGreaterThanOrEqual(100);
  });
});

/** `bundleMessage` 에 넣을 만큼 만든다. flushPending 의 LIMIT 50 이 최대치다 */
function 밀린알림(건수: number, 이름: string, 시장: "KR" | "US") {
  return Array.from({ length: 건수 }, (_, i) => ({
    id: i + 1,
    market: 시장,
    message: `${이름}: 권장 매수 구간 진입 1,234,567원 (구간 1,200,000원~1,300,000원)`,
    created_at: "2026-09-21T00:35:00.000Z",
  }));
}

describe("장중 묶음 — 고친 이유가 실재하는가", () => {
  const 이때 = new Date("2026-09-21T00:35:00.000Z");

  it("미국 종목명이 섞인 50건은 상한을 넘는다", () => {
    // watchlist 는 COALESCE(s.name_ko, s.name_en, s.ticker) 로 영문명을 쓴다
    const 글 = bundleMessage(밀린알림(50, "Taiwan Semiconductor Manufacturing Company Limited", "US"), 이때);

    expect(글.length).toBeGreaterThan(MAX_LEN);
  });

  it("국내만 50건이어도 안전선은 넘는다", () => {
    const 글 = bundleMessage(밀린알림(50, "어떤긴이름주식회사우선주", "KR"), 이때);

    expect(글.length).toBeGreaterThan(SAFE_LEN);
  });

  it("나누면 모든 조각이 상한 안에 든다", () => {
    const 글 = bundleMessage(밀린알림(50, "Taiwan Semiconductor Manufacturing Company Limited", "US"), 이때);
    const 조각들 = splitMessage(글);

    expect(조각들.length).toBeGreaterThanOrEqual(2);
    for (const 조각 of 조각들) expect(조각.length).toBeLessThanOrEqual(MAX_LEN);
  });

  it("묶음 자체는 고지를 적지 않는다 — 보내는 쪽이 붙인다", () => {
    // 2026-09-22 (docs/infra.md 25.115). 부르는 쪽마다 적으면 언젠가 빠뜨린다.
    // 실제로 무응답 알림이 빠뜨리고 있었다
    const 글 = bundleMessage(밀린알림(2, "삼성전자", "KR"), 이때);

    expect(글).toContain("자동 매매는 없습니다");
    expect(글).not.toContain(DISCLAIMER);
  });
});

describe("보내기", () => {
  const 원래 = { ...process.env };

  afterEach(() => {
    process.env.TELEGRAM_BOT_TOKEN = 원래.TELEGRAM_BOT_TOKEN;
    process.env.TELEGRAM_CHAT_ID = 원래.TELEGRAM_CHAT_ID;
  });

  type 부름기록 = [url: RequestInfo | URL, init?: RequestInit];

  function 성공하는fetch() {
    return vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 }),
    );
  }

  /** 각 호출이 실제로 보낸 text. 나눈 결과를 그대로 보냈는지 보는 데 쓴다 */
  function 보낸글(calls: 부름기록[]): string[] {
    return calls.map((c) => JSON.parse(String(c[1]?.body)).text as string);
  }

  it("짧은 글은 한 번만 부른다", async () => {
    process.env.TELEGRAM_BOT_TOKEN = "t";
    process.env.TELEGRAM_CHAT_ID = "c";
    const 부름 = 성공하는fetch();

    await sendTelegram("짧다", 부름 as unknown as typeof fetch);

    expect(부름).toHaveBeenCalledTimes(1);
  });

  it("긴 글은 나눠서 여러 번 부른다", async () => {
    process.env.TELEGRAM_BOT_TOKEN = "t";
    process.env.TELEGRAM_CHAT_ID = "c";
    const 부름 = 성공하는fetch();
    const 글 = Array.from({ length: 200 }, (_, i) => `${i}번째 줄 ${"가".repeat(40)}`).join("\n");

    await sendTelegram(글, 부름 as unknown as typeof fetch);

    expect(부름.mock.calls.length).toBeGreaterThanOrEqual(2);
    const 보낸것 = 보낸글(부름.mock.calls);
    for (const 조각 of 보낸것) expect(조각.length).toBeLessThanOrEqual(SAFE_LEN);
    expect(보낸것.join("\n")).toBe(글); // 고지는 붙이지 않는다 (2026-10-02 사용자 지시, 25.878)
  });

  it("조각 하나가 거절당하면 거기서 멈추고 알린다", async () => {
    process.env.TELEGRAM_BOT_TOKEN = "t";
    process.env.TELEGRAM_CHAT_ID = "c";
    let 횟수 = 0;
    const 부름 = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => {
      횟수 += 1;
      return new Response(
        JSON.stringify(횟수 === 1 ? { ok: true } : { ok: false, description: "Bad Request: chat not found" }),
        { status: 횟수 === 1 ? 200 : 400 },
      );
    });
    const 글 = Array.from({ length: 200 }, (_, i) => `${i}번째 줄 ${"가".repeat(40)}`).join("\n");

    await expect(sendTelegram(글, 부름 as unknown as typeof fetch)).rejects.toThrow("chat not found");
    // 뒤 조각을 계속 보내지 않는다 — 다음 호출이 처음부터 다시 보낸다
    expect(부름).toHaveBeenCalledTimes(2);
  });

  it("응답이 JSON 이 아니면 HTTP 상태로 알린다", async () => {
    process.env.TELEGRAM_BOT_TOKEN = "t";
    process.env.TELEGRAM_CHAT_ID = "c";
    const 부름 = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response("<html>502</html>", { status: 502 }));

    await expect(sendTelegram("짧다", 부름 as unknown as typeof fetch)).rejects.toThrow("502");
  });

  it("토큰이 없으면 부르지 않고 알린다", async () => {
    delete process.env.TELEGRAM_BOT_TOKEN;
    process.env.TELEGRAM_CHAT_ID = "c";
    const 부름 = 성공하는fetch();

    await expect(sendTelegram("짧다", 부름 as unknown as typeof fetch)).rejects.toThrow("TELEGRAM_BOT_TOKEN");
    expect(부름).not.toHaveBeenCalled();
  });

  it("토큰을 주소에 넣고 본문에는 넣지 않는다", async () => {
    process.env.TELEGRAM_BOT_TOKEN = "비밀토큰";
    process.env.TELEGRAM_CHAT_ID = "c";
    const 부름 = 성공하는fetch();

    await sendTelegram("짧다", 부름 as unknown as typeof fetch);

    expect(String(부름.mock.calls[0]?.[0])).toContain("bot비밀토큰");
    expect(String(부름.mock.calls[0]?.[1]?.body)).not.toContain("비밀토큰");
  });
});

/**
 * 책임 고지는 **보내기 직전에 한 번** 붙인다 (2026-09-22, docs/infra.md 25.115).
 *
 * CLAUDE.md 절대 규칙이다. 파이썬은 `notify/telegram.send` 가 한 곳에서 붙이는데
 * 웹은 그러지 않아서 같은 문장이 다섯 벌 손으로 적혀 있었고, **무응답 알림 하나는
 * 아예 안 붙이고 있었다.** 부르는 쪽마다 지키게 하면 언젠가 빠뜨린다 — 실제로 빠뜨렸다.
 */
describe("책임 고지", () => {
  const 원래 = { ...process.env };

  afterEach(() => {
    process.env.TELEGRAM_BOT_TOKEN = 원래.TELEGRAM_BOT_TOKEN;
    process.env.TELEGRAM_CHAT_ID = 원래.TELEGRAM_CHAT_ID;
  });

  function 보내기(글: string) {
    process.env.TELEGRAM_BOT_TOKEN = "t";
    process.env.TELEGRAM_CHAT_ID = "c";
    const 부름 = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    return sendTelegram(글, 부름 as unknown as typeof fetch).then(() =>
      부름.mock.calls.map((c) => JSON.parse(String(c[1]?.body ?? "{}")).text as string),
    );
  }

  // 2026-10-02 사용자 지시: "텔레그램 메시지에 '투자 판단의 책임은 본인에게 있습니다.' 이거 다 빼" (docs/infra.md 25.878).
  // 화면 푸터의 고지는 그대로다(footerOnEveryPage.test.ts)
  it("텔레그램에는 붙이지 않는다 — 리포트·무응답 알림 모두 (25.878)", async () => {
    for (const 글 of ["무응답: 국내 일일 배치가 안 돌았습니다", "[무응답] 국내 일일 배치가 2026-09-22 까지 성공 기록이 없습니다."]) {
      expect((await 보내기(글)).join("\n")).not.toContain(DISCLAIMER);
    }
  });

  it("긴 글도 나눌 때 고지를 넣지 않고 조각마다 상한을 지킨다", async () => {
    const 긴글 = Array.from({ length: 300 }, (_, i) => `${i}번 종목 추천 근거 문장`).join("\n");
    const 보낸것 = await 보내기(긴글);
    expect(보낸것.length).toBeGreaterThanOrEqual(2);
    expect(보낸것.join("\n")).not.toContain(DISCLAIMER);
    for (const 조각 of 보낸것) expect(조각.length).toBeLessThanOrEqual(SAFE_LEN);
  });
});

describe("강제로 쪼갤 때 이모지를 가르지 않는다 (docs/infra.md 25.256)", () => {
  it("서러게이트 쌍이 경계에 걸리면 한 칸 앞에서 자른다", async () => {
    const { splitMessage } = await import("@/lib/telegram");
    const 조각 = splitMessage("a".repeat(9) + "📈" + "z", 10);
    expect(조각.join("")).toBe("a".repeat(9) + "📈" + "z");
    for (const c of 조각) expect(c).not.toMatch(/[\uD800-\uDBFF]$|^[\uDC00-\uDFFF]/);
  });
});

describe("응답 시간 초과 (docs/infra.md 25.613, 감사)", () => {
  it("보냈는지 모르면 TelegramUncertainError 로 던진다 — 부르는 쪽이 다시 보내지 않게", async () => {
    const { TelegramUncertainError } = await import("@/lib/telegram");
    vi.stubEnv("TELEGRAM_BOT_TOKEN", "t");
    vi.stubEnv("TELEGRAM_CHAT_ID", "1");
    const 시간초과 = vi.fn(async () => {
      throw Object.assign(new Error("The operation was aborted due to timeout"), { name: "TimeoutError" });
    });
    await expect(sendTelegram("짧다", 시간초과 as unknown as typeof fetch)).rejects.toBeInstanceOf(TelegramUncertainError);
  });

  it("연결 실패 같은 다른 오류는 보통 실패로 던진다", async () => {
    const { TelegramUncertainError } = await import("@/lib/telegram");
    vi.stubEnv("TELEGRAM_BOT_TOKEN", "t");
    vi.stubEnv("TELEGRAM_CHAT_ID", "1");
    const 끊김 = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const 결과 = await sendTelegram("짧다", 끊김 as unknown as typeof fetch).catch((e) => e);
    expect(결과).toBeInstanceOf(TypeError);
    expect(결과).not.toBeInstanceOf(TelegramUncertainError);
  });

  it("장중·무응답 경로는 모름이어도 푼다 — 알림은 빠지는 편이 두 번 가는 편보다 나쁘다 (25.618)", () => {
    const fs = require("node:fs") as typeof import("node:fs");
    const 장중 = fs.readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(장중).toContain("const 남은 = 묶음들.slice(순번).flat();");
    const 감시 = fs.readFileSync("app/api/cron/health/route.ts", "utf8");
    expect(감시).not.toContain("TelegramUncertainError");
    expect(감시).toContain("if (잡음) {");
  });

  it("헤더 200 뒤 본문 읽기 시간 초과는 보낸 것으로 본다", async () => {
    vi.stubEnv("TELEGRAM_BOT_TOKEN", "t");
    vi.stubEnv("TELEGRAM_CHAT_ID", "1");
    const 느린본문 = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw Object.assign(new Error("timeout"), { name: "TimeoutError" });
      },
    }));
    await expect(sendTelegram("짧다", 느린본문 as unknown as typeof fetch)).resolves.toBeUndefined();
  });
});


describe("헤더 200 뒤 연결 끊김 (docs/infra.md 25.619, 교차검증)", () => {
  it("본문을 읽다 끊겨도(terminated) 보낸 것으로 본다", async () => {
    vi.stubEnv("TELEGRAM_BOT_TOKEN", "t");
    vi.stubEnv("TELEGRAM_CHAT_ID", "1");
    const 끊김 = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new TypeError("terminated");
      },
    }));
    await expect(sendTelegram("짧다", 끊김 as unknown as typeof fetch)).resolves.toBeUndefined();
  });
});
