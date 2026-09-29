/**
 * PustakVerse Cloudflare Pages Edge Worker
 * Connects https://pustakverse.pages.dev with Cloudflare D1 Database
 * and handles dynamic routing and static caching at the edge.
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 1. Direct Edge Health Check & D1 DB Status
    if (url.pathname === "/api/edge-health") {
      try {
        let dbStatus = "Not Bound";
        if (env.DB) {
          const result = await env.DB.prepare("SELECT count(*) as count FROM users").first();
          dbStatus = `Connected (Users: ${result ? result.count : 0})`;
        }
        return new Response(JSON.stringify({
          status: "online",
          platform: "Cloudflare Pages Edge",
          database: dbStatus,
          timestamp: new Date().toISOString()
        }), {
          headers: { "Content-Type": "application/json" }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: { "Content-Type": "application/json" } });
      }
    }

    // 2. Direct Cloudflare D1 Query Endpoint for Edge APIs
    if (url.pathname.startsWith("/api/d1/")) {
      if (!env.DB) {
        return new Response(JSON.stringify({ error: "D1 database not bound to Pages" }), { status: 500, headers: { "Content-Type": "application/json" } });
      }
      if (url.pathname === "/api/d1/books") {
        const { results } = await env.DB.prepare("SELECT id, title, catalog, price_paise, cover_image FROM books WHERE is_quarantined = 0 ORDER BY id DESC LIMIT 20").all();
        return new Response(JSON.stringify(results), { headers: { "Content-Type": "application/json" } });
      }
    }

    // 3. Try to serve static assets first if available
    if (env.ASSETS) {
      try {
        const assetResponse = await env.ASSETS.fetch(request);
        if (assetResponse.status < 400) {
          return assetResponse;
        }
      } catch (e) {
        // Fall through to backend proxy
      }
    }

    // 4. Dynamic Proxy to Backend Application
    const targetUrl = new URL(url.pathname + url.search, "https://pustakverse.onrender.com");
    const modifiedHeaders = new Headers(request.headers);
    modifiedHeaders.set("Host", "pustakverse.onrender.com");
    modifiedHeaders.set("X-Forwarded-Host", url.hostname);
    modifiedHeaders.set("X-Forwarded-Proto", "https");

    const modifiedRequest = new Request(targetUrl, {
      method: request.method,
      headers: modifiedHeaders,
      body: request.method !== "GET" && request.method !== "HEAD" ? request.body : null,
      redirect: "manual"
    });

    try {
      const response = await fetch(modifiedRequest);
      return response;
    } catch (error) {
      return new Response(
        `<html><body style="font-family:system-ui;text-align:center;padding:50px;">
          <h2>PustakVerse Edge Gateway</h2>
          <p>Connecting to PustakVerse services...</p>
        </body></html>`,
        { status: 502, headers: { "Content-Type": "text/html" } }
      );
    }
  }
};
