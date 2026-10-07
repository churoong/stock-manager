import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import PortfolioView from "@/components/PortfolioView";

export const dynamic = "force-dynamic";

/**
 * 매매 기록 · 보유 · 손익 (docs/portfolio.md, Step 12).
 *
 * 입력은 여기서 하고 계산은 배치가 한다. 저장 뒤 1~2분은 "계산 중" 으로 보인다.
 */
export default function PortfolioPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/portfolio" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="내 포트폴리오" doc="docs/portfolio.md">
          매매와 배당은 직접 입력합니다. 손익·보유·평가는 배치가 계산합니다(먼저 산 것부터 판 것으로 보고,
          환차손익은 따로 셉니다). 자동 매매는 없습니다.
        </PageHeader>
        <PortfolioView />
      </main>
      <Footer />
    </div>
  );
}
