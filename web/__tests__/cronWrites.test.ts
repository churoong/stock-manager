import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * **크론 경로가 무엇을 쓰는가 — import 를 타고 본다** (docs/infra.md 25.191).
 *
 * 크론 경로들은 "무엇을 쓰는가" 를 문서와 머리말에 **닫는 단정**으로 적어 두었다.
 *
 * * `docs/intraday.md`: "장중 경로는 읽고 `alerts` 만 쓴다"
 * * `app/api/cron/health/route.ts`: "쓰기 대상은 health_alerts 와 cron_heartbeats **뿐이다**"
 *
 * 둘 다 사실이 아니었다. 두 경로 다 `watchTursoReturn()` 을 불러 `settings` 에 복귀
 * 표시를 쓴다(25.12). 그 쓰기는 **일부러 넣은 것**이고 백업에서도 사유와 함께 빠져
 * 있다(25.156) — 틀린 것은 **단정과 그 단정을 지킨다는 그물**이었다.
 *
 * 그물이 왜 못 봤나. 둘 다 **파일 이름을 손으로 적은 목록**을 훑는다. 장중 쪽 검사는
 * 자기 주석에 이렇게까지 적어 두었다.
 *
 * > 경로가 부르는 파일을 다 훑지 않으면 이 검사가 조용히 헐거워진다.
 *
 * 그리고 그대로 헐거워져 있었다 — `lib/tursoWatch.ts` 가 목록에 없다.
 *
 * 여기서는 **import 를 타고** 닿는 파일을 모은다. 새 도우미를 부르면 저절로 따라온다.
 * DB 층(`@/lib/db`·`@/lib/d1`)에서 멈춘다 — 거기 쓰기는 모든 경로가 공유하는 배관이고,
 * 세면 모든 경로가 모든 표를 쓴다고 나온다.
 */

const 웹 = process.cwd();
const 크론 = join(웹, "app", "api", "cron");

/** DB 배관. 여기서 멈춘다 — 모든 경로가 지나므로 세면 뜻이 없다 */
const 멈출_모듈 = new Set(["@/lib/db", "@/lib/d1"]);

/** 경로마다 **써도 되는 표**와 왜인지 */
const 써도_되는_표: Record<string, Record<string, string>> = {
  intraday: {
    alerts: "이 경로가 하는 일 자체다 (docs/intraday.md)",
    cron_heartbeats: "우리가 뭘 했는지의 기록. 판단 자료가 아니다 (25.60)",
    api_usage: "DART 를 직접 부르므로 한도 카운터에 센다 (25.105, CLAUDE.md 비용 규칙)",
    settings: "Turso 복귀 표시 한 줄(`db_return_requested_at`). 10분마다 본다 (25.12)." +
      " 사용자 설정이 아니라 운영 표시라 백업에서 사유와 함께 뺀다 (25.156)",
  },
  health: {
    health_alerts: "이 경로가 하는 일 자체다 (docs/health.md 1장)",
    cron_heartbeats: "감시가 실제로 돌고 있는지의 기록",
    settings: "Turso 복귀 표시. 뉴스 크론이 10분마다 보지만 그 크론이 멈춰도 매시 한 번은 본다 (25.12)",
  },
  news: {
    news: "받은 기사. 제목·URL·발행시각·점수만 저장한다 (CLAUDE.md 뉴스 규칙)",
    news_fetch_log: "언제 무엇을 몇 건 받았는지의 기록. 판단 자료가 아니다",
    cron_heartbeats: "우리가 뭘 했는지의 기록. 판단 자료가 아니다 (25.60)",
    settings: "Turso 복귀 표시 한 줄. 10분마다 본다 (25.12)",
  },
  "news-kr": {
    // news_fetch_log 는 없다. 국내 경로는 종목별 수집 기록을 남기지 않는다 —
    // 25.195 전까지 여기 적혀 있었던 것은 `lib/news.ts` 의 상수를 가져오다 딸려 온 허용이었다
    news: "받은 기사. 제목·URL·발행시각·점수만 저장한다 (CLAUDE.md 뉴스 규칙)",
    cron_heartbeats: "우리가 뭘 했는지의 기록. 판단 자료가 아니다 (25.60)",
  },
};

