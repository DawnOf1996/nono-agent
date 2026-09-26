const LEFT_WIDTH_KEY = "nono.layout.leftWidth";
const RIGHT_WIDTH_KEY = "nono.layout.rightWidth";
const LEFT_OPEN_KEY = "nono.layout.leftOpen";
const RIGHT_OPEN_KEY = "nono.layout.rightOpen";

/** 菜单最多占屏幕宽度的 20%。 */
export const MENU_MAX_RATIO = 0.2;
/** 菜单收起后的图标栏宽度。 */
export const MENU_RAIL_WIDTH = 52;
const LEFT_DEFAULT = 260;
const RIGHT_DEFAULT = 320;
const LEFT_MIN = 180;
const RIGHT_MIN = 240;
const CENTER_MIN = 280;

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
  };
}

export function saveLayoutPreference(pref: {
  leftPreferred: number;
  rightPreferred: number;
  leftOpen: boolean;
  rightOpen: boolean;
}) {
  try {
    localStorage.setItem(LEFT_WIDTH_KEY, String(Math.round(pref.leftPreferred)));
    localStorage.setItem(RIGHT_WIDTH_KEY, String(Math.round(pref.rightPreferred)));
    localStorage.setItem(LEFT_OPEN_KEY, pref.leftOpen ? "1" : "0");
    localStorage.setItem(RIGHT_OPEN_KEY, pref.rightOpen ? "1" : "0");
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
    const min = Math.min(LEFT_MIN, max);
    left = Math.round(Math.min(max, Math.max(min, leftPreferred)));
  }
  const leftOccupied = leftOpen ? left : MENU_RAIL_WIDTH;
  let right = 0;
  if (rightOpen) {
    const max = Math.max(0, width - leftOccupied - CENTER_MIN);
    const min = Math.min(RIGHT_MIN, max);
    right = Math.round(Math.min(max, Math.max(min, rightPreferred)));
  }
  return { left, right };
}
