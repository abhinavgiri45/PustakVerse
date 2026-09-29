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
      } catch (_) {}
    }

    // 2. Google Search Console ownership verification file
    if (url.pathname === "/google8a9af3f8fe8a3567.html") {
      return new Response("google-site-verification: google8a9af3f8fe8a3567.html", {
        headers: { "Content-Type": "text/html; charset=utf-8" }
      });
    }

    // 3. Direct Sitemap and Robots response
    if (url.pathname === "/sitemap.xml") {
      const sitemapXml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://pustakverse.pages.dev/</loc><lastmod>2026-09-29</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>
  <url><loc>https://pustakverse.pages.dev/ask_ai/</loc><lastmod>2026-09-29</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>
  <url><loc>https://pustakverse.pages.dev/granthmind</loc><lastmod>2026-09-29</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>
  <url><loc>https://pustakverse.pages.dev/tools</loc><lastmod>2026-09-29</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>
  <url><loc>https://pustakverse.pages.dev/category/Educational</loc><lastmod>2026-09-29</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>
  <url><loc>https://pustakverse.pages.dev/category/Fiction</loc><lastmod>2026-09-29</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>
  <url><loc>https://pustakverse.pages.dev/category/Non-Fiction</loc><lastmod>2026-09-29</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>
  <url><loc>https://pustakverse.pages.dev/login</loc><lastmod>2026-09-29</lastmod><changefreq>monthly</changefreq><priority>0.7</priority></url>
  <url><loc>https://pustakverse.pages.dev/signup</loc><lastmod>2026-09-29</lastmod><changefreq>monthly</changefreq><priority>0.7</priority></url>
  <url><loc>https://pustakverse.pages.dev/contact</loc><lastmod>2026-09-29</lastmod><changefreq>monthly</changefreq><priority>0.6</priority></url>
  <url><loc>https://pustakverse.pages.dev/terms</loc><lastmod>2026-09-29</lastmod><changefreq>yearly</changefreq><priority>0.5</priority></url>
</urlset>`;
      return new Response(sitemapXml, {
        headers: { "Content-Type": "application/xml; charset=utf-8" }
      });
    }

    if (url.pathname === "/robots.txt") {
      return new Response("User-agent: *\\nAllow: /\\n\\nSitemap: https://pustakverse.pages.dev/sitemap.xml\\n", {
        headers: { "Content-Type": "text/plain; charset=utf-8" }
      });
    }

    // 4. Health check endpoint
    if (url.pathname === "/api/edge-health") {
      return new Response(JSON.stringify({
        status: "online",
        platform: "Cloudflare Pages Edge",
        timestamp: new Date().toISOString()
      }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // 3. Proxy to the live backend application
    const backendUrl = "https://pustakverse.onrender.com" + url.pathname + url.search;

    const newHeaders = new Headers();
    for (const [key, value] of request.headers.entries()) {
      if (key.toLowerCase() !== "host") {
        newHeaders.append(key, value);
      }
    }
    newHeaders.set("X-Forwarded-Host", url.host);
    newHeaders.set("X-Forwarded-Proto", "https");

    const fetchOptions = {
      method: request.method,
      headers: newHeaders,
      redirect: "manual"
    };

    if (request.method !== "GET" && request.method !== "HEAD") {
      fetchOptions.body = request.body;
    }

    try {
      return await fetch(backendUrl, fetchOptions);
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
