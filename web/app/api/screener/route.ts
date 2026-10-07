import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import {
  SECTORS,
  buildQuery,
  describe,
  screenerFilterSchema,
  screenerBasisLine,
  ratioFilterNote,
  whyEmpty,
  withoutValueFilters,
  type ScreenerRow,
  metricsEmptyNote,
} from "@/lib/screener";

/**
 * 스크리너 조회.
 *
 * 조건을 바꿔 가며 목록을 본다. 추천 리포트(텔레그램)는 이 조건을 쓰지 않는다 (25.772).
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }

  const parsed = screenerFilterSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      {
        errors: parsed.error.issues.map((i) =>
          i.path.length ? `${i.path.join(".")}: ${i.message}` : i.message,
        ),
      },
      { status: 400 },
    );
  }

  const filters = parsed.data;
  const { sql, args } = buildQuery(filters);

  try {
    const started = Date.now();
    const rs = await execute(sql, args);
    const rows = rowsToObjects<ScreenerRow>(rs);
    // 업종 목록은 화면이 고를 수 있게 함께 준다. 배치가 넣은 값이 정의처라 화면이 표를 들지 않는다
    // **못 읽은 것을 "없다" 로 만들지 않는다** (docs/infra.md 25.163). 업종 고르개가
    // 비어 있으면 "업종이 안 채워졌다" 로 읽히는데, 조회가 실패한 것일 수 있다
    let sectorsError: string | null = null;
    const sectors = await execute(SECTORS, [filters.country])
      .then((r) => r.rows.map((row) => String(row[0])))
      .catch((e: unknown) => {
        sectorsError = e instanceof Error ? e.message : "업종 목록을 읽지 못했습니다";
        return [] as string[];
      });

    // **0건일 때 "범위를 넓혀 보세요" 라고만 하지 않는다** (docs/infra.md 25.149).
    // 아래 안내들은 전부 `rows.length > 0` 일 때만 붙는다 — 정작 한 건도 없을 때
    // 아무 말도 안 했다. 그때 값 조건을 뺀 같은 조회를 한 번 더 해서 원인을 가린다.
    // 질의가 하나 늘지만 **0건인 화면에서만** 늘고, 읽기는 넉넉하다(하루 500만 행)
    const empty: string[] = [];
    if (rows.length === 0) {
      const 맨조건 = buildQuery(withoutValueFilters(filters));
      // **진단 조회가 실패하면 진단하지 않는다** (docs/infra.md 25.163). 빈 배열을
      // 그대로 넘기면 `whyEmpty` 가 "종목 마스터나 유니버스가 비어 있습니다" 라고
      // 단정한다 — 사람을 엉뚱한 데로 보낸다 (25.74 에서 배운 것)
      const 후보 = await execute(맨조건.sql, 맨조건.args)
        .then((r) => rowsToObjects<ScreenerRow>(r))
        .catch(() => null);
      empty.push(
        ...(후보 === null
          ? ["0건인 이유를 알아보려던 조회가 실패했습니다. 조건이 좁아서인지 자료가 없어서인지 가리지 못했습니다"]
          : whyEmpty(filters, 후보)),
      );
    }

    const notes: string[] = [];
    // 성과 지표가 비어 있으면 왜 비었는지 알려 준다 — 판정은 lib 에 (25.779)
    const 성과말 = metricsEmptyNote(rows, filters.metric_window);
    if (성과말) notes.push(성과말);
    const withValuation = rows.filter((r) => r.per !== null).length;
    // 미국 20일 평균 거래대금은 전종목 수집(2026-09-16 시작)이 20 거래일 쌓이고 주간 유니버스
    // 갱신이 돌아야 채워진다. 비어 있으면 거래대금 순 정렬이 종목 번호 순(동점 기준, 25.577)이라 그 사실을 알린다.
    if (filters.country === "US" && rows.length > 0 && rows.every((r) => r.avg_turnover_20d === null)) {
      notes.push(
        "미국 20일 평균 거래대금이 아직 없습니다. 시세가 20 거래일 쌓이고 주간 유니버스 갱신이 돌면 채워집니다."
          + " 지금 순서는 거래대금 순이 아닙니다",
      );
    }

    // **두 나라 모두 본다** (docs/infra.md 25.249). 예전에는 "미국은 재무 수집 경로가 없다" 며 국내만 봤다 —
    // 미국 재무(`jobs/us_financials`)가 생긴 뒤에도. 비어 있는 이유는 데이터가 말하게 한다
    if (rows.length > 0 && withValuation === 0) {
      // **"먼저 돌리세요" 라고 시키지 않는다** (2026-09-21, docs/infra.md 25.86).
      // 재무 수집은 따라잡기 2단계가 이미 매일 돌린다 — 아직 안 채워진 것이지
      // 사람이 안 돌려서가 아니다. 바로 위 성과 지표 안내와 말투도 맞췄다.
      // 배치가 정말 안 도는지는 이 화면이 아니라 /status 가 답할 일이다.
      // 25.490 뒤로 PER·PBR 은 **점수 팩터**에서 온다 — 비는 이유는 대개 점수를 내지 않는 종목(유니버스 밖)이거나 그날
      // 팩터가 없는 것이다. 예전 문구("재무 미수집", 스크리너에 없는 PSR)는 기준 줄과 모순됐다 (25.577, 감사)
      notes.push(
        "이 결과에는 PER·PBR 이 있는 종목이 없습니다 — 점수를 내지 않는 종목(유니버스 밖 등)이거나 그날 점수가 아직 없습니다."
          + " 배치가 도는지는 /status 에서 봅니다",
      );
    }

    // 비율 조건이 유니버스 밖 종목을 조용히 빼는 것 (docs/infra.md 25.497)
    const 비율안내 = ratioFilterNote(filters);
    if (비율안내) notes.push(비율안내);

    return NextResponse.json({
      rows,
      count: rows.length,
      conditions: describe(filters),
      // 값마다 언제 기준인지 (docs/infra.md 25.364)
      basis: screenerBasisLine(rows),
      sectors,
      notes: [...empty, ...notes, ...(sectorsError ? [`업종 목록을 읽지 못했습니다: ${sectorsError}`] : [])],
      elapsed_ms: Date.now() - started,
    });
  } catch (error) {
    return NextResponse.json(
      { errors: [error instanceof Error ? error.message : "조회에 실패했습니다"] },
      { status: 500 },
    );
  }
}
