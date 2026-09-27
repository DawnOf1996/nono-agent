import { useEffect, useState } from "react";

import { PreviewLoading } from "@/features/files/preview/Loading";
import { parseDelimited, SheetTable } from "@/features/files/preview/SheetTable";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

function extensionOf(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function delimiterOf(path: string, text: string): string {
  if (extensionOf(path) === "tsv") return "\t";
  const line = text.split(/\r?\n/, 1)[0] ?? "";
  const tabs = line.split("\t").length;
  const commas = line.split(",").length;
  return tabs > commas ? "\t" : ",";
}

type Grid = { name: string; rows: string[][] };

export function SheetBody({ path, content }: DocumentPreviewProps) {
  const [sheets, setSheets] = useState<Grid[] | null>(null);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    setSheets(null);
    void (async () => {
      try {
        if (content.kind === "text") {
          const rows = parseDelimited(content.text, delimiterOf(path, content.text));
          if (live) setSheets([{ name: "", rows }]);
          return;
        }
        if (content.kind !== "bytes") return;
        const xlsx = await import("xlsx");
        const book = xlsx.read(content.data, { type: "array" });
        const grids = book.SheetNames.map((name) => {
          const sheet = book.Sheets[name];
          const rows = (
            sheet ? xlsx.utils.sheet_to_json<(string | number | boolean | null)[]>(sheet, { header: 1, raw: false }) : []
          ).map((row) => row.map((cell) => (cell == null ? "" : String(cell))));
          return { name, rows };
        });
        if (live) {
          setSheets(grids);
          setActive(0);
        }
      } catch (cause) {
        if (live) setError(cause instanceof Error ? cause.message : "无法预览这个表格");
      }
    })();
    return () => {
      live = false;
    };
  }, [content, path]);

  if (error) return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  if (!sheets) return <PreviewLoading label="正在读取表格" />;
  const current = sheets[active] ?? sheets[0];
  if (!current) return <p className="px-4 py-6 text-[13px] text-muted">这个表格是空的。</p>;
  return (
    <div className="flex h-full min-h-0 flex-col">
      {sheets.length > 1 ? (
        <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-line px-2 py-1">
          {sheets.map((sheet, index) => (
            <button
              key={sheet.name}
              type="button"
              className={`h-6 shrink-0 rounded-md px-2 text-[12px] ${index === active ? "bg-active text-ink" : "text-muted hover:bg-hover"}`}
              onClick={() => setActive(index)}
            >
              {sheet.name}
            </button>
          ))}
        </div>
      ) : null}
      <SheetTable name={sheets.length > 1 ? undefined : current.name} rows={current.rows} />
    </div>
  );
}
