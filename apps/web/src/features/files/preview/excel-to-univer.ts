import type { Border, Cell, Fill, Style, Workbook, Worksheet } from "exceljs";
import { ValueType } from "exceljs";
import {
  BaselineOffset,
  BooleanNumber,
  BorderStyleTypes,
  CellValueType,
  HorizontalAlign,
  TextDecoration,
  VerticalAlign,
  WorksheetHiddenState,
  WrapStrategy,
  type IBorderData,
  type IBorderStyleData,
  type ICellData,
  type IColumnData,
  type IFreeze,
  type IRange,
  type IRowData,
  type IStyleData,
  type ITextDecoration,
  type ITextRotation,
  type IWorkbookData,
  type IWorksheetData,
  type LocaleType,
} from "@univerjs/presets";

type ExcelColor = {
  argb?: string;
  theme?: number;
  tint?: number;
  indexed?: number;
};

const THEME = [
  "#000000",
  "#FFFFFF",
  "#44546A",
  "#E7E6E6",
  "#5B9BD5",
  "#ED7D31",
  "#A5A5A5",
  "#FFC000",
  "#4472C4",
  "#70AD47",
  "#0563C1",
  "#954F72",
];

const INDEXED = [
  "#000000", "#FFFFFF", "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF",
  "#000000", "#FFFFFF", "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF",
  "#800000", "#008000", "#000080", "#808000", "#800080", "#008080", "#C0C0C0", "#808080",
  "#9999FF", "#993366", "#FFFFCC", "#CCFFFF", "#660066", "#FF8080", "#0066CC", "#CCCCFF",
  "#000080", "#FF00FF", "#FFFF00", "#00FFFF", "#800080", "#800000", "#008080", "#0000FF",
  "#00CCFF", "#CCFFFF", "#CCFFCC", "#FFFF99", "#99CCFF", "#FF99CC", "#CC99FF", "#FFCC99",
  "#3366FF", "#33CCCC", "#99CC00", "#FFCC00", "#FF9900", "#FF6600", "#666699", "#969696",
  "#003366", "#339966", "#003300", "#333300", "#993300", "#993366", "#333399", "#333333",
];

const BORDERS: Record<string, BorderStyleTypes> = {
  thin: BorderStyleTypes.THIN,
  hair: BorderStyleTypes.HAIR,
  dotted: BorderStyleTypes.DOTTED,
  dashed: BorderStyleTypes.DASHED,
  dashDot: BorderStyleTypes.DASH_DOT,
  dashDotDot: BorderStyleTypes.DASH_DOT_DOT,
  double: BorderStyleTypes.DOUBLE,
  medium: BorderStyleTypes.MEDIUM,
  mediumDashed: BorderStyleTypes.MEDIUM_DASHED,
  mediumDashDot: BorderStyleTypes.MEDIUM_DASH_DOT,
  mediumDashDotDot: BorderStyleTypes.MEDIUM_DASH_DOT_DOT,
  slantDashDot: BorderStyleTypes.SLANT_DASH_DOT,
  thick: BorderStyleTypes.THICK,
};

const HORIZONTAL: Record<string, HorizontalAlign> = {
  left: HorizontalAlign.LEFT,
  center: HorizontalAlign.CENTER,
  right: HorizontalAlign.RIGHT,
  justify: HorizontalAlign.JUSTIFIED,
  distributed: HorizontalAlign.DISTRIBUTED,
  centerContinuous: HorizontalAlign.CENTER,
  fill: HorizontalAlign.LEFT,
};

const VERTICAL: Record<string, VerticalAlign> = {
  top: VerticalAlign.TOP,
  middle: VerticalAlign.MIDDLE,
  bottom: VerticalAlign.BOTTOM,
  distributed: VerticalAlign.BOTTOM,
  justify: VerticalAlign.BOTTOM,
};

class StyleBook {
  readonly styles: Record<string, IStyleData> = {};
  private readonly ids = new Map<string, string>();

  id(style: IStyleData | null): string | undefined {
    if (style === null) return undefined;
    const key = JSON.stringify(style);
    const existing = this.ids.get(key);
    if (existing !== undefined) return existing;
    const next = `s${this.ids.size}`;
    this.ids.set(key, next);
    this.styles[next] = style;
    return next;
  }
}

