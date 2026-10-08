import AnalysisHub from "@/components/AnalysisHub";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import StockSearch from "@/components/StockSearch";

export const dynamic = "force-dynamic";

/**
 * 종목 분석 (docs/analysis.md). 한 종목의 결론·근거·반대 목소리. 의견은 일일 배치가 규칙의 결과로 만든다 —
 * 종목을 찾으면 그 종목 화면 맨 위의 분석 카드로 간다.
 */
export default function AnalysisPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/analysis" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="종목 분석" doc="docs/analysis.md">
          종목을 찾으면 점수·신호 판정표·보유·매도 플래그·수급과 증권사 의견·최근 공시를 모아 결론 한 줄과 근거, 반대 목소리를 보여 줍니다.
          새 예측이 아니라 이미 있는 규칙의 결과입니다.
        </PageHeader>
        <div className="mb-4">
          <StockSearch />
        </div>
        <AnalysisHub />
      </main>
      <Footer />
    </div>
  );
}
