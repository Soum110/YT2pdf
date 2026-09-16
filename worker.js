/**
 * worker.js — Cloudflare Worker Edge Router & Reverse Proxy for YT2PDFS
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Proxy API requests to Python FastAPI backend
    if (url.pathname.startsWith("/api/")) {
      const backendBase = env.BACKEND_URL;
      if (!backendBase || backendBase.includes("localhost") || backendBase.includes("127.0.0.1")) {
        return new Response(JSON.stringify({
          status: "waiting_for_backend",
          message: "Frontend is running on Cloudflare Workers edge. To enable video conversion & contact API, point BACKEND_URL to your deployed Python server (e.g. on Render, Railway, Fly.io, or Cloudflare Tunnel).",
          instruction: "Set BACKEND_URL in wrangler.toml or via Cloudflare Dashboard"
        }, null, 2), {
          status: 503,
          headers: { "Content-Type": "application/json; charset=utf-8" }
        });
      }
      
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
          suggestion: "Ensure your Python backend is running and BACKEND_URL is configured."
        }), {
          status: 502,
          headers: { "Content-Type": "application/json" }
        });
      }
    }

    // 2. Map /static/* requests to root assets (e.g. /static/brand-icon.png -> /brand-icon.png)
    if (url.pathname.startsWith("/static/")) {
      const strippedPath = url.pathname.replace(/^\/static/, "");
      const assetUrl = new URL(strippedPath + url.search, request.url);
      return env.ASSETS.fetch(new Request(assetUrl, request));
    }

    // 3. Serve static assets & MPA routes (with automatic 404-page fallback via wrangler.toml)
    return env.ASSETS.fetch(request);
  }
};
