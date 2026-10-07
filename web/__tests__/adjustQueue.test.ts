import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ADJUST_QUEUE_STALE_DAYS, adjustQueueNote } from "@/lib/health";

/**
 * **어긋난 줄 알면서 아무 데도 말하지 않았다** (docs/infra.md 25.165).
 *
 * 일일 배치가 미국 수정주가의 어긋남을 찾아 `adjust_refresh_queue` 에 적는다. 그 사실이
 * 남는 곳은 `log.info` **하나뿐**이었다 — 그리고 우리는 Actions 로그를 못 읽는다(25.17).
 *
 * 그 사이가 문제다. 대기 중인 종목의 **옛 `adj_close` 는 지금 기준과 어긋나 있고**,
 * 모멘텀(12-1·52주 고점)·성과지표(CAGR·MDD·샤프)·백테스트가 그 값을 읽는다.
 * 토요일 재수집이 예산 부족으로 미루면(`status: skipped`) 그대로 한 주가 더 간다.
 */

const NOW = new Date("2026-09-23T04:00:00Z");
const 날 = (n: number) => new Date(NOW.getTime() - n * 86_400_000).toISOString();

describe("재수집 대기열 한 줄", () => {
  it("비어 있으면 아무 말도 안 한다", () => {
    // **빈 것이 정상이다.** 늘 한 줄을 띄우면 그 줄을 아무도 안 읽는다
    expect(adjustQueueNote(null, NOW)).toBeNull();
    expect(adjustQueueNote({ n: 0, oldest: null }, NOW)).toBeNull();
  });

  it("쌓여 있으면 몇 종목인지 말한다", () => {
    const q = adjustQueueNote({ n: 12, oldest: 날(3) }, NOW);

    expect(q?.text).toContain("12종목");
    expect(q?.text).toContain("3일 전");
  });

  it("한 주 안쪽은 경고가 아니다", () => {
    // 월요일에 감지해 토요일에 비우는 것이 정상 흐름이다
    expect(adjustQueueNote({ n: 12, oldest: 날(3) }, NOW)?.late).toBe(false);
    expect(adjustQueueNote({ n: 1, oldest: 날(ADJUST_QUEUE_STALE_DAYS) }, NOW)?.late).toBe(false);
  });

  it("예정된 재수집을 두 번 지나치면 경고한다", () => {
    const q = adjustQueueNote({ n: 4, oldest: 날(ADJUST_QUEUE_STALE_DAYS + 1) }, NOW);

    expect(q?.late).toBe(true);
    expect(q?.text, "왜 나쁜지를 말해야 한다").toContain("어긋나 있습니다");
  });

  it("감지 시각을 모르면 수만 말한다", () => {
    const q = adjustQueueNote({ n: 5, oldest: null }, NOW);

    expect(q?.text).toContain("5종목");
    expect(q?.late, "모르는 것을 나쁘다고 단정하지 않는다").toBe(false);
  });
});

describe("경로와 화면이 그것을 쓴다", () => {
  it("상태 경로가 대기열을 읽는다", () => {
    const 글 = readFileSync(join(process.cwd(), "app", "api", "status", "route.ts"), "utf-8");

    expect(글).toContain("ADJUST_QUEUE");
    expect(글).toContain("adjust_queue:");
    // 못 읽은 것과 빈 것을 가린다 (25.163)
    expect(글).toContain('읽되_이유를_남긴다("adjust_queue")');
  });

  it("화면이 그 줄을 그린다", () => {
    const 글 = readFileSync(join(process.cwd(), "components", "StatusView.tsx"), "utf-8");

    expect(글).toContain("adjustQueueNote(");
    expect(글).toContain("adjust_queue");
  });

  it("배치와 같은 문턱을 쓴다", () => {
    // 두 곳이 다른 수를 쓰면 화면과 텔레그램이 서로 다른 말을 한다
    const 글 = readFileSync(
      join(process.cwd(), "..", "batch", "jobs", "refresh_us_adjusted.py"),
      "utf-8",
    );

    expect(글).toContain(`MAX_PENDING_DAYS = ${ADJUST_QUEUE_STALE_DAYS}`);
  });

  it("일일 배치도 같은 말을 리포트에 싣는다", () => {
    const 글 = readFileSync(join(process.cwd(), "..", "batch", "jobs", "daily.py"), "utf-8");

    expect(글, "log.info 에만 남으면 아무도 못 본다 (25.17)").toContain("pending_note(");
    expect(글).toContain("warnings.append(밀림)");
  });
});
