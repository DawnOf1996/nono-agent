import type {
  Border,
  Cell,
  CellHyperlinkValue,
  Fill,
  Font,
  RichText as RichTextRun,
  Style,
  Workbook,
  Worksheet,
  WorksheetModel,
} from "exceljs";
import { ValueType } from "exceljs";
import {
  BaselineOffset,
  BooleanNumber,
  BorderStyleTypes,
  CellValueType,
  createParagraphId,
  createSectionId,
  CustomRangeType,
  HorizontalAlign,
  TextDecoration,
  VerticalAlign,
  WorksheetHiddenState,
  WrapStrategy,
  type IBorderData,
  type IBorderStyleData,
  type ICellData,
  type IColumnData,
  type IDocumentBody,
  type IDocumentData,
  type IFreeze,
  type IParagraph,
  type IRange,
  type IRowData,
  type IStyleData,
  type ISectionBreak,
  type ITextDecoration,
  type ITextRotation,
  type ITextRun,
  type ITextStyle,
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
  const mergeRanges = mergedRangesOf(sheet);
  const rowNumbers = rowNumbersOf(sheet);

  // eachRow 的默认分支按 row.hasValues 过滤：整行都只有边框没有值时 hasValues 为 false，
  // 整行会被跳过，下面的 eachCell 根本不会执行。所以改用 model.rows 取文件里真实存在的行号，
  // 既不漏行，也不会像 eachRow({includeEmpty:true}) 那样把中间的空行全部物化出来。
  for (const rowNumber of rowNumbers) {
    const row = sheet.getRow(rowNumber);
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
    // includeEmpty 必须为 true：只有边框没有值的格子 type 是 Null，默认分支会把它们整个丢掉，
    // 导致撑版式的空格子边框全部不渲染。cellOf 内部会过滤掉真正无样式的空格。
    row.eachCell({ includeEmpty: true }, (cell, colNumber) => {
      const columnIndex = colNumber - 1;
      const master = cell.master;
      const at = positionOf(master);
      const range = cell.isMerged ? mergeRanges.get(`${at.row - 1}:${at.col - 1}`) : undefined;
      let data: ICellData | null = null;
      if (range) {
        const perimeter = mergedBorderOf(sheet, range);
        if (cell.isMerged && master.address !== cell.address) {
          // 合并从格：Univer 是逐格绘制边框的，从格没有样式就会让合并区缺边。
          // 这里补一份只含外框的样式，边按所在位置只朝合并区外侧。
          const edge = perimeter ? mergeEdgeOf(perimeter, range, rowIndex, columnIndex) : null;
          const styleId = styles.id(edge ? { bd: edge } : null);
          if (styleId !== undefined) data = { s: styleId };
        } else {
          data = cellOf(cell, styles, date1904, perimeter);
        }
      } else {
        data = cellOf(cell, styles, date1904, null);
      }
      if (data === null) return;
      const rowCells = cellData[rowIndex] ?? {};
      rowCells[columnIndex] = data;
      cellData[rowIndex] = rowCells;
    });
  }

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

  const view = sheet.views?.[0];
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

function cellOf(cell: Cell, styles: StyleBook, date1904: boolean, mergedBorder?: IBorderData | null): ICellData | null {
  const out: ICellData = {};
  const value = plainValue(cell, date1904);
  if (value !== null) {
    out.t = value.t;
    out.v = value.v;
    // v 保留纯文本当兜底，p 承载富文本与超链接。Univer 自己的富文本单元格也是 v/p 同时写
    // （见 sheets-ui 里 cellData.v = newDataStream 后再 cellData.p = snapshot），
    // 这样即使 p 的渲染出问题，至少文字还在。
    if (value.body) out.p = value.body;
  }
  const formula = cell.formula?.trim();
  if (formula) out.f = formula.startsWith("=") ? formula : `=${formula}`;
  const style = styles.id(styleOf(cell.style, mergedBorder));
  if (style !== undefined) out.s = style;
  if (value === null && !formula && style === undefined) return null;
  return out;
}

type PlainValue = { t: CellValueType; v: string | number | boolean; body?: IDocumentData };
type RichBody = { text: string; body: IDocumentBody };

/**
 * 预览会原样渲染用户提供的文件，所以链接协议要自己把关。
 * Univer 内部渲染时也会过一遍 isSafeUrl，但那是运行期的最后一道防线；
 * 这里在生成快照时就拦掉，避免把 javascript: / data: 之类写进文档模型。
 */
const SAFE_LINK = /^(?:https?|mailto|ftp):/i;

function safeUrlOf(url: unknown): string | null {
  if (typeof url !== "string") return null;
  const trimmed = url.trim();
  return SAFE_LINK.test(trimmed) ? trimmed : null;
}

/** 单元格内换行统一成 \r：Univer 的 dataStream 只认 \r 作为段分隔。 */
function normalizeBreaks(text: string): string {
  return text.replace(/\r\n|\n/g, "\r");
}

/**
 * 按 Univer 的约定拼 dataStream / paragraphs：以 \r\n 收尾，
 * 每个 \r 结束一段，paragraph.startIndex 指向这个 \r 本身。
 * 调用方需保证传入的 text 已做过换行归一化，否则偏移量会和 dataStream 对不上。
 */
function bodyOfText(text: string, extra?: Partial<IDocumentBody>): IDocumentBody {
  const dataStream = text.endsWith("\r\n") ? text : `${text}\r\n`;
  const ids = new Set<string>();
  const paragraphs: IParagraph[] = [];
  for (let index = 0; index < dataStream.length; index += 1) {
    if (dataStream[index] === "\r") paragraphs.push({ startIndex: index, paragraphId: createParagraphId(ids) });
  }
  const body: IDocumentBody = { dataStream, paragraphs, ...extra };
  if (paragraphs.length > 1) {
    const sectionBreaks: ISectionBreak[] = [{ sectionId: createSectionId(new Set()), startIndex: dataStream.indexOf("\n") }];
    body.sectionBreaks = sectionBreaks;
  }
  return body;
}

let documentSeq = 0;
let linkSeq = 0;

function documentOf(body: IDocumentBody): IDocumentData {
  documentSeq += 1;
  return { id: `doc-${documentSeq}`, body, documentStyle: {} };
}

/**
 * 富文本转成 Univer 的文档体：每段带字体的生成一个 textRun。
 * textRun.ed 是开区间（与 Univer 的 getCellStyleBySnapshot 对齐：`ed - st >= 正文长度`）。
 * 偏移量用 JS 的 .length 累加，和 Univer 的下标一致（都是 UTF-16 码元，不能用 Array.from）。
 */
function richTextBody(runs: RichTextRun[]): RichBody | null {
  let text = "";
  const textRuns: ITextRun[] = [];
  for (const run of runs) {
    const chunk = normalizeBreaks(run.text ?? "");
    if (chunk === "") continue;
    const start = text.length;
    text += chunk;
    const ts = textStyleOf(run.font);
    if (ts) textRuns.push({ st: start, ed: text.length, ts });
  }
  if (text === "") return null;
  return { text, body: bodyOfText(text, textRuns.length > 0 ? { textRuns } : {}) };
}

/**
 * 超链接转成文档体：显示文字进 dataStream，链接本身进 customRanges。
 * customRange.endIndex 是闭区间（对齐 sheets-ui 的 generateBody：urlText.length - 1）。
 */
function hyperlinkBody(value: CellHyperlinkValue): RichBody | null {
  const url = safeUrlOf(value.hyperlink);
  if (url === null) return null;
  // Excel 允许只存链接不写显示文字，这种情况下用链接本身当显示文本。
  const text = normalizeBreaks(String(value.text ?? "").trim()) || url;
  linkSeq += 1;
  return {
    text,
    body: bodyOfText(text, {
      customRanges: [
        {
          startIndex: 0,
          endIndex: text.length - 1,
          rangeId: `link-${linkSeq}`,
          rangeType: CustomRangeType.HYPERLINK,
          properties: { url },
        },
      ],
    }),
  };
}

function plainValue(cell: Cell, date1904: boolean): PlainValue | null {
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
    const rich = richTextBody(value.richText);
    return rich ? { t: CellValueType.STRING, v: rich.text, body: documentOf(rich.body) } : null;
  }
  if (value && typeof value === "object" && "text" in value && "hyperlink" in value) {
    const link = hyperlinkBody(value as CellHyperlinkValue);
    if (link) return { t: CellValueType.STRING, v: link.text, body: documentOf(link.body) };
    // 协议不在白名单里就不生成链接，但显示文字要留住。
    const text = String((value as CellHyperlinkValue).text ?? "");
    return text ? { t: CellValueType.STRING, v: text } : null;
  }
  if (typeof value === "string") return value ? { t: CellValueType.STRING, v: value } : null;
  if (typeof value === "number") return { t: CellValueType.NUMBER, v: value };
  if (typeof value === "boolean") return { t: CellValueType.BOOLEAN, v: value };
  return null;
}

