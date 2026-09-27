import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import {
  bracketMatching,
  defaultHighlightStyle,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { Compartment, EditorState, type Extension, type Text } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { useEffect, useImperativeHandle, useRef, type Ref } from "react";

import { languageForPath } from "@/features/files/preview/languages";

export type CodeEditorHandle = {
  getValue: () => string;
  /** 换成新内容并把它当作已保存的基线（不进撤销历史的脏判断）。 */
  reset: (text: string) => void;
  /** 把当前内容记为已保存。 */
  markSaved: () => void;
  focus: () => void;
};

const surface = EditorView.theme({
  "&": { height: "100%", fontSize: "12.5px", backgroundColor: "var(--color-bg)" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.6" },
  ".cm-content": { padding: "12px 0", caretColor: "var(--color-ink)" },
  ".cm-gutters": {
    backgroundColor: "var(--color-bg)",
    color: "var(--color-faint)",
    borderRight: "1px solid var(--color-line)",
  },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 10px 0 12px", fontSize: "11px" },
  ".cm-activeLine": { backgroundColor: "#f6f7f9" },
  ".cm-activeLineGutter": { backgroundColor: "#f6f7f9", color: "var(--color-ink)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "#dbe4f5 !important",
  },
  ".cm-selectionMatch": { backgroundColor: "#eef2d6" },
  ".cm-panels": { backgroundColor: "var(--color-sidebar)", borderColor: "var(--color-line)" },
  ".cm-panels input, .cm-panels button": { fontSize: "12px" },
});

async function languageExtension(path: string): Promise<Extension | null> {
  const language = languageForPath(path);
  const lower = path.toLowerCase();
  switch (language) {
    case "typescript":
    case "javascript": {
      const { javascript } = await import("@codemirror/lang-javascript");
      return javascript({ typescript: language === "typescript", jsx: /\.(tsx|jsx)$/.test(lower) });
    }
    case "json":
      return (await import("@codemirror/lang-json")).json();
    case "python":
      return (await import("@codemirror/lang-python")).python();
    case "markdown":
    case "mdx":
      return (await import("@codemirror/lang-markdown")).markdown();
    case "html":
      return (await import("@codemirror/lang-html")).html();
    case "css":
    case "scss":
    case "less":
      return (await import("@codemirror/lang-css")).css();
    case "yaml":
      return (await import("@codemirror/lang-yaml")).yaml();
    case "rust":
      return (await import("@codemirror/lang-rust")).rust();
    case "go":
      return (await import("@codemirror/lang-go")).go();
    case "java":
      return (await import("@codemirror/lang-java")).java();
    case "c":
    case "cpp":
      return (await import("@codemirror/lang-cpp")).cpp();
    case "sql":
      return (await import("@codemirror/lang-sql")).sql();
    case "xml":
      return (await import("@codemirror/lang-xml")).xml();
    case "php":
      return (await import("@codemirror/lang-php")).php();
    default:
      return null;
  }
}

/** 可编辑的源码视图：CodeMirror 6，Ctrl/Cmd+S 保存，报告是否偏离已保存内容。 */
export function CodeEditor({
  path,
  initial,
  wrap,
  onDirtyChange,
  onSave,
  editorRef,
}: {
  path: string;
  initial: string;
  wrap: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSave: () => void;
  editorRef: Ref<CodeEditorHandle>;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const baselineRef = useRef<Text | null>(null);
  const wrapCompartment = useRef(new Compartment());
  const languageCompartment = useRef(new Compartment());
  const callbacks = useRef({ onDirtyChange, onSave });
  callbacks.current = { onDirtyChange, onSave };
  const initialRef = useRef(initial);

  useImperativeHandle(
    editorRef,
    () => ({
      getValue: () => viewRef.current?.state.doc.toString() ?? "",
      reset: (text: string) => {
        const view = viewRef.current;
        if (!view) return;
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
        baselineRef.current = view.state.doc;
        callbacks.current.onDirtyChange(false);
      },
      markSaved: () => {
        const view = viewRef.current;
        if (!view) return;
        baselineRef.current = view.state.doc;
        callbacks.current.onDirtyChange(false);
      },
      focus: () => viewRef.current?.focus(),
    }),
    [],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const state = EditorState.create({
      doc: initialRef.current,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        highlightSelectionMatches(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        EditorState.tabSize.of(2),
        EditorView.contentAttributes.of({ spellcheck: "false" }),
        surface,
        wrapCompartment.current.of(wrap ? EditorView.lineWrapping : []),
        languageCompartment.current.of([]),
        keymap.of([
          {
            key: "Mod-s",
            preventDefault: true,
            run: () => {
              callbacks.current.onSave();
              return true;
            },
          },
          indentWithTab,
          ...defaultKeymap,
          ...historyKeymap,
          ...searchKeymap,
        ]),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          const baseline = baselineRef.current;
          callbacks.current.onDirtyChange(baseline === null || !update.state.doc.eq(baseline));
        }),
      ],
    });
    const view = new EditorView({ state, parent: host });
    viewRef.current = view;
    baselineRef.current = view.state.doc;
    let live = true;
    void languageExtension(path).then((extension) => {
      if (live && extension) view.dispatch({ effects: languageCompartment.current.reconfigure(extension) });
    });
    return () => {
      live = false;
      view.destroy();
      viewRef.current = null;
    };
    // 编辑器只在换文件时重建；wrap 通过 compartment 就地切换。
  }, [path]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: wrapCompartment.current.reconfigure(wrap ? EditorView.lineWrapping : []),
    });
  }, [wrap]);

  return <div ref={hostRef} className="h-full min-h-0 overflow-hidden" />;
}

export default CodeEditor;
