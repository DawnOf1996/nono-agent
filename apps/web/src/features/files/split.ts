/** 底部工作台的分栏树。拖到边缘拆出新栏，拖到中间并入已有栏。 */

export type DropZone = "left" | "right" | "up" | "down" | "center";

export type SplitLeaf = {
  kind: "leaf";
  id: string;
  tabIds: string[];
  active: string | null;
};

export type SplitBranch = {
  kind: "split";
  id: string;
  dir: "row" | "col";
  /** 第一个子栏所占比例。 */
  ratio: number;
  children: [SplitNode, SplitNode];
};

export type SplitNode = SplitLeaf | SplitBranch;

export function emptySplit(): SplitLeaf {
  return { kind: "leaf", id: "bottom-root", tabIds: [], active: null };
}

export function collectTabIds(node: SplitNode): string[] {
  if (node.kind === "leaf") return node.tabIds;
  return [...collectTabIds(node.children[0]), ...collectTabIds(node.children[1])];
}

export function firstLeaf(node: SplitNode): SplitLeaf {
  if (node.kind === "leaf") return node;
  return firstLeaf(node.children[0]);
}

function findLeaf(node: SplitNode, id: string): SplitLeaf | null {
  if (node.kind === "leaf") return node.id === id ? node : null;
  return findLeaf(node.children[0], id) ?? findLeaf(node.children[1], id);
}

function mapNode(node: SplitNode, id: string, visit: (leaf: SplitLeaf) => SplitNode): SplitNode {
  if (node.kind === "leaf") return node.id === id ? visit(node) : node;
  const left = mapNode(node.children[0], id, visit);
  const right = mapNode(node.children[1], id, visit);
  if (left === node.children[0] && right === node.children[1]) return node;
  return { ...node, children: [left, right] };
}

export function collapse(node: SplitNode): SplitNode {
  if (node.kind === "leaf") return node;
  const left = collapse(node.children[0]);
  const right = collapse(node.children[1]);
  const leftEmpty = left.kind === "leaf" && left.tabIds.length === 0;
  const rightEmpty = right.kind === "leaf" && right.tabIds.length === 0;
  if (leftEmpty && rightEmpty) return { kind: "leaf", id: left.id, tabIds: [], active: null };
  if (leftEmpty) return right;
  if (rightEmpty) return left;
  if (left === node.children[0] && right === node.children[1]) return node;
  return { ...node, children: [left, right] };
}

export function removeTab(node: SplitNode, tabId: string): SplitNode {
  if (node.kind === "leaf") {
    if (!node.tabIds.includes(tabId)) return node;
    const tabIds = node.tabIds.filter((id) => id !== tabId);
    const active = node.active === tabId ? (tabIds[tabIds.length - 1] ?? null) : node.active;
    return { ...node, tabIds, active };
  }
  return collapse({
    ...node,
    children: [removeTab(node.children[0], tabId), removeTab(node.children[1], tabId)],
  });
}

/** 把标签放进某个分栏。目标栏不在了就落到第一栏。 */
export function placeTab(node: SplitNode, tabId: string, leafId: string, zone: DropZone): SplitNode {
  const stripped = removeTab(node, tabId);
  const target = findLeaf(stripped, leafId) ?? firstLeaf(stripped);
  if (zone === "center") {
    return mapNode(stripped, target.id, (leaf) => ({
      ...leaf,
      tabIds: [...leaf.tabIds, tabId],
      active: tabId,
    }));
  }
  const dir: SplitBranch["dir"] = zone === "left" || zone === "right" ? "row" : "col";
  const front = zone === "left" || zone === "up";
  const fresh: SplitLeaf = {
    kind: "leaf",
    id: `pane-${crypto.randomUUID()}`,
    tabIds: [tabId],
    active: tabId,
  };
  return collapse(
    mapNode(stripped, target.id, (leaf) => {
      const kept: SplitLeaf = { ...leaf, tabIds: leaf.tabIds.filter((id) => id !== tabId) };
      const branch: SplitBranch = {
        kind: "split",
        id: `split-${crypto.randomUUID()}`,
        dir,
        ratio: 0.5,
        children: front ? [fresh, kept] : [kept, fresh],
      };
      return branch;
    }),
  );
}

export function setRatio(node: SplitNode, splitId: string, ratio: number): SplitNode {
  const next = Math.min(0.8, Math.max(0.2, ratio));
  if (node.kind === "leaf") return node;
  if (node.id === splitId) return { ...node, ratio: next };
  return {
    ...node,
    children: [setRatio(node.children[0], splitId, ratio), setRatio(node.children[1], splitId, ratio)],
  };
}

export function activateInLeaf(node: SplitNode, leafId: string, tabId: string): SplitNode {
  return mapNode(node, leafId, (leaf) => (leaf.tabIds.includes(tabId) ? { ...leaf, active: tabId } : leaf));
}
