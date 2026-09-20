/**
 * worker.js — Cloudflare Worker Edge Router & Reverse Proxy for YT2PDFS
 */

const DEFAULT_BACKEND = "https://yt2pdf-214301889618.europe-west1.run.app";
const ALLOWED_HOSTS = new Set(["yt2pdfs.com", "www.yt2pdfs.com"]);

export default {
  // Scheduled Cron Trigger: Fires every 30 mins to keep the backend awake 24/7
  async scheduled(event, env, ctx) {
    let backendBase = env?.BACKEND_URL || DEFAULT_BACKEND;
    if (backendBase.includes("onrender.com") || backendBase.includes("render.com")) {
      backendBase = DEFAULT_BACKEND;
    }
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
    const hostname = url.hostname.toLowerCase();
    const isAllowedHost = ALLOWED_HOSTS.has(hostname) || hostname === "localhost" || hostname === "127.0.0.1";

    // ── 0. Strict Host Restriction & Duplicate Domain Protection ──
    // Restrict any domain other than yt2pdfs.com and www.yt2pdfs.com (e.g. *.workers.dev, *.run.app)
    if (!isAllowedHost) {
      // If a web crawler asks for robots.txt on any unauthorized domain, disallow everything:
      if (url.pathname === "/robots.txt") {
        return new Response("User-agent: *\nDisallow: /\n", {
          status: 200,
          headers: {
            "Content-Type": "text/plain; charset=utf-8",
            "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
            "Cache-Control": "public, max-age=86400"
          }
        });
      }

      // If it's a programmatic API call (e.g. from companion extension), allow it to proxy, but with strict noindex
      if (!url.pathname.startsWith("/api/")) {
        // For all webpage and asset navigation, issue an HTTP 301 Permanent Redirect to canonical domain
        const canonicalUrl = new URL(url.pathname + url.search + url.hash, "https://yt2pdfs.com");
        return new Response(null, {
          status: 301,
          headers: {
            "Location": canonicalUrl.toString(),
            "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
            "Cache-Control": "public, max-age=86400"
          }
        });
      }
    }

    let backendBase = env?.BACKEND_URL || DEFAULT_BACKEND;
    if (backendBase.includes("onrender.com") || backendBase.includes("render.com")) {
      backendBase = DEFAULT_BACKEND;
    }

    // Handle CORS preflight requests
    if (request.method === "OPTIONS") {
      const corsHeaders = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400",
      };
      if (!isAllowedHost) {
        corsHeaders["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet";
      }
      return new Response(null, {
        status: 204,
        headers: corsHeaders
      });
    }

    // 1. Proxy API requests to Python FastAPI backend
    if (url.pathname.startsWith("/api/")) {
      try {
        const targetUrl = new URL(url.pathname + url.search, backendBase);
        const reqHeaders = new Headers(request.headers);
        reqHeaders.set("Host", targetUrl.host);
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
          const errHeaders = {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*"
          };
          if (!isAllowedHost) {
            errHeaders["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet";
          }
          return new Response(JSON.stringify({
            detail: "Backend conversion service is currently offline or unreachable.",
            error: "Upstream gateway error",
            status: backendResponse.status
          }), {
            status: backendResponse.status >= 400 ? backendResponse.status : 502,
            headers: errHeaders
          });
        }

        // Forward response with CORS header
        const resHeaders = new Headers(backendResponse.headers);
        resHeaders.set("Access-Control-Allow-Origin", "*");
        if (!isAllowedHost) {
          resHeaders.set("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet");
        }
        return new Response(backendResponse.body, {
          status: backendResponse.status,
          statusText: backendResponse.statusText,
          headers: resHeaders
        });

      } catch (err) {
        const errHeaders = {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*"
        };
        if (!isAllowedHost) {
          errHeaders["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet";
        }
        return new Response(JSON.stringify({
          error: "Backend service unreachable",
          message: err.message,
          suggestion: "Ensure your Python backend is running."
        }), {
          status: 502,
          headers: errHeaders
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
    reqHeaders.set("Host", targetUrl.host);
    reqHeaders.set("X-Forwarded-Host", url.host);
    reqHeaders.set("X-Forwarded-Proto", url.protocol.replace(":", ""));
    const fallbackRes = await fetch(targetUrl.toString(), {
      method: request.method,
      headers: reqHeaders,
      body: (request.method !== "GET" && request.method !== "HEAD") ? request.body : undefined,
      redirect: "follow"
    });
    if (!isAllowedHost) {
      const fbHeaders = new Headers(fallbackRes.headers);
      fbHeaders.set("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet");
      return new Response(fallbackRes.body, {
        status: fallbackRes.status,
        statusText: fallbackRes.statusText,
        headers: fbHeaders
      });
    }
    return fallbackRes;
  }
};
