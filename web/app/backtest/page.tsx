import BacktestView from "@/components/BacktestView";
import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";

export const dynamic = "force-dynamic";

/**
 * 백테스트·스트레스 화면 (Step 16).
 *
 * **규칙이 과거에 통했는지**(백테스트)와 **지금 바스켓이 급락장에서 얼마나 버티는지**(스트레스)를
 * 한 화면에서 본다. 둘 다 같은 배치가 돌리고 결과 표도 나란히 있어 화면을 나누지 않았다.
 *
 * 여기 숫자는 규칙을 고칠 때만 쓴다. 매일 보는 화면이 아니다. 그래서 실행도 수동이다.
 */
export default function BacktestPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/backtest" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="백테스트" doc="docs/backtest.md · docs/stress.md">
          규칙이 과거에 통했는지 봅니다. 결과에는 경고가 항상 붙습니다. 상장폐지 종목이 DB 에 없어 생존편향이
          있고, 그래서 여기 수익률은 실제보다 좋습니다. 경고가 붙은 결과로 규칙을 확정하지 않습니다.
        </PageHeader>
        <BacktestView />
      </main>
      <Footer />
    </div>
  );
}
