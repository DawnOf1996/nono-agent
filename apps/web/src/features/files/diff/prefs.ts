import { useCallback, useState } from "react";

import type { DiffLayout } from "@/features/files/diff/DiffView";

const LAYOUT_KEY = "nono.diff.layout";
const WRAP_KEY = "nono.diff.wrap";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // private mode / blocked storage
  }
}

/** 对比视图的布局与换行偏好，跨标签页记住。 */
export function useDiffPrefs(): {
  layout: DiffLayout;
  wrap: boolean;
  toggleLayout: () => void;
  toggleWrap: () => void;
} {
  const [layout, setLayout] = useState<DiffLayout>(() => (read(LAYOUT_KEY) === "split" ? "split" : "unified"));
  const [wrap, setWrap] = useState(() => read(WRAP_KEY) !== "false");
  const toggleLayout = useCallback(() => {
    setLayout((value) => {
      const next = value === "split" ? "unified" : "split";
      write(LAYOUT_KEY, next);
      return next;
    });
  }, []);
  const toggleWrap = useCallback(() => {
    setWrap((value) => {
      write(WRAP_KEY, String(!value));
      return !value;
    });
  }, []);
  return { layout, wrap, toggleLayout, toggleWrap };
}
