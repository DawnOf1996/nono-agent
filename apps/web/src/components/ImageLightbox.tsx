import { X } from "lucide-react";
import { useEffect } from "react";
import { createPortal } from "react-dom";

export function ImageLightbox({ src, onClose }: { src: string | null; onClose: () => void }) {
  useEffect(() => {
    if (!src) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [src, onClose]);

  if (!src) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
      onClick={onClose}
    >
      <button
        type="button"
        className="absolute top-4 right-4 flex size-9 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
        title="关闭"
        onClick={onClose}
      >
        <X className="size-4" />
      </button>
      <img
        src={src}
        alt=""
        className="max-h-[90vh] max-w-[min(90vw,72rem)] rounded-2xl object-contain shadow-[0_24px_64px_rgba(0,0,0,0.35)]"
        onClick={(event) => event.stopPropagation()}
      />
    </div>,
    document.body,
  );
}
