import { unzipSync } from "fflate";
import { useEffect, useState } from "react";

import { PreviewLoading } from "@/features/files/preview/Loading";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

const TEXT_TAG = /<a:t[^>]*>([^<]*)<\/a:t>/g;

function slideNumber(name: string): number {
  const match = /slide(\d+)\.xml$/i.exec(name);
  return match ? Number(match[1]) : 0;
}

function slidesOf(data: Uint8Array): string[][] {
  const files = unzipSync(data);
  const names = Object.keys(files)
    .filter((name) => /ppt\/slides\/slide\d+\.xml$/i.test(name))
    .sort((left, right) => slideNumber(left) - slideNumber(right));
  const decoder = new TextDecoder("utf-8");
  return names.map((name) => {
    const xml = decoder.decode(files[name]);
    const lines: string[] = [];
    for (const match of xml.matchAll(TEXT_TAG)) {
      const text = match[1]?.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").trim();
      if (text) lines.push(text);
    }
    return lines;
  });
}

export function PptxBody({ content }: DocumentPreviewProps) {
  const [slides, setSlides] = useState<string[][] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = content.kind === "bytes" ? content.data : null;

  useEffect(() => {
    if (data === null) return;
    let live = true;
    setError(null);
    try {
      const next = slidesOf(data);
      if (live) setSlides(next);
    } catch (cause) {
      if (live) setError(cause instanceof Error ? cause.message : "无法预览这个演示文稿");
    }
    return () => {
      live = false;
    };
  }, [data]);

  if (data === null) return null;
  if (error) return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  if (!slides) return <PreviewLoading label="正在读取幻灯片" />;
  if (slides.length === 0) return <p className="px-4 py-6 text-[13px] text-muted">没有找到幻灯片。</p>;
  return (
    <div className="h-full overflow-auto bg-sidebar px-4 py-4">
      <ol className="mx-auto flex max-w-3xl flex-col gap-3">
        {slides.map((lines, index) => (
          <li key={index} className="rounded-xl border border-line bg-white px-5 py-4 shadow-sm">
            <p className="mb-2 text-[11px] font-medium text-faint">第 {index + 1} 页</p>
            {lines.length ? (
              lines.map((line, lineIndex) => (
                <p key={lineIndex} className={lineIndex === 0 ? "text-[15px] font-medium" : "mt-1 text-[13px] text-ink/80"}>
                  {line}
                </p>
              ))
            ) : (
              <p className="text-[13px] text-faint">这一页没有文字。</p>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
