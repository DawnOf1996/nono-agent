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
}: DropdownProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [style, setStyle] = useState<CSSProperties>({});

  useLayoutEffect(() => {
    if (!open || !rootRef.current) return;

    function place() {
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      const gap = 6;
      const spaceBelow = window.innerHeight - rect.bottom - gap - 8;
      const spaceAbove = rect.top - gap - 8;
      const placeTop =
        placement === "top" || (placement === "bottom" && spaceBelow < 160 && spaceAbove > spaceBelow);
      const next: CSSProperties = {
        position: "fixed",
        zIndex: 80,
        maxHeight: Math.min(280, Math.max(120, placeTop ? spaceAbove : spaceBelow)),
      };
      if (placeTop) next.bottom = window.innerHeight - rect.top + gap;
      else next.top = rect.bottom + gap;
      if (align === "right") next.right = window.innerWidth - rect.right;
      else next.left = rect.left;
      if (matchTriggerWidth) next.width = rect.width;
      setStyle(next);
    }

    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
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
    <div ref={rootRef} className="relative">
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
