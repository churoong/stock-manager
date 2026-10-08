import { describe, expect, it, vi } from "vitest";
// 장중 감시가 켜졌을 때의 동작을 본다 — 운영 기본값은 꺼짐 (docs/infra.md 25.1026)
vi.mock("@/lib/intradaySwitch", () => ({ INTRADAY_ENABLED: true, INTRADAY_OFF_NOTE: "꺼짐" }));
import { CRON_EXPECTED, cronVerdict, intradayVerdict, type CronExpect } from "@/lib/health";

/**
 * 바깥 크론이 **아직 부르고 있나** (docs/infra.md 25.141).
 *
 * **있었던 일.** `/status` 의 "크론 호출" 구역은 시각·결과·오늘 호출 수를 적기만 했다.
 * `health` 행은 마이그레이션이 넣은 씨앗 그대로 `1970-01-01 · never` 였는데(25.19),
 * 화면에서는 회색 작은 글씨 한 줄이었다. 그 줄의 뜻은 **"무응답 감시가 아예 안 돈다"** 다 —
 * 배치가 멈춰도 알림이 안 온다는 뜻이고, 이 앱에서 가장 나쁜 한 가지다.
 *
 * "한 번도 없음" 은 전부터 말하고 있었다. 못 보던 것은 **불리다가 멈춘 것**이다.
 */

const 지금 = new Date("2026-09-23T06:00:00.000Z");

function 분전(n: number): string {
  return new Date(지금.getTime() - n * 60_000).toISOString();
}

const 감시 = (called_at: string, outcome = "checked") => ({ job: "health", market: "ALL", called_at, outcome });

describe("멈췄나", () => {
  it("제때 불렸으면 초록", () => {
    expect(cronVerdict(감시(분전(30)), 지금).tone).toBe("ok");
  });

  it("주기의 세 배를 넘으면 노랑", () => {
    expect(cronVerdict(감시(분전(60 * 3 + 10)), 지금).tone).toBe("warn");
  });

  it("여섯 배를 넘으면 빨강 — 멈춘 것이다", () => {
    const v = cronVerdict(감시(분전(60 * 24)), 지금);
    expect(v.tone).toBe("bad");
    expect(v.why).toContain("cron-job.org");
  });

  it("한 번도 안 불렸으면 빨강", () => {
    const v = cronVerdict(감시("1970-01-01T00:00:00Z", "never"), 지금);
    expect(v.tone).toBe("bad");
    expect(v.text).toBe("한 번도 안 불렸다");
  });

  it("무엇을 잃는지 함께 말한다", () => {
    // "늦음" 만 적으면 사람은 그것이 얼마나 나쁜지 모른다
    expect(cronVerdict(감시(분전(60 * 24)), 지금).why).toContain("아침 리포트");
  });
});

// 미국 뉴스가 매시로 바뀌어(2026-10-02, docs/infra.md 25.880) 운영 표에 한 시간보다 짧은 상시 크론이 없다.
// 규칙은 남겨 두므로 시험용 표로 잰다
const 빠른표: CronExpect[] = [
  { job: "fast", market: "ALL", label: "시험 1분 크론", everyMinutes: 1, when: "always", stakes: "시험" },
];
const 빠른 = (분: number) =>
  cronVerdict({ job: "fast", market: "ALL", called_at: 분전(분), outcome: "checked" }, 지금, 빠른표);

describe("잣대의 굵기에 말투를 맞춘다", () => {
  /**
   * **같은 날 고친 것** (docs/infra.md 25.141 덧). 처음에는 전부 `sinceText` 를 썼는데,
   * 그 함수는 한 시간 미만을 "1시간 이내" 로 뭉친다. 1분 크론이 20분을 걸러 **노랗게**
   * 떴는데 글자는 "1시간 이내" 였다 — 색과 글자가 딴말을 하면 사람은 글자를 믿는다.
   */
  it("1분 크론은 분으로 말한다", () => {
    const v = 빠른(23);
    expect(v.text).toBe("23분 전");
    expect(v.tone).toBe("warn");
  });

  it("막 다녀왔으면 '방금'", () => {
    expect(빠른(0).text).toBe("방금");
  });

  it("시간 단위 크론은 그대로 시간으로 말한다", () => {
    expect(cronVerdict(감시(분전(60 * 5)), 지금).text).toBe("5시간 전");
  });

  it("분으로 말하는 것도 오래되면 시간으로 돌아간다", () => {
    // "300분 전" 은 읽기 나쁘다. 두 시간을 넘으면 굵은 말로 바꾼다
    expect(빠른(300).text).toBe("5시간 전");
  });
});

