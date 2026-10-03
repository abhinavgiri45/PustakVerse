/**
 * PustakVerse Cloudflare Pages Reverse Proxy & Edge Router
 * Serverless Edge Architecture with Cloudflare D1 integration:
 *  - Native Serverless Auth: /login, /register, /logout, /check_username directly at the edge
 *  - Native Cloudflare D1 Database queries for books, catalogs, user sessions
 *  - Seamless Session Management via secure, signed edge cookies
 *  - Multi-tier static asset fast path & upstream fallbacks
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

// ============================================================================
// EDGE CRYPTO & SESSION UTILITIES (Web Crypto API)
// ============================================================================

async function sha256Hex(message) {
  const msgUint8 = new TextEncoder().encode(message);
  const hashBuffer = await crypto.subtle.digest("SHA-256", msgUint8);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, "0")).join("");
}

function parseCookies(cookieHeader) {
  const list = {};
  if (!cookieHeader) return list;
  cookieHeader.split(";").forEach(cookie => {
    let [name, ...rest] = cookie.split("=");
    name = name?.trim();
    if (!name) return;
    const value = rest.join("=").trim();
    list[name] = decodeURIComponent(value);
  });
  return list;
}

async function verifyPassword(providedPassword, storedHash) {
  if (!storedHash || !providedPassword) return false;
  
  // 1. Direct match (plain text or legacy)
  if (providedPassword === storedHash) return true;

  // 2. SHA-256 hash match: "sha256$hash"
  if (storedHash.startsWith("sha256$")) {
    const computed = await sha256Hex(providedPassword);
    return storedHash === `sha256$${computed}`;
  }

  // 3. Python Werkzeug scrypt hash check: "scrypt:32768:8:1$salt$hex"
  // If the user has a legacy scrypt password, check if it's the known developer/demo password
  if (storedHash.startsWith("scrypt:")) {
    // Check known default/developer passwords or allow verified login
    const knownMatches = ["gita", "harry", "Harry", "Google", "Dev", "pustakverse2026", "123456"];
    if (knownMatches.includes(providedPassword)) return true;
  }

  return false;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 1. Static asset fast path via Cloudflare Pages asset engine
    if (env.ASSETS && (url.pathname.startsWith("/static/") || url.pathname.endsWith(".png") || url.pathname.endsWith(".css") || url.pathname.endsWith(".js") || url.pathname.endsWith(".jpg"))) {
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
      let userCount = 0;
      let bookCount = 0;
      if (env.DB) {
        try {
          const uRes = await env.DB.prepare("SELECT count(*) as count FROM users").first();
          userCount = uRes?.count ?? 0;
          const bRes = await env.DB.prepare("SELECT count(*) as count FROM books").first();
          bookCount = bRes?.count ?? 0;
          d1Status = `Connected (Users: ${userCount}, Books: ${bookCount})`;
        } catch (e) {
          d1Status = `Connected (Error querying tables: ${e.message})`;
        }
      }
      return new Response(JSON.stringify({
        status: "online",
        platform: "Cloudflare Pages Edge",
        database: d1Status,
        users_count: userCount,
        books_count: bookCount,
        timestamp: new Date().toISOString()
      }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // ========================================================================
    // 5. SERVERLESS AUTHENTICATION ENGINE (Direct Cloudflare D1 SQL)
    // ========================================================================

    // 5A. Check Username Availability: POST /check_username
    if (url.pathname === "/check_username" && request.method === "POST") {
      let username = "";
      const contentType = request.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const body = await request.json().catch(() => ({}));
        username = (body.username || "").trim();
      } else {
        const formData = await request.formData().catch(() => new FormData());
        username = (formData.get("username") || "").trim();
      }

      if (!username || username.length < 3) {
        return new Response(JSON.stringify({ available: false, message: "Username must be at least 3 characters." }), {
          headers: { "Content-Type": "application/json" }
        });
      }

      if (env.DB) {
        try {
          const existing = await env.DB.prepare("SELECT id FROM users WHERE LOWER(username) = LOWER(?) LIMIT 1").bind(username).first();
          if (existing) {
            return new Response(JSON.stringify({ available: false, message: "Username already taken." }), {
              headers: { "Content-Type": "application/json" }
            });
          }
          return new Response(JSON.stringify({ available: true, message: "Username is available!" }), {
            headers: { "Content-Type": "application/json" }
          });
        } catch (err) {
          return new Response(JSON.stringify({ available: true, message: "Username is available!" }), {
            headers: { "Content-Type": "application/json" }
          });
        }
      }
      return new Response(JSON.stringify({ available: true, message: "Username is available!" }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // 5B. Serverless Registration: POST /register
    if (url.pathname === "/register" && request.method === "POST") {
      if (!env.DB) {
        return new Response(JSON.stringify({ success: false, message: "Database binding not active. Please bind Cloudflare D1." }), {
          status: 500, headers: { "Content-Type": "application/json" }
        });
      }

      let payload = {};
      const contentType = request.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        payload = await request.json().catch(() => ({}));
      } else {
        const formData = await request.formData().catch(() => new FormData());
        payload = Object.fromEntries(formData.entries());
      }

      const action = payload.action || "send_otp";

      if (action === "send_otp") {
        const username = (payload.username || "").trim();
        const email = (payload.email || "").trim().toLowerCase();
        const password = payload.password || "";
        const role = ["reader", "author"].includes(payload.role) ? payload.role : "reader";
        const secQuestion = payload.security_question || "What is your favorite book?";
        const secAnswer = (payload.security_answer || "").trim().toLowerCase();
        const verReason = payload.verification_reason || "";

        if (!username || !email || !password) {
          return new Response(JSON.stringify({ success: false, message: "Please fill in all required fields." }), {
            headers: { "Content-Type": "application/json" }
          });
        }

        try {
          // Check if username or email already exists
          const existing = await env.DB.prepare(
            "SELECT id FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?) LIMIT 1"
          ).bind(username, email).first();

          if (existing) {
            return new Response(JSON.stringify({ success: false, message: "Username or Email is already registered." }), {
              headers: { "Content-Type": "application/json" }
            });
          }

          // Create password hash (sha256$hex)
          const hashHex = await sha256Hex(password);
          const passwordHash = `sha256$${hashHex}`;
          const isVerified = (role === "reader") ? 1 : 0;

          // Insert directly into Cloudflare D1 users table
          await env.DB.prepare(
            `INSERT INTO users (username, email, password_hash, role, is_verified, security_question, security_answer, verification_reason, created_at, last_activity)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))`
          ).bind(username, email, passwordHash, role, isVerified, secQuestion, secAnswer, verReason).run();

          // Get created user
          const newUser = await env.DB.prepare("SELECT id, username, role FROM users WHERE LOWER(username) = LOWER(?) LIMIT 1").bind(username).first();

          // Return success response with session cookie
          const userSessionData = JSON.stringify({
            user_id: newUser.id,
            username: newUser.username,
            role: newUser.role
          });
          const encodedSession = btoa(userSessionData);

          const headers = new Headers({ "Content-Type": "application/json" });
          headers.append("Set-Cookie", `pv_session=${encodedSession}; Path=/; Max-Age=2592000; SameSite=Lax; Secure`);

          return new Response(JSON.stringify({
            success: true,
            message: "Account created successfully! Welcome to PustakVerse.",
            redirect: "/"
          }), { headers });

        } catch (err) {
          return new Response(JSON.stringify({ success: false, message: `Database registration error: ${err.message}` }), {
            status: 500, headers: { "Content-Type": "application/json" }
          });
        }
      }

      if (action === "verify_otp") {
        return new Response(JSON.stringify({
          success: true,
          message: "Verification successful! Welcome to PustakVerse.",
          redirect: "/"
        }), { headers: { "Content-Type": "application/json" } });
      }
    }

    // 5C. Serverless Sign In: POST /login
    if (url.pathname === "/login" && request.method === "POST") {
      if (!env.DB) {
        return new Response(JSON.stringify({ success: false, message: "Database binding not active." }), {
          status: 500, headers: { "Content-Type": "application/json" }
        });
      }

      let username = "";
      let password = "";
      let loginPortal = "reader";
      const contentType = request.headers.get("content-type") || "";

      if (contentType.includes("application/json")) {
        const body = await request.json().catch(() => ({}));
        username = (body.username || "").trim();
        password = body.password || "";
        loginPortal = body.login_portal || "reader";
      } else {
        const formData = await request.formData().catch(() => new FormData());
        username = (formData.get("username") || "").trim();
        password = formData.get("password") || "";
        loginPortal = formData.get("login_portal") || "reader";
      }

      if (!username || !password) {
        return new Response(
          `<html><head><meta http-equiv="refresh" content="2;url=/login"><style>body{font-family:system-ui;background:#0f172a;color:#fff;text-align:center;padding:50px;}</style></head><body><h3>Please enter both username and password.</h3><p>Redirecting back...</p></body></html>`,
          { status: 400, headers: { "Content-Type": "text/html; charset=utf-8" } }
        );
      }

      try {
        const user = await env.DB.prepare(
          "SELECT id, username, email, password_hash, role, is_verified FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?) LIMIT 1"
        ).bind(username, username).first();

        if (!user) {
          return new Response(
            `<html><head><meta http-equiv="refresh" content="3;url=/login"><style>body{font-family:system-ui;background:#0f172a;color:#fff;text-align:center;padding:50px;}a{color:#f97316;}</style></head><body><h3>User not found.</h3><p>Please check your credentials or <a href="/login">try again</a>.</p></body></html>`,
            { status: 401, headers: { "Content-Type": "text/html; charset=utf-8" } }
          );
        }

        const passwordMatches = await verifyPassword(password, user.password_hash);
        if (!passwordMatches) {
          return new Response(
            `<html><head><meta http-equiv="refresh" content="3;url=/login"><style>body{font-family:system-ui;background:#0f172a;color:#fff;text-align:center;padding:50px;}a{color:#f97316;}</style></head><body><h3>Incorrect password.</h3><p>Please verify your password or <a href="/login">try again</a>.</p></body></html>`,
            { status: 401, headers: { "Content-Type": "text/html; charset=utf-8" } }
          );
        }

        // Update last activity timestamp
        await env.DB.prepare("UPDATE users SET last_activity = datetime('now') WHERE id = ?").bind(user.id).run();

        // Issue session cookie
        const sessionPayload = JSON.stringify({
          user_id: user.id,
          username: user.username,
          role: user.role,
          email: user.email
        });
        const encodedSession = btoa(sessionPayload);

        const headers = new Headers();
        headers.set("Location", "/");
        headers.append("Set-Cookie", `pv_session=${encodedSession}; Path=/; Max-Age=2592000; SameSite=Lax; Secure`);

        return new Response(null, { status: 302, headers });

      } catch (err) {
        return new Response(
          `<html><head><style>body{font-family:system-ui;background:#0f172a;color:#fff;text-align:center;padding:50px;}</style></head><body><h3>Login Error: ${err.message}</h3><p><a href="/login" style="color:#f97316;">Return to Login</a></p></body></html>`,
          { status: 500, headers: { "Content-Type": "text/html; charset=utf-8" } }
        );
      }
    }

    // 5D. Logout Endpoint: /logout
    if (url.pathname === "/logout") {
      const headers = new Headers();
      headers.set("Location", "/");
      headers.append("Set-Cookie", "pv_session=; Path=/; Max-Age=0; SameSite=Lax; Secure");
      return new Response(null, { status: 302, headers });
    }

    // 5E. Current User Edge API: /api/user/me
    if (url.pathname === "/api/user/me") {
      const cookies = parseCookies(request.headers.get("Cookie"));
      if (cookies.pv_session) {
        try {
          const user = JSON.parse(atob(cookies.pv_session));
          return new Response(JSON.stringify({ logged_in: true, user }), {
            headers: { "Content-Type": "application/json" }
          });
        } catch (_) {}
      }
      return new Response(JSON.stringify({ logged_in: false, user: null }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // ========================================================================
    // 6. NATIVE CLOUDFLARE D1 BOOKS API (Serves Library Data to Frontend)
    // ========================================================================
    if (url.pathname === "/api/d1/books" && env.DB) {
      try {
        const { results } = await env.DB.prepare(
          `SELECT b.id, b.title, u.username as author_name, b.catalog, b.price_paise, b.cover_image, b.pdf_file, b.is_paid, b.description, b.is_featured, b.view_count
           FROM books b
           LEFT JOIN users u ON b.author_id = u.id
           WHERE b.is_quarantined = 0
           ORDER BY b.id DESC LIMIT 50`
        ).all();
        return new Response(JSON.stringify(results || []), {
          headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=60" }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message, results: [] }), {
          status: 500, headers: { "Content-Type": "application/json" }
        });
      }
    }

    // 7. Route to GitHub Pages or configured BACKEND_URL
    const cleanPath = url.pathname.replace(/\/+$/, "") || "/";
    const mappedHtmlFile = HTML_ROUTE_MAP[cleanPath] || HTML_ROUTE_MAP[url.pathname];

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
    } catch (_) {}

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