/**
 * 字符级字体属性。IStyleData 和 ITextStyle 都继承 IStyleBase，
 * 所以单元格样式和富文本分段可以共用这一份映射。
 */
function textStyleOf(font: Partial<Font> | undefined): ITextStyle | null {
  if (!font) return null;
  const out: ITextStyle = {};
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
  return Object.keys(out).length > 0 ? out : null;
}

function styleOf(style: Partial<Style> | undefined, mergedBorder?: IBorderData | null): IStyleData | null {
  if (!style && !mergedBorder) return null;
  const out: IStyleData = {};
  const font = textStyleOf(style?.font);
  if (font) Object.assign(out, font);
  const background = fillColor(style?.fill);
  if (background) out.bg = { rgb: background };
  const own = borderOf(style?.border);
  const border = mergedBorder ? { ...own, ...mergedBorder } : own;
  if (border && Object.keys(border).length > 0) out.bd = border;
  const alignment = style?.alignment;
  if (alignment) {
    if (alignment.horizontal) out.ht = HORIZONTAL[alignment.horizontal];
    if (alignment.vertical) out.vt = VERTICAL[alignment.vertical];
    if (alignment.wrapText) out.tb = WrapStrategy.WRAP;
    if (alignment.shrinkToFit) out.stf = BooleanNumber.TRUE;
    const rotation = rotationOf(alignment.textRotation);
    if (rotation) out.tr = rotation;
  }
  if (style?.numFmt && style.numFmt !== "General") out.n = { pattern: style.numFmt };
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
  for (const view of sheet.views ?? []) {
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

/**
 * 文件里真实存在的行号。
 * exceljs 的 WorksheetModel 类型漏了 rows，但 get model() 运行时会从 _rows 里组装出来，
 * 只收录真正带 model 的行，也就是 sheetData 里出现过的那些。
 */
function rowNumbersOf(sheet: Worksheet): number[] {
  const rows = (sheet.model as WorksheetModel & { rows?: Array<{ number?: number }> }).rows;
  const numbers: number[] = [];
  for (const row of rows ?? []) {
    if (typeof row.number === "number" && row.number > 0) numbers.push(row.number);
  }
  return numbers;
}

/**
 * exceljs 的 Address 接口把 row/col 错标成 string，运行时 Cell 实际返回的是 1 起的 number。
 * 这里窄化一次，免得整条链路上到处做类型断言。
 */
function positionOf(cell: Cell): { row: number; col: number } {
  const { row, col } = cell as unknown as { row: number; col: number };
  return { row, col };
}

/** 合并区按左上角坐标索引，键是 0 基的 `${row}:${col}`。 */
function mergedRangesOf(sheet: Worksheet): Map<string, IRange> {
  const map = new Map<string, IRange>();
  for (const merge of sheet.model.merges ?? []) {
    const range = decodeRange(merge);
    if (range) map.set(`${range.startRow}:${range.startColumn}`, range);
  }
  return map;
}

/**
 * 快照里只有主格代表整个合并区，而 Excel 的外框常由周边格子各自拼出，
 * 只留主格会缺边。exceljs 导入时特意不合并各格样式（Worksheet#_parseMergeCells），
 * 所以这里按区域把四条边依次回退到周边格子上取，主格自身的边框优先。
 */
function mergedBorderOf(sheet: Worksheet, range: IRange): IBorderData | null {
  const { startRow, startColumn, endRow, endColumn } = range;
  const edgeOf = (row: number, column: number, side: "top" | "bottom" | "left" | "right") =>
    borderSide(sheet.getCell(row + 1, column + 1).style?.border?.[side]);
  const out: IBorderData = {};
  for (let column = startColumn; column <= endColumn; column += 1) {
    const side = edgeOf(startRow, column, "top");
    if (side) {
      out.t = side;
      break;
    }
  }
  for (let column = startColumn; column <= endColumn; column += 1) {
    const side = edgeOf(endRow, column, "bottom");
    if (side) {
      out.b = side;
      break;
    }
  }
  for (let row = startRow; row <= endRow; row += 1) {
    const side = edgeOf(row, startColumn, "left");
    if (side) {
      out.l = side;
      break;
    }
  }
  for (let row = startRow; row <= endRow; row += 1) {
    const side = edgeOf(row, endColumn, "right");
    if (side) {
      out.r = side;
      break;
    }
  }
  return out.t || out.b || out.l || out.r ? out : null;
}

/**
 * 合并区里某个从格应该承担的外框边：只有落在合并区最外圈的那几条边。
 * 逐格渲染时这些边首尾相接，正好拼出完整轮廓；内部格子返回 null 不出样式。
 */
function mergeEdgeOf(perimeter: IBorderData, range: IRange, row: number, column: number): IBorderData | null {
  const out: IBorderData = {};
  if (row === range.startRow && perimeter.t) out.t = perimeter.t;
  if (row === range.endRow && perimeter.b) out.b = perimeter.b;
  if (column === range.startColumn && perimeter.l) out.l = perimeter.l;
  if (column === range.endColumn && perimeter.r) out.r = perimeter.r;
  return out.t || out.b || out.l || out.r ? out : null;
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
