import { NextResponse } from "next/server";
import { currentBackend, execute, ifMissingTable, ifMissingTableThen, rowsToObjects } from "@/lib/db";
import { whyNoSignals, type 재료 } from "@/lib/whyEmpty";
import { issueTexts } from "@/lib/validationErrors";
import {
  LAST_CALC_DATE,
  LAST_CALC_DATE_FALLBACK,
  OUTCOME_STATS,
  LATEST_OUTCOME_RUN,
  parseCalibration,
  type Calibration,
  PREVIOUS_AS_OF,
  PREVIOUS_AS_OF_FALLBACK,
  SIGNALS_ON,
  signalsOnArgs,
  buildQuery,
  diffSignals,
  recommendQuerySchema,
  type OutcomeStat,
  type RecommendRow,
  type SignalKeyRow,
  LAST_SIGNAL_SUCCESS,
  LATEST_SIGNAL_RUN,
  signalRunNote,
  type SignalRunRow,
} from "@/lib/recommend";

/**
 * 추천 종목 조회.
 *
 * 스크리너와 달리 사용자가 조건을 정하지 않는다. 규칙이 고른 결과를 읽는다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
/**
 * 신호가 0건인 이유를 가리는 데 쓸 재료 (docs/infra.md 25.86).
 *
 * **한 질의로 묶는다.** 읽기가 Turso 를 잠근 적이 있다(24절). 그리고 이 함수는
 * 신호가 0건인 날에만 불린다 — 정상인 날에는 한 번도 안 돈다.
 *
 * **못 읽으면 `null` 이다.** 0 으로 채우면 "없다" 가 되어 엉뚱한 진단이 나간다(25.74).
 */
async function 재료읽기(country: string): Promise<재료> {
  try {
    const rs = await execute(
      "SELECT"
      + " (SELECT COUNT(*) FROM batch_runs WHERE job_name = 'signals' AND market = ?"
      + "  AND status IN ('success', 'partial')) AS runs,"
      // 거래일 수: **그 나라 보통주 앞 20개 중 가장 긴 시세 이력**, 200행에서 멈춘다 (docs/infra.md 25.900). 예전(25.859)에는 날짜 색인을
      // 한 칸씩 걸으며 날마다 그 나라 행이 있나를 봤는데, 한 날짜 안에서는 국내 행(아침 저장)이 미국 행(밤 저장)보다 앞이라 미국 탭은
      // 날마다 국내 행 약 2,800개를 지나쳤다(25.899 의 같은 원인 — 미국 점수 걷기 334걸음에 152만 행). 지금은 (stock_id, date) 색인으로
      // 종목마다 최대 200행 — 많아야 4,000행. 판정(`whyEmpty.RISK_DAYS`)은 200 미만인가만 본다. 가격이 없으면 0
      + " (SELECT COALESCE(MAX((SELECT COUNT(*) FROM (SELECT 1 FROM prices p WHERE p.stock_id = s.id LIMIT 200))), 0)"
      + "  FROM (SELECT id FROM stocks WHERE country = ? AND status = 'active' AND asset_type = 'stock'"
      + "   ORDER BY id LIMIT 20) s) AS price_days,"
      + " (SELECT COUNT(DISTINCT f.stock_id) FROM financials f JOIN stocks s ON s.id = f.stock_id"
      + "  WHERE s.country = ?) AS financials,"
      + " (SELECT COUNT(*) FROM stocks WHERE country = ? AND sector IS NOT NULL) AS sectors",
      [country, country, country, country],
    );
    const row = rs.rows[0];
    if (!row) return { signalRuns: null, priceDays: null, financials: null, sectors: null };
    return {
      signalRuns: Number(row[0] ?? 0),
      priceDays: Number(row[1] ?? 0),
      financials: Number(row[2] ?? 0),
      sectors: Number(row[3] ?? 0),
    };
  } catch {
    return { signalRuns: null, priceDays: null, financials: null, sectors: null };
  }
}

