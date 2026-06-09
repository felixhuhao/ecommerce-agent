import http from "node:http";

const port = Number(process.env.PORT || "3000");

function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

function svgDataUrl(payload) {
  const type = String(payload.type || payload.tool || "chart");
  const data = Array.isArray(payload.data) ? payload.data : [];
  const title = String(payload.title || `${type} chart`);
  const safeTitle = title.replace(/[<>&]/g, "");
  const safeType = type.replace(/[<>&]/g, "");
  const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
  <rect width="640" height="360" fill="#ffffff"/>
  <rect x="32" y="32" width="576" height="296" rx="8" fill="#f8fafc" stroke="#94a3b8"/>
  <text x="56" y="78" fill="#0f172a" font-family="Arial, sans-serif" font-size="24">${safeTitle}</text>
  <text x="56" y="118" fill="#334155" font-family="Arial, sans-serif" font-size="16">AntV MCP dev renderer</text>
  <text x="56" y="148" fill="#334155" font-family="Arial, sans-serif" font-size="16">type: ${safeType}</text>
  <text x="56" y="178" fill="#334155" font-family="Arial, sans-serif" font-size="16">points: ${data.length}</text>
  <polyline points="64,276 168,230 272,250 376,168 480,198 576,118" fill="none" stroke="#2563eb" stroke-width="4"/>
  <circle cx="64" cy="276" r="5" fill="#2563eb"/>
  <circle cx="168" cy="230" r="5" fill="#2563eb"/>
  <circle cx="272" cy="250" r="5" fill="#2563eb"/>
  <circle cx="376" cy="168" r="5" fill="#2563eb"/>
  <circle cx="480" cy="198" r="5" fill="#2563eb"/>
  <circle cx="576" cy="118" r="5" fill="#2563eb"/>
</svg>`;
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;
}

const server = http.createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/generate") {
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ success: false, errorMessage: "not found" }));
    return;
  }

  try {
    const payload = await readJson(req);
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ success: true, resultObj: svgDataUrl(payload) }));
  } catch (error) {
    res.writeHead(400, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        success: false,
        errorMessage: error instanceof Error ? error.message : "invalid request",
      }),
    );
  }
});

server.listen(port, "0.0.0.0", () => {
  console.log(`chart renderer stub listening on ${port}`);
});