/** 把 ExcelJS 读到的工作簿（含字体、填充、边框、对齐、数字格式）转成 Univer 快照。 */
export function workbookToUniver(book: Workbook, appVersion: string, locale: LocaleType): IWorkbookData {
  const styles = new StyleBook();
  const date1904 = book.properties?.date1904 === true;
  const sheetOrder: string[] = [];
  const sheets: Record<string, Partial<IWorksheetData>> = {};

  book.worksheets.forEach((sheet, index) => {
    const sheetId = `sheet-${index}`;
    sheetOrder.push(sheetId);
    sheets[sheetId] = worksheetToUniver(sheet, sheetId, styles, date1904);
  });

  if (sheetOrder.length === 0) {
    sheetOrder.push("sheet-0");
    sheets["sheet-0"] = {
      id: "sheet-0",
      name: "Sheet1",
      rowCount: 20,
      columnCount: 10,
      cellData: {},
      rowData: {},
      columnData: {},
      mergeData: [],
    };
  }

  return {
    id: "workbook",
    name: book.title || "Workbook",
    appVersion,
    locale,
    styles: styles.styles,
    sheetOrder,
    sheets,
  };
}

function worksheetToUniver(
  sheet: Worksheet,
  sheetId: string,
  styles: StyleBook,
  date1904: boolean,
): Partial<IWorksheetData> {
  const cellData: IWorksheetData["cellData"] = {};
  const rowData: IWorksheetData["rowData"] = {};
  const columnData: IWorksheetData["columnData"] = {};

  sheet.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    const rowIndex = rowNumber - 1;
    const rowStyle = styles.id(styleOf(row));
    const height = Number.isFinite(row.height) ? Math.round((row.height * 96) / 72) : undefined;
    if (rowStyle !== undefined || height !== undefined || row.hidden) {
      const data: Partial<IRowData> = {};
      if (height !== undefined && height > 0) data.h = height;
      if (row.hidden) data.hd = BooleanNumber.TRUE;
      if (rowStyle !== undefined) data.s = rowStyle;
      rowData[rowIndex] = data;
    }
    row.eachCell({ includeEmpty: false }, (cell, colNumber) => {
      if (cell.isMerged && cell.master.address !== cell.address) return;
      const converted = cellOf(cell, styles, date1904);
      if (converted === null) return;
      const rowCells = cellData[rowIndex] ?? {};
      rowCells[colNumber - 1] = converted;
      cellData[rowIndex] = rowCells;
    });
  });

  for (let index = 1; index <= sheet.columnCount; index += 1) {
    const column = sheet.getColumn(index);
    const style = styles.id(styleOf(column.style));
    const width = typeof column.width === "number" ? columnWidthPx(column.width) : undefined;
    if (style === undefined && width === undefined && !column.hidden) continue;
    const data: Partial<IColumnData> = {};
    if (width !== undefined) data.w = width;
    if (column.hidden) data.hd = BooleanNumber.TRUE;
    if (style !== undefined) data.s = style;
    columnData[index - 1] = data;
  }

  const view = sheet.views[0];
  const zoom = view?.zoomScale;
  return {
    id: sheetId,
    name: sheet.name,
    tabColor: cssColor(sheet.properties.tabColor) ?? "",
    hidden: hiddenOf(sheet.state),
    freeze: freezeOf(sheet),
    rowCount: Math.max(sheet.rowCount, 20),
    columnCount: Math.max(sheet.columnCount, 10),
    zoomRatio: typeof zoom === "number" && zoom > 0 ? zoom / 100 : 1,
    defaultColumnWidth: 72,
    defaultRowHeight: 20,
    mergeData: mergesOf(sheet),
    cellData,
    rowData,
    columnData,
    rowHeader: { width: 46 },
    columnHeader: { height: 20 },
    showGridlines: view?.showGridLines === false ? BooleanNumber.FALSE : BooleanNumber.TRUE,
    rightToLeft: BooleanNumber.FALSE,
  };
}

function cellOf(cell: Cell, styles: StyleBook, date1904: boolean): ICellData | null {
  const out: ICellData = {};
  const value = plainValue(cell, date1904);
  if (value !== null) {
    out.t = value.t;
    out.v = value.v;
  }
  const formula = cell.formula?.trim();
  if (formula) out.f = formula.startsWith("=") ? formula : `=${formula}`;
  const style = styles.id(styleOf(cell.style));
  if (style !== undefined) out.s = style;
  if (value === null && !formula && style === undefined) return null;
  return out;
}