/**
 * 쓰기 문장이 있지만 **그 경로가 부르지는 않는** 파일과 사유.
 *
 * 상수 하나 때문에 import 한 모듈까지 세면 거짓 양성이 된다. 다만 사유를 적게 해서
 * "안 부른다" 가 판단으로 남게 한다 — 나중에 진짜 부르면 이 줄이 거짓이 된다.
 */
const 상수만_가져오는_파일: Record<string, Record<string, string>> = {
  intraday: {
    "lib/health.ts": "`MAX_SESSION_GAP_DAYS` 하나만 가져온다 (25.162)." +
      " 무응답 알림을 쓰는 것은 감시 경로이지 장중 경로가 아니다",
  },
  news: {
    "lib/health.ts": "`FRESHNESS`·문턱 상수만 본다. `health_alerts` 는 감시 경로가 쓴다",
  },
  "news-kr": {
    "lib/health.ts": "위와 같다",
    "lib/news.ts": "`FEED_TIMEOUT_MS`·`FEED_USER_AGENT` 상수만 가져온다. 기사 저장은 `lib/newsKr.ts` 의" +
      " `NEWS_INSERT_KR` 로 하고, `news_fetch_log` 는 미국 경로만 쓴다",
  },
};

function 모듈_파일(mod: string): string | null {
  const 상대 = `${mod.replace("@/", "")}.ts`;
  try {
    readFileSync(join(웹, 상대), "utf-8");
    return 상대;
  } catch {
    return null;
  }
}

/** 그 경로에서 import 를 타고 닿는 파일들 (DB 층에서 멈춘다) */
function 닿는_파일(이름: string): string[] {
  const 시작 = `app/api/cron/${이름}/route.ts`;
  const 본것 = new Set<string>();
  const 대기 = [시작];
  while (대기.length) {
    const 파일 = 대기.pop()!;
    if (본것.has(파일)) continue;
    본것.add(파일);
    const 글 = readFileSync(join(웹, 파일), "utf-8");
    for (const m of 글.matchAll(/from "(@\/[\w/-]+)"/g)) {
      if (멈출_모듈.has(m[1])) continue;
      const 다음 = 모듈_파일(m[1]);
      if (다음) 대기.push(다음);
    }
  }
  return [...본것].sort();
}

/** 그 파일의 쓰기 대상 표. `ON CONFLICT … DO UPDATE SET` 의 UPDATE 는 표 이름이 아니다 */
function 쓰는_표(파일: string): string[] {
  const 글 = readFileSync(join(웹, 파일), "utf-8");
  const 나온것 = new Set<string>();
  for (const m of 글.matchAll(/\b(?:INSERT\s+INTO|(?<!DO\s)UPDATE|DELETE\s+FROM)\s+([a-z_]+)/gi)) {
    나온것.add(m[1].toLowerCase());
  }
  return [...나온것].sort();
}

const 경로들 = readdirSync(크론, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name)
  .sort();

