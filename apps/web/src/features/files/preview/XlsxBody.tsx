import { useEffect, useRef, useState } from "react";

import { workbookToUniver } from "@/features/files/preview/excel-to-univer";
import { PreviewLoading } from "@/features/files/preview/Loading";
import type { DocumentPreviewProps } from "@/features/files/preview/types";
import "@univerjs/preset-sheets-core/lib/index.css";

type LoadState = { status: "loading" } | { status: "ready" } | { status: "error"; message: string };

export function XlsxBody({ content }: DocumentPreviewProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<{ dispose: () => void } | null>(null);
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const data = content.kind === "bytes" ? content.data : null;

  useEffect(() => {
    const host = hostRef.current;
    if (data === null || host === null) return;
    let live = true;
    setLoad({ status: "loading" });
    void (async () => {
      try {
        const { Workbook } = await import("exceljs");
        const { createUniver, LocaleType, mergeLocales } = await import("@univerjs/presets");
        const { UniverSheetsCorePreset } = await import("@univerjs/preset-sheets-core");
        const book = new Workbook();
        const buffer = new ArrayBuffer(data.byteLength);
        new Uint8Array(buffer).set(data);
        await book.xlsx.load(buffer);
        if (!live) return;
        const isZh = navigator.language.toLowerCase().startsWith("zh");
        const locale = isZh ? LocaleType.ZH_CN : LocaleType.EN_US;
        const localePack = isZh
          ? (await import("@univerjs/preset-sheets-core/locales/zh-CN")).default
          : (await import("@univerjs/preset-sheets-core/locales/en-US")).default;
        const snapshot = workbookToUniver(book, "1.0.2", locale);
        if (!live) return;
        const { univer, univerAPI } = createUniver({
          locale,
          locales: { [locale]: mergeLocales(localePack) },
          presets: [
            UniverSheetsCorePreset({
              container: host,
              header: false,
              toolbar: false,
              formulaBar: true,
              footer: { sheetBar: true, statisticBar: false },
              disableAutoFocus: true,
            }),
          ],
        });
        univerRef.current = univer;
        univerAPI.createWorkbook(snapshot);
        if (live) setLoad({ status: "ready" });
      } catch (cause) {
        if (!live) return;
        try {
          univerRef.current?.dispose();
        } catch {
          // 初始化中途失败时，实例可能还不能销毁。
        }
        univerRef.current = null;
        host.innerHTML = "";
        setLoad({ status: "error", message: cause instanceof Error ? cause.message : "无法预览这个表格" });
      }
    })();
    return () => {
      live = false;
      try {
        univerRef.current?.dispose();
      } catch {
        // 已经销毁过。
      }
      univerRef.current = null;
      host.innerHTML = "";
    };
  }, [data]);

  if (data === null) return null;
  return (
    <div className="relative h-full min-h-0 bg-bg">
      <div ref={hostRef} className="h-full w-full min-h-0" />
      {load.status !== "ready" ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-bg">
          {load.status === "loading" ? <PreviewLoading label="正在读取表格" /> : null}
          {load.status === "error" ? <p className="px-4 py-6 text-[13px] leading-6 text-danger">{load.message}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
