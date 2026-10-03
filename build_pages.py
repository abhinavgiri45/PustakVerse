"""
PustakVerse Automated Static Site Generator for GitHub Pages.
Generates complete, production-grade static HTML files and assets from Flask routes.
"""

import os
import shutil
import sys

# Ensure current working directory is in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import app

# Target output directory for GitHub Pages
SITE_DIR = os.path.join(os.path.dirname(__file__), "_site")

# Routes to generate statically
STATIC_ROUTES = [
    ("/", "index.html"),
    ("/contact", "contact/index.html"),
    ("/terms", "terms/index.html"),
    ("/tools", "tools/index.html"),
    ("/login", "login/index.html"),
    ("/register", "register/index.html"),
    ("/ask_ai", "ask_ai/index.html"),
    ("/google8a9af3f8fe8a3567.html", "google8a9af3f8fe8a3567.html"),
    ("/sitemap.xml", "sitemap.xml"),
    ("/robots.txt", "robots.txt"),
]

def build():
    print(f"[*] Building PustakVerse static site into: {SITE_DIR}")
    
    if os.path.exists(SITE_DIR):
        try:
            shutil.rmtree(SITE_DIR, ignore_errors=True)
        except Exception:
            pass
    os.makedirs(SITE_DIR, exist_ok=True)

    # 1. Copy static folder
    static_src = os.path.join(os.path.dirname(__file__), "static")
    static_dst = os.path.join(SITE_DIR, "static")
    if os.path.exists(static_src):
        print("[+] Copying static assets...")
        shutil.copytree(static_src, static_dst, dirs_exist_ok=True)

    # 2. Touch .nojekyll (tells GitHub Pages not to ignore files starting with _)
    nojekyll_path = os.path.join(SITE_DIR, ".nojekyll")
    with open(nojekyll_path, "w", encoding="utf-8") as f:
        f.write("")
    print("[+] Created .nojekyll")

    # 3. Render routes via Flask test client
    with app.test_client() as client:
        for route, out_file in STATIC_ROUTES:
            print(f"[+] Rendering route '{route}' -> '{out_file}'...")
            resp = client.get(route)
            
            out_path = os.path.join(SITE_DIR, out_file)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            
            content = resp.data
            
            # For HTML files, ensure base href or relative paths work gracefully on both GitHub Pages and Cloudflare Pages
            if out_file.endswith(".html"):
                html_text = content.decode("utf-8", errors="ignore")
                base_script = """<script>
    if (window.location.hostname.endsWith('github.io') && window.location.pathname.startsWith('/PustakVerse')) {
        var b = document.createElement('base');
        b.href = '/PustakVerse/';
        document.head.prepend(b);
    }
</script>"""
                if "<head>" in html_text:
                    html_text = html_text.replace("<head>", f"<head>\n    {base_script}", 1)
                elif "<HEAD>" in html_text:
                    html_text = html_text.replace("<HEAD>", f"<HEAD>\n    {base_script}", 1)
                content = html_text.encode("utf-8")

            with open(out_path, "wb") as f:
                f.write(content)

        # 4. Generate custom 404 page for GitHub Pages
        print("[+] Generating 404.html...")
        resp_404 = client.get("/")
        not_found_html = resp_404.data.decode("utf-8", errors="ignore")
        with open(os.path.join(SITE_DIR, "404.html"), "w", encoding="utf-8") as f:
            f.write(not_found_html)

    print("[SUCCESS] Static site generation complete!")
    print(f"Total files in _site: {len(os.listdir(SITE_DIR))}")

if __name__ == "__main__":
    build()
