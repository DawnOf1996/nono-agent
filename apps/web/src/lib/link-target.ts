/**
 * 链接目标解析：把正文里的一个 href 判成「网址 / 工作区文件 / 页内锚点 / 其它」。
 *
 * 主对话区和各个文件预览区共用这一份判断，保证同一个地址在对话气泡里和在 README
 * 预览里点开是同一个地方。打开动作在 lib/use-link-router，这里只负责判定。
 *
 * 最难的一段是「没写协议的地址」：mermaid.live 该开浏览器，./README.md 该开文件，
 * 两者长得几乎一样。判据是扩展名与 TLD 的重叠 —— `foo.md` 里 md 绝不会当域名，
 * `foo.live` 里 live 也不会当文件后缀。歧义项（.sh 这类既是 TLD 又是后缀的）
 * 按文件处理：在这个工具里，「指着一个脚本」比「指向一个岛国顶级域名」常见得多。
 */

const PROTOCOL = /^[a-z][a-z0-9+.-]*:/i;
const SCHEME_WINDOWS = /^[a-zA-Z]:[\\/]/;
/** 末段像域名：至少一个点、每段非空、TLD 是 2–24 位纯字母。 */
const DOMAIN_SHAPE = /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,24}$/i;

/**
 * 判文件还是网址时用到的后缀表。
 *
 * 只收「当域名解释一定错」的扩展名。像 com / io / co / sh / dev 这些既是顶级域名
 * 又是常见文件名的，这里刻意不收（.sh 除外，见文件头注释），让它们按网址处理 ——
 * 把 example.com 当成文件才是真正会坏的那种误判。
 */
const FILE_EXTENSIONS = new Set([
  "md", "markdown", "mdx", "txt", "text", "log", "lock", "csv", "tsv",
  "json", "jsonc", "jsonl", "yaml", "yml", "toml", "ini", "conf", "cfg", "env",
  "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts", "css", "scss", "less",
  "html", "htm", "xml", "svg", "vue", "svelte",
  "py", "pyi", "rb", "go", "rs", "java", "kt", "kts", "swift", "scala", "php",
  "c", "h", "cc", "cpp", "cxx", "hpp", "cs", "m", "mm", "pl", "lua", "r", "dart",
  "sql", "graphql", "proto", "sh", "bash", "zsh", "fish", "ps1", "bat", "cmd",
  "png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "tiff",
  "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods",
  "zip", "tar", "gz", "bz2", "7z", "rar", "exe", "dll", "so", "dylib", "bin",
  "mp3", "mp4", "wav", "mov", "webm", "ttf", "otf", "woff", "woff2",
]);

export type LinkKind = "web" | "file" | "anchor" | "external" | "unknown";

export type LinkTarget =
  | { kind: "web"; url: string }
  | { kind: "file"; path: string }
  | { kind: "anchor"; fragment: string }
  | { kind: "external"; url: string }
  | { kind: "unknown" };

