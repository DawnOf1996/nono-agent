/**
 * 选区宿主契约的测试：三种文本预览都要能被选中挂引用/评论。
 *   node --experimental-strip-types --test src/features/files/preview/host-contract.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";

/** 极简 DOM 替身：只实现 closest 沿祖先链往上找的能力。 */
type Attrs = Record<string, string>;

function makeNode(own: Attrs, parent: FakeNode | null): FakeNode {
  const node: FakeNode = {
    own,
    parent,
    closest(selector: string): FakeNode | null {
      // 真实 DOM 的 [attr] 是属性名精确匹配，不能用 in 做子串判断 ——
      // 否则 [data-line] 会命中只带 data-line-number 的节点。
      const key = selectorToKey(selector);
      let cursor: FakeNode | null = node;
      while (cursor) {
        if (Object.prototype.hasOwnProperty.call(cursor.own, key)) return cursor;
        cursor = cursor.parent;
      }
      return null;
    },
  };
  return node;
}

interface FakeNode {
  own: Attrs;
  parent: FakeNode | null;
  closest(selector: string): FakeNode | null;
}

function selectorToKey(selector: string): string {
  return selector.replace(/[[\]]/g, "");
}

/** 复刻 SelectionQuote.lineOf 的判定顺序：data-line 优先，退回 data-line-number。 */
function lineOf(node: FakeNode): number | null {
  const marked = node.closest("data-line");
  if (marked) {
    const offsetEl = node.closest("data-line-offset");
    // 缺偏移容器时基准是 1，不是 0。
    const offset = offsetEl ? Number(offsetEl.own["data-line-offset"]) : 1;
    const inSegment = Number(marked.own["data-line"]);
    if (Number.isFinite(inSegment)) return (Number.isFinite(offset) ? offset : 1) + Math.max(1, inSegment) - 1;
  }
  const numbered = node.closest("data-line-number");
  if (numbered) {
    const value = Number(numbered.own["data-line-number"]);
    if (Number.isFinite(value)) return value;
  }
  return null;
}

test("Markdown：data-line 加段偏移得到源文件行号", () => {
  const root = makeNode({ "data-quote-source": "file", "data-file-path": "a.md" }, null);
  const seg = makeNode({ "data-line-offset": "40" }, root);
  const para = makeNode({ "data-line": "3" }, seg);
  const text = makeNode({}, para);
  assert.equal(lineOf(text), 42);
});

test("Markdown：没有偏移容器时偏移按 0 算", () => {
  const root = makeNode({ "data-quote-source": "file" }, null);
  const para = makeNode({ "data-line": "1" }, root);
  assert.equal(lineOf(para), 1);
});

test("代码预览：data-line-number 直接给出行号", () => {
  const root = makeNode({ "data-quote-source": "file", "data-file-path": "a.py" }, null);
  const line = makeNode({ "data-line-number": "17" }, root);
  const span = makeNode({}, line);
  assert.equal(lineOf(span), 17);
});

test("纯文本预览：data-line-number 同样生效", () => {
  const root = makeNode({ "data-quote-source": "file", "data-file-path": "notes.txt" }, null);
  const line = makeNode({ "data-line-number": "1" }, root);
  assert.equal(lineOf(line), 1);
});

test("没有任何行号标记时返回 null，不猜", () => {
  const root = makeNode({ "data-quote-source": "file" }, null);
  const para = makeNode({}, root);
  assert.equal(lineOf(para), null);
});

test("行号标记不在宿主内时不误取", () => {
  // 标记挂在另一个树上（另一个文件预览），不该被算进来。
  const otherFile = makeNode({ "data-quote-source": "file", "data-line-number": "99" }, null);
  const mine = makeNode({ "data-quote-source": "file" }, null);
  const para = makeNode({}, mine);
  assert.equal(lineOf(para), null);
  assert.equal(lineOf(otherFile), 99);
});