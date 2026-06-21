"use strict";

const fs = require("fs");
const http = require("http");
const https = require("https");
const path = require("path");
const { URL } = require("url");

const root = __dirname;
const port = Number(process.env.PORT || 8080);
const apiBaseUrl = (process.env.ORDER_PROCESSOR_API_BASE_URL || process.env.APIM_API_BASE_URL || "").replace(/\/$/, "");
const subscriptionKey = process.env.ORDER_PROCESSOR_APIM_SUBSCRIPTION_KEY || process.env.APIM_SUBSCRIPTION_KEY || "";

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8"
};

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(payload));
}

function serveStatic(request, response) {
  const parsedUrl = new URL(request.url, "http://localhost");
  const requestedPath = parsedUrl.pathname === "/" ? "/index.html" : parsedUrl.pathname;
  const resolvedPath = path.normalize(path.join(root, decodeURIComponent(requestedPath)));
  if (!resolvedPath.startsWith(root)) {
    sendJson(response, 403, { error: "forbidden" });
    return;
  }

  fs.readFile(resolvedPath, (error, data) => {
    if (error) {
      sendJson(response, 404, { error: "notFound" });
      return;
    }
    response.writeHead(200, {
      "Content-Type": contentTypes[path.extname(resolvedPath)] || "application/octet-stream",
      "Cache-Control": "no-store"
    });
    response.end(data);
  });
}

function proxyApi(request, response) {
  if (!apiBaseUrl) {
    sendJson(response, 503, { error: "apiBaseNotConfigured" });
    return;
  }

  const targetPath = request.url.replace(/^\/api/, "") || "/";
  const target = new URL(`${apiBaseUrl}${targetPath}`);
  const headers = {
    Accept: request.headers.accept || "application/json",
    "Content-Type": request.headers["content-type"] || "application/json"
  };

  for (const name of [
    "x-ms-client-principal",
    "x-ms-client-principal-id",
    "x-ms-client-principal-idp",
    "x-ms-client-principal-name"
  ]) {
    if (request.headers[name]) headers[name] = request.headers[name];
  }
  if (subscriptionKey) headers["Ocp-Apim-Subscription-Key"] = subscriptionKey;

  const proxyRequest = https.request(
    target,
    {
      method: request.method,
      headers
    },
    (proxyResponse) => {
      const responseHeaders = {
        "Content-Type": proxyResponse.headers["content-type"] || "application/json; charset=utf-8",
        "Cache-Control": "no-store"
      };
      response.writeHead(proxyResponse.statusCode || 502, responseHeaders);
      proxyResponse.pipe(response);
    }
  );

  proxyRequest.on("error", (error) => {
    sendJson(response, 502, { error: "apiProxyFailed", message: error.message });
  });
  request.pipe(proxyRequest);
}

const server = http.createServer((request, response) => {
  if (request.url === "/healthz") {
    sendJson(response, 200, { ok: true });
    return;
  }
  if (request.url.startsWith("/api/")) {
    proxyApi(request, response);
    return;
  }
  serveStatic(request, response);
});

server.listen(port, () => {
  console.log(`Order Processor console listening on ${port}`);
});
