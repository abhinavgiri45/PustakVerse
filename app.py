import os
import secrets
import random
import smtplib
import logging
import re
import time
import io
import base64
import razorpay
from email.message import EmailMessage
from email.mime.text import MIMEText
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import mysql.connector
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth
from PyPDF2 import PdfReader, PdfWriter
import requests

# ==========================================
# APPLICATION SETUP & LOGGING CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__, static_folder='static', template_folder='templates')

app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_pustakverse_fallback_key')

UPLOAD_FOLDER = 'static/uploads'
PRIVATE_PDF_FOLDER = 'private_uploads/pdfs'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PRIVATE_PDF_FOLDER'] = PRIVATE_PDF_FOLDER
payment_schema_ready = False

os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'covers'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'logos'), exist_ok=True)
os.makedirs(os.path.join(app.config['PRIVATE_PDF_FOLDER']), exist_ok=True)

# ==========================================
# IN-MEMORY CACHE & DYNAMIC SETTINGS
# ==========================================
global_cache = {
    'settings': {'logo_image': 'PustakVerse.png', 'hero_title': 'PustakVerse', 'hero_subtitle': 'Every Book. Every Mind. Free.', 'donation_active': False, 'donation_qr': None, 'rp_key_id': '', 'rp_key_secret': ''},
    'catalogs': [{'name': 'Fiction'}, {'name': 'Non-Fiction'}, {'name': 'Educational'}, {'name': 'History'}, {'name': 'Poetry'}],
    'last_update': 0
}

def invalidate_cache():
    global_cache['last_update'] = 0

def clean_book_data(books):
    if not books: return []
    for b in books:
        b['cover_image'] = str(b.get('cover_image') or "")
        b['pdf_file'] = str(b.get('pdf_file') or "")
        b['author_name'] = str(b.get('author_name') or "Unknown")
    return books

@app.context_processor
def inject_global_settings():
    current_time = time.time()
    if current_time - global_cache['last_update'] > 60:
        db = None
        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM front_page_settings WHERE id = 1")
            fetched_settings = cursor.fetchone()
            cursor.execute("SELECT * FROM catalogs")
            fetched_catalogs = cursor.fetchall()
            
            if fetched_settings and fetched_catalogs:
                fetched_settings['logo_image'] = str(fetched_settings.get('logo_image') or "PustakVerse.png")
                fetched_settings['donation_qr'] = str(fetched_settings.get('donation_qr') or "")
                fetched_settings['hero_title'] = str(fetched_settings.get('hero_title') or "PustakVerse")
                fetched_settings['hero_subtitle'] = str(fetched_settings.get('hero_subtitle') or "")
                fetched_settings['rp_key_id'] = str(fetched_settings.get('rp_key_id') or "")
                fetched_settings['rp_key_secret'] = str(fetched_settings.get('rp_key_secret') or "")
                
                global_cache['settings'] = fetched_settings
                global_cache['catalogs'] = fetched_catalogs
                global_cache['last_update'] = current_time
        except Exception as e:
            logging.error(f"Context Processor Cache Error: {e}")
        finally:
            if db:
                try: db.close()
                except: pass
    return dict(site_settings=global_cache['settings'], site_catalogs=global_cache['catalogs'])

@app.template_filter('drive_img')
def drive_img(url):
    if url and 'drive.google.com' in url:
        match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        if not match: match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
        if match: return f"https://drive.google.com/thumbnail?id={match.group(1)}&sz=w1000"
    return url

# ==========================================
# GOOGLE OAUTH & GMAIL API (HYBRID EMAIL SYSTEM)
# ==========================================
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID', '').strip(),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', '').strip(),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

def send_email_wrapper(to_email, subject, body):
    client_id = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()
    refresh_token = os.environ.get('GOOGLE_REFRESH_TOKEN', '').strip()
    email_password = os.environ.get('EMAIL_PASSWORD', '').replace(' ', '').strip()

    if refresh_token and client_secret:
        try:
            token_url = "https://oauth2.googleapis.com/token"
            token_data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }
            r = requests.post(token_url, data=token_data, timeout=5)
            res_json = r.json()
            access_token = res_json.get("access_token")

            if access_token:
                message = EmailMessage()
                message.set_content(body)
                message['To'] = to_email
                message['Subject'] = subject
                message['From'] = "noreply.pustakverse@gmail.com"

                encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
                send_url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }
                send_res = requests.post(send_url, json={"raw": encoded_message}, headers=headers, timeout=5)
                if send_res.status_code in [200, 201]:
                    return True
        except Exception as e: pass

    if email_password:
        try:
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = "noreply.pustakverse@gmail.com"
            msg['To'] = to_email
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=5) as server:
                server.login("noreply.pustakverse@gmail.com", email_password)
                server.send_message(msg)
            return True
        except Exception as smtp_err: pass
    return True

def send_otp_email(to_email, otp):
    return send_email_wrapper(to_email, 'PustakVerse - Password Reset OTP', f"Your password reset code is: {otp}")

def send_2fa_email(to_email, otp):
    return send_email_wrapper(to_email, 'PustakVerse - 2-Step Login Verification', f"Your Login Verification code is: {otp}")

def send_welcome_reader(to_email, username):
    return send_email_wrapper(to_email, 'Welcome to PustakVerse!', f"Hello {username},\n\nWelcome to PustakVerse! Dive into our extensive Global Library today!")

def send_pending_author(to_email, username):
    return send_email_wrapper(to_email, 'PustakVerse - Author Account Under Review', f"Hello {username},\n\nThank you for registering as an Author! Your account is currently under review.")

def send_approved_author(to_email, username):
    return send_email_wrapper(to_email, 'Your PustakVerse Author Account is Approved!', f"Hello {username},\n\nCongratulations! Your Author account is officially approved. You can now publish your books.")

def send_official_welcome(to_email, username, password):
    return send_email_wrapper(to_email, 'Welcome to the PustakVerse Official Team', f"Hello {username},\n\nWelcome to the administrative team!\n\nUsername: {username}\nPassword: {password}")

def send_revoked_official_email(to_email, username, reason):
    return send_email_wrapper(to_email, 'PustakVerse - Administrative Privileges Revoked', f"Hello {username},\n\nYour official administrative privileges on PustakVerse have been revoked.\n\nReason: {reason}")

def send_account_deleted_email(to_email, username, reason):
    return send_email_wrapper(to_email, 'PustakVerse - Account Deletion Notice', f"Hello {username},\n\nYour PustakVerse account has been permanently deleted by an administrator.\n\nReason: {reason}\n\nIf you believe this was a mistake, please contact support.")

def send_author_rejected_email(to_email, username, reason):
    return send_email_wrapper(to_email, 'PustakVerse - Author Application Status', f"Hello {username},\n\nWe regret to inform you that your application for an Author account has been rejected.\n\nReason: {reason}")

def send_book_deleted_email(to_email, username, book_title, reason):
    return send_email_wrapper(to_email, 'PustakVerse - Book Removal Notice', f"Hello {username},\n\nYour book titled '{book_title}' has been removed from PustakVerse by the platform Developer.\n\nReason: {reason}\n\nPlease ensure your future publications adhere to our platform guidelines.")

# ==========================================
# SECURE TiDB (MYSQL) DATABASE CONNECTION
# ==========================================
def get_db_connection(retries=2, delay=1.0):
    last_exception = None
    db_host = os.environ.get('DB_HOST', "gateway01.ap-southeast-1.prod.aws.tidbcloud.com")
    db_port = int(os.environ.get('DB_PORT', 4000))
    db_user = os.environ.get('DB_USER', "39proe1L4PTbJ3X.root")
    db_pass = os.environ.get('DB_PASSWORD', "cOXI6Co9lYTGuTsM")
    db_name = os.environ.get('DB_NAME', "test")

    for attempt in range(retries):
        try:
            conn = mysql.connector.connect(host=db_host, port=db_port, user=db_user, password=db_pass, database=db_name, ssl_verify_cert=False, ssl_verify_identity=False, connection_timeout=8)
            if conn.is_connected(): return conn
        except mysql.connector.Error as err:
            last_exception = err; time.sleep(delay)
    raise last_exception

def payment_gateway_configured():
    settings = global_cache.get('settings', {})
    return bool(settings.get('rp_key_id') and settings.get('rp_key_secret'))