function plainValue(cell: Cell, date1904: boolean): { t: CellValueType; v: string | number | boolean } | null {
  const value = cell.value;
  if (cell.type === ValueType.Number && typeof value === "number") return { t: CellValueType.NUMBER, v: value };
  if (cell.type === ValueType.Boolean && typeof value === "boolean") return { t: CellValueType.BOOLEAN, v: value };
  if (cell.type === ValueType.Date && value instanceof Date) return { t: CellValueType.NUMBER, v: excelSerial(value, date1904) };
  if (cell.type === ValueType.Error) return { t: CellValueType.FORCE_STRING, v: errorText(value) };
  if (cell.type === ValueType.Formula || cell.formula) {
    const result = cell.result;
    if (typeof result === "number") return { t: CellValueType.NUMBER, v: result };
    if (typeof result === "boolean") return { t: CellValueType.BOOLEAN, v: result };
    if (result instanceof Date) return { t: CellValueType.NUMBER, v: excelSerial(result, date1904) };
    if (result && typeof result === "object") return { t: CellValueType.FORCE_STRING, v: errorText(result) };
    if (typeof result === "string" && result !== "") return { t: CellValueType.STRING, v: result };
    return null;
  }
  if (value && typeof value === "object" && "richText" in value) {
    const text = value.richText.map((part) => part.text).join("");
    return text ? { t: CellValueType.STRING, v: text } : null;
  }
  if (value && typeof value === "object" && "text" in value && "hyperlink" in value) {
    return { t: CellValueType.STRING, v: String(value.text ?? "") };
  }
  if (typeof value === "string") return value ? { t: CellValueType.STRING, v: value } : null;
  if (typeof value === "number") return { t: CellValueType.NUMBER, v: value };
  if (typeof value === "boolean") return { t: CellValueType.BOOLEAN, v: value };
  return null;
}

function styleOf(style: Partial<Style> | undefined): IStyleData | null {
  if (!style) return null;
  const out: IStyleData = {};
  const font = style.font;
  if (font) {
    if (font.name) out.ff = font.name;
    if (typeof font.size === "number" && font.size > 0) out.fs = font.size;
    if (font.bold) out.bl = BooleanNumber.TRUE;
    if (font.italic) out.it = BooleanNumber.TRUE;
    const color = cssColor(font.color);
    if (color) out.cl = { rgb: color };
    const underline = underlineOf(font.underline);
    if (underline) out.ul = underline;
    if (font.strike) out.st = { s: BooleanNumber.TRUE };
    if (font.vertAlign === "superscript") out.va = BaselineOffset.SUPERSCRIPT;
    if (font.vertAlign === "subscript") out.va = BaselineOffset.SUBSCRIPT;
  }
  const background = fillColor(style.fill);
  if (background) out.bg = { rgb: background };
  const border = borderOf(style.border);
  if (border) out.bd = border;
  const alignment = style.alignment;
  if (alignment) {
    if (alignment.horizontal) out.ht = HORIZONTAL[alignment.horizontal];
    if (alignment.vertical) out.vt = VERTICAL[alignment.vertical];
    if (alignment.wrapText) out.tb = WrapStrategy.WRAP;
    if (alignment.shrinkToFit) out.stf = BooleanNumber.TRUE;
    const rotation = rotationOf(alignment.textRotation);
    if (rotation) out.tr = rotation;
  }
  if (style.numFmt && style.numFmt !== "General") out.n = { pattern: style.numFmt };
  return Object.keys(out).length > 0 ? out : null;
}

function underlineOf(value: NonNullable<Style["font"]>["underline"] | undefined): ITextDecoration | undefined {
  if (!value || value === "none") return undefined;
  if (value === "double" || value === "doubleAccounting") return { s: BooleanNumber.TRUE, t: TextDecoration.DOUBLE };
  return { s: BooleanNumber.TRUE, t: TextDecoration.SINGLE };
}

function fillColor(fill: Fill | undefined): string | null {
  if (!fill) return null;
  if (fill.type === "pattern") {
    if (fill.pattern === "none") return null;
    return cssColor(fill.fgColor) ?? cssColor(fill.bgColor);
  }
  return cssColor(fill.stops[0]?.color);
}

function borderOf(border: Partial<Style["border"]> | undefined): IBorderData | null {
  if (!border) return null;
  const out: IBorderData = {};
  const top = borderSide(border.top);
  const right = borderSide(border.right);
  const bottom = borderSide(border.bottom);
  const left = borderSide(border.left);
  if (top) out.t = top;
  if (right) out.r = right;
  if (bottom) out.b = bottom;
  if (left) out.l = left;
  return top || right || bottom || left ? out : null;
}