describe("빠른 크론을 제 잣대로 잰다", () => {
  it("1분 크론은 3분이 아니라 최소 15분으로 본다", () => {
    // 세 배(3분)로 재면 화면을 열 때마다 빨갛다. 거짓 경보가 쌓이면 아무도 안 읽는다
    expect(빠른(10).tone).toBe("ok");
    expect(빠른(20).tone).toBe("warn");
    expect(빠른(60).tone).toBe("bad");
  });

  it("미국 뉴스는 매시라 두 시간은 멀쩡하고 여섯 시간을 넘으면 멈춘 것이다", () => {
    const 뉴스 = (분: number) => cronVerdict({ job: "news", market: "US", called_at: 분전(분), outcome: "checked" }, 지금);
    expect(뉴스(120).tone).toBe("ok");
    expect(뉴스(200).tone).toBe("warn");
    expect(뉴스(400).tone).toBe("bad");
  });

  it("국내 뉴스는 1시간짜리라 20분은 멀쩡하다", () => {
    // **같은 `news` 이름인데 시장마다 주기가 다르다.** 한 잣대로 재면 둘 중 하나가 거짓말한다
    const v = cronVerdict({ job: "news", market: "KR", called_at: 분전(20), outcome: "checked" }, 지금);
    expect(v.tone).toBe("ok");
  });
});

describe("판정하지 않기로 한 것", () => {
  it("장중 크론은 여기서 재지 않는다", () => {
    // 장 밖이 훨씬 길다. "17시간 전" 은 밤일 뿐인데 빨갛게 칠하면 매일 아침 거짓 경보다
    const v = cronVerdict({ job: "intraday", market: "KR", called_at: 분전(60 * 17), outcome: "skipped:장 밖" }, 지금);
    expect(v.tone).toBe("mute");
    expect(v.why).toContain("[알림]");
  });

  it("모르는 크론은 지어내지 않는다", () => {
    expect(cronVerdict({ job: "없는것", market: "KR", called_at: 분전(5), outcome: "checked" }, 지금).tone).toBe("mute");
  });

  it("시각을 못 읽으면 조용하다", () => {
    expect(cronVerdict(감시("이상한값"), 지금).tone).toBe("mute");
  });
});

describe("표가 제 모양인가", () => {
  it("읽어 냈다", () => {
    expect(CRON_EXPECTED.length).toBeGreaterThanOrEqual(5);
  });

  it("모든 줄이 근거를 들고 있다", () => {
    for (const c of CRON_EXPECTED) {
      expect(c.everyMinutes, `${c.job}/${c.market}`).toBeGreaterThan(0);
      expect(c.stakes.length, `${c.job}/${c.market}`).toBeGreaterThan(20);
    }
  });

  it("근거 글에 docs 파일 경로를 적지 않는다", () => {
    // 툴팁으로 화면에 나간다. 폰에서 저장소 파일을 열 수 없다
    for (const c of CRON_EXPECTED) {
      const v = cronVerdict({ job: c.job, market: c.market, called_at: 분전(1), outcome: "checked" }, 지금);
      expect(v.why, c.job).not.toMatch(/docs\/[a-z_]+\.md/);
    }
  });
});

describe("장중 크론은 [알림] 화면이 판정한다", () => {
  /**
   * **미룬 곳이 미룰 곳이 아니었다** (docs/infra.md 25.144).
   *
   * `cronVerdict` 는 장중 크론을 "[알림] 화면이 본다" 며 넘겼는데, 그 화면도 시각을
   * 회색으로 적기만 했다. `docs/intraday.md` 7장도 같은 약속을 하고 있었다 —
   * "장중인데 오래 비어 있으면 확인한다". **누가 '장중' 과 '오래' 를 판정하나.**
   */
  const 없음 = undefined;

  it("장 밖에는 판정하지 않는다", () => {
    const v = intradayVerdict("KR", { called_at: 분전(600), outcome: "skipped:장 밖" }, false, 지금);
    expect(v.tone).toBe("mute");
  });

  it("정규장인데 호출 기록이 아예 없으면 빨강", () => {
    const v = intradayVerdict("KR", 없음, true, 지금);
    expect(v.tone).toBe("bad");
    expect(v.why).toContain("손절선");
  });

  it("정규장에 제때 불렸으면 초록", () => {
    expect(intradayVerdict("KR", { called_at: 분전(4), outcome: "checked" }, true, 지금).tone).toBe("ok");
  });

  it("세 주기를 넘으면 노랑, 여섯 주기를 넘으면 빨강", () => {
    expect(intradayVerdict("KR", { called_at: 분전(20), outcome: "checked" }, true, 지금).tone).toBe("warn");
    expect(intradayVerdict("KR", { called_at: 분전(40), outcome: "checked" }, true, 지금).tone).toBe("bad");
  });

  it("주기를 새로 만들지 않고 CRON_EXPECTED 에서 가져온다", () => {
    // **잣대가 둘이 되면 화면 둘이 서로 다른 말을 한다** (25.0 "한 규칙이 두 곳에 있다")
    const 주기 = CRON_EXPECTED.find((c) => c.job === "intraday" && c.market === "KR")?.everyMinutes;
    expect(주기).toBe(5);
    expect(intradayVerdict("KR", { called_at: 분전(1), outcome: "checked" }, true, 지금).why).toContain(`${주기}분마다`);
  });

  it("무엇을 잃는지 말한다", () => {
    expect(intradayVerdict("KR", { called_at: 분전(40), outcome: "checked" }, true, 지금).why).toContain("알림");
  });

  it("시각을 못 읽으면 조용하다", () => {
    expect(intradayVerdict("KR", { called_at: "이상한값", outcome: "checked" }, true, 지금).tone).toBe("mute");
  });
});

