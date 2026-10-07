import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { MAX_SESSION_GAP_DAYS } from "@/lib/health";
import { FRESHNESS } from "@/lib/health";
import { MONITOR_STALE_DAYS, bundleMessage, staleTargetsNote } from "@/lib/intraday";

/**
 * **감시 목록이 언제 만들어진 것인지 아무도 안 봤다** (docs/infra.md 25.162).
 *
 * `monitor_targets.built_at` 은 스키마에 `NOT NULL` 로 있고, `/api/alerts` 가
 * `MAX(built_at)` 으로 꺼내고, 화면이 상태에 담기까지 했다. **그리고 거기서 끝났다** —
 * 그리지도 않고 판정하지도 않았다.
 *
 * 왜 이것이 다른 신선도와 다른가: **장중 경로는 Actions 밖에서 돈다.**
 * cron-job.org → Vercel 이라 일일 배치가 무료 분 소진으로 멈춰도 계속 돈다.
 * 그래서 3주 전 신호의 권장 매수 구간으로 "진입했습니다" 가 나간다.
 *
 * 25.136(포트폴리오 평가)·25.161(매도 플래그)과 **같은 모양**이다 —
 * 날짜를 적어 놓고 판정에서 안 본다.
 */

const NOW = new Date("2026-09-23T04:00:00Z");
const 날 = (n: number) => new Date(NOW.getTime() - n * 86_400_000).toISOString();

describe("묵은 감시 목록", () => {
  it("문턱을 넘으면 말한다", () => {
    const note = staleTargetsNote([{ market: "KR", built_at: 날(20) }], NOW);

    expect(note).toContain("국내");
    expect(note).toContain("20일 전");
    expect(note).toContain("옛 매수 구간");
  });

  it("정상 휴장으로는 안 걸린다", () => {
    // 잣대는 거래일 사이 최장 간격이다. 연휴가 경보를 만들면 아무도 안 읽는다
    expect(staleTargetsNote([{ market: "KR", built_at: 날(MONITOR_STALE_DAYS) }], NOW)).toBeNull();
    expect(staleTargetsNote([{ market: "KR", built_at: 날(MONITOR_STALE_DAYS + 1) }], NOW)).not.toBeNull();
  });

  it("배치와 같은 잣대를 쓴다", () => {
    // 두 곳이 다른 수를 쓰면 화면과 알림이 서로 다른 말을 한다
    expect(MONITOR_STALE_DAYS).toBe(MAX_SESSION_GAP_DAYS);
  });

  it("묵은 시장만 고르고 이름을 적는다", () => {
    const note = staleTargetsNote(
      [
        { market: "US", built_at: 날(30) },
        { market: "KR", built_at: 날(1) },
      ],
      NOW,
    );

    expect(note).toContain("미국");
    expect(note, "안 묵은 쪽까지 적으면 무엇이 문제인지 흐려진다").not.toContain("국내");
  });

  it("둘 다 묵으면 둘 다 적는다", () => {
    const note = staleTargetsNote(
      [
        { market: "US", built_at: 날(30) },
        { market: "KR", built_at: 날(20) },
      ],
      NOW,
    );

    expect(note).toContain("국내");
    expect(note).toContain("미국");
  });

  it("목록이 없거나 날짜를 모르면 조용하다", () => {
    expect(staleTargetsNote([], NOW)).toBeNull();
    expect(staleTargetsNote([{ market: "KR", built_at: null }], NOW)).toBeNull();
    expect(staleTargetsNote([{ market: "KR", built_at: "깨진값" }], NOW)).toBeNull();
  });
});

describe("알림 묶음", () => {
  const 알림 = [{ market: "KR", message: "삼성전자: 권장 매수 구간 진입", created_at: NOW.toISOString() }];

  it("말을 머리에 한 번만 붙인다", () => {
    const note = staleTargetsNote([{ market: "KR", built_at: 날(20) }], NOW)!;
    const text = bundleMessage(알림, NOW, note);
    const lines = text.split("\n");

    expect(lines[0]).toContain("장중 알림 1건");
    expect(lines[1]).toBe(note);
    // 줄마다 붙이면 시끄러워 아무도 안 읽는다
    expect(text.split("감시 목록이").length - 1).toBe(1);
  });

  it("안 묵었으면 아무 말도 안 붙는다", () => {
    const text = bundleMessage(알림, NOW, null);

    expect(text).not.toContain("감시 목록이");
    expect(text.split("\n")[1]).toContain("삼성전자");
  });

  it("예전처럼 두 인자로 불러도 된다", () => {
    expect(bundleMessage(알림, NOW)).not.toContain("감시 목록이");
  });
});

