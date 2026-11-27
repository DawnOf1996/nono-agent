/**
 * 权限/提问卡片的共用外壳。
 *
 * 两处都用同一套定位与阴影，单独抽出来是为了它们别各自漂一份。
 * 非模态：只盖住对话区，且不吃点击 —— 挂起等确认时仍能切到别的会话。
 */
export const SHELL =
  "pointer-events-none fixed inset-y-0 left-[260px] right-0 z-20 flex items-end justify-center p-6";

export const CARD =
  "pointer-events-auto w-full max-w-lg rounded-2xl border border-line bg-bg p-5 shadow-[0_16px_48px_var(--shadow-color-strong)]";