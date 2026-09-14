/// <reference types="vite/client" />

type NonoDesktop = {
  platform: string;
  isDesktop?: boolean;
  selectWorkspace?: () => Promise<string | null>;
};

interface Window {
  nonoDesktop?: NonoDesktop;
}