describe("경로와 화면이 그 판정을 쓴다", () => {
  it("장중 크론이 보내기 전에 본다", () => {
    const 글 = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf-8");

    expect(글).toContain("MAX(built_at)");
    expect(글).toContain("staleTargetsNote(built, now)");
    // 말 한 줄 때문에 알림이 막히면 안 된다
    expect(글).toContain("as Built[]");
  });

  it("알림 센터가 목록 날짜를 그린다", () => {
    const 글 = readFileSync(join(process.cwd(), "components", "AlertCenter.tsx"), "utf-8");

    // 날짜는 한국 날짜로 바꿔 그린다 (25.240) — 앞 10자는 UTC 날짜라 아침에 하루 이르다
    expect(글, "값을 실어 오고도 안 그리고 있었다").toContain("목록 {userDateOf(t.built_at)}");
    expect(글).toContain("staleTargetsNote(");
  });

  it("신선도 칸에도 있다", () => {
    const keys = FRESHNESS.map((f) => f.key);

    expect(keys).toContain("monitor_kr");
    expect(keys).toContain("monitor_us");
    // 미국은 D1 임시 운영 중 쉰다(25.14). 빨갛게 칠하면 거짓 경보다
    expect(FRESHNESS.find((f) => f.key === "monitor_us")?.restsOnD1).toBe(true);
    expect(FRESHNESS.find((f) => f.key === "monitor_kr")?.every).toBe("session");
  });
});

describe("시각 열을 한국 날짜로 읽는다 (docs/infra.md 25.240)", () => {
  it("08:27 KST(전날 23:27 UTC)에 만든 목록은 만든 날짜다", async () => {
    const { userDateOf } = await import("@/lib/market");
    expect(userDateOf("2026-09-27T23:27:00.123456+00:00")).toBe("2026-09-28");
    expect(userDateOf("이상함")).toBe("이상함");
    expect(userDateOf(null)).toBe("");
  });

  it("신선도 SQL 이 시각 열의 앞 10자를 날짜로 쓰지 않는다", () => {
    for (const row of FRESHNESS) {
      expect(row.sql, row.key).not.toMatch(/substr\(MAX\(/);
    }
  });

  it("실제 SQLite 에서 한국 날짜가 나온다", async () => {
    const { DatabaseSync } = await import("node:sqlite");
    const db = new DatabaseSync(":memory:");
    db.exec("CREATE TABLE monitor_targets (market TEXT, built_at TEXT)");
    db.exec("INSERT INTO monitor_targets VALUES ('KR', '2026-09-27T23:27:00.5+00:00')");
    const sql = FRESHNESS.find((f) => f.key === "monitor_kr")!.sql;
    expect((db.prepare(sql).get() as { v: string }).v).toBe("2026-09-28");
  });
});

describe("조용시간 해제 설정 (docs/infra.md 25.254)", () => {
  it("해제가 07:00 보다 늦으면 실제로는 09:00 에 나간다고 말한다", async () => {
    const { releaseNote } = await import("@/lib/intraday");
    expect(releaseNote("07:00")).toContain("07:00 해제 호출에");
    expect(releaseNote("06:30")).toContain("07:00 해제 호출에");
    expect(releaseNote("08:00")).toContain("09:00");
  });

  it("시작과 해제가 같으면 저장하지 않는다", async () => {
    const { settingsSchema } = await import("@/lib/settings");
    const 조용 = settingsSchema.shape.quiet_hours;
    expect(조용.safeParse({ enabled: true, start: "00:00", end: "00:00", deliver_on_release: true }).success).toBe(false);
    expect(조용.safeParse({ enabled: true, start: "00:00", end: "07:00", deliver_on_release: true }).success).toBe(true);
  });
});

describe("적립 종목 경고의 상한은 설정값이다 (docs/infra.md 25.255)", () => {
  it("설정을 5%·20% 로 바꾸면 그 숫자를 말한다", async () => {
    const { sectorCapWarning, ACCUMULATION_WARNINGS } = await import("@/lib/accumulation");
    expect(sectorCapWarning(5, 20)).toContain("한 종목 5%·업종 20%");
    expect(ACCUMULATION_WARNINGS.join(" ")).not.toMatch(/10%|30%/);
  });
});

describe("화면의 시각은 KST 다 (docs/infra.md 25.266)", () => {
  it("08:27 KST 에 만든 것은 08:27 로 보인다", async () => {
    const { userTimeOf } = await import("@/lib/market");
    expect(userTimeOf("2026-09-25T23:27:10.5+00:00")).toBe("2026-09-26 08:27");
  });

  it("UTC 를 그대로 잘라 보여 주는 화면이 없다", async () => {
    const { readdirSync, readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const 부품 = join(process.cwd(), "components");
    const 걸린 = readdirSync(부품).filter((f) => /\.slice\(0, 16\)\.replace\("T", " "\)|\.replace\("T", " "\)\.slice\(0, 16\)/.test(readFileSync(join(부품, f), "utf-8")));
    expect(걸린, "userTimeOf 를 써라").toEqual([]);
  });
});