describe("화면이 실제로 그 판정을 쓴다", () => {
  /**
   * **부르는 곳이 없으면 함수를 하나 더 만든 것일 뿐이다** (25.123 이 고친 그 모양).
   * 그리고 '정규장인가' 를 스스로 다시 계산하면 규칙이 두 곳에 생긴다.
   */
  it("AlertCenter 가 intradayVerdict 와 activeSession 을 쓴다", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const 글 = readFileSync(join(process.cwd(), "components", "AlertCenter.tsx"), "utf-8");

    expect(글).toContain("intradayVerdict(");
    expect(글, "정규장 판정을 여기서 다시 짜면 안 된다").toContain("activeSession(");
    expect(글, "닫는 시각 없이는 장중인지 알 수 없다").toContain("next_close");
  });

  it("호출 기록을 못 읽었을 때는 판정하지 않는다", async () => {
    // 못 읽은 것을 "안 불렸다" 로 적지 않는다 (docs/infra.md 25.74)
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const 글 = readFileSync(join(process.cwd(), "components", "AlertCenter.tsx"), "utf-8");

    expect(글).toContain("!beatsError");
  });
});

/**
 * **불린 것과 해낸 것은 다르다** (docs/infra.md 25.183).
 *
 * `cronVerdict` 가 `outcome` 을 보는 곳은 `"never"` 하나뿐이었다. 그래서 크론이
 * 제시간에 불리기만 하면 **매 호출이 실패해도 초록**이었다 — `cron/news` 는 예외를
 * 잡아 `outcome: "error"` 로 심장박동을 남기고, 판정은 `called_at` 만 보고 "1분 전"
 * 이라며 `ok` 를 돌려줬다. 뉴스가 일주일 내내 안 들어와도 /status 는 멀쩡했다.
 *
 * 25.0 「잃고서 초록으로 알린다」 — 그것도 **그 고장을 알려 주라고 만든 화면**에서.
 */
describe("실패한 호출을 초록으로 말하지 않는다", () => {
  const 지금 = new Date("2026-09-23T04:00:00Z");
  const 방금 = "2026-09-23T03:59:00Z";

  it("방금 불렸어도 마지막 호출이 실패했으면 빨강", () => {
    const v = cronVerdict({ job: "news", market: "KR", called_at: 방금, outcome: "error" }, 지금);

    expect(v.tone).toBe("bad");
    expect(v.why).toContain("마지막 호출이 실패");
    expect(v.why, "무엇을 봐야 하는지 말해야 한다").toContain("detail");
  });

  it("성공한 호출은 그대로 초록", () => {
    expect(cronVerdict({ job: "news", market: "KR", called_at: 방금, outcome: "checked" }, 지금).tone).toBe("ok");
  });

  it("건너뛴 것은 정상이되 왜인지 적는다", () => {
    const v = cronVerdict({ job: "news", market: "KR", called_at: 방금, outcome: "skipped:대상 없음" }, 지금);

    expect(v.tone, "대상이 없어 건너뛴 것은 고장이 아니다").toBe("ok");
    expect(v.why, "며칠씩 이어지면 그것 자체가 자취다").toContain("대상 없음");
  });

  it("한 번도 안 불린 것과 실패한 것을 다르게 말한다", () => {
    const 한번도 = cronVerdict({ job: "news", market: "KR", called_at: 방금, outcome: "never" }, 지금);
    const 실패 = cronVerdict({ job: "news", market: "KR", called_at: 방금, outcome: "error" }, 지금);

    expect(한번도.tone).toBe("bad");
    expect(실패.tone).toBe("bad");
    expect(한번도.text).not.toBe(실패.text);
  });

  it("장중 크론도 실패는 빨갛다", () => {
    // 장 밖에서는 판정을 미루지만(`mute`), **실패는 미룰 일이 아니다**
    const v = cronVerdict({ job: "intraday", market: "KR", called_at: 방금, outcome: "error" }, 지금);

    expect(v.tone).toBe("bad");
  });
});
