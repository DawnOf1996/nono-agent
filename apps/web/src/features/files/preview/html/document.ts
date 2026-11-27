import type { HtmlBundle } from "@/features/files/preview/html/pack";

function bytesToBase64(data: Uint8Array): string {
  const chunkSize = 0x8000;
  let binary = "";
  for (let index = 0; index < data.length; index += chunkSize) {
    binary += String.fromCharCode(...data.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

/**
 * 外层文档在 iframe 里执行。依赖的 Blob URL 也在 iframe 里创建，
 * 这样不透明源点不会去加载父页面的地址。
 */
export function createHtmlDocument(bundle: HtmlBundle): string {
  const payload = bytesToBase64(
    new TextEncoder().encode(JSON.stringify({ html: bundle.html, assets: bundle.assets })),
  );
  return `<!doctype html><meta charset="utf-8"><script>(()=>{
const bytes=data=>Uint8Array.from(atob(data),character=>character.charCodeAt(0));
const text=data=>new TextDecoder("utf-8",{fatal:true}).decode(bytes(data));
const bundle=JSON.parse(text("${payload}"));
// 预览页里的链接不许自己开窗口：sandbox 放开 allow-popups 后，target=_blank 会让
// 桌面版弹出一个原生窗体。改成把地址递给宿主，由宿主开成侧边栏的浏览器/文件标签。
// 监听器必须挂在 document.open 之前 —— document.open/write 只换 DOM，window 还在，
// 反过来写就只剩一个已经作废的文档引用了。
window.addEventListener("click",(event)=>{
  if(event.defaultPrevented||event.button!==0)return;
  if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  const anchor=event.target&&event.target.closest?event.target.closest("a[href]"):null;
  if(!anchor)return;
  const href=anchor.getAttribute("href")||"";
  if(!href||href.startsWith("#"))return;
  event.preventDefault();
  // 原样递过去：相对路径要由宿主按这个 HTML 文件的位置解析，iframe 里没有这个上下文。
  parent.postMessage({nono:"open-link",href},"*");
},true);
let html=bundle.html;
if(bundle.assets.length){
  const parsed=new DOMParser().parseFromString(html,"text/html");
  for(const asset of bundle.assets){
    const script=asset.kind==="script";
    const url=URL.createObjectURL(new Blob([asset.text],{type:script?"application/javascript":"text/css"}));
    const attribute=script?"src":"href";
    for(const element of parsed.querySelectorAll(script?"script[src]":"link[rel~=\\"stylesheet\\" i][href]")){
      if(element.getAttribute(attribute)===asset.reference)element.setAttribute(attribute,url);
    }
  }
  html="<!doctype html>"+parsed.documentElement.outerHTML;
}
document.open();document.write(html);document.close();
})()</script>`;
}