export async function POST(request: Request) {
  let body: unknown = {};
  try {
    body = await request.json();
  } catch {
    // 본문이 없어도 기본값으로 돈다. 조건 없이 보는 것이 이 화면의 기본이다.
    body = {};
  }

  const parsed = recommendQuerySchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { errors: issueTexts(parsed.error.issues) },
      { status: 400 },
    );
  }

  try {
    // **먼저 "마지막으로 계산한 날" 을 정한다** (docs/infra.md 25.145).
    // `signals` 의 MAX 만 보면 한 건도 안 걸린 날에 어제 신호가 오늘 것처럼 나온다.
    // 판정표는 신호가 없어도 전 종목에 남으므로 둘의 MAX 가 계산이 돈 날이다.
    // 판정표 표가 아직 없는 DB 에서는 옛 잣대로 되돌아간다
    const calc = await execute(LAST_CALC_DATE, [parsed.data.country, parsed.data.country]).catch(
      ifMissingTableThen(() => execute(LAST_CALC_DATE_FALLBACK, [parsed.data.country])),
    );
    const asOf = (calc.rows[0]?.[0] as string | null) ?? null;
    const { sql, args } = buildQuery(parsed.data, asOf ?? "");

    const rs = asOf ? await execute(sql, args) : { columns: [], rows: [], affectedRows: 0 };
    const 읽은 = rowsToObjects<RecommendRow>(rs);
    // 상한보다 많으면 잘렸다 — 조용히 자르지 않는다 (docs/infra.md 25.377)
    const truncated = 읽은.length > parsed.data.limit;
    const rows = truncated ? 읽은.slice(0, parsed.data.limit) : 읽은;

    // 비어 있을 때 "추천 없음" 과 "아직 안 돌았음" 은 다르다.
    // 사용자가 원인을 알 수 있게 나눠서 알려준다.
    const notes: string[] = [];
    if (rows.length === 0) {
      const any = await execute(
        "SELECT COUNT(*) FROM signals sg JOIN stocks s ON s.id = sg.stock_id WHERE s.country = ?",
        [parsed.data.country],
      );
      const total = Number(any.rows[0]?.[0] ?? 0);
      if (total > 0) {
        notes.push("오늘 기준으로 조건을 만족한 종목이 없습니다. 조건을 만족하지 못한 것이지 오류가 아닙니다");
      } else if (parsed.data.country === "US" && (await currentBackend()) === "d1") {
        // **박아 둔 글 대신 지금 상태를 말한다** (docs/infra.md 25.249). 예전 글("유니버스에 든 종목이 없다 ·
        // SEC 재무 수집 뒤에 나온다")은 미국 재무 경로가 생긴 뒤에도 그대로였고, 미국 신호가 정말 멈춰도 같은 말을 했다.
        // D1 동안 미국을 쉬는 것은 설계다(25.11·25.14). 그 밖에는 국내와 같은 진단을 탄다
        notes.push(
          "미국 배치는 임시 DB(D1) 운영 중 쉬고 있어 신호가 없습니다 (운영 기록 25.14)."
          + " 원래 DB 로 돌아가면 다시 계산합니다",
        );
      } else {
        // **여기서만 재료를 읽는다** (docs/infra.md 25.86). 신호가 0건인 날에만 드는 비용이고,
        // 한 질의에 스칼라 서브쿼리로 묶어 왕복도 한 번이다 — Turso 가 잠긴 원인이 읽기였다(24절).
        notes.push(whyNoSignals(await 재료읽기(parsed.data.country)));
      }
    } else if (rows.every((r) => r.suggested_amount === null)) {
      notes.push(
        // 까닭을 여기서 짐작하지 않는다 — 배치가 근거표에 적었다 (docs/infra.md 25.295).
        // 예전에는 늘 "총액을 넣으라" 했는데 미국은 환율이 없어서 비는 날이 있다
        "권장 금액이 비어 있습니다. 까닭은 카드를 펼친 근거표의 '권장 금액 (참고)' 행에 있습니다"
          + " (총 투자가능금액 미설정 · 환율 없음(미국) · 최소 주문 미만)",
      );
    }

    if (truncated) {
      notes.push(`신호가 ${parsed.data.limit}건을 넘어 종합 점수 상위 ${parsed.data.limit}건만 보입니다. 기간·시장으로 좁혀 보세요`);
    }
    if (rows.some((r) => r.sector_cap_applied === 0)) {
      notes.push(
        // 이 화면(1부)의 금액은 **모든 카드가** 섹터 상한 반영 전이다 — 예전 문구는 업종이 있는 카드는 적용된 것처럼 읽혔다 (25.586, 감사)
        "업종 데이터가 없는 종목이 있습니다 — 그 종목은 리포트 2부에서도 섹터 상한을 적용할 수 없습니다. 이 화면의 금액은 모두 섹터 상한 반영 전입니다",
      );
    }

    // 마지막으로 성공한 신호 배치. "이 화면이 언제 것인가" 에 답한다.
    // 예약 실행은 지연되거나 조용히 건너뛰므로 실제 실행 시각을 보여준다(CLAUDE.md).
    const lastRun = await execute(LAST_SIGNAL_SUCCESS, [parsed.data.country]);
    const last = lastRun.rows[0];
    // **오늘 신호 계산을 건너뛰었거나 실패했으면 말한다** (docs/infra.md 25.798, 추천 감사 #4). 성공한 실행만 읽어, 시세 일부 실패로 `skipped` 된 날에도
    // 어제 기준일의 카드가 말없이 "오늘의 추천" 으로 보였다 — 텔레그램은 25.547 부터 "어제 신호를 그대로 씁니다" 를 적는다
    const 최근 = rowsToObjects<SignalRunRow>(
      await execute(LATEST_SIGNAL_RUN, [parsed.data.country]).catch(ifMissingTable({ columns: [], rows: [], affectedRows: 0 })),
    )[0];
    const 건너뜀 = signalRunNote(최근, last ? String(last[0] ?? "") : null, asOf);
    if (건너뜀) {
      // "오늘 기준으로 조건을 만족한 종목이 없습니다" 는 오늘 계산했을 때만 맞다 — 건너뛴 날은 그 전 기준일의 말로 바꾼다 (25.801, 교차검증)
      const i = notes.findIndex((n) => n.startsWith("오늘 기준으로 조건을 만족한 종목이 없습니다"));
      if (i >= 0) notes[i] = `${asOf ?? "그 전"} 기준으로 조건을 만족한 종목이 없었습니다`;
      notes.unshift(건너뜀);
    }

    // 어제와 비교: 오늘 새로 들어온 신호와 어제 있다가 빠진 신호를 가려낸다.
    // "오늘 뭐가 달라졌나" 가 이 화면에서 가장 먼저 보고 싶은 것이라서다.
    // 전 기준일이 없으면(첫 실행·휴장) 비교하지 않는다 — 전부 NEW 로 보이면 뜻이 없다.
    //
    // 예전에는 여기에 인계 메모의 절 번호가 적혀 있었는데 그 절이 사라졌다.
    // **인계 메모는 매 세션 다시 쓰이므로 절 번호로 가리키면 안 된다**(docs/infra.md 25.46).
    let previousAsOf: string | null = null;
    let diff: { added: string[]; dropped: SignalKeyRow[] } = { added: [], dropped: [] };
    if (asOf) {
      // "표 없음" 일 때만 옛 질의로 — 일시 실패가 직전 기준일을 바꿔 거짓 "빠짐/NEW" 를 내지 않게 (25.669, 교차검증)
      const prev = await execute(PREVIOUS_AS_OF, [parsed.data.country, asOf, parsed.data.country, asOf]).catch(
        ifMissingTableThen(() => execute(PREVIOUS_AS_OF_FALLBACK, [parsed.data.country, asOf])),
      );
      previousAsOf = (prev.rows[0]?.[0] as string | null) ?? null;
      if (previousAsOf) {
        const before = rowsToObjects<SignalKeyRow>(await execute(SIGNALS_ON, signalsOnArgs(parsed.data.country, previousAsOf)));
        // 오늘 것은 필터(시장·기간) 전 전체와 견줘야 "빠짐" 이 필터 탓이 아니다
        const today = rowsToObjects<SignalKeyRow>(await execute(SIGNALS_ON, signalsOnArgs(parsed.data.country, asOf)));
        diff = diffSignals(today, before);
      }
    }

    // 신호 성적표. **표가 아직 없을 때만** 빈 목록이다 (docs/infra.md 25.163).
    // 전에는 어떤 실패든 삼켜서, 한도에 걸린 날에도 성적표가 조용히 사라졌다
    const outcomes = await execute(OUTCOME_STATS, [parsed.data.country])
      .then((rs) => rowsToObjects<OutcomeStat>(rs))
      .catch(ifMissingTable([] as OutcomeStat[]));

    // 점수 보정표 (docs/signals.md 10.1) — 성적표 작업의 마지막 성공 실행 기록에서. 못 읽으면 null (없는 것과 같다)
    const calibration = await execute(LATEST_OUTCOME_RUN, [parsed.data.country])
      .then((rs) => parseCalibration((rs.rows[0]?.[0] as string | null) ?? null))
      .catch(ifMissingTable(null as Calibration[] | null));

    return NextResponse.json({
      rows,
      count: rows.length,
      as_of: asOf,
      outcomes,
      calibration,
      last_run: last ? { finished_at: last[0], market: last[1] } : null,
      notes,
      previous_as_of: previousAsOf,
      added: diff.added,
      dropped: diff.dropped,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";

    // 마이그레이션이 아직 적용되지 않은 DB 에서는 signals 표가 없다.
    // 배치를 한 번이라도 돌리면 자동으로 만들어진다. 날것의 SQL 오류를
    // 그대로 보여주면 "앱이 깨졌다" 로 읽히지만, 사실은 아직 안 돌린 것뿐이다.
    if (/no such table/i.test(message)) {
      return NextResponse.json({
        rows: [],
        count: 0,
        as_of: null,
        notes: [
          "추천 표가 아직 없습니다. 매수 신호 배치를 한 번 돌리면 만들어집니다" +
            " (Actions → 매수 신호 계산). 오류가 아니라 아직 준비되지 않은 상태입니다",
        ],
      });
    }

    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}
