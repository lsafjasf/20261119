import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize, sep } from "node:path";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(dirname(fileURLToPath(import.meta.url))), "artifacts");
const port = Number(process.env.PORT ?? 4173);
const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
};

createServer(async (request, response) => {
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  } catch {
    response.writeHead(400).end("Bad request");
    return;
  }
  const relativePath = pathname === "/" ? "lifecycle-report.html" : pathname.replace(/^\/+/, "");
  const requestedPath = normalize(join(root, relativePath));
  if (requestedPath !== root && !requestedPath.startsWith(`${root}${sep}`)) {
    response.writeHead(403).end("Forbidden");
    return;
  }
  try {
    const info = await stat(requestedPath);
    if (!info.isFile()) {
      throw new Error("Not a file");
    }
    const body = await readFile(requestedPath);
    response.writeHead(200, {
      "content-type": contentTypes[extname(requestedPath)] ?? "application/octet-stream",
      "cache-control": "no-store",
    });
    response.end(body);
  } catch {
    response.writeHead(404).end("Not found");
  }
}).listen(port, () => {
  console.log(`Lifecycle report: http://localhost:${port}/`);
});
