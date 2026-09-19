/**
 * worker.js — Cloudflare Worker Edge Router & Reverse Proxy for YT2PDFS
 */

const DEFAULT_BACKEND = "https://yt2pdf-214301889618.europe-west1.run.app";

export default {
  // Scheduled Cron Trigger: Fires every 30 mins to keep the backend awake 24/7
  async scheduled(event, env, ctx) {
    const backendBase = env?.BACKEND_URL || DEFAULT_BACKEND;
    if (backendBase && !backendBase.includes("localhost") && !backendBase.includes("127.0.0.1")) {
      try {
        const pingUrl = new URL("/api/health", backendBase);
        await fetch(pingUrl.toString(), {
          headers: { "User-Agent": "YT2PDF-KeepAlive/1.0" }
        });
      } catch (err) {
        console.error("Keep-alive ping error:", err.message);
      }
    }
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const backendBase = env?.BACKEND_URL || DEFAULT_BACKEND;

    // 1. Proxy API requests to Python FastAPI backend
    if (url.pathname.startsWith("/api/")) {
      try {
        const targetUrl = new URL(url.pathname + url.search, backendBase);
        const reqHeaders = new Headers(request.headers);
        reqHeaders.set("X-Forwarded-Host", url.host);
        reqHeaders.set("X-Forwarded-Proto", url.protocol.replace(":", ""));

        const init = {
          method: request.method,
          headers: reqHeaders,
          redirect: "follow"
        };

        if (request.method !== "GET" && request.method !== "HEAD") {
          init.body = request.body;
        }

        const backendResponse = await fetch(targetUrl.toString(), init);

        // If backend returned an upstream HTML error (e.g. 502/530 Tunnel expired), wrap in clean JSON
        const contentType = backendResponse.headers.get("content-type") || "";
        if (!backendResponse.ok && !contentType.includes("application/json")) {
          return new Response(JSON.stringify({
            detail: "Backend conversion service is currently offline or unreachable.",
            error: "Upstream gateway error",
            status: backendResponse.status
          }), {
            status: backendResponse.status >= 400 ? backendResponse.status : 502,
            headers: { "Content-Type": "application/json; charset=utf-8" }
          });
        }

        return backendResponse;
      } catch (err) {
        return new Response(JSON.stringify({
          error: "Backend service unreachable",
          message: err.message,
          suggestion: "Ensure your Python backend is running."
        }), {
          status: 502,
          headers: { "Content-Type": "application/json" }
        });
      }
    }

    // 2. Serve static assets if Cloudflare Workers Assets binding is available
    if (env?.ASSETS?.fetch) {
      if (url.pathname.startsWith("/static/")) {
        const strippedPath = url.pathname.replace(/^\/static/, "");
        const assetUrl = new URL(strippedPath + url.search, request.url);
        return env.ASSETS.fetch(new Request(assetUrl, request));
      }
      return env.ASSETS.fetch(request);
    }

    // 3. Fallback: Proxy everything directly to Google Cloud Run
    const targetUrl = new URL(url.pathname + url.search, backendBase);
    const reqHeaders = new Headers(request.headers);
    reqHeaders.set("X-Forwarded-Host", url.host);
    reqHeaders.set("X-Forwarded-Proto", url.protocol.replace(":", ""));
    return fetch(targetUrl.toString(), {
      method: request.method,
      headers: reqHeaders,
      body: (request.method !== "GET" && request.method !== "HEAD") ? request.body : undefined,
      redirect: "follow"
    });
  }
};
