import { lazy, Suspense } from "react";

import { BrowserPane } from "@/features/files/BrowserPane";
import { FilePreview } from "@/features/files/FilePreview";
import { SideChatPane } from "@/features/files/SideChatPane";
import { TerminalPane } from "@/features/files/TerminalPane";
import type { PanelTab } from "@/features/files/panel-store";
import { PreviewLoading } from "@/features/files/preview/Loading";

const DiffTab = lazy(() => import("@/features/files/diff/DiffTab").then((module) => ({ default: module.DiffTab })));

/** 底部工作台里的一个标签。和右侧栏用同一套内容，但只挂一份。 */
export function DockedTab({
  tab,
  active,
  root,
  revision,
}: {
  tab: PanelTab;
  active: boolean;
  root: string;
  revision: number;
}) {
  if (tab.kind === "file") return <FilePreview root={root} path={tab.path} revision={revision} tabId={tab.id} />;
  if (tab.kind === "diff") {
    return (
      <Suspense fallback={<PreviewLoading />}>
        <DiffTab tabId={tab.id} target={tab.target} visible={active} />
      </Suspense>
    );
  }
  if (tab.kind === "browser") return <BrowserPane tabId={tab.id} url={tab.url} />;
  if (tab.kind === "sidechat") {
    return <SideChatPane tabId={tab.id} productId={tab.productId} engineId={tab.engineId} />;
  }
  return (
    <TerminalPane
      tabId={tab.id}
      serverId={tab.serverId}
      shell={tab.shell}
      cwd={tab.cwd}
      root={root}
      active={active}
    />
  );
}
