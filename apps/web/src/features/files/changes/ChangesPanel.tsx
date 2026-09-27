import { ChangePreview } from "@/features/files/changes/ChangePreview";
import { GitLens } from "@/features/files/changes/GitLens";
import { SessionLens } from "@/features/files/changes/SessionLens";
import { usePanelStore, type ChangesLens } from "@/features/files/panel-store";
import { cn } from "@/lib/utils";

const LENSES: Array<{ id: ChangesLens; label: string }> = [
  { id: "git", label: "Git" },
  { id: "session", label: "对话" },
];

/** 「变更」标签页：Git 看仓库里的真实状态，对话看智能体每轮改了什么。 */
export function ChangesPanel({ root, visible }: { root: string; visible: boolean }) {
  const lens = usePanelStore((state) => state.lens);
  const setLens = usePanelStore((state) => state.setLens);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center px-2 pt-2">
        <div role="group" aria-label="变更视角" className="flex rounded-lg bg-card p-0.5">
          {LENSES.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={lens === item.id}
              className={cn(
                "h-6 rounded-md px-3 text-[12px]",
                lens === item.id ? "bg-bg text-ink shadow-[0_1px_2px_rgba(15,17,21,0.08)]" : "text-muted hover:text-ink",
              )}
              onClick={() => setLens(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className={cn("min-h-0 flex-1 flex-col", lens === "git" ? "flex" : "hidden")}>
        <GitLens root={root} visible={visible && lens === "git"} onShowSession={() => setLens("session")} />
      </div>
      {lens === "session" ? <SessionLens /> : null}
      <ChangePreview />
    </div>
  );
}