describe("크론 경로의 쓰기 대상", () => {
  it("경로를 실제로 찾아 냈다", () => {
    // 목록이 비면 아래가 0건으로 통과한다 (25.188)
    expect(경로들.length).toBeGreaterThanOrEqual(4);
    expect(경로들).toContain("intraday");
  });

  it("새 크론 경로는 목록에 적어야 한다", () => {
    // **반대 방향.** 경로를 만들고 여기 안 적으면 그 경로는 그물 밖에서 돈다
    const 빠진것 = 경로들.filter((이름) => !써도_되는_표[이름]);
    expect(빠진것, "`써도_되는_표` 에 이 경로가 무엇을 써도 되는지 적어라").toEqual([]);
  });

  it("없는 경로의 목록이 남아 있지 않다", () => {
    expect(Object.keys(써도_되는_표).filter((이름) => !경로들.includes(이름))).toEqual([]);
  });

  it.each(경로들)("%s 가 import 를 타고 닿는 파일을 찾아 냈다", (이름) => {
    // 훑기가 route.ts 하나만 찾으면 이 파일의 뜻이 사라진다
    expect(닿는_파일(이름).length).toBeGreaterThanOrEqual(3);
  });

  it.each(경로들)("%s 는 적어 둔 표만 쓴다", (이름) => {
    const 허용 = 써도_되는_표[이름] ?? {};
    const 면제 = 상수만_가져오는_파일[이름] ?? {};
    const 어긴것: string[] = [];

    for (const 파일 of 닿는_파일(이름)) {
      if (면제[파일]) continue;
      for (const 표 of 쓰는_표(파일)) {
        if (!(표 in 허용)) 어긴것.push(`${파일} → ${표}`);
      }
    }

    expect(
      어긴것,
      `${이름} 경로가 적어 두지 않은 표에 쓴다.\n` +
        "정말 써야 하면 `써도_되는_표` 에 **왜인지**와 함께 적고, 경로 머리말과 문서의 단정도 고쳐라.\n" +
        "그 파일을 부르지 않는다면 `상수만_가져오는_파일` 에 사유를 적어라",
    ).toEqual([]);
  });

  it.each(경로들)("%s 의 사유가 비어 있지 않다", (이름) => {
    const 짧은것 = Object.entries(써도_되는_표[이름] ?? {})
      .filter(([, 사유]) => 사유.trim().length < 15)
      .map(([표]) => 표);
    expect(짧은것).toEqual([]);
  });

  it.each(경로들)("%s 의 면제 목록이 낡지 않았다", (이름) => {
    const 닿는것 = new Set(닿는_파일(이름));
    const 없는것 = Object.keys(상수만_가져오는_파일[이름] ?? {}).filter((f) => !닿는것.has(f));
    expect(없는것, "더는 import 하지 않는 파일의 면제가 남아 있다").toEqual([]);
  });

  it.each(경로들)("%s 의 머리말이 쓰는 표를 다 말한다", (이름) => {
    // **25.195.** 25.191 은 이 그물을 치고 docs/intraday.md 와 health 머리말을 고쳤지만,
    // intraday·news 머리말의 "…만 쓴다"/"…뿐이다" 는 그대로 남았다. 위 검사는 **코드**가
    // 허용 목록 안인지만 보고, 사람이 읽는 **단정**은 안 봤다
    const 글 = readFileSync(join(크론, 이름, "route.ts"), "utf-8");
    const 머리말 = 글.match(/\/\*\*([\s\S]*?)\*\/\s*export const dynamic/)?.[1];
    expect(머리말, "route.ts 에서 `export const dynamic` 앞 머리말을 못 찾았다").toBeTruthy();

    const 빠진것 = Object.keys(써도_되는_표[이름] ?? {}).filter((표) => !new RegExp(`\\b${표}\\b`).test(머리말!));
    expect(빠진것, `${이름} 머리말이 이 표를 쓴다고 말하지 않는다. 머리말을 고쳐라`).toEqual([]);
  });

  it("점수·신호·매매·보유는 어느 크론도 못 쓴다", () => {
    // **이것이 지키려던 진짜 규칙이다** (CLAUDE.md: 장중 알림은 스코어를 바꾸지 않는다)
    const 금지 = ["scores", "signals", "trades", "positions", "factors", "sell_flags", "portfolio_values"];
    for (const 이름 of 경로들) {
      for (const 표 of Object.keys(써도_되는_표[이름] ?? {})) {
        expect(금지, `${이름} 이 ${표} 를 쓴다고 적혀 있다`).not.toContain(표);
      }
    }
  });
});
