import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { REVIEWS, REVIEW_LIMIT, REVIEW_STATS, type ReviewRow, type ReviewStat } from "@/lib/review";

/**
 * 매매 복기 조회 (docs/review.md). 배치가 계산해 둔 파생 표를 읽기만 한다.
 *
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 * 표가 아직 없으면(마이그레이션 전) 500 대신 빈 결과와 안내를 준다. 포트폴리오 경로와 같은 방식.
 */
export async function GET() {
  try {
    const [reviewsRs, statsRs] = await Promise.all([execute(REVIEWS), execute(REVIEW_STATS)]);
    const reviews = rowsToObjects<ReviewRow>(reviewsRs);
    return NextResponse.json({
      reviews: reviews.slice(0, REVIEW_LIMIT),
      truncated: reviews.length > REVIEW_LIMIT,
      stats: rowsToObjects<ReviewStat>(statsRs),
      notice: null,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";
    if (/no such table/i.test(message)) {
      return NextResponse.json({
        reviews: [], stats: [],
        notice: "복기 표가 아직 없습니다. 마이그레이션(migrate.yml) 뒤 포트폴리오 배치를 한 번 돌리면 만들어집니다",
      });
    }
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}
