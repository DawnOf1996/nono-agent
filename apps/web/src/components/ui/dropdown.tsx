import { ChevronDown } from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

type DropdownProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: ReactNode;
  children: ReactNode;
  align?: "left" | "right";
  placement?: "top" | "bottom";
  matchTriggerWidth?: boolean;
  panelClassName?: string;
  className?: string;
};

export function Dropdown({
  open,
  onOpenChange,
  trigger,
  children,
  align = "left",
  placement = "bottom",
  matchTriggerWidth = false,
  panelClassName,
  className,
}: DropdownProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const placedRef = useRef("");
  const [style, setStyle] = useState<CSSProperties>({});

  useLayoutEffect(() => {
    if (!open || !rootRef.current) return;

    function place() {
      const anchor = rootRef.current?.getBoundingClientRect();
      const panel = panelRef.current;
      if (!anchor || !panel) return;
      const gap = 6;
      const margin = 8;
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const panelWidth = panel.offsetWidth;
      const panelHeight = Math.min(panel.scrollHeight, 280);
      const spaceBelow = viewportHeight - anchor.bottom - gap - margin;
      const spaceAbove = anchor.top - gap - margin;
      const placeTop =
        placement === "top"
          ? panelHeight <= spaceAbove || spaceAbove >= spaceBelow
          : panelHeight > spaceBelow && spaceAbove > spaceBelow;
      const roomRight = viewportWidth - anchor.left - margin;
      const roomLeft = anchor.right - margin;
      const alignRight =
        align === "right"
          ? panelWidth <= roomLeft || roomLeft >= roomRight
          : panelWidth > roomRight && roomLeft > roomRight;
      const next: CSSProperties = {
        position: "fixed",
        zIndex: 80,
        maxHeight: Math.min(280, Math.max(120, placeTop ? spaceAbove : spaceBelow)),
      };
      if (placeTop) next.bottom = viewportHeight - anchor.top + gap;
      else next.top = anchor.bottom + gap;
      if (alignRight) {
        const right = Math.max(margin, viewportWidth - anchor.right);
        if (viewportWidth - right - panelWidth < margin) next.left = margin;
        else next.right = right;
      } else if (anchor.left + panelWidth > viewportWidth - margin) {
        next.left = Math.max(margin, viewportWidth - margin - panelWidth);
      } else {
        next.left = Math.max(margin, anchor.left);
      }
      if (matchTriggerWidth) next.width = anchor.width;
      const key = `${next.top}|${next.bottom}|${next.left}|${next.right}|${next.maxHeight}|${next.width}`;
      if (placedRef.current === key) return;
      placedRef.current = key;
      setStyle(next);
    }

    place();
    const observer = new ResizeObserver(place);
    if (panelRef.current) observer.observe(panelRef.current);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      placedRef.current = "";
      observer.disconnect();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, align, placement, matchTriggerWidth]);

  useEffect(() => {
    if (!open) return;
    function onPointer(event: PointerEvent) {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      onOpenChange(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onOpenChange(false);
      }
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onOpenChange]);

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      {trigger}
      {open
        ? createPortal(
            <div
              ref={panelRef}
              style={style}
              className={cn(
                "overflow-auto rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_rgba(15,17,21,0.12)]",
                panelClassName,
              )}
            >
              {children}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

export function DropdownItem({
  selected,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean }) {
  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-hover",
        selected && "bg-active",
        className,
      )}
      {...props}
    />
  );
}

export function FieldSelectButton({
  open,
  label,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { open: boolean; label: string }) {
  return (
    <button
      type="button"
      aria-haspopup="listbox"
      aria-expanded={open}
      className={cn(
        "flex h-9 w-full items-center gap-2 rounded-lg border border-line bg-bg px-2.5 text-sm text-ink hover:bg-hover focus:border-line-strong focus:outline-none disabled:opacity-40",
        className,
      )}
      {...props}
    >
      <span className="min-w-0 flex-1 truncate text-left">{label}</span>
      <ChevronDown className={cn("size-3.5 shrink-0 text-muted", open && "rotate-180")} />
    </button>
  );
}
