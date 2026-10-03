/**
 * PustakVerse Cloudflare Pages Reverse Proxy & Edge Router
 * High-performance edge routing with multi-tier fallback:
 *  1. Direct Cloudflare Pages static asset bundle (env.ASSETS)
 *  2. Cloudflare D1 edge APIs (if bound)
 *  3. Upstream dynamic backend / GitHub Pages
 *  4. Direct resilient Edge fallback to ensure 200 OK availability
 */

const RAW_GITHUB_STATIC_BASE = "https://raw.githubusercontent.com/abhinavgiri45/PustakVerse/main/static";

// Path mapping to static HTML files stored in the repository
const HTML_ROUTE_MAP = {
  "/": "index.html",
  "/index.html": "index.html",
  "/contact": "contact.html",
  "/contact/": "contact.html",
  "/terms": "terms.html",
  "/terms/": "terms.html",
  "/tools": "tools.html",
  "/tools/": "tools.html",
  "/login": "login.html",
  "/login/": "login.html",
  "/register": "register.html",
  "/register/": "register.html",
  "/signup": "register.html",
  "/signup/": "register.html",
  "/ask_ai": "ask_ai.html",
  "/ask_ai/": "ask_ai.html",
  "/granthmind": "ask_ai.html"
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 1. Static asset fast path via Cloudflare Pages asset engine
    if (env.ASSETS) {
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
  <url><loc>https://pustakverse.pages.dev/</loc><lastmod>2026-10-03</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>
  <url><loc>https://pustakverse.pages.dev/ask_ai/</loc><lastmod>2026-10-03</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>
  <url><loc>https://pustakverse.pages.dev/granthmind</loc><lastmod>2026-10-03</lastmod><changefreq>daily</changefreq><priority>0.9</priority></url>
  <url><loc>https://pustakverse.pages.dev/tools</loc><lastmod>2026-10-03</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>
  <url><loc>https://pustakverse.pages.dev/login</loc><lastmod>2026-10-03</lastmod><changefreq>monthly</changefreq><priority>0.7</priority></url>
  <url><loc>https://pustakverse.pages.dev/signup</loc><lastmod>2026-10-03</lastmod><changefreq>monthly</changefreq><priority>0.7</priority></url>
  <url><loc>https://pustakverse.pages.dev/contact</loc><lastmod>2026-10-03</lastmod><changefreq>monthly</changefreq><priority>0.6</priority></url>
  <url><loc>https://pustakverse.pages.dev/terms</loc><lastmod>2026-10-03</lastmod><changefreq>yearly</changefreq><priority>0.5</priority></url>
</urlset>`;
      return new Response(sitemapXml, {
        headers: { "Content-Type": "application/xml; charset=utf-8", "Cache-Control": "public, max-age=3600" }
      });
    }

    if (url.pathname === "/robots.txt") {
      return new Response("User-agent: *\nAllow: /\n\nSitemap: https://pustakverse.pages.dev/sitemap.xml\n", {
        headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=86400" }
      });
    }

    // 4. Health check endpoint & Cloudflare D1 Diagnostic
    if (url.pathname === "/api/edge-health") {
      let d1Status = "Not bound";
      if (env.DB) {
        try {
          const countRes = await env.DB.prepare("SELECT count(*) as count FROM users").first();
          d1Status = `Connected (Users: ${countRes?.count ?? 0})`;
        } catch (e) {
          d1Status = `Error: ${e.message}`;
        }
      }
      return new Response(JSON.stringify({
        status: "online",
        platform: "Cloudflare Pages Edge",
        database: d1Status,
        timestamp: new Date().toISOString()
      }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // 5. Native Cloudflare D1 Edge Books API
    if (url.pathname === "/api/d1/books" && env.DB) {
      try {
        const { results } = await env.DB.prepare(
          "SELECT id, title, author, catalog, price_paise, cover_image, is_quarantined FROM books WHERE is_quarantined = 0 ORDER BY id DESC LIMIT 50"
        ).all();
        return new Response(JSON.stringify(results), {
          headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=60" }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: { "Content-Type": "application/json" } });
      }
    }

    // 6. Direct Edge Asset Mapping (Try static files in repo directly via raw GitHub if ASSETS didn't match)
    const cleanPath = url.pathname.replace(/\/+$/, "") || "/";
    const mappedHtmlFile = HTML_ROUTE_MAP[cleanPath] || HTML_ROUTE_MAP[url.pathname];

    // 7. Route to GitHub Pages or configured BACKEND_URL
    const backendBase = env.BACKEND_URL || "https://abhinavgiri45.github.io";
    let targetPath = url.pathname;

    if (backendBase.includes("github.io")) {
      if (targetPath === "/" || targetPath === "") {
        targetPath = "/PustakVerse/index.html";
      } else if (!targetPath.includes(".") && !targetPath.endsWith("/")) {
        targetPath = "/PustakVerse" + targetPath + "/index.html";
      } else {
        targetPath = "/PustakVerse" + targetPath;
      }
    }

    const backendUrl = backendBase.replace(/\/+$/, "") + targetPath + url.search;
    const newHeaders = new Headers();
    for (const [key, value] of request.headers.entries()) {
      if (key.toLowerCase() !== "host") {
        newHeaders.append(key, value);
      }
    }
    newHeaders.set("X-Forwarded-Host", url.host);
    newHeaders.set("X-Forwarded-Proto", "https");

    try {
      const response = await fetch(backendUrl, {
        method: request.method,
        headers: newHeaders,
        redirect: "follow"
      });

      if (response && response.status < 400) {
        return response;
      }
    } catch (_) {
      // Backend request error, fall through to raw GitHub static fallback
    }

    // 8. Resilient Edge Fallback: Serve static HTML from repository static folder
    if (mappedHtmlFile) {
      try {
        const rawUrl = `${RAW_GITHUB_STATIC_BASE}/${mappedHtmlFile}`;
        const rawResp = await fetch(rawUrl, {
          headers: { "User-Agent": "PustakVerse-Edge-Proxy" }
        });
        if (rawResp && rawResp.status < 400) {
          const body = await rawResp.text();
          return new Response(body, {
            status: 200,
            headers: {
              "Content-Type": "text/html; charset=utf-8",
              "Cache-Control": "public, max-age=300"
            }
          });
        }
      } catch (_) {}
    }

    // 9. Root fallback to index.html if subpath wasn't found
    try {
      const indexFallback = await fetch(`${RAW_GITHUB_STATIC_BASE}/index.html`, {
        headers: { "User-Agent": "PustakVerse-Edge-Proxy" }
      });
      if (indexFallback && indexFallback.status < 400) {
        const body = await indexFallback.text();
        return new Response(body, {
          status: 200,
          headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "public, max-age=300"
          }
        });
      }
    } catch (_) {}

    // 10. Graceful startup banner if network is completely unreachable
    return new Response(
      `<!DOCTYPE html>
      <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>PustakVerse · Digital Library</title>
        <meta http-equiv="refresh" content="4">
        <style>
          * { box-sizing: border-box; margin: 0; padding: 0; }
          body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; text-align: center; }
          .card { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 48px 32px; max-width: 480px; width: 100%; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }
          .spinner { width: 44px; height: 44px; border: 4px solid #334155; border-top-color: #6366f1; border-radius: 50%; margin: 24px auto; animation: spin 0.9s linear infinite; }
          @keyframes spin { to { transform: rotate(360deg); } }
          h2 { font-size: 1.6rem; font-weight: 700; margin-bottom: 8px; color: #fff; }
          p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; margin-bottom: 20px; }
          .btn { display: inline-block; background: #6366f1; color: white; padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 0.9rem; transition: background 0.2s; }
          .btn:hover { background: #4f46e5; }
        </style>
      </head>
      <body>
        <div class="card">
          <h2>PustakVerse is Loading</h2>
          <div class="spinner"></div>
          <p>Syncing library catalog with Cloudflare Edge. This page will automatically refresh in a moment...</p>
          <a href="/" class="btn" onclick="location.reload(); return false;">Reload Now</a>
        </div>
      </body>
      </html>`,
      { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
    );
  }
};
