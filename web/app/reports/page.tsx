import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import ReportView from "@/components/ReportView";

export const dynamic = "force-dynamic";

/**
 * 일일 리포트 이력 (docs/reports.md). CLAUDE.md: "웹앱은 오늘 리포트와 이전 리포트 이력을 보여준다".
 * 텔레그램으로 보낸 본문 그대로와 그 재료를 보여 준다. 여기서 다시 계산하지 않는다.
 */
export default function ReportsPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/reports" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="일일 리포트">
          장 시작 전 텔레그램으로 보낸 리포트를 그대로 보관합니다. 날짜를 골라 지난 추천을 되짚을 수 있습니다.
          오늘 배치가 실패하면 마지막 성공분이 남고, 며칠 지난 것인지 표시합니다.
        </PageHeader>
        <ReportView />
      </main>
      <Footer />
    </div>
  );
}
