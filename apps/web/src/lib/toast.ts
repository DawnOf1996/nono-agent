/**
 * 轻量 toast：一份模块级状态 + 订阅，不引第三方库。
 *
 * 为什么不跟 store/chat.ts 那些放一起：toast 没有业务状态，只是「冒出来一条、
 * 几秒后自己没了」。为一个几十行的东西单开一个领域 store 是过度包装。放 lib/
 * 是因为它两头都要用 —— 任何组件能调 toast()，而 <Toaster /> 只在 App 里挂一次。
 *
 * 之所以能和 React 拆开：这一层完全不碰 React，只有纯数据进出，因此能像
 * lib/updater.ts 那样用 node --test 直接跑，不用起浏览器。
 */

/**
 * 挂在屏幕上的一条。id 用来定位和手动关掉。 */
export type ToastItem = {
  id: number;
  message: string;
};

/**
 * toast 的落点：一个容器栈，提示贴栈顶那个。
 *
 * 为什么需要这个：提示属于「用户此刻在看的那层界面」。在设置弹框里点了「检查更新」，
 * 提示就该贴在弹框底部，而不是飘到整个窗口底部 —— 后者看着像这条提示跟当前弹框
 * 没关系。弹框自己登记进来，没登记时退回整窗。
 *
 * 用栈而不是单个值：弹框可以套弹框（设置里再弹一个确认框），后来的一定盖在
 * 先来的上面，取栈顶自然就是「最上面那层」。
 */
let anchors: HTMLElement[] = [];

export function registerToastAnchor(element: HTMLElement): () => void {
  anchors = [...anchors, element];
  emit();
  return () => {
    anchors = anchors.filter((item) => item !== element);
    emit();
  };
}

/** 栈顶那个容器；没人登记就是 null，调用方退回 document.body。 */
export function getToastAnchor(): HTMLElement | null {
  return anchors.length > 0 ? anchors[anchors.length - 1] : null;
}

/**
 * 2.6 秒。短到不像在催用户，长到够读完一句「已是最新版本 0.3.6」。
 * 比常见的 3~4 秒短，因为这里的提示都是「结果告知」而不是「需要处理」。
 */
const DEFAULT_DURATION = 2600;

let seq = 0;
let items: ToastItem[] = [];
const timers = new Map<number, ReturnType<typeof setTimeout>>();
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function subscribeToasts(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * 给 useSyncExternalStore 用，必须是「内容没变就返回同一个数组」。
 * 每次调用都造新数组会被 React 判定为快照一直在变，陷入死循环。
 */
export function getToasts(): ToastItem[] {
  return items;
}

export function dismissToast(id: number): void {
  const timer = timers.get(id);
  if (timer !== undefined) {
    clearTimeout(timer);
    timers.delete(id);
  }
  const next = items.filter((item) => item.id !== id);
  // 已经不在列表里还换引用，等于凭空多触发一次重渲染。
  if (next.length === items.length) return;
  items = next;
  emit();
}

/** 弹一条，返回它的 id（想提前关掉时用得上）。 */
export function toast(message: string, options?: { duration?: number }): number {
  const id = ++seq;
  items = [...items, { id, message }];
  emit();
  timers.set(
    id,
    setTimeout(() => dismissToast(id), options?.duration ?? DEFAULT_DURATION),
  );
  return id;
}