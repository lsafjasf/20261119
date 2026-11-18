import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT || 4173);
const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url, `http://${request.headers.host || "localhost"}`);
    const requestPath = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
    const filePath = path.resolve(root, `.${requestPath}`);
    if (!filePath.startsWith(`${root}${path.sep}`)) {
      response.writeHead(403);
      response.end("Forbidden");
      return;
    }
    const body = await fs.readFile(filePath);
    response.writeHead(200, {
      "content-type": mime[path.extname(filePath)] || "application/octet-stream",
      "cache-control": "no-cache",
      "content-length": body.length,
    });
    response.end(body);
  } catch (error) {
    response.writeHead(error.code === "ENOENT" ? 404 : 500, { "content-type": "text/plain; charset=utf-8" });
    response.end(error.code === "ENOENT" ? "Not found" : "Server error");
  }
});

server.listen(port, "0.0.0.0", () => {
  console.log(`Pinpoint EXIF archive running at http://localhost:${port}`);
});