function borderSide(side: Partial<Border> | undefined): IBorderStyleData | undefined {
  if (!side?.style) return undefined;
  const kind = BORDERS[side.style];
  if (kind === undefined) return undefined;
  return { s: kind, cl: { rgb: cssColor(side.color) ?? "#000000" } };
}

function rotationOf(value: NonNullable<Style["alignment"]>["textRotation"] | undefined): ITextRotation | undefined {
  if (value === "vertical" || value === 255) return { a: 0, v: BooleanNumber.TRUE };
  if (typeof value !== "number" || value === 0) return undefined;
  return { a: value > 90 ? 90 - value : value };
}

function cssColor(color: Partial<ExcelColor> | undefined): string | null {
  if (!color) return null;
  const argb = color.argb?.replace(/^#/, "");
  if (argb && /^[0-9a-f]{6,8}$/i.test(argb)) {
    if (argb.length === 8 && argb.slice(0, 2).toLowerCase() === "00") return null;
    return `#${argb.slice(-6)}`;
  }
  if (typeof color.theme === "number") {
    const base = THEME[color.theme];
    return base ? applyTint(base, color.tint ?? 0) : null;
  }
  if (color.indexed === 64) return "#000000";
  if (color.indexed === 65) return "#FFFFFF";
  if (typeof color.indexed === "number") return INDEXED[color.indexed] ?? null;
  return null;
}

function applyTint(hex: string, tint: number): string {
  if (!tint) return hex;
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (source: number) => {
    const next = tint < 0 ? source * (1 + tint) : source * (1 - tint) + 255 * tint;
    return Math.max(0, Math.min(255, Math.round(next)));
  };
  const red = channel((value >> 16) & 255);
  const green = channel((value >> 8) & 255);
  const blue = channel(value & 255);
  return `#${[red, green, blue].map((item) => item.toString(16).padStart(2, "0")).join("")}`;
}

function columnWidthPx(width: number): number {
  const maxDigitWidth = 7;
  return Math.max(8, Math.floor(((256 * width + Math.floor(128 / maxDigitWidth)) / 256) * maxDigitWidth));
}

function freezeOf(sheet: Worksheet): IFreeze {
  for (const view of sheet.views) {
    if (view.state !== "frozen") continue;
    const xSplit = view.xSplit ?? 0;
    const ySplit = view.ySplit ?? 0;
    if (xSplit > 0 || ySplit > 0) return { xSplit, ySplit, startRow: ySplit, startColumn: xSplit };
  }
  return { xSplit: 0, ySplit: 0, startRow: -1, startColumn: -1 };
}

function hiddenOf(state: Worksheet["state"]): WorksheetHiddenState {
  if (state === "veryHidden") return WorksheetHiddenState.VERY_HIDDEN;
  if (state === "hidden") return WorksheetHiddenState.HIDDEN;
  return WorksheetHiddenState.VISIBLE;
}

function mergesOf(sheet: Worksheet): IRange[] {
  const ranges: IRange[] = [];
  for (const merge of sheet.model.merges ?? []) {
    const range = decodeRange(merge);
    if (range) ranges.push(range);
  }
  return ranges;
}

function decodeRange(range: string): IRange | null {
  const [start, end] = range.split(":");
  if (!start) return null;
  const from = decodeAddr(start);
  const to = decodeAddr(end || start);
  if (!from || !to) return null;
  return { startRow: from.r, endRow: to.r, startColumn: from.c, endColumn: to.c };
}

function decodeAddr(addr: string): { r: number; c: number } | null {
  const match = /^([A-Z]+)(\d+)$/i.exec(addr.trim());
  if (!match?.[1] || !match[2]) return null;
  let column = 0;
  for (const char of match[1].toUpperCase()) column = column * 26 + (char.charCodeAt(0) - 64);
  return { r: Number(match[2]) - 1, c: column - 1 };
}

function excelSerial(date: Date, date1904: boolean): number {
  const utc = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate(), date.getHours(), date.getMinutes(), date.getSeconds());
  const epoch = date1904 ? Date.UTC(1904, 0, 1) : Date.UTC(1899, 11, 30);
  return (utc - epoch) / 86_400_000;
}

function errorText(value: unknown): string {
  if (value && typeof value === "object" && "error" in value && typeof value.error === "string") return value.error;
  return "#VALUE!";
}
