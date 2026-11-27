import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
// 副作用导入：theme.ts 在模块加载时就落一次主题，并把选中的模式推给桌面壳，
// 让窗口按钮那类原生控件跟着换色。首屏就得推，早于任何组件渲染，所以挂在入口
// 这里，而不是指望哪个组件恰好 import 到它。
import "./lib/theme";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