def get_payment_fee_paise(price_paise):
    rate = Decimal('0.0236')
    return int((Decimal(price_paise) * rate / (Decimal('1') - rate)).to_integral_value(rounding=ROUND_CEILING))

def ensure_payment_schema():
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        cursor.execute("CREATE TABLE IF NOT EXISTS users (id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(100) NOT NULL UNIQUE, email VARCHAR(150) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, role ENUM('reader', 'author', 'official', 'developer') DEFAULT 'reader', is_verified BOOLEAN DEFAULT FALSE, security_question VARCHAR(255) NOT NULL, security_answer VARCHAR(255) NOT NULL, verification_reason TEXT, payout_details VARCHAR(255) DEFAULT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)")
        try:
            cursor.execute("SHOW COLUMNS FROM users LIKE 'two_factor_enabled'")
            if not cursor.fetchone(): cursor.execute("ALTER TABLE users ADD COLUMN two_factor_enabled BOOLEAN DEFAULT FALSE")
        except Exception: pass
        
        try:
            cursor.execute("SHOW COLUMNS FROM users LIKE 'razorpay_account_id'")
            if not cursor.fetchone(): cursor.execute("ALTER TABLE users ADD COLUMN razorpay_account_id VARCHAR(100) DEFAULT NULL")
        except Exception: pass
        
        cursor.execute("CREATE TABLE IF NOT EXISTS username_requests (id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, new_username VARCHAR(100) NOT NULL, reason TEXT NOT NULL, status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)")
        
        cursor.execute("CREATE TABLE IF NOT EXISTS books (id INT AUTO_INCREMENT PRIMARY KEY, title VARCHAR(255) NOT NULL, author_id INT NOT NULL, catalog VARCHAR(100) NOT NULL, cover_image VARCHAR(1000) NOT NULL, pdf_file VARCHAR(1000) NOT NULL, is_paid BOOLEAN NOT NULL DEFAULT FALSE, price_paise INT NOT NULL DEFAULT 0, private_pdf BOOLEAN NOT NULL DEFAULT FALSE, preview_pages INT NOT NULL DEFAULT 5, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE)")
        
        try:
            cursor.execute("SHOW COLUMNS FROM books LIKE 'preview_pages'")
            if not cursor.fetchone(): cursor.execute("ALTER TABLE books ADD COLUMN preview_pages INT NOT NULL DEFAULT 5")
        except Exception: pass

        cursor.execute("CREATE TABLE IF NOT EXISTS personal_library (id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, book_id INT NOT NULL, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE, UNIQUE(user_id, book_id))")
        cursor.execute("CREATE TABLE IF NOT EXISTS interactions (id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, book_id INT NOT NULL, rating INT CHECK (rating >= 1 AND rating <= 5), review TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE)")
        cursor.execute("CREATE TABLE IF NOT EXISTS deletion_requests (id INT AUTO_INCREMENT PRIMARY KEY, target_user_id INT NOT NULL, requested_by INT NOT NULL, reason TEXT, status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE CASCADE)")
        
        cursor.execute("CREATE TABLE IF NOT EXISTS book_deletion_requests (id INT AUTO_INCREMENT PRIMARY KEY, book_id INT NOT NULL, requested_by INT NOT NULL, reason TEXT, status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE, FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE CASCADE)")

        cursor.execute("CREATE TABLE IF NOT EXISTS front_page_settings (id INT AUTO_INCREMENT PRIMARY KEY, hero_title VARCHAR(255) DEFAULT 'PustakVerse', hero_subtitle VARCHAR(255) DEFAULT 'Every Book. Every Mind. Free.', logo_image VARCHAR(255) DEFAULT 'PustakVerse.png', font_color VARCHAR(50) DEFAULT '#ffffff', donation_qr VARCHAR(255) DEFAULT NULL, donation_active BOOLEAN DEFAULT FALSE)")
        
        try:
            cursor.execute("SHOW COLUMNS FROM front_page_settings LIKE 'donation_qr'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE front_page_settings ADD COLUMN donation_qr VARCHAR(255) DEFAULT NULL")
                cursor.execute("ALTER TABLE front_page_settings ADD COLUMN donation_active BOOLEAN DEFAULT FALSE")
        except Exception: pass

        try:
            cursor.execute("SHOW COLUMNS FROM front_page_settings LIKE 'rp_key_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE front_page_settings ADD COLUMN rp_key_id VARCHAR(255) DEFAULT NULL")
                cursor.execute("ALTER TABLE front_page_settings ADD COLUMN rp_key_secret VARCHAR(255) DEFAULT NULL")
        except Exception: pass

        cursor.execute("INSERT IGNORE INTO front_page_settings (id) VALUES (1)")
        cursor.execute("CREATE TABLE IF NOT EXISTS catalogs (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE)")
        cursor.execute("INSERT IGNORE INTO catalogs (name) VALUES ('Fiction'), ('Non-Fiction'), ('Educational'), ('History'), ('Poetry')")
        cursor.execute("CREATE TABLE IF NOT EXISTS purchases (id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, book_id INT NOT NULL, razorpay_order_id VARCHAR(100) NOT NULL UNIQUE, razorpay_payment_id VARCHAR(100) NULL UNIQUE, amount_paise INT NOT NULL, fee_paise INT NOT NULL DEFAULT 0, status ENUM('pending', 'paid', 'failed', 'refunded') NOT NULL DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, paid_at TIMESTAMP NULL, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE)")
        cursor.execute("CREATE TABLE IF NOT EXISTS official_activities (id INT AUTO_INCREMENT PRIMARY KEY, official_id INT NOT NULL, action VARCHAR(255) NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (official_id) REFERENCES users(id) ON DELETE CASCADE)")
        db.commit()
        return True
    except Exception as error:
        if db: db.rollback()
        return False
    finally:
        if db:
            try: db.close()
            except: pass

def log_official_activity(official_id, action_desc):
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor()
        cursor.execute("INSERT INTO official_activities (official_id, action) VALUES (%s, %s)", (official_id, action_desc))
        db.commit()
    except Exception as e: pass
    finally:
        if db:
            try: db.close()
            except: pass

@app.before_request
def ensure_payment_schema_before_request():
    global payment_schema_ready
    if not payment_schema_ready: payment_schema_ready = ensure_payment_schema()

def create_master_developer():
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE username = 'abhinavgiri45'")
        if not cursor.fetchone():
            hashed_pw = generate_password_hash('123@Abhinav')
            cursor.execute("INSERT IGNORE INTO users (username, email, password_hash, role, is_verified, security_question, security_answer, verification_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", ('abhinavgiri45', 'abhinavgiri370@gmail.com', hashed_pw, 'developer', True, 'What is your favorite book?', 'gita', 'Master Admin'))
            db.commit()
    except Exception as e: pass
    finally:
        if db:
            try: db.close()
            except: pass

