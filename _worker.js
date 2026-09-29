/**
 * PustakVerse Cloudflare Pages Reverse Proxy & Edge Router
 * Forwards traffic from https://pustakverse.pages.dev to the live backend server.
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 1. Static asset fast path
    if (url.pathname.startsWith("/static/") && env.ASSETS) {
      try {
        const assetResponse = await env.ASSETS.fetch(request.clone());
        if (assetResponse && assetResponse.status < 400) {
          return assetResponse;
        }
      } catch (_) {
        // Fall through to backend
      }
    }

    // 2. Health check endpoint
    if (url.pathname === "/api/edge-health") {
      return new Response(JSON.stringify({
        status: "online",
        platform: "Cloudflare Pages",
        url: request.url,
        timestamp: new Date().toISOString()
      }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // 3. Proxy to the live backend application
    const backendOrigin = "https://pustakverse.onrender.com";
    const targetUrl = new URL(url.pathname + url.search, backendOrigin);

    // Clean headers for backend proxy
    const newHeaders = new Headers(request.headers);
    newHeaders.set("X-Forwarded-Host", url.host);
    newHeaders.set("X-Forwarded-Proto", "https");

    const fetchOptions = {
      method: request.method,
      headers: newHeaders,
      redirect: "manual"
    };

    // Only attach body for POST/PUT/PATCH/DELETE
    if (request.method !== "GET" && request.method !== "HEAD") {
      fetchOptions.body = request.body;
    }

    try {
      const response = await fetch(targetUrl.toString(), fetchOptions);
      return response;
    } catch (err) {
      return new Response(
        `<!DOCTYPE html>
        <html>
        <head>
          <title>PustakVerse</title>
          <meta http-equiv="refresh" content="3">
          <style>
            body { font-family: system-ui, sans-serif; background: #0b0f19; color: #fff; text-align: center; padding-top: 100px; }
            .spinner { width: 40px; height: 40px; border: 4px solid #333; border-top-color: #6366f1; border-radius: 50%; margin: 20px auto; animation: spin 1s linear infinite; }
            @keyframes spin { to { transform: rotate(360deg); } }
          </style>
        </head>
        <body>
          <h2>PustakVerse is starting up...</h2>
          <div class="spinner"></div>
          <p style="color: #94a3b8;">Waking up services, please wait a moment...</p>
        </body>
        </html>`,
        { status: 503, headers: { "Content-Type": "text/html" } }
      );
    }
  }
};
