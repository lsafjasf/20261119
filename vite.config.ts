import { defineConfig } from "vite";

export default defineConfig({
  worker: {
    format: "es",
  },
  server: {
    port: 4287,
    strictPort: true,
  },
});