/** 末段后缀（小写、不含点）。 */
function extensionOf(bare: string): string {
  const name = bare.slice(Math.max(bare.lastIndexOf("/"), bare.lastIndexOf("\\")) + 1);
  const dot = name.lastIndexOf(".");
  // 开头的点不算后缀（`.gitignore` 是文件名，不是后缀为空）。
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** 没写协议的地址到底该按网址还是按文件解释。 */
function looksLikeWeb(bare: string): boolean {
  // 反斜杠只会出现在 Windows 路径里，网址不用它。
  if (/\\/.test(bare)) return false;
  if (bare.startsWith(".")) return false;

  // 正斜杠两种都可能：`docs/guide.md` 和 `example.com/guide` 都是常见写法。
  // 判据是最后一段的后缀 —— 命中文件后缀表就是路径（example.com/x.ts），
  // 否则看整串是否像域名（example.com/guide）。
  if (FILE_EXTENSIONS.has(extensionOf(bare))) return false;
  const host = bare.split("/")[0] ?? "";
  return DOMAIN_SHAPE.test(host);
}

/**
 * 判一个 href 该怎么打开。
 *
 * `fromFile` 是这段正文所在文件的**完整路径**（不是它的目录），相对路径以它所在
 * 的那一级为基准解析 —— README 里的 `../img/a.png` 要相对 README 自己那层去上跳。
 * 对话区没有这个上下文，传空串即可，这时按工作区根解析。
 *
 * `root` 是当前工作区。相对路径越出工作区时返回 unknown —— 文件接口只认工作区内
 * 的路径，硬开只会得到一个永远加载不出来的标签页。
 */
export function resolveLinkTarget(
  href: string | undefined,
  options: { root?: string; fromFile?: string } = {},
): LinkTarget {
  const raw = (href ?? "").trim();
  if (!raw) return { kind: "unknown" };
  // 页内锚点留在原地，不开标签页。
  if (raw.startsWith("#")) return { kind: "anchor", fragment: raw.slice(1) };

  // 盘符必须在协议判断之前：`C:\x` 会被 ^[a-z][a-z0-9+.-]*: 认成 C 协议。
  if (SCHEME_WINDOWS.test(raw)) return { kind: "file", path: raw };

  if (PROTOCOL.test(raw)) {
    if (/^https?:/i.test(raw)) {
      const safe = httpUrlOf(raw);
      return safe ? { kind: "web", url: safe } : { kind: "unknown" };
    }
    if (/^file:/i.test(raw)) {
      const local = fileUrlToPath(raw);
      return local ? { kind: "file", path: local } : { kind: "unknown" };
    }
    // mailto:、vscode: 之类交给系统，但只认协议本身，不去猜参数。
    return { kind: "external", url: raw };
  }

  // 查询串与锚点只在判「是不是路径」时剥掉：网址要带着它们。
  const bare = raw.split("#")[0]!.split("?")[0]!.trim();
  if (!bare) return { kind: "unknown" };

  if (looksLikeWeb(bare)) {
    const safe = httpUrlOf(raw);
    if (safe) return { kind: "web", url: safe };
  }

  const path = resolvePathTarget(bare, options.root ?? "", options.fromFile ?? "");
  return path ? { kind: "file", path } : { kind: "unknown" };
}

/** 把无协议的地址补上 https 再校验；只放行 http/https。 */
function httpUrlOf(target: string): string | null {
  const withScheme = PROTOCOL.test(target) ? target : `https://${target}`;
  try {
    const url = new URL(withScheme);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString();
  } catch {
    return null;
  }
}

/** `file:///D:/work/a.md` → `D:\work\a.md`。UNC 与百分号编码一并处理。 */
function fileUrlToPath(target: string): string | null {
  try {
    const url = new URL(target);
    // 先剥开头的斜杠再换分隔符：换完 `/D:/x` 就变成了 `\D:\x`，再也认不出盘符。
    let path = decodeURIComponent(url.pathname).replace(/^\//, "");
    path = path.replace(/\//g, "\\");
    if (url.hostname) path = `\\\\${url.hostname}${path}`;
    return path.replace(/[\\/]+$/, "") || null;
  } catch {
    return null;
  }
}

/**
 * 把路径类目标解析成工作区内的绝对路径。
 *
 * 绝对路径原样返回（是否越界交给服务端判）；相对路径以 `fromFile` 所在目录或工作区
 * 根为基准，折叠 `.` / `..` 后必须还落在 `root` 里，否则返回 null。
 */
function resolvePathTarget(target: string, root: string, fromFile: string): string | null {
  if (SCHEME_WINDOWS.test(target) || target.startsWith("/") || target.startsWith("\\")) return target;
  // 没有工作区就没有「相对」的基准，宁可不给开，也不要猜一个必然错的位置。
  if (!root) return null;
  // 通配与查询/片段字符在文件名里本来就非法，出现即说明这不是路径。
  if (/[*?<>|":]/.test(target)) return null;

  // 基准是「正文所在文件」，所以要先砍掉文件名再拼：`./a.md` 相对的是它自己这层目录。
  const base = normalizePathKey(fromFile ? parentOf(fromFile) : root);
  const resolved = foldPath(`${base}\\${target}`);
  const rootKey = normalizePathKey(root);
  if (resolved !== rootKey && !resolved.toLowerCase().startsWith(`${rootKey.toLowerCase()}\\`)) return null;
  return resolved;
}

/** 折叠 `.` / `..`，统一成反斜杠，方便和 `normalizeWorkspace` 的输出比较。 */
export function foldPath(path: string): string {
  const slash = path.replace(/\\/g, "/");
  const drive = /^([a-zA-Z]:)\//.exec(slash)?.[1];
  const body = drive ? slash.slice(drive.length + 1) : slash;
  const out: string[] = [];
  for (const part of body.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      // 越过根就停住，不能再往上退。
      out.pop();
      continue;
    }
    out.push(part);
  }
  return drive ? `${drive}\\${out.join("\\")}` : out.join("\\");
}

function normalizePathKey(path: string): string {
  return foldPath(path).replace(/[\\/]+$/, "");
}

/** 砍掉末段文件名，得到所在目录（保留盘符与根）。 */
function parentOf(path: string): string {
  const normalized = path.replace(/[\\/]+$/, "");
  const cut = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
  if (cut < 0) return "";
  // `D:\a\b.md` 在 cut=2 处切开，剩 `D:` —— 正好是根，不能再往上砍。
  if (cut === 2 && /^[a-zA-Z]:/.test(normalized)) return normalized;
  return normalized.slice(0, cut);
}
