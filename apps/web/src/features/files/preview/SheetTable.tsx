const MAX_ROWS = 2000;
const MAX_COLS = 40;

export function SheetTable({
  name,
  rows,
  truncatedRows,
}: {
  name?: string;
  rows: string[][];
  truncatedRows?: boolean;
}) {
  const width = Math.min(
    MAX_COLS,
    rows.reduce((max, row) => Math.max(max, row.length), 0),
  );
  const body = rows.slice(0, MAX_ROWS);
  const clippedCols = rows.some((row) => row.length > MAX_COLS);
  if (width === 0) return <p className="px-4 py-6 text-[13px] text-muted">这个表格是空的。</p>;
  return (
    <div className="flex h-full min-h-0 flex-col">
      {name ? <div className="shrink-0 border-b border-line px-3 py-1.5 text-[12px] text-muted">{name}</div> : null}
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="border-collapse text-[12px]">
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex} className={rowIndex === 0 ? "bg-sidebar font-medium" : undefined}>
                {Array.from({ length: width }, (_, column) => (
                  <td key={column} className="max-w-64 truncate border border-line px-2 py-1 align-top" title={row[column] ?? ""}>
                    {row[column] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncatedRows || rows.length > MAX_ROWS || clippedCols ? (
        <p className="shrink-0 border-t border-line px-3 py-1.5 text-[11px] text-faint">
          只显示前 {Math.min(rows.length, MAX_ROWS)} 行、{width} 列。
        </p>
      ) : null}
    </div>
  );
}

/** 解析 CSV / TSV。引号内的分隔符和换行保留。 */
export function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else quoted = false;
      } else cell += char;
      continue;
    }
    if (char === '"') {
      quoted = true;
      continue;
    }
    if (char === delimiter) {
      row.push(cell);
      cell = "";
      continue;
    }
    if (char === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
      continue;
    }
    if (char !== "\r") cell += char;
  }
  if (cell.length > 0 || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((item, index) => item.some((value) => value !== "") || index < rows.length - 1);
}
