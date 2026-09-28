import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const require = createRequire(import.meta.url);

/** 开发时直接读 pdfjs-dist，构建时把 CMap 和标准字体拷进 dist/pdfjs。 */
function pdfjsAssets(): Plugin {
  const packageRoot = path.dirname(require.resolve("pdfjs-dist/package.json"));
  const folders = ["cmaps", "standard_fonts"];
  return {
    name: "pdfjs-assets",
    configureServer(server) {
      server.middlewares.use("/pdfjs", (req, res, next) => {
        const raw = decodeURIComponent((req.url ?? "").split("?")[0] ?? "").replace(/^\/+/, "");
        const file = path.resolve(packageRoot, raw);
        const relative = path.relative(packageRoot, file);
        if (relative.startsWith("..") || path.isAbsolute(relative) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
          next();
          return;
        }
        fs.createReadStream(file).pipe(res);
      });
    },
    generateBundle() {
      for (const folder of folders) {
        const dir = path.join(packageRoot, folder);
        for (const name of fs.readdirSync(dir)) {
          const file = path.join(dir, name);
          if (!fs.statSync(file).isFile()) continue;
          this.emitFile({
            type: "asset",
            fileName: `pdfjs/${folder}/${name}`,
            source: fs.readFileSync(file),
          });
        }
      }
    },
  };
}

const rootDir = path.dirname(fileURLToPath(import.meta.url));

const sidecar = process.env.NONO_SIDECAR_URL || "http://127.0.0.1:8000";

const desktopPackage = JSON.parse(
  fs.readFileSync(path.resolve(rootDir, "../desktop/package.json"), "utf8"),
) as { version?: string };
const appVersion = desktopPackage.version ?? "";

export default defineConfig({
  define: {
    __NONO_SIDECAR_HTTP__: JSON.stringify(sidecar),
    __NONO_APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [react(), tailwindcss(), pdfjsAssets()],
  resolve: {
    alias: {
      "@": path.resolve(rootDir, "./src"),
      "@nono/protocol": path.resolve(rootDir, "../packages/protocol/src/index.ts"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/health": sidecar,
      "/chat": sidecar,
      "/session": sidecar,
      "/sessions": sidecar,
      "/command": sidecar,
      "/commands": sidecar,
      "/abort": sidecar,
      "/app": { target: sidecar, ws: true },
    },
  },
});
