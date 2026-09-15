/// <reference types="vite/client" />

type NonoDesktop = {
  platform: string;
  isDesktop?: boolean;
  selectWorkspace?: () => Promise<string | null>;
  openPath?: (target: string) => Promise<{ ok: boolean; error?: string }>;
};

interface Window {
  nonoDesktop?: NonoDesktop;
}
