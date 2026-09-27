import { useEffect, useRef } from "react";

import { parseSse } from "@/api/sse";
import { normalizeWorkspace } from "@/lib/workspace";

/** 订阅已经展开的目录。目录项变化时回调绝对路径，断线后自动重连。 */
export function useDirectoryWatch(root: string, paths: readonly string[], onChange: (dir: string) => void): void {
  const callback = useRef(onChange);
  callback.current = onChange;
  const key = paths
    .map((path) => normalizeWorkspace(path).toLowerCase())
    .filter(Boolean)
    .sort()
    .join("\n");

  useEffect(() => {
    const watched = key ? key.split("\n") : [];
    if (!root.trim() || watched.length === 0) return;
    const controller = new AbortController();
    let stopped = false;

    async function run() {
      while (!stopped) {
        try {
          const response = await fetch("/app/fs/watch", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
            body: JSON.stringify({ root, paths: watched }),
            signal: controller.signal,
          });
          if (!response.ok || !response.body) throw new Error("watch failed");
          for await (const frame of parseSse(response)) {
            if (frame.event !== "change" || !frame.data) continue;
            const payload = JSON.parse(frame.data) as { dir?: string };
            if (payload.dir) callback.current(payload.dir);
          }
        } catch {
          if (controller.signal.aborted) return;
        }
        if (stopped) return;
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
    }

    void run();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [root, key]);
}
