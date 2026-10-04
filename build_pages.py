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
    ("/forgot_password", "forgot_password/index.html"),
    ("/forgot-password", "forgot-password/index.html"),
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

        # 4B. Render master authentic dashboard from templates/dashboard.html
        print("[+] Rendering authentic full dashboard from templates/dashboard.html...")
        from flask import render_template, session
        with app.test_request_context():
            session['user_id'] = 1
            session['username'] = 'abhinavgiri45'
            session['role'] = 'developer'
            session['email'] = 'abhinavgiri370@gmail.com'
            session['official_designation'] = 'Founder & Lead Architect'
            session['is_absolute_power'] = True
            session['post_tier'] = 1
            
            master_dash_html = render_template('dashboard.html',
                all_categories=[
                    {'id': 1, 'name': 'Fiction', 'book_count': 12},
                    {'id': 2, 'name': 'Non-Fiction', 'book_count': 8},
                    {'id': 3, 'name': 'Philosophy', 'book_count': 15},
                    {'id': 4, 'name': 'Academic', 'book_count': 5},
                    {'id': 5, 'name': 'Science', 'book_count': 9}
                ],
                leadership_team=[
                    {
                        'id': 1,
                        'name': 'Abhinav Giri',
                        'role_title': 'Founder & Chief Technology Officer (CTO)',
                        'email': 'abhinavgiri370@gmail.com',
                        'phone': '+91 99999 99999',
                        'address': 'Greater Noida, Uttar Pradesh, India',
                        'bio': 'Visionary founder and lead architect behind PustakVerse and Girionix AI. Dedicated to democratizing high-quality academic literature, research papers, and AI-powered learning tools worldwide.',
                        'photo': '/static/PustakVerse.png',
                        'is_founder': 1,
                        'display_order': 1,
                        'instagram_id': 'https://www.instagram.com/abhinavgiri45/',
                        'x_id': 'https://x.com/abhinavgiri45',
                        'linkedin_id': 'https://www.linkedin.com/in/abhinav-giri',
                        'github_id': 'https://github.com/abhinavgiri45',
                        'website_url': 'https://pustakverse.com'
                    }
                ],
                site_settings={
                    'donation_active': True,
                    'checkout_donation_active': True,
                    'upi_id': 'abhinavgiri370@okaxis',
                    'rp_key_id': 'rzp_live_key',
                    'rp_key_secret': 'rzp_live_secret'
                },
                two_factor_enabled=True,
                security_score=100,
                user_profile={
                    'id': 1,
                    'username': 'abhinavgiri45',
                    'email': 'abhinavgiri370@gmail.com',
                    'role': 'developer',
                    'is_verified': 1,
                    'two_factor_enabled': 1,
                    'security_question': 'What is your primary development framework?',
                    'created_at': '2026-01-01',
                    'last_activity': '2026-10-04'
                },
                my_books=[],
                pending_authors=[],
                all_users=[
                    {'id': 1, 'username': 'abhinavgiri45', 'email': 'abhinavgiri370@gmail.com', 'role': 'developer', 'last_activity': 'Active now', 'failed_attempts': 0, 'locked_until': None}
                ],
                searched_users=[],
                official_logs=[],
                del_requests=[],
                book_del_requests=[],
                username_requests=[],
                search_query='',
                show_delete_otp_form=False,
                client_ip='127.0.0.1',
                user_agent_str='Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                system_metrics={'cached_items': 120, 'maintenance_mode': False, 'upload_freeze': False},
                archive_books=[],
                official_designation='Founder & Lead Architect',
                is_absolute_power=True,
                post_tier=1
            )
            dash_path = os.path.join(static_src, "dashboard.html")
            with open(dash_path, "w", encoding="utf-8") as f:
                f.write(master_dash_html)

        # 5. Mirror key HTML pages into static/ so Cloudflare Pages env.ASSETS can serve them directly
        print("[+] Syncing static HTML files into static/ for Cloudflare Pages deployment...")
        static_html_map = [
            ("index.html", "index.html"),
            ("contact/index.html", "contact.html"),
            ("terms/index.html", "terms.html"),
            ("tools/index.html", "tools.html"),
            ("login/index.html", "login.html"),
            ("register/index.html", "register.html"),
            ("forgot_password/index.html", "forgot_password.html"),
            ("ask_ai/index.html", "ask_ai.html"),
        ]
        for src_rel, dst_name in static_html_map:
            src_f = os.path.join(SITE_DIR, src_rel)
            dst_f = os.path.join(static_src, dst_name)
            if os.path.exists(src_f):
                shutil.copy(src_f, dst_f)

        # 4C. Render master authentic my_library from templates/my_library.html
        print("[+] Rendering authentic my_library from templates/my_library.html...")
        with app.test_request_context():
            session['user_id'] = 1
            session['username'] = 'abhinavgiri45'
            session['role'] = 'developer'
            master_lib_html = render_template('my_library.html', saved_books=[])
            lib_path = os.path.join(static_src, "my_library.html")
            with open(lib_path, "w", encoding="utf-8") as f:
                f.write(master_lib_html)

        # Ensure my_library is available in _site for GitHub Pages
        lib_dst_dir = os.path.join(SITE_DIR, "my-library")
        os.makedirs(lib_dst_dir, exist_ok=True)
        shutil.copy(lib_path, os.path.join(lib_dst_dir, "index.html"))
        lib_alt_dst = os.path.join(SITE_DIR, "my_library")
        os.makedirs(lib_alt_dst, exist_ok=True)
        shutil.copy(lib_path, os.path.join(lib_alt_dst, "index.html"))

        # 4D. Render activity monitor template
        print("[+] Rendering activity monitor template...")
        with app.test_request_context():
            session['user_id'] = 1
            session['username'] = 'abhinavgiri45'
            session['role'] = 'developer'
            master_am_html = render_template('activity_monitor.html', pin_verified=False, pin_error=False)
            am_path = os.path.join(static_src, "activity_monitor.html")
            with open(am_path, "w", encoding="utf-8") as f:
                f.write(master_am_html)

        am_dst_dir = os.path.join(SITE_DIR, "admin", "activity-monitor")
        os.makedirs(am_dst_dir, exist_ok=True)
        shutil.copy(am_path, os.path.join(am_dst_dir, "index.html"))

        # Ensure dashboard is available in _site for GitHub Pages
        dash_src = os.path.join(static_src, "dashboard.html")
        dash_dst_dir = os.path.join(SITE_DIR, "dashboard")
        os.makedirs(dash_dst_dir, exist_ok=True)
        if os.path.exists(dash_src):
            shutil.copy(dash_src, os.path.join(dash_dst_dir, "index.html"))

        # Ensure viewer is available in _site for GitHub Pages
        viewer_src = os.path.join(static_src, "viewer.html")
        viewer_dst_dir = os.path.join(SITE_DIR, "viewer")
        os.makedirs(viewer_dst_dir, exist_ok=True)
        if os.path.exists(viewer_src):
            shutil.copy(viewer_src, os.path.join(viewer_dst_dir, "index.html"))
            shutil.copy(viewer_src, os.path.join(SITE_DIR, "viewer.html"))

    print("[SUCCESS] Static site generation complete!")
    print(f"Total files in _site: {len(os.listdir(SITE_DIR))}")

if __name__ == "__main__":
    build()
