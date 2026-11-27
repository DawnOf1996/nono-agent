const LEFT_WIDTH_KEY = "nono.layout.leftWidth";
const RIGHT_WIDTH_KEY = "nono.layout.rightWidth";
const LEFT_OPEN_KEY = "nono.layout.leftOpen";
const RIGHT_OPEN_KEY = "nono.layout.rightOpen";
const BOTTOM_OPEN_KEY = "nono.layout.bottomOpen";
const BOTTOM_HEIGHT_KEY = "nono.layout.bottomHeight";

/** 菜单最多占屏幕宽度的 20%。 */
export const MENU_MAX_RATIO = 0.2;
/** 菜单收起后的图标栏宽度。 */
export const MENU_RAIL_WIDTH = 52;
const LEFT_DEFAULT = 260;
const RIGHT_DEFAULT = 320;
const LEFT_MIN = 180;
const RIGHT_MIN = 240;
// 中间栏的最小宽度。不是"能显示点什么"的底线，而是「对话页底部那排控件不打架」
// 的宽度 —— Composer 的 px-6（48）+ 内框 px-3（24）先扣掉 72，剩下的一行里：
//   左组：更多 / 连接器两个 size-8、附件 size-8、权限模式选择（约 98，标签最长
//         「全部允许」四个汉字）；
//   右组：模型选择（max-w-[10rem] = 160）+ 发送 size-8。
// 合起来约 414，加那 72 是 480 上下。以前留 280，中间栏被两边的面板挤到三百几
// 时，这一行的右组是 shrink-0（永不收缩），左组 min-w-0 压到 0 还是不够，于是
// 模型名、权限标签、发送键挤成一团，看着像叠在一起。
// 代价：窗口不够宽时先挤侧栏（左侧会掉到 MENU_MAX_RATIO 以内），而不是挤正文。
// 桌面端 minWidth 960，两侧都开也还有 960 - 180 - 240 = 480，不会真的被顶穿。
const CENTER_MIN = 480;
export const BOTTOM_MIN = 160;
const BOTTOM_DEFAULT = 260;

function readNumber(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    const value = raw ? Number(raw) : Number.NaN;
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}

function readOpen(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    if (raw === "0") return false;
    if (raw === "1") return true;
    return fallback;
  } catch {
    return fallback;
  }
}

export function loadLayoutPreference() {
  return {
    leftPreferred: readNumber(LEFT_WIDTH_KEY, LEFT_DEFAULT),
    rightPreferred: readNumber(RIGHT_WIDTH_KEY, RIGHT_DEFAULT),
    leftOpen: readOpen(LEFT_OPEN_KEY, true),
    rightOpen: readOpen(RIGHT_OPEN_KEY, true),
    bottomOpen: readOpen(BOTTOM_OPEN_KEY, false),
    bottomHeight: readNumber(BOTTOM_HEIGHT_KEY, BOTTOM_DEFAULT),
  };
}

export function saveLayoutPreference(pref: {
  leftPreferred: number;
  rightPreferred: number;
  leftOpen: boolean;
  rightOpen: boolean;
  bottomOpen: boolean;
  bottomHeight: number;
}) {
  try {
    localStorage.setItem(LEFT_WIDTH_KEY, String(Math.round(pref.leftPreferred)));
    localStorage.setItem(RIGHT_WIDTH_KEY, String(Math.round(pref.rightPreferred)));
    localStorage.setItem(LEFT_OPEN_KEY, pref.leftOpen ? "1" : "0");
    localStorage.setItem(RIGHT_OPEN_KEY, pref.rightOpen ? "1" : "0");
    localStorage.setItem(BOTTOM_OPEN_KEY, pref.bottomOpen ? "1" : "0");
    localStorage.setItem(BOTTOM_HEIGHT_KEY, String(Math.round(pref.bottomHeight)));
  } catch {
    // private mode / blocked storage
  }
}

export function columnWidths(
  viewport: number,
  leftPreferred: number,
  rightPreferred: number,
  leftOpen: boolean,
  rightOpen: boolean,
): { left: number; right: number } {
  const width = Math.max(0, viewport);
  let left = 0;
  if (leftOpen) {
    const ratioMax = Math.floor(width * MENU_MAX_RATIO);
    const reserved = CENTER_MIN + (rightOpen ? RIGHT_MIN : 0);
    const max = Math.max(0, Math.min(ratioMax, width - reserved));
    // 放不下 LEFT_MIN 就别硬挤。以前这里是 min(LEFT_MIN, max) —— LEFT_MIN 被
    // max 顶小了，窗口一窄就把菜单压到一百来像素，里面"新会话""展开其余 4 个会话"
    // 一个字一行地竖着排，比收成图标栏还难看。放不下就当它收起了。
    if (max >= LEFT_MIN) {
      left = Math.round(Math.min(max, Math.max(LEFT_MIN, leftPreferred)));
    }
  }
  // 收成图标栏时占的是 MENU_RAIL_WIDTH，不是 0：以前这里只看 leftOpen，菜单被挤成
  // 0 宽的同一帧里，图标栏其实已经画出来了，右侧面板却按"不占宽度"算，多算出 52px。
  const leftOccupied = left > 0 ? left : MENU_RAIL_WIDTH;
  let right = 0;
  if (rightOpen) {
    const max = Math.max(0, width - leftOccupied - CENTER_MIN);
    // 同样的道理给右栏：文件栏挤到几十像素时连标签都放不下，不如整个收起来。
    if (max >= RIGHT_MIN) {
      right = Math.round(Math.min(max, Math.max(RIGHT_MIN, rightPreferred)));
    }
  }
  return { left, right };
}
