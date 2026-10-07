/**
 * 리포트 날짜 목록도 "보냈는지 모름" 을 말한다 (docs/infra.md 25.504, 교차검증).
 * 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { type HistoryRow, PARTIAL_SEND_MARK, REPORT_HISTORY, UNKNOWN_SEND_MARK, historySendLabel } from "@/lib/reports";

describe("날짜 목록의 발송 표시 (25.504)", () => {
  it("모름 경고가 있으면 미발송이라 단정하지 않는다", () => {
    const db = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort())
      db.exec(readFileSync(join(dir, file), "utf-8"));
    const 넣기 = db.prepare(
      "INSERT INTO daily_reports (market, trade_date, status, generated_at, sent_at, summary_text, warnings_json)"
        + " VALUES ('KR', ?, 'partial', 't', ?, 't', ?)",
    );
    넣기.run("2026-09-25", null, JSON.stringify([`텔레그램 응답 시간 초과로 ${UNKNOWN_SEND_MARK} — …`]));
    넣기.run("2026-09-24", null, "[]");
    넣기.run("2026-09-23", "2026-09-22T23:30:00+00:00", "[]");
    넣기.run("2026-09-22", "2026-09-21T23:30:00+00:00", JSON.stringify([`텔레그램 리포트 3조각 가운데 1${PARTIAL_SEND_MARK}`]));
    const rows = db.prepare(REPORT_HISTORY).all("KR") as unknown as HistoryRow[];
    expect(rows.map(historySendLabel)).toEqual(["발송 모름", "미발송", "보냄", "일부 보냄"]);
    expect(PARTIAL_SEND_MARK).toBe("조각만 보냈습니다"); // SQL 안의 글자와 같아야 한다 (25.511)
    expect(UNKNOWN_SEND_MARK).toBe("발송 여부를 알 수 없습니다"); // SQL 안의 글자와 같아야 한다
  });

  it("화면이 그 표시를 쓴다", () => {
    expect(readFileSync("components/ReportView.tsx", "utf8")).toContain("{historySendLabel(h)}");
  });
});
