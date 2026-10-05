/* Mermaid 运行时初始化。
 *
 * `markdown_extensions` 的 superfences 把 ```mermaid 渲染成 <div class="mermaid">，
 * Material 本身不带 mermaid 运行时，所以要在这里手动跑一遍。
 *
 * 用 document$.subscribe 而不是 mermaid.initialize({ startOnLoad: true })：
 * 站点开了 navigation.instant，左侧导航切页是替换 DOM 而不触发 window load，
 * startOnLoad 只会在首次打开时生效，之后切到含图的页面图就是空的。
 */
document$.subscribe(function () {
  if (typeof mermaid === "undefined") return;

  mermaid.initialize({
    startOnLoad: false,
    theme: "default",
    // 图里有中文标签，显式给个能完整显示的字体栈，避免回退成方块
    fontFamily: 'ui-sans-serif, system-ui, "Segoe UI", "Microsoft YaHei", sans-serif',
    securityLevel: "loose",
  });

  mermaid
    .run({ querySelector: ".mermaid" })
    .catch(function (err) {
      // 语法错误时保留原始代码块，不要把整页变成空白
      console.error("mermaid render failed:", err);
    });
});