# ==========================================
# PUBLIC ROUTES & COMPREHENSIVE TERMS
# ==========================================
@app.route('/terms')
def terms():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Terms & Conditions - PustakVerse</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; background-color: #f7fafc; color: #2d3748; margin: 0; padding: 0; }
            .container { max-width: 900px; margin: 40px auto; padding: 40px; background: #ffffff; box-shadow: 0 10px 25px rgba(0,0,0,0.05); border-radius: 12px; border-top: 6px solid #f66d2f; }
            h1 { color: #1a202c; border-bottom: 2px solid #edf2f7; padding-bottom: 15px; font-size: 2.2rem; }
            h2 { color: #2b6cb0; margin-top: 35px; font-size: 1.5rem; }
            .alert-box { background-color: #fff5f5; border-left: 5px solid #e53e3e; padding: 20px; margin: 30px 0; border-radius: 4px; }
            .alert-box h2 { color: #c53030; margin-top: 0; }
            ul { padding-left: 20px; }
            li { margin-bottom: 10px; }
            .btn { display: inline-block; padding: 12px 30px; background: #f66d2f; color: white; text-decoration: none; border-radius: 30px; font-weight: bold; transition: 0.3s; margin-top: 20px; }
            .btn:hover { background: #dd6b20; transform: translateY(-2px); }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>PustakVerse Comprehensive Terms & Conditions</h1>
            <p>Welcome to PustakVerse. By registering an account, accessing, or using our platform, you agree to be bound by these Terms and Conditions. Please read them carefully to understand your rights and responsibilities.</p>
            
            <h2>1. Terms for Readers (Users)</h2>
            <ul>
                <li><strong>Personal Use Guarantee:</strong> Books purchased or accessed for free on PustakVerse are strictly for your personal, non-commercial use. You may not distribute, copy, resell, or share secure PDFs outside of the platform framework.</li>
                <li><strong>Account Integrity & Security:</strong> You are fully responsible for maintaining the confidentiality of your login credentials and Two-Factor Authentication (2FA) codes. PustakVerse is not liable for unauthorized access resulting from user negligence.</li>
                <li><strong>Content Availability:</strong> While we strive to maintain a permanent library, PustakVerse does not guarantee that any specific author-published book will remain available indefinitely. Independent Authors retain the right to modify or remove their content.</li>
                <li><strong>Refund Policy:</strong> All digital sales on PustakVerse are final. Refunds are only issued in verified instances of technical failure resulting in the non-delivery of purchased content. Dislike of a book's content does not qualify for a refund.</li>
            </ul>

            <h2>2. Terms for Authors (Publishers)</h2>
            <ul>
                <li><strong>Copyright, Ownership, & Plagiarism:</strong> By uploading a book or document to PustakVerse, you legally warrant and represent that you are the original creator or possess the explicit commercial rights to distribute the content. Plagiarism, piracy, or copyright infringement is strictly prohibited and will result in an immediate permanent ban.</li>
                <li><strong>Content Guidelines & Lawfulness:</strong> You agree not to publish content that is illegal, incites physical violence, contains explicit illegal material, or violates local, national, and international laws.</li>
                <li><strong>Transparent Payouts & Gateway Fees:</strong> Authors receive exactly 100% of their set list price. To facilitate secure transactions, a mandatory platform convenience fee (covering Razorpay gateway costs) is calculated and added on top of your list price, which is paid by the buyer. PustakVerse reserves the right to suspend payouts or accounts if fraudulent transaction activity is detected.</li>
                <li><strong>Right to Review & Moderation:</strong> PustakVerse Administrators and Officials reserve the explicit right to manually review, reject, suspend, or permanently remove your books or author account at any time for violating these platform terms.</li>
            </ul>

            <div class="alert-box">
                <h2>3. Absolute Liability Disclaimer (Crucial)</h2>
                <p><strong>Platform Immunity:</strong> PustakVerse operates solely as a user-generated digital hosting platform. The Developers, Officials, and Administrators of PustakVerse take <strong>ABSOLUTELY NO RESPONSIBILITY OR LEGAL LIABILITY</strong> for the books, documents, text, or content uploaded by independent Authors.</p>
                <p><strong>The "Archive" Curatorial Exception:</strong> The <em>only</em> exception to this rule is content officially published within the <strong>"Archive"</strong> catalog. The platform's Developer maintains full administrative authority, legal oversight, and management over Archive books. All other catalogs (Fiction, Non-Fiction, Educational, History, Poetry, etc.) are the sole legal and moral responsibility of the respective uploading Author.</p>
            </div>

            <h2>4. Administrative Enforcement & Termination</h2>
            <p>PustakVerse reserves the absolute right to terminate, ban, or suspend your account, without prior notice or liability, for any reason whatsoever, including without limitation if you breach the Terms outlined in this agreement. Administrative decisions made by the Developer are final.</p>

            <div style="text-align: center; margin-top: 40px; border-top: 1px solid #edf2f7; padding-top: 30px;">
                <a href="javascript:window.close();" class="btn">I Understand - Close Window</a>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content

@app.route('/check_username', methods=['POST'])
def check_username():
    username = request.form.get('username', '').strip()
    if not username: return jsonify({'available': False, 'message': ''})
    if not re.match(r'^[a-zA-Z0-9_]+$', username): return jsonify({'available': False, 'message': 'Username cannot contain spaces or special characters.'})
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if user: return jsonify({'available': False, 'message': 'Username is already taken'})
        return jsonify({'available': True, 'message': 'Username is available!'})
    except Exception as e: return jsonify({'available': False, 'message': 'Checking...'})
    finally:
        if db:
            try: db.close()
            except: pass

@app.route('/category/<name>')
def category_view(name):
    db = None; books = []
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("""SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, users.username as author_name, users.role as author_role 
            FROM books JOIN users ON books.author_id = users.id WHERE books.catalog = %s ORDER BY books.created_at DESC""", (name,))
        books = clean_book_data(cursor.fetchall())
    except Exception: flash("Experiencing high traffic. Please refresh to load books.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('category.html', books=books, page_title=name)

@app.route('/archives')
def archives_view():
    db = None; books = []
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("""SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, users.username as author_name, users.role as author_role 
            FROM books JOIN users ON books.author_id = users.id WHERE books.catalog = 'Archives' ORDER BY books.created_at ASC""")
        books = clean_book_data(cursor.fetchall())
    except Exception: flash("Experiencing high traffic. Please refresh to load books.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('category.html', books=books, page_title="Archives (Free Classics)")

@app.route('/')
def index():
    db = None; books = []
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("""SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, users.username as author_name, users.role as author_role 
            FROM books JOIN users ON books.author_id = users.id ORDER BY books.created_at DESC""")
        books = clean_book_data(cursor.fetchall())
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('index.html', books=books)

# ==========================================
# AUTHENTICATION
# ==========================================
@app.route('/register', methods=['POST'])
def register():
    username = request.form['username'].strip(); email = request.form['email'].strip(); password = request.form['password']
    role = request.form['role']; sec_question = request.form['security_question']; sec_answer = request.form['security_answer'].lower().strip()
    verification_reason = request.form.get('verification_reason', '')
    
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        flash("Username can only contain letters, numbers, and underscores (no spaces or special characters).", "error")
        return redirect(url_for('login'))

    if role not in ['reader', 'author']: role = 'reader'
    hashed_pw = generate_password_hash(password)
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(); is_verified = (role == 'reader')
        cursor.execute("INSERT INTO users (username, email, password_hash, role, is_verified, security_question, security_answer, verification_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (username, email, hashed_pw, role, is_verified, sec_question, sec_answer, verification_reason))
        db.commit()
        if role == 'reader': send_welcome_reader(email, username); flash("Account created successfully! Please sign in.", "success")
        elif role == 'author': send_pending_author(email, username); flash("Author Account created! Wait for approval.", "success")
    except mysql.connector.IntegrityError: flash("Username or Email already exists. Please choose another.", "error")
    except Exception: flash("Network error occurred during registration. Please try again.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action', 'login')
        if action == 'login':
            login_portal = request.form.get('login_portal', 'reader')
            db = None; user = None
            try:
                db = get_db_connection(); cursor = db.cursor(dictionary=True)
                cursor.execute("SELECT * FROM users WHERE username = %s", (request.form['username'].strip(),))
                user = cursor.fetchone()
            except Exception as e:
                flash("Secure connection to the database timed out. Please try logging in again.", "error")
                return render_template('login.html', active_tab=login_portal)
            finally:
                if db:
                    try: db.close()
                    except: pass
            
            if user and check_password_hash(user['password_hash'], request.form['password']):
                if login_portal == 'reader' and user['role'] != 'reader': flash("Please use the 'Author / Official' tab to log in to your account.", "error"); return render_template('login.html', active_tab='reader')
                if login_portal == 'author_official' and user['role'] not in ['author', 'official', 'developer']: flash("Readers must log in using the 'Reader Login' tab.", "error"); return render_template('login.html', active_tab='official')

                if user['role'] in ['official', 'developer'] or user.get('two_factor_enabled'):
                    otp = str(random.randint(100000, 999999))
                    session['login_2fa_otp'] = otp
                    session['pending_2fa_user'] = {'id': user['id'], 'username': user['username'], 'role': user['role'], 'is_verified': user['is_verified'], 'email': user['email']}
                    
                    if send_2fa_email(user['email'], otp): 
                        flash("A 2-Step Verification code has been sent to your email.", "info")
                        return render_template('login.html', show_2fa_form=True, email=user['email'])
                    else: 
                        flash("Failed to send 2FA email. Contact admin.", "error")
                        return render_template('login.html', active_tab=login_portal)

                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = user['role']
                session['is_verified'] = user['is_verified']
                session['show_telegram_popup'] = True
                return redirect(url_for('dashboard'))
            
            flash("Invalid username or password.", "error")
            return render_template('login.html', active_tab=login_portal)
            
        elif action == 'verify_2fa':
            # FIX: This strips out all accidental spaces so the code matches perfectly!
            user_otp = request.form.get('otp', '').replace(' ', '').strip()
            pending_user = session.get('pending_2fa_user')
            correct_otp = session.get('login_2fa_otp')
            
            if pending_user and user_otp == correct_otp:
                session['user_id'] = pending_user['id']
                session['username'] = pending_user['username']
                session['role'] = pending_user['role']
                session['is_verified'] = pending_user['is_verified']
                session.pop('login_2fa_otp', None)
                session.pop('pending_2fa_user', None)
                session['show_telegram_popup'] = True
                flash(f"Welcome back, {pending_user['username']}!", "success")
                return redirect(url_for('dashboard'))
            else: 
                flash("Invalid Verification Code. Please try again.", "error")
                return render_template('login.html', show_2fa_form=True, email=pending_user.get('email', ''))
                
    return render_template('login.html', active_tab='reader')

@app.route('/login/google')
def google_login(): return google.authorize_redirect(url_for('google_authorize', _external=True))

@app.route('/login/google/callback')
def google_authorize():
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            flash("Google login failed. User info not received.", "error")
            return redirect(url_for('login'))
        
        email = user_info.get('email')
        name = user_info.get('name')
        base_username = re.sub(r'[^a-zA-Z0-9_]', '', name.lower()) if name else email.split('@')[0]
        if not base_username: base_username = f"user_{secrets.randbelow(9999)}"
        
        db = None; user = None
        try:
            db = get_db_connection(); cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            if not user:
                cursor.execute("SELECT * FROM users WHERE username = %s", (base_username,))
                if cursor.fetchone(): base_username = f"{base_username}{secrets.randbelow(9999)}"
                cursor.execute("INSERT INTO users (username, email, password_hash, role, is_verified, security_question, security_answer) VALUES (%s, %s, %s, 'reader', TRUE, 'Google', 'Google')", (base_username, email, generate_password_hash(secrets.token_urlsafe(16))))
                db.commit()
                send_welcome_reader(email, base_username)
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()
        except Exception as e:
            flash("Database connection timeout during Google Sign-In.", "error")
            return redirect(url_for('login'))
        finally:
            if db:
                try: db.close()
                except: pass

        if user['role'] in ['official', 'developer'] or user.get('two_factor_enabled'):
            otp = str(random.randint(100000, 999999))
            session['login_2fa_otp'] = otp
            session['pending_2fa_user'] = {'id': user['id'], 'username': user['username'], 'role': user['role'], 'is_verified': user['is_verified'], 'email': user['email']}
            if send_2fa_email(user['email'], otp): 
                flash("A 2-Step Verification code has been sent to your email.", "info")
                return render_template('login.html', show_2fa_form=True, email=user['email'])
            else: 
                flash("Failed to send 2FA email.", "error")
                return redirect(url_for('login'))

        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        session['is_verified'] = user['is_verified']
        session['show_telegram_popup'] = True
        return redirect(url_for('dashboard'))
    except Exception as e:
        flash("Google Authentication failed. Please try again.", "error")
        return redirect(url_for('login'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        action = request.form.get('action')
        db = None
        if action == 'send_otp':
            email = request.form.get('email')
            try:
                db = get_db_connection(); cursor = db.cursor(dictionary=True)
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,)); user = cursor.fetchone()
            except Exception:
                flash("Database connection timeout. Please try again.", "error")
                return render_template('forgot_password.html', show_otp_form=False)
            finally:
                if db:
                    try: db.close()
                    except: pass
            if user:
                otp = str(random.randint(100000, 999999)); session['reset_otp'] = otp; session['reset_email'] = email
                if send_otp_email(email, otp): flash("An OTP has been sent.", "success"); return render_template('forgot_password.html', show_otp_form=True, email=email)
            else: flash("If this email exists, an OTP will be sent.", "info") 
                
        elif action == 'verify_otp':
            user_otp = request.form.get('otp'); new_password = request.form.get('new_password'); email = session.get('reset_email')
            if user_otp and user_otp == session.get('reset_otp'):
                hashed_pw = generate_password_hash(new_password)
                try:
                    db = get_db_connection(); cursor = db.cursor()
                    cursor.execute("UPDATE users SET password_hash = %s WHERE email = %s", (hashed_pw, email)); db.commit()
                except Exception: flash("Database connection timeout. Please try again.", "error"); return render_template('forgot_password.html', show_otp_form=True, email=email)
                finally:
                    if db:
                        try: db.close()
                        except: pass
                session.pop('reset_otp', None); session.pop('reset_email', None)
                flash("Password changed successfully. You may now log in.", "success"); return redirect(url_for('login'))
            else: flash("Invalid OTP. Please try again.", "error"); return render_template('forgot_password.html', show_otp_form=True, email=email)
    return render_template('forgot_password.html', show_otp_form=False)

# ==========================================
# CHANGE USERNAME LOGIC
# ==========================================
@app.route('/change_username', methods=['POST'])
def change_username():
    if 'user_id' not in session: return redirect(url_for('login'))
    new_username = request.form.get('new_username', '').strip()
    reason = request.form.get('reason', '').strip()
    role = session.get('role')

    if not new_username:
        flash("New username cannot be empty.", "error"); return redirect(url_for('dashboard'))

    if not re.match(r'^[a-zA-Z0-9_]+$', new_username):
        flash("Username can only contain letters, numbers, and underscores (no spaces or special characters).", "error")
        return redirect(url_for('dashboard'))

    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE username = %s", (new_username,))
        if cursor.fetchone():
            flash("Username is already taken.", "error"); return redirect(url_for('dashboard'))

        if role in ['reader', 'developer']:
            cursor.execute("UPDATE users SET username = %s WHERE id = %s", (new_username, session['user_id']))
            db.commit()
            session['username'] = new_username
            flash("Username changed successfully!", "success")
        else:
            if not reason:
                flash("You must provide a reason for requesting a username change.", "error")
                return redirect(url_for('dashboard'))
            cursor.execute("INSERT INTO username_requests (user_id, new_username, reason) VALUES (%s, %s, %s)", (session['user_id'], new_username, reason))
            db.commit()
            flash("Username change request submitted for administrative approval.", "info")
    except Exception as e:
        flash("Database connection error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/handle_username_request/<int:req_id>/<action>', methods=['POST'])
def handle_username_request(req_id, action):
    role = session.get('role')
    if role not in ['official', 'developer']: return redirect(url_for('dashboard'))

    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM username_requests WHERE id = %s", (req_id,))
        req = cursor.fetchone()
        if not req or req['status'] != 'pending':
            flash("Invalid or already processed request.", "error"); return redirect(url_for('dashboard'))

        cursor.execute("SELECT role FROM users WHERE id = %s", (req['user_id'],))
        target_user = cursor.fetchone()

        if target_user['role'] == 'official' and role != 'developer':
            flash("Only developers can approve official username changes.", "error"); return redirect(url_for('dashboard'))

        if action == 'approve':
            cursor.execute("SELECT id FROM users WHERE username = %s", (req['new_username'],))
            if cursor.fetchone():
                flash("That username was taken by someone else while pending.", "error")
                cursor.execute("UPDATE username_requests SET status = 'rejected' WHERE id = %s", (req_id,)); db.commit()
                return redirect(url_for('dashboard'))

            cursor.execute("UPDATE users SET username = %s WHERE id = %s", (req['new_username'], req['user_id']))
            cursor.execute("UPDATE username_requests SET status = 'approved' WHERE id = %s", (req_id,))
            db.commit(); flash("Username change approved.", "success")
        elif action == 'reject':
            cursor.execute("UPDATE username_requests SET status = 'rejected' WHERE id = %s", (req_id,))
            db.commit(); flash("Username change rejected.", "info")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/send_change_password_otp', methods=['POST'])
def send_change_password_otp():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = None; user = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT email FROM users WHERE id = %s", (session['user_id'],)); user = cursor.fetchone()
    except Exception:
        flash("Database connection error. Please try again.", "error")
        return redirect(url_for('dashboard'))
    finally:
        if db:
            try: db.close()
            except: pass

    if user:
        otp = str(random.randint(100000, 999999)); session['change_pw_otp'] = otp
        if send_otp_email(user['email'], otp): flash("An OTP has been sent to your registered email.", "info")
        else: flash("Failed to send OTP. Please check the email server.", "error")
    return redirect(url_for('dashboard'))

@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_otp = request.form.get('otp'); old_password = request.form.get('old_password'); new_password = request.form.get('new_password')
    valid_otp = session.pop('change_pw_otp', None)
    if not user_otp or not valid_otp or user_otp != valid_otp: flash("Invalid or expired OTP. Please request a new one.", "error"); return redirect(url_for('dashboard'))
        
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT password_hash FROM users WHERE id = %s", (session['user_id'],)); user = cursor.fetchone()
        if not user or not check_password_hash(user['password_hash'], old_password): 
            flash("Incorrect current password.", "error"); return redirect(url_for('dashboard'))
            
        hashed_pw = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed_pw, session['user_id'])); db.commit()
        flash("Your password has been securely updated!", "success"); 
    except Exception:
        flash("Database connection error. Please try again.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
        
    return redirect(url_for('dashboard'))

@app.route('/cancel_password_change')
def cancel_password_change(): session.pop('change_pw_otp', None); return redirect(url_for('dashboard'))

# ==========================================
# E-COMMERCE & BUY NOW LOGIC
# ==========================================
@app.route('/buy_book/<int:book_id>', methods=['POST'])
def buy_book(book_id):
    if 'user_id' not in session: 
        flash('Please sign in or register before purchasing a book.', 'error')
        return redirect(url_for('login'))
    
    db = None; book = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT b.id, b.title, b.is_paid, b.price_paise, b.cover_image, u.razorpay_account_id, u.username as author_name FROM books b JOIN users u ON b.author_id = u.id WHERE b.id = %s", (book_id,))
        book = cursor.fetchone()
        if book:
            book['cover_image'] = book.get('cover_image') or ""
        
        if not book: abort(404)

        if not book['is_paid'] or not book['price_paise']:
            cursor.execute('INSERT IGNORE INTO personal_library (user_id, book_id) VALUES (%s, %s)', (session['user_id'], book_id))
            db.commit(); return redirect(url_for('read_book', book_id=book_id))

        cursor.execute("SELECT id FROM purchases WHERE user_id = %s AND book_id = %s AND status = 'paid'", (session['user_id'], book_id))
        if cursor.fetchone(): return redirect(url_for('read_book', book_id=book_id))
    except Exception: flash("Database connection error. Please try again.", "error"); return redirect(request.referrer or url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass

    if not payment_gateway_configured(): flash('Online payments are not configured by the developer yet.', 'error'); return redirect(request.referrer or url_for('index'))
    
    author_acc_id = book['razorpay_account_id']
    if not author_acc_id:
        flash('The author has not completed their KYC onboarding. Purchases are temporarily disabled for this book.', 'error')
        return redirect(request.referrer or url_for('index'))

    fee_paise = get_payment_fee_paise(book['price_paise'])
    total_paise = book['price_paise'] + fee_paise
    
    db = None
    try:
        settings = global_cache.get('settings', {})
        rp_key_id = settings.get('rp_key_id')
        rp_key_secret = settings.get('rp_key_secret')
        client = razorpay.Client(auth=(rp_key_id, rp_key_secret))
        
        order_data = {
            'amount': total_paise,
            'currency': 'INR',
            'receipt': f"pv-{session['user_id']}-{book_id}-{secrets.token_hex(4)}",
            'transfers': [
                {
                    'account': author_acc_id,
                    'amount': book['price_paise'], 
                    'currency': 'INR',
                    'notes': {'type': 'author_payout', 'book_title': book['title']},
                    'on_hold': 0
                }
            ]
        }
        order = client.order.create(order_data)
        
        db = get_db_connection(); cursor = db.cursor()
        cursor.execute("INSERT INTO purchases (user_id, book_id, razorpay_order_id, amount_paise, fee_paise, status) VALUES (%s, %s, %s, %s, %s, 'pending')", (session['user_id'], book_id, order['id'], book['price_paise'], fee_paise))
        db.commit()
    except Exception as e: 
        logging.error(f"Razorpay Order Error: {e}")
        flash('Unable to start payment. Please try again shortly.', 'error'); return redirect(request.referrer or url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass
            
    return render_template('checkout.html', book=book, order_id=order['id'], total_paise=total_paise, fee_paise=fee_paise, base_price=book['price_paise'], razorpay_key=rp_key_id)

@app.route('/payment/verify', methods=['POST'])
def verify_payment():
    if 'user_id' not in session: abort(401)
    order_id = request.form.get('razorpay_order_id', ''); payment_id = request.form.get('razorpay_payment_id', ''); signature = request.form.get('razorpay_signature', '')
    if not payment_gateway_configured() or not all([order_id, payment_id, signature]): abort(400)
    db = None
    try:
        settings = global_cache.get('settings', {})
        client = razorpay.Client(auth=(settings.get('rp_key_id'), settings.get('rp_key_secret')))
        client.utility.verify_payment_signature({'razorpay_order_id': order_id, 'razorpay_payment_id': payment_id, 'razorpay_signature': signature})
        
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, book_id FROM purchases WHERE razorpay_order_id = %s AND user_id = %s", (order_id, session['user_id']))
        purchase = cursor.fetchone()
        
        if purchase:
            cursor.execute("UPDATE purchases SET razorpay_payment_id = %s, status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE id = %s", (payment_id, purchase['id']))
            cursor.execute('INSERT IGNORE INTO personal_library (user_id, book_id) VALUES (%s, %s)', (session['user_id'], purchase['book_id']))
            db.commit()
        flash('Payment successful! Book has been saved to My Library and unlocked.', 'success'); return redirect(url_for('read_book', book_id=purchase['book_id']))
    except Exception: flash('Payment verification failed.', 'error'); return redirect(url_for('my_library'))
    finally:
        if db:
            try: db.close()
            except: pass

@app.route('/payment_history')
def payment_history():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = None; payments = []
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.razorpay_order_id, p.amount_paise, p.status, p.paid_at, b.title as book_title
            FROM purchases p JOIN books b ON p.book_id = b.id
            WHERE p.user_id = %s ORDER BY p.created_at DESC
        """, (session['user_id'],))
        payments = cursor.fetchall()
    except Exception: flash("Could not load payment history.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('payment_history.html', payments=payments)

@app.route('/book_sales/<int:book_id>')
def book_sales(book_id):
    if session.get('role') not in ['author', 'developer', 'official']: return redirect(url_for('login'))
    db = None; sales = []; book = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT title, author_id, price_paise FROM books WHERE id = %s", (book_id,))
        book = cursor.fetchone()
        if not book or (book['author_id'] != session['user_id'] and session['role'] not in ['developer', 'official']):
            flash("Unauthorized access to book sales.", "error"); return redirect(url_for('dashboard'))

        cursor.execute("""
            SELECT p.razorpay_order_id, p.amount_paise, p.status, p.paid_at, u.username as buyer_name, u.email as buyer_email
            FROM purchases p JOIN users u ON p.user_id = u.id
            WHERE p.book_id = %s AND p.status = 'paid' ORDER BY p.paid_at DESC
        """, (book_id,))
        sales = cursor.fetchall()
    except Exception: flash("Could not load sales history.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('sales_history.html', sales=sales, book=book)

@app.route('/read_book/<int:book_id>')
def read_book(book_id):
    if 'user_id' not in session:
        flash("Please sign in or register to read or preview books.", "error")
        return redirect(url_for('login'))

    db = None; can_read = False
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT id, title, author_id, pdf_file, is_paid, private_pdf, preview_pages FROM books WHERE id = %s', (book_id,))
        book = cursor.fetchone()
        if not book: abort(404)
        can_read = not book['is_paid'] or session.get('user_id') == book['author_id'] or session.get('role') in ('official', 'developer')
        if book['is_paid'] and not can_read and session.get('user_id'):
            cursor.execute("SELECT id FROM purchases WHERE user_id = %s AND book_id = %s AND status = 'paid'", (session['user_id'], book_id))
            can_read = bool(cursor.fetchone())
    except Exception: flash("Database error.", "error"); return redirect(url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass

    return render_template('viewer.html', book=book, can_read=can_read)

@app.route('/serve_secure_pdf/<int:book_id>')
def serve_secure_pdf(book_id):
    if 'user_id' not in session: abort(401)

    db = None; book = None; can_read = False
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT author_id, pdf_file, is_paid, private_pdf, preview_pages FROM books WHERE id = %s', (book_id,))
        book = cursor.fetchone()
        if not book: abort(404)
        
        user_id = session.get('user_id')
        user_role = session.get('role')
        can_read = not book['is_paid'] or user_id == book['author_id'] or user_role in ('official', 'developer')
        
        if book['is_paid'] and not can_read and user_id:
            cursor.execute("SELECT id FROM purchases WHERE user_id = %s AND book_id = %s AND status = 'paid'", (user_id, book_id))
            can_read = bool(cursor.fetchone())
    except Exception: abort(500)
    finally:
        if db:
            try: db.close()
            except: pass

    if book['pdf_file'].startswith('http'): abort(400)
    
    folder = app.config['PRIVATE_PDF_FOLDER'] if book['is_paid'] or book['private_pdf'] else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs')
    full_path = os.path.join(folder, book['pdf_file'])

    if not os.path.exists(full_path):
        abort(404)

    if can_read:
        return send_from_directory(folder, book['pdf_file'])
        
    try:
        reader = PdfReader(full_path)
        writer = PdfWriter()
        author_preview_setting = book.get('preview_pages') or 5
        preview_limit = min(max(1, author_preview_setting), 10)
        num_pages = min(preview_limit, len(reader.pages))
        
        for page_num in range(num_pages):
            writer.add_page(reader.pages[page_num])
            
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return send_file(output, mimetype='application/pdf', download_name=f"preview_{book['pdf_file']}")
    except Exception as e:
        logging.error(f"Error slicing PDF preview: {e}")
        abort(500)

@app.route('/save_book/<int:book_id>', methods=['POST'])
def save_book(book_id):
    if 'user_id' not in session: flash("Please sign in or register first.", "error"); return redirect(url_for('login'))
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor()
        cursor.execute("INSERT IGNORE INTO personal_library (user_id, book_id) VALUES (%s, %s)", (session['user_id'], book_id))
        db.commit(); flash("Book saved to My Library!", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(request.referrer or url_for('index'))

@app.route('/my-library')
def my_library():
    if 'user_id' not in session: flash("Please log in.", "error"); return redirect(url_for('login'))
    db = None; saved_books = []
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        role = session.get('role')
        if role == 'author': cursor.execute("SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, users.username as author_name, users.role as author_role FROM books JOIN users ON books.author_id = users.id WHERE books.author_id = %s ORDER BY books.created_at DESC", (session['user_id'],))
        elif role in ['official', 'developer']: cursor.execute("SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, users.username as author_name, users.role as author_role FROM books JOIN users ON books.author_id = users.id ORDER BY books.created_at DESC")
        else: cursor.execute("SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, users.username as author_name, users.role as author_role FROM personal_library JOIN books ON personal_library.book_id = books.id JOIN users ON books.author_id = users.id WHERE personal_library.user_id = %s ORDER BY personal_library.added_at DESC", (session['user_id'],))
        saved_books = clean_book_data(cursor.fetchall())
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('my_library.html', saved_books=saved_books)

# ==========================================
# DASHBOARD MANAGEMENT
# ==========================================
@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = None
    show_telegram_popup = session.pop('show_telegram_popup', False)
    
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        role = session.get('role')
        search_query = request.args.get('search', '')
        role_filter = request.args.get('role_filter', 'all')

        cursor.execute("SELECT two_factor_enabled FROM users WHERE id = %s", (session['user_id'],))
        tf_data = cursor.fetchone()
        two_factor_enabled = tf_data['two_factor_enabled'] if tf_data else False

        if request.method == 'POST' and 'toggle_2fa' in request.form:
            current_status = request.form.get('current_status') == 'True'
            new_status = not current_status
            cursor.execute("UPDATE users SET two_factor_enabled = %s WHERE id = %s", (new_status, session['user_id']))
            db.commit()
            status_text = "enabled" if new_status else "disabled"
            flash(f"Two-Step Verification has been {status_text}.", "success")
            return redirect(url_for('dashboard'))

        if role == 'author' and request.method == 'POST' and 'create_route_account' in request.form:
            legal_name = request.form.get('legal_name').strip()
            phone = request.form.get('phone').strip()
            try:
                settings = global_cache.get('settings', {})
                client = razorpay.Client(auth=(settings.get('rp_key_id'), settings.get('rp_key_secret')))
                cursor.execute("SELECT email FROM users WHERE id = %s", (session['user_id'],))
                author_email = cursor.fetchone()['email']
                
                account_data = {
                    "email": author_email,
                    "phone": phone,
                    "type": "route",
                    "reference_id": f"pv_{session['user_id']}",
                    "legal_business_name": legal_name,
                    "business_type": "individual"
                }
                route_account = client.account.create(account_data)
                acc_id = route_account['id']
                
                cursor.execute("UPDATE users SET razorpay_account_id = %s WHERE id = %s", (acc_id, session['user_id']))
                db.commit()
                flash(f"Success! Linked Account created. Please check your email to upload KYC documents securely to Razorpay.", "success")
            except Exception as e:
                flash(f"Razorpay Integration Error: Developer needs to add valid keys in settings.", "error")
            return redirect(url_for('dashboard'))

        if request.method == 'POST' and 'title' in request.form:
            catalog = request.form.get('catalog', '')
            if role == 'author':
                cursor.execute("SELECT is_verified FROM users WHERE id = %s", (session['user_id'],))
                if not cursor.fetchone()['is_verified']: flash("Must be verified to publish.", "error"); return redirect(url_for('dashboard'))
                if catalog.lower() == 'archives': flash("Cannot publish to Archives.", "error"); return redirect(url_for('dashboard'))

            c_link = request.form.get('cover_link', '').strip(); p_link = request.form.get('pdf_link', '').strip()
            c_file = request.files.get('cover_image'); p_file = request.files.get('pdf_file')
            is_paid = request.form.get('is_paid') == 'on'
            if catalog.lower() == 'archives': is_paid = False

            try: price_paise = int((Decimal(request.form.get('price_inr', '0').strip() or '0') * 100).quantize(Decimal('1')))
            except (InvalidOperation, ValueError): price_paise = -1

            raw_preview = int(request.form.get('preview_pages', 5) or 5)
            preview_pages = min(max(1, raw_preview), 10)

            if is_paid and price_paise <= 0: flash('Paid books need a valid price.', 'error'); return redirect(url_for('dashboard'))
            
            f_cov = c_link if c_link else (secure_filename(c_file.filename) if c_file and c_file.filename else "")
            f_pdf = p_link if p_link else (secure_filename(p_file.filename) if p_file and p_file.filename else "")
            
            if c_file and not c_link: c_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'covers', f_cov))
            if p_file and not p_link:
                pdf_folder = app.config['PRIVATE_PDF_FOLDER'] if is_paid else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs')
                p_file.save(os.path.join(pdf_folder, f_pdf))
                
            if f_cov and f_pdf:
                cursor.execute("INSERT INTO books (title, author_id, catalog, cover_image, pdf_file, is_paid, price_paise, private_pdf, preview_pages) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (request.form['title'], session['user_id'], request.form['catalog'], f_cov, f_pdf, is_paid, price_paise if is_paid else 0, is_paid, preview_pages))
                db.commit(); flash("Book published successfully!", "success"); return redirect(url_for('dashboard'))

        if role in ['developer', 'official'] and request.method == 'POST':
            if 'approve_author_id' in request.form: 
                auth_id = request.form['approve_author_id']
                cursor.execute("UPDATE users SET is_verified = TRUE WHERE id = %s", (auth_id,)); db.commit()
                cursor.execute("SELECT username, email, role FROM users WHERE id = %s", (auth_id,))
                author_data = cursor.fetchone()
                if author_data: send_approved_author(author_data['email'], author_data['username'])
                flash("Author approved and notified!", "success")
            elif 'reject_author_id' in request.form:
                auth_id = request.form['reject_author_id']
                reason = request.form.get('reject_reason', 'Did not meet platform guidelines.')
                cursor.execute("SELECT username, email FROM users WHERE id = %s", (auth_id,))
                user_data = cursor.fetchone()
                author_name = user_data['username'] if user_data else "Unknown"
                
                if user_data:
                    send_author_rejected_email(user_data['email'], author_name, reason)
                    
                cursor.execute("DELETE FROM users WHERE id = %s", (auth_id,))
                db.commit()
                if role == 'official': log_official_activity(session['user_id'], f"Rejected & deleted author: {author_name}. Reason: {reason}")
                flash("Author rejected and removed.", "success")

        username_requests = []
        if role == 'developer':
            try:
                cursor.execute("SELECT r.id, u.username as current_username, r.new_username, r.reason FROM username_requests r JOIN users u ON r.user_id = u.id WHERE u.role = 'official' AND r.status = 'pending'")
                username_requests = cursor.fetchall()
            except Exception: pass 
        elif role == 'official':
            try:
                cursor.execute("SELECT r.id, u.username as current_username, r.new_username, r.reason FROM username_requests r JOIN users u ON r.user_id = u.id WHERE u.role = 'author' AND r.status = 'pending'")
                username_requests = cursor.fetchall()
            except Exception: pass

        if role == 'developer':
            params = []
            base_query = "SELECT id, username, email, role, last_activity FROM users WHERE role != 'developer'"
            if search_query:
                base_query += " AND (username LIKE %s OR email LIKE %s)"
                params.extend([f"%{search_query}%", f"%{search_query}%"])
            if role_filter and role_filter != 'all':
                base_query += " AND role = %s"
                params.append(role_filter)
            base_query += " ORDER BY last_activity DESC LIMIT 50"
            
            cursor.execute(base_query, tuple(params))
            searched_users = cursor.fetchall()
            
            cursor.execute("SELECT dr.id, u.username as target_name, o.username as official_name, dr.reason FROM deletion_requests dr JOIN users u ON dr.target_user_id = u.id JOIN users o ON dr.requested_by = o.id WHERE dr.status = 'pending'")
            del_requests = cursor.fetchall()
            
            # Fetch Book Deletion Requests for Developer
            cursor.execute("SELECT bdr.id, b.title as book_title, u.username as author_name, o.username as official_name, bdr.reason FROM book_deletion_requests bdr JOIN books b ON bdr.book_id = b.id JOIN users u ON b.author_id = u.id JOIN users o ON bdr.requested_by = o.id WHERE bdr.status = 'pending'")
            book_del_requests = cursor.fetchall()

            cursor.execute("SELECT id, username, email, verification_reason, last_activity FROM users WHERE role = 'author' AND is_verified = FALSE")
            pending_authors = cursor.fetchall()
            cursor.execute("SELECT oa.action, oa.timestamp, u.username FROM official_activities oa JOIN users u ON oa.official_id = u.id WHERE oa.timestamp >= NOW() - INTERVAL 30 DAY ORDER BY oa.timestamp DESC LIMIT 200")
            official_logs = cursor.fetchall()
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file, preview_pages FROM books WHERE author_id = %s", (session['user_id'],))
            my_books = clean_book_data(cursor.fetchall())
            return render_template('dashboard.html', searched_users=searched_users, del_requests=del_requests, book_del_requests=book_del_requests, search_query=search_query, pending_authors=pending_authors, official_logs=official_logs, my_books=my_books, username_requests=username_requests, show_telegram_popup=show_telegram_popup, two_factor_enabled=two_factor_enabled)

        if role == 'official':
            if search_query: cursor.execute("SELECT id, username, role, last_activity FROM users WHERE role IN ('reader', 'author') AND (username LIKE %s OR email LIKE %s)", (f"%{search_query}%", f"%{search_query}%"))
            else: cursor.execute("SELECT id, username, role, last_activity FROM users WHERE role IN ('reader', 'author') ORDER BY last_activity DESC")
            all_users = cursor.fetchall()
            cursor.execute("SELECT id, username, email, verification_reason, last_activity FROM users WHERE role = 'author' AND is_verified = FALSE")
            pending_authors = cursor.fetchall()
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file, preview_pages FROM books WHERE author_id = %s", (session['user_id'],))
            my_books = clean_book_data(cursor.fetchall())
            return render_template('dashboard.html', pending_authors=pending_authors, all_users=all_users, search_query=search_query, my_books=my_books, username_requests=username_requests, show_telegram_popup=show_telegram_popup, two_factor_enabled=two_factor_enabled)

        if role == 'author':
            cursor.execute("SELECT is_verified, razorpay_account_id FROM users WHERE id = %s", (session['user_id'],))
            author_data = cursor.fetchone()
            session['is_verified'] = author_data['is_verified']; razorpay_account_id = author_data['razorpay_account_id']
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file, preview_pages FROM books WHERE author_id = %s", (session['user_id'],))
            my_books = clean_book_data(cursor.fetchall())
            return render_template('dashboard.html', my_books=my_books, razorpay_account_id=razorpay_account_id, show_telegram_popup=show_telegram_popup, two_factor_enabled=two_factor_enabled)

        return render_template('dashboard.html', show_telegram_popup=show_telegram_popup, two_factor_enabled=two_factor_enabled)
        
    except Exception as e:
        if show_telegram_popup: session['show_telegram_popup'] = True
        flash(f"System Notice: Database schema updating. {str(e)}", "error")
        return redirect(url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass

@app.route('/edit_book/<int:book_id>', methods=['POST'])
def edit_book(book_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    db = None
    try:
        role = session.get('role'); db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM books WHERE id = %s", (book_id,)); book = cursor.fetchone()
        if not book: flash("Book not found.", "error"); return redirect(url_for('dashboard'))
        if book['author_id'] != session['user_id'] and role not in ['official', 'developer']: flash("Unauthorized.", "error"); return redirect(url_for('dashboard'))
            
        title = request.form.get('title', book['title']); catalog = request.form.get('catalog', book['catalog']); is_paid = request.form.get('is_paid') == 'on'
        if catalog.lower() == 'archives': is_paid = False
        
        try: price_paise = int((Decimal(request.form.get('price_inr', '0').strip() or '0') * 100).quantize(Decimal('1')))
        except (InvalidOperation, ValueError): price_paise = book['price_paise'] if is_paid else 0
            
        raw_preview = int(request.form.get('preview_pages', book.get('preview_pages', 5)) or 5)
        preview_pages = min(max(1, raw_preview), 10)
        
        c_link = request.form.get('cover_link', '').strip(); p_link = request.form.get('pdf_link', '').strip()
        c_file = request.files.get('cover_image'); p_file = request.files.get('pdf_file')
        
        f_cov = book['cover_image']
        if c_link: f_cov = c_link
        elif c_file and c_file.filename: f_cov = secure_filename(c_file.filename); c_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'covers', f_cov))
            
        f_pdf = book['pdf_file']
        if p_link: f_pdf = p_link
        elif p_file and p_file.filename:
            f_pdf = secure_filename(p_file.filename); pdf_folder = app.config['PRIVATE_PDF_FOLDER'] if is_paid else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs'); p_file.save(os.path.join(pdf_folder, f_pdf))
            
        cursor.execute("UPDATE books SET title=%s, catalog=%s, cover_image=%s, pdf_file=%s, is_paid=%s, price_paise=%s, private_pdf=%s, preview_pages=%s WHERE id=%s", (title, catalog, f_cov, f_pdf, is_paid, price_paise if is_paid else 0, is_paid, preview_pages, book_id))
        db.commit(); flash("Book updated!", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/update_front_page', methods=['POST'])
def update_front_page():
    if session.get('role') != 'developer': return redirect(url_for('dashboard'))
    title = request.form.get('hero_title'); subtitle = request.form.get('hero_subtitle'); font_color = request.form.get('font_color')
    logo_file = request.files.get('logo_image'); donation_active = request.form.get('donation_active') == 'on'; donation_qr_file = request.files.get('donation_qr')
    
    rp_key_id = request.form.get('rp_key_id', '').strip()
    rp_key_secret = request.form.get('rp_key_secret', '').strip()
    
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT logo_image, donation_qr, rp_key_id, rp_key_secret FROM front_page_settings WHERE id=1")
        settings_data = cursor.fetchone()
        
        final_logo = settings_data['logo_image']; final_qr = settings_data['donation_qr']
        if logo_file and logo_file.filename: final_logo = secure_filename(logo_file.filename); logo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'logos', final_logo))
        if donation_qr_file and donation_qr_file.filename: final_qr = secure_filename(donation_qr_file.filename); donation_qr_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'logos', final_qr))
        
        final_rp_id = rp_key_id if rp_key_id else settings_data.get('rp_key_id', '')
        final_rp_secret = rp_key_secret if rp_key_secret else settings_data.get('rp_key_secret', '')
        
        cursor.execute("UPDATE front_page_settings SET hero_title=%s, hero_subtitle=%s, font_color=%s, logo_image=%s, donation_active=%s, donation_qr=%s, rp_key_id=%s, rp_key_secret=%s WHERE id=1", (title, subtitle, font_color, final_logo, donation_active, final_qr, final_rp_id, final_rp_secret))
        db.commit(); invalidate_cache(); flash("Platform settings updated!", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/add_catalog', methods=['POST'])
def add_catalog():
    if session.get('role') not in ['developer', 'official']: return redirect(url_for('dashboard'))
    new_catalog = request.form['catalog_name'].strip()
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor()
        cursor.execute("INSERT IGNORE INTO catalogs (name) VALUES (%s)", (new_catalog,)); db.commit(); invalidate_cache(); flash(f"Catalog '{new_catalog}' added!", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/delete_catalog/<int:cat_id>', methods=['POST'])
def delete_catalog(cat_id):
    if session.get('role') not in ['developer', 'official']: return redirect(url_for('dashboard'))
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(); cursor.execute("DELETE FROM catalogs WHERE id = %s", (cat_id,)); db.commit(); invalidate_cache(); flash("Catalog removed.", "success") 
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/create_official', methods=['POST'])
def create_official():
    if session.get('role') != 'developer': return redirect(url_for('dashboard'))
    db = None
    try:
        raw_password = request.form['password']
        db = get_db_connection(); cursor = db.cursor()
        cursor.execute("INSERT IGNORE INTO users (username, email, password_hash, role, is_verified, security_question, security_answer) VALUES (%s, %s, %s, 'official', TRUE, 'Dev', 'Dev')", (request.form['username'], request.form['email'], generate_password_hash(raw_password)))
        db.commit(); send_official_welcome(request.form['email'], request.form['username'], raw_password); flash("Official created and welcome email sent!", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/revoke_official/<int:user_id>', methods=['POST'])
def revoke_official(user_id):
    if session.get('role') != 'developer': return redirect(url_for('dashboard'))
    reason = request.form.get('reason', 'Administrative decision.')
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT username, email FROM users WHERE id = %s AND role = 'official'", (user_id,))
        user_data = cursor.fetchone()
        cursor.execute("UPDATE users SET role = 'reader' WHERE id = %s AND role = 'official'", (user_id,))
        db.commit()
        if user_data: send_revoked_official_email(user_data['email'], user_data['username'], reason)
        flash("Official privileges revoked and email sent.", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/request_deletion/<int:user_id>', methods=['POST'])
def request_deletion(user_id):
    if session.get('role') != 'official': return redirect(url_for('dashboard'))
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("INSERT INTO deletion_requests (target_user_id, requested_by, reason) VALUES (%s, %s, %s)", (user_id, session['user_id'], request.form['reason'])); db.commit()
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,)); target_name = cursor.fetchone()['username'] if cursor.rowcount > 0 else "Unknown"
        log_official_activity(session['user_id'], f"Requested deletion of user: {target_name}"); flash("Deletion request sent.", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/handle_deletion/<int:req_id>/<action>', methods=['POST'])
def handle_deletion(req_id, action):
    if session.get('role') != 'developer': return redirect(url_for('dashboard'))
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT target_user_id FROM deletion_requests WHERE id = %s", (req_id,))
        req = cursor.fetchone()
        if not req:
            flash("Request not found.", "error")
            return redirect(url_for('dashboard'))
            
        if action == 'approve':
            reason = request.form.get('reason', 'Violation of platform policies.')
            uid = req['target_user_id']
            
            cursor.execute("SELECT username, email FROM users WHERE id = %s", (uid,))
            user_data = cursor.fetchone()
            
            tables = ['personal_library', 'interactions', 'books', 'users']
            for table in tables: 
                column = 'author_id' if table == 'books' else ('id' if table == 'users' else 'user_id')
                cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (uid,))
                
            cursor.execute("UPDATE deletion_requests SET status = 'approved' WHERE id = %s", (req_id,))
            
            if user_data:
                send_account_deleted_email(user_data['email'], user_data['username'], reason)
            flash("User deleted and notified with your reason.", "success")
        else: 
            cursor.execute("UPDATE deletion_requests SET status = 'rejected' WHERE id = %s", (req_id,))
            flash("Deletion request rejected.", "info")
        db.commit()
    except Exception as e: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/admin_delete_user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if session.get('role') != 'developer': return redirect(url_for('dashboard'))
    reason = request.form.get('reason', 'Violation of platform terms.')
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT username, email FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        
        tables = ['personal_library', 'interactions', 'books', 'users']
        for table in tables: 
            column = 'author_id' if table == 'books' else ('id' if table == 'users' else 'user_id')
            cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (user_id,))
        db.commit()
        
        if user_data: send_account_deleted_email(user_data['email'], user_data['username'], reason)
        flash("User deleted and notification email sent.", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

# ==========================================
# BOOK DELETION (OFFICIALS REQUEST -> DEVELOPER FORCES)
# ==========================================
@app.route('/request_book_deletion/<int:book_id>', methods=['POST'])
def request_book_deletion(book_id):
    if session.get('role') != 'official': return redirect(url_for('dashboard'))
    db = None
    try:
        reason = request.form.get('reason', 'Violates guidelines.')
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("INSERT INTO book_deletion_requests (book_id, requested_by, reason) VALUES (%s, %s, %s)", (book_id, session['user_id'], reason))
        db.commit()
        cursor.execute("SELECT title FROM books WHERE id = %s", (book_id,))
        book_title = cursor.fetchone()['title']
        log_official_activity(session['user_id'], f"Requested deletion of book: '{book_title}'")
        flash("Book deletion request sent to the Developer.", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/handle_book_deletion/<int:req_id>/<action>', methods=['POST'])
def handle_book_deletion(req_id, action):
    if session.get('role') != 'developer': return redirect(url_for('dashboard'))
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT bdr.book_id, b.title, u.email, u.username FROM book_deletion_requests bdr JOIN books b ON bdr.book_id = b.id JOIN users u ON b.author_id = u.id WHERE bdr.id = %s", (req_id,))
        req = cursor.fetchone()
        
        if action == 'approve' and req:
            reason = request.form.get('reason', 'Policy violation.')
            send_book_deleted_email(req['email'], req['username'], req['title'], reason)
            cursor.execute("DELETE FROM personal_library WHERE book_id = %s", (req['book_id'],))
            cursor.execute("DELETE FROM books WHERE id = %s", (req['book_id'],))
            cursor.execute("UPDATE book_deletion_requests SET status = 'approved' WHERE id = %s", (req_id,))
            db.commit()
            flash("Book deleted and author notified.", "success")
        else:
            cursor.execute("UPDATE book_deletion_requests SET status = 'rejected' WHERE id = %s", (req_id,))
            db.commit()
            flash("Book deletion request rejected.", "info")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/delete_book/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    role = session.get('role')
    user_id = session.get('user_id')
    
    # Only Authors (for their own books) and Developers (for any book) can directly delete.
    if role in ['author', 'developer']:
        db = None
        try:
            db = get_db_connection(); cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT b.author_id, b.title, u.email, u.username FROM books b JOIN users u ON b.author_id = u.id WHERE b.id = %s", (book_id,))
            book = cursor.fetchone()
            if not book:
                flash("Book not found.", "error")
                return redirect(url_for('dashboard'))
                
            if role == 'developer' or (role == 'author' and book['author_id'] == user_id):
                if role == 'developer' and book['author_id'] != user_id:
                    reason = request.form.get('reason', 'Violation of platform guidelines.')
                    send_book_deleted_email(book['email'], book['username'], book['title'], reason)
                    
                cursor.execute("DELETE FROM personal_library WHERE book_id = %s", (book_id,))
                cursor.execute("DELETE FROM books WHERE id = %s", (book_id,))
                db.commit()
                flash("Book deleted successfully.", "success")
            else:
                flash("Unauthorized to delete this book.", "error")
        except Exception: flash("Database error.", "error")
        finally:
            if db:
                try: db.close()
                except: pass
    return redirect(url_for('dashboard'))

@app.route('/contact')
def contact(): return render_template('contact.html')

if __name__ == '__main__':
    ensure_payment_schema()
    create_master_developer()
    app.run(debug=True)
