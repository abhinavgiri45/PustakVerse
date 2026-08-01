import os
import secrets
import random
import smtplib
import logging
import re
import time
import razorpay
from email.mime.text import MIMEText
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import mysql.connector
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth

# ==========================================
# APPLICATION SETUP & LOGGING CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__, static_folder='static', template_folder='templates')

# SECURE: Pulls secret key from environment variables
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
# DATA CLEANER FOR MISSING DB VALUES
# ==========================================
def clean_book_data(books):
    """ Prevents NoneType concatenation errors in HTML templates """
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
                # STRICT DATA CLEANER: Forces all settings to strings to prevent crashes
                fetched_settings['logo_image'] = str(fetched_settings.get('logo_image') or "PustakVerse.png")
                fetched_settings['donation_qr'] = str(fetched_settings.get('donation_qr') or "")
                fetched_settings['hero_title'] = str(fetched_settings.get('hero_title') or "PustakVerse")
                fetched_settings['hero_subtitle'] = str(fetched_settings.get('hero_subtitle') or "")
                
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

# ==========================================
# GOOGLE DRIVE IMAGE FILTER
# ==========================================
@app.template_filter('drive_img')
def drive_img(url):
    if url and 'drive.google.com' in url:
        match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        if not match: match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
        if match: return f"https://drive.google.com/thumbnail?id={match.group(1)}&sz=w1000"
    return url

# ==========================================
# SMTP EMAIL CONFIGURATION & CLIENT
# ==========================================
SMTP_EMAIL = 'noreply.pustakverse@gmail.com'
# SECURE: Hidden from GitHub, must be set in your host's environment variables
SMTP_PASSWORD = os.environ.get('EMAIL_PASSWORD')  

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id='593863629217-7penq1jh89r0e6mbtundabk8cu3t6cdd.apps.googleusercontent.com',
    # SECURE: Hidden from GitHub
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

import requests

def send_email_wrapper(to_email, subject, body):
    api_key = os.environ.get('RESEND_API_KEY')
    
    if not api_key:
        print(f"\n\n=================================\n🚨 FALLBACK EMAIL TO {to_email}:\nSubject: {subject}\nBody:\n{body}\n=================================\n\n", flush=True)
        return True
        
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    # Note: On Resend's free tier, use 'onboarding@resend.dev' as the sender until you verify your own domain.
    payload = {
        "from": "onboarding@resend.dev",
        "to": [to_email],
        "subject": subject,
        "text": body
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        if response.status_code in [200, 201]:
            return True
        else:
            logging.error(f"Resend API Error: {response.text}")
            return True
    except Exception as e:
        logging.error(f"Email API connection failed: {e}")
        return True
def send_otp_email(to_email, otp):
    body = f"Your PustakVerse password reset code is: {otp}\n\nPlease do not share this code with anyone for your own security."
    return send_email_wrapper(to_email, 'PustakVerse - Password Reset OTP', body)

def send_2fa_email(to_email, otp):
    body = f"Your PustakVerse Login Verification code is: {otp}\n\nPlease enter this code to securely access your account. Do not share this."
    return send_email_wrapper(to_email, 'PustakVerse - 2-Step Login Verification', body)

def send_telegram_email(to_email, username):
    body = f"Hello {username},\n\nWelcome back to PustakVerse!\n\nJoin our Telegram channel: https://t.me/PustakVerse\n\nHappy Reading,\nThe PustakVerse Team"
    return send_email_wrapper(to_email, 'Join the PustakVerse Telegram Community!', body)

def send_welcome_reader(to_email, username):
    body = f"Hello {username},\n\nWelcome to PustakVerse! Your Reader account is officially active. Dive into our extensive Global Library today!\n\nHappy Reading,\nThe PustakVerse Team"
    return send_email_wrapper(to_email, 'Welcome to PustakVerse!', body)

def send_pending_author(to_email, username):
    body = f"Hello {username},\n\nThank you for registering as an Author on PustakVerse! Your account is currently under review by our administrative team. We will notify you when approved.\n\nBest Regards,\nThe PustakVerse Team"
    return send_email_wrapper(to_email, 'PustakVerse - Author Account Under Review', body)

def send_approved_author(to_email, username):
    body = f"Hello {username},\n\nCongratulations! Your Author account on PustakVerse has been officially approved. You can now publish your books.\n\nWelcome aboard,\nThe PustakVerse Team"
    return send_email_wrapper(to_email, 'Your PustakVerse Author Account is Approved!', body)

def send_official_welcome(to_email, username, password):
    body = f"Hello {username},\n\nWelcome to the administrative team! You have been officially appointed as a dedicated PustakVerse Official.\n\nHere are your secure login credentials:\nUsername: {username}\nPassword: {password}\n\nPlease log in to access your administrative dashboard to review authors and manage the community.\n\nBest Regards,\nThe PustakVerse Team"
    return send_email_wrapper(to_email, 'Welcome to the PustakVerse Official Team', body)

# ==========================================
# TiDB (MYSQL) DATABASE CONNECTION ENGINE
# ==========================================
def get_db_connection(retries=2, delay=1.0):
    last_exception = None
    for attempt in range(retries):
        try:
            conn = mysql.connector.connect(
                host="gateway01.ap-southeast-1.prod.aws.tidbcloud.com",
                port=4000,
                user="39proe1L4PTbJ3X.root",
                password="cOXI6Co9lYTGuTsM",
                database="test",
                ssl_verify_cert=False,       
                ssl_verify_identity=False,   
                connection_timeout=8
            )
            if conn.is_connected():
                return conn
        except mysql.connector.Error as err:
            last_exception = err
            logging.warning(f"DB Connection attempt {attempt+1} failed: {err}")
            time.sleep(delay)
    raise last_exception

def payment_gateway_configured():
    return bool(os.environ.get('RAZORPAY_KEY_ID') and os.environ.get('RAZORPAY_KEY_SECRET'))

def get_payment_fee_paise(price_paise):
    try:
        rate = Decimal(os.environ.get('PAYMENT_FEE_PERCENT', '2.36')) / Decimal('100')
    except InvalidOperation:
        rate = Decimal('0.0236')
    if rate <= 0 or rate >= 1: return 0
    return int((Decimal(price_paise) * rate / (Decimal('1') - rate)).to_integral_value(rounding=ROUND_CEILING))

def ensure_payment_schema():
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(100) NOT NULL UNIQUE, email VARCHAR(150) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL, role ENUM('reader', 'author', 'official', 'developer') DEFAULT 'reader',
                is_verified BOOLEAN DEFAULT FALSE, security_question VARCHAR(255) NOT NULL, security_answer VARCHAR(255) NOT NULL,
                verification_reason TEXT, payout_details VARCHAR(255) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        try:
            cursor.execute("SHOW COLUMNS FROM users LIKE 'two_factor_enabled'")
            if not cursor.fetchone(): cursor.execute("ALTER TABLE users ADD COLUMN two_factor_enabled BOOLEAN DEFAULT FALSE")
        except Exception: pass
        
        # --- RAZORPAY ROUTE ACCOUNT TRACKING ---
        try:
            cursor.execute("SHOW COLUMNS FROM users LIKE 'razorpay_account_id'")
            if not cursor.fetchone(): cursor.execute("ALTER TABLE users ADD COLUMN razorpay_account_id VARCHAR(100) DEFAULT NULL")
        except Exception: pass
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS username_requests (
                id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, new_username VARCHAR(100) NOT NULL,
                reason TEXT NOT NULL, status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INT AUTO_INCREMENT PRIMARY KEY, title VARCHAR(255) NOT NULL, author_id INT NOT NULL,
                catalog VARCHAR(100) NOT NULL, cover_image VARCHAR(1000) NOT NULL, pdf_file VARCHAR(1000) NOT NULL,
                is_paid BOOLEAN NOT NULL DEFAULT FALSE, price_paise INT NOT NULL DEFAULT 0, private_pdf BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS personal_library (
                id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, book_id INT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
                UNIQUE(user_id, book_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, book_id INT NOT NULL,
                rating INT CHECK (rating >= 1 AND rating <= 5), review TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deletion_requests (
                id INT AUTO_INCREMENT PRIMARY KEY, target_user_id INT NOT NULL, requested_by INT NOT NULL, reason TEXT,
                status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS front_page_settings (
                id INT AUTO_INCREMENT PRIMARY KEY, hero_title VARCHAR(255) DEFAULT 'PustakVerse',
                hero_subtitle VARCHAR(255) DEFAULT 'Every Book. Every Mind. Free. Read More. Grow More. Inspire India.',
                logo_image VARCHAR(255) DEFAULT 'PustakVerse.png', font_color VARCHAR(50) DEFAULT '#ffffff',
                donation_qr VARCHAR(255) DEFAULT NULL, donation_active BOOLEAN DEFAULT FALSE
            )
        """)
        try:
            cursor.execute("SHOW COLUMNS FROM front_page_settings LIKE 'donation_qr'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE front_page_settings ADD COLUMN donation_qr VARCHAR(255) DEFAULT NULL")
                cursor.execute("ALTER TABLE front_page_settings ADD COLUMN donation_active BOOLEAN DEFAULT FALSE")
        except Exception: pass

        cursor.execute("INSERT IGNORE INTO front_page_settings (id) VALUES (1)")
        cursor.execute("CREATE TABLE IF NOT EXISTS catalogs (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE)")
        cursor.execute("INSERT IGNORE INTO catalogs (name) VALUES ('Fiction'), ('Non-Fiction'), ('Educational'), ('History'), ('Poetry')")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, book_id INT NOT NULL, razorpay_order_id VARCHAR(100) NOT NULL UNIQUE,
                razorpay_payment_id VARCHAR(100) NULL UNIQUE, amount_paise INT NOT NULL, fee_paise INT NOT NULL DEFAULT 0,
                status ENUM('pending', 'paid', 'failed', 'refunded') NOT NULL DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP NULL, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS official_activities (
                id INT AUTO_INCREMENT PRIMARY KEY, official_id INT NOT NULL, action VARCHAR(255) NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (official_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        db.commit()
        return True
    except Exception as error:
        if db: db.rollback()
        logging.error(f"Schema Initialization Error: {error}")
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
    except Exception as e:
        logging.warning(f"Failed to log official activity: {e}")
    finally:
        if db:
            try: db.close()
            except: pass

# ==========================================
# IN-MEMORY CACHE
# ==========================================
global_cache = {
    'settings': {'logo_image': 'PustakVerse.png', 'hero_title': 'PustakVerse', 'hero_subtitle': 'Every Book. Every Mind. Free.', 'donation_active': False, 'donation_qr': None},
    'catalogs': [{'name': 'Fiction'}, {'name': 'Non-Fiction'}, {'name': 'Educational'}, {'name': 'History'}, {'name': 'Poetry'}],
    'last_update': 0
}

def invalidate_cache():
    global_cache['last_update'] = 0

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
            cursor.execute(
                "INSERT IGNORE INTO users (username, email, password_hash, role, is_verified, security_question, security_answer, verification_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                ('abhinavgiri45', 'abhinavgiri370@gmail.com', hashed_pw, 'developer', True, 'What is your favorite book?', 'gita', 'Master Admin')
            )
            db.commit(); logging.info("Master Developer initialized.")
    except Exception as e:
        pass
    finally:
        if db:
            try: db.close()
            except: pass

# ==========================================
# PUBLIC ROUTES
# ==========================================
@app.route('/check_username', methods=['POST'])
def check_username():
    username = request.form.get('username', '').strip()
    if not username: return jsonify({'available': False, 'message': ''})
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if user: return jsonify({'available': False, 'message': 'Username is already taken'})
        return jsonify({'available': True, 'message': 'Username is available!'})
    except Exception as e: 
        return jsonify({'available': False, 'message': 'Checking...'})
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

                # Check if 2FA is required
                if user['role'] in ['official', 'developer'] or user.get('two_factor_enabled'):
                    otp = str(random.randint(100000, 999999)); session['login_2fa_otp'] = otp
                    session['pending_2fa_user'] = {'id': user['id'], 'username': user['username'], 'role': user['role'], 'is_verified': user['is_verified'], 'email': user['email']}
                    if send_2fa_email(user['email'], otp): flash("A 2-Step Verification code has been sent to your email.", "info"); return render_template('login.html', show_2fa_form=True, email=user['email'])
                    else: flash("Failed to send 2FA email. Contact admin.", "error"); return render_template('login.html', active_tab=login_portal)

                session['user_id'] = user['id']; session['username'] = user['username']; session['role'] = user['role']; session['is_verified'] = user['is_verified']
                session['show_telegram_popup'] = True; return redirect(url_for('dashboard'))
            
            flash("Invalid username or password.", "error"); return render_template('login.html', active_tab=login_portal)
            
        elif action == 'verify_2fa':
            user_otp = request.form.get('otp'); pending_user = session.get('pending_2fa_user')
            if pending_user and user_otp == session.get('login_2fa_otp'):
                session['user_id'] = pending_user['id']; session['username'] = pending_user['username']; session['role'] = pending_user['role']; session['is_verified'] = pending_user['is_verified']
                session.pop('login_2fa_otp', None); session.pop('pending_2fa_user', None)
                session['show_telegram_popup'] = True; flash(f"Welcome back, {pending_user['username']}!", "success"); return redirect(url_for('dashboard'))
            else: flash("Invalid Verification Code. Please try again.", "error"); return render_template('login.html', show_2fa_form=True, email=pending_user.get('email', ''))
    return render_template('login.html', active_tab='reader')

@app.route('/login/google')
def google_login(): return google.authorize_redirect(url_for('google_authorize', _external=True))

@app.route('/login/google/callback')
def google_authorize():
    user_info = google.authorize_access_token().get('userinfo')
    if not user_info: flash("Google login failed.", "error"); return redirect(url_for('login'))
    email = user_info.get('email'); name = user_info.get('name')
    base_username = name.replace(" ", "").lower() if name else email.split('@')[0]
    
    db = None; user = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,)); user = cursor.fetchone()
        if not user:
            cursor.execute("SELECT * FROM users WHERE username = %s", (base_username,))
            if cursor.fetchone(): base_username = f"{base_username}{secrets.randbelow(9999)}"
            cursor.execute("INSERT INTO users (username, email, password_hash, role, is_verified, security_question, security_answer) VALUES (%s, %s, %s, 'reader', TRUE, 'Google', 'Google')", (base_username, email, generate_password_hash(secrets.token_urlsafe(16))))
            db.commit(); send_welcome_reader(email, base_username)
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,)); user = cursor.fetchone()
    except Exception as e:
        flash("Secure connection to the database timed out. Please try logging in again.", "error")
        return redirect(url_for('login'))
    finally:
        if db:
            try: db.close()
            except: pass
    
    if user['role'] in ['official', 'developer'] or user.get('two_factor_enabled'):
        otp = str(random.randint(100000, 999999)); session['login_2fa_otp'] = otp
        session['pending_2fa_user'] = {'id': user['id'], 'username': user['username'], 'role': user['role'], 'is_verified': user['is_verified'], 'email': user['email']}
        if send_2fa_email(user['email'], otp): flash("A 2-Step Verification code has been sent to your email.", "info"); return render_template('login.html', show_2fa_form=True, email=user['email'])
        else: flash("Failed to send 2FA email. Please contact server administration.", "error"); return redirect(url_for('login'))
            
    session['user_id'] = user['id']; session['username'] = user['username']; session['role'] = user['role']; session['is_verified'] = user['is_verified']
    session['show_telegram_popup'] = True; return redirect(url_for('dashboard'))

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
# E-COMMERCE: AUTOMATED ROUTE PAYOUTS
# ==========================================
@app.route('/buy_book/<int:book_id>', methods=['POST'])
def buy_book(book_id):
    if 'user_id' not in session: flash('Please sign in before purchasing a book.', 'error'); return redirect(url_for('login'))
    
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

    if not payment_gateway_configured(): flash('Online payments are not configured yet.', 'error'); return redirect(request.referrer or url_for('index'))
    
    author_acc_id = book['razorpay_account_id']
    if not author_acc_id:
        flash('The author has not completed their KYC onboarding. Purchases are temporarily disabled for this book.', 'error')
        return redirect(request.referrer or url_for('index'))

    fee_paise = get_payment_fee_paise(book['price_paise'])
    total_paise = book['price_paise'] + fee_paise
    
    db = None
    try:
        client = razorpay.Client(auth=(os.environ['RAZORPAY_KEY_ID'], os.environ['RAZORPAY_KEY_SECRET']))
        
        # --- RAZORPAY ROUTE SPLIT LOGIC ---
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
            
    return render_template('checkout.html', book=book, order_id=order['id'], total_paise=total_paise, fee_paise=fee_paise, base_price=book['price_paise'], razorpay_key=os.environ['RAZORPAY_KEY_ID'])

@app.route('/payment/verify', methods=['POST'])
def verify_payment():
    if 'user_id' not in session: abort(401)
    order_id = request.form.get('razorpay_order_id', ''); payment_id = request.form.get('razorpay_payment_id', ''); signature = request.form.get('razorpay_signature', '')
    if not payment_gateway_configured() or not all([order_id, payment_id, signature]): abort(400)
    db = None
    try:
        client = razorpay.Client(auth=(os.environ['RAZORPAY_KEY_ID'], os.environ['RAZORPAY_KEY_SECRET']))
        client.utility.verify_payment_signature({'razorpay_order_id': order_id, 'razorpay_payment_id': payment_id, 'razorpay_signature': signature})
        
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id, book_id FROM purchases WHERE razorpay_order_id = %s AND user_id = %s", (order_id, session['user_id']))
        purchase = cursor.fetchone()
        
        if purchase:
            cursor.execute("UPDATE purchases SET razorpay_payment_id = %s, status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE id = %s", (payment_id, purchase['id']))
            cursor.execute('INSERT IGNORE INTO personal_library (user_id, book_id) VALUES (%s, %s)', (session['user_id'], purchase['book_id']))
            db.commit()
        flash('Payment successful — your book is now unlocked.', 'success'); return redirect(url_for('read_book', book_id=purchase['book_id']))
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
    db = None; can_read = False
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT id, title, author_id, pdf_file, is_paid, private_pdf FROM books WHERE id = %s', (book_id,))
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

    if not can_read: flash('Please purchase or sign in to access this book.', 'error'); return redirect(url_for('index'))
    return render_template('viewer.html', book=book)

@app.route('/serve_secure_pdf/<int:book_id>')
def serve_secure_pdf(book_id):
    if 'user_id' not in session: abort(401)
    db = None; book = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT author_id, pdf_file, is_paid, private_pdf FROM books WHERE id = %s', (book_id,))
        book = cursor.fetchone()
        if not book: abort(404)
        can_read = not book['is_paid'] or session.get('user_id') == book['author_id'] or session.get('role') in ('official', 'developer')
        if book['is_paid'] and not can_read:
            cursor.execute("SELECT id FROM purchases WHERE user_id = %s AND book_id = %s AND status = 'paid'", (session['user_id'], book_id))
            if not cursor.fetchone(): abort(403)
    except Exception: abort(500)
    finally:
        if db:
            try: db.close()
            except: pass
    if book['pdf_file'].startswith('http'): abort(400)
    folder = app.config['PRIVATE_PDF_FOLDER'] if book['is_paid'] or book['private_pdf'] else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs')
    return send_from_directory(folder, book['pdf_file'])

@app.route('/save_book/<int:book_id>', methods=['POST'])
def save_book(book_id):
    if 'user_id' not in session: flash("Please sign in first.", "error"); return redirect(url_for('login'))
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor()
        cursor.execute("INSERT IGNORE INTO personal_library (user_id, book_id) VALUES (%s, %s)", (session['user_id'], book_id))
        db.commit(); flash("Book added to My Library!", "success")
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
        role = session.get('role'); search_query = request.args.get('search', '')

        # Fetch 2FA Status
        cursor.execute("SELECT two_factor_enabled FROM users WHERE id = %s", (session['user_id'],))
        tf_data = cursor.fetchone()
        two_factor_enabled = tf_data['two_factor_enabled'] if tf_data else False

        # Toggle 2FA Setting
        if request.method == 'POST' and 'toggle_2fa' in request.form:
            current_status = request.form.get('current_status') == 'True'
            new_status = not current_status
            cursor.execute("UPDATE users SET two_factor_enabled = %s WHERE id = %s", (new_status, session['user_id']))
            db.commit()
            status_text = "enabled" if new_status else "disabled"
            flash(f"Two-Step Verification has been {status_text}.", "success")
            return redirect(url_for('dashboard'))

        # --- AUTHOR ROUTE ACCOUNT CREATION API ---
        if role == 'author' and request.method == 'POST' and 'create_route_account' in request.form:
            legal_name = request.form.get('legal_name').strip()
            phone = request.form.get('phone').strip()
            try:
                client = razorpay.Client(auth=(os.environ['RAZORPAY_KEY_ID'], os.environ['RAZORPAY_KEY_SECRET']))
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
                flash(f"Razorpay Integration Error: Make sure your API keys are correct. ({str(e)})", "error")
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

            if is_paid and price_paise <= 0: flash('Paid books need a valid price.', 'error'); return redirect(url_for('dashboard'))
            
            f_cov = c_link if c_link else (secure_filename(c_file.filename) if c_file and c_file.filename else "")
            f_pdf = p_link if p_link else (secure_filename(p_file.filename) if p_file and p_file.filename else "")
            
            if c_file and not c_link: c_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'covers', f_cov))
            if p_file and not p_link:
                pdf_folder = app.config['PRIVATE_PDF_FOLDER'] if is_paid else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs')
                p_file.save(os.path.join(pdf_folder, f_pdf))
                
            if f_cov and f_pdf:
                cursor.execute("INSERT INTO books (title, author_id, catalog, cover_image, pdf_file, is_paid, price_paise, private_pdf) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", (request.form['title'], session['user_id'], request.form['catalog'], f_cov, f_pdf, is_paid, price_paise if is_paid else 0, is_paid))
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
                cursor.execute("SELECT username FROM users WHERE id = %s", (auth_id,)); author_name = cursor.fetchone()['username'] if cursor.rowcount > 0 else "Unknown"
                cursor.execute("DELETE FROM users WHERE id = %s", (auth_id,)); db.commit()
                if role == 'official': log_official_activity(session['user_id'], f"Rejected & deleted author: {author_name}")
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
            if search_query: cursor.execute("SELECT id, username, email, role, last_activity FROM users WHERE username LIKE %s OR email LIKE %s", (f"%{search_query}%", f"%{search_query}%"))
            else: cursor.execute("SELECT id, username, email, role, last_activity FROM users WHERE role != 'developer' ORDER BY last_activity DESC LIMIT 50")
            searched_users = cursor.fetchall()
            cursor.execute("SELECT dr.id, u.username as target_name, o.username as official_name, dr.reason FROM deletion_requests dr JOIN users u ON dr.target_user_id = u.id JOIN users o ON dr.requested_by = o.id WHERE dr.status = 'pending'")
            del_requests = cursor.fetchall()
            cursor.execute("SELECT id, username, email, verification_reason, last_activity FROM users WHERE role = 'author' AND is_verified = FALSE")
            pending_authors = cursor.fetchall()
            cursor.execute("SELECT oa.action, oa.timestamp, u.username FROM official_activities oa JOIN users u ON oa.official_id = u.id ORDER BY oa.timestamp DESC LIMIT 100")
            official_logs = cursor.fetchall()
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file FROM books WHERE author_id = %s", (session['user_id'],))
            my_books = clean_book_data(cursor.fetchall())
            return render_template('dashboard.html', searched_users=searched_users, del_requests=del_requests, search_query=search_query, pending_authors=pending_authors, official_logs=official_logs, my_books=my_books, username_requests=username_requests, show_telegram_popup=show_telegram_popup, two_factor_enabled=two_factor_enabled)

        if role == 'official':
            if search_query: cursor.execute("SELECT id, username, role, last_activity FROM users WHERE role IN ('reader', 'author') AND (username LIKE %s OR email LIKE %s)", (f"%{search_query}%", f"%{search_query}%"))
            else: cursor.execute("SELECT id, username, role, last_activity FROM users WHERE role IN ('reader', 'author') ORDER BY last_activity DESC")
            all_users = cursor.fetchall()
            cursor.execute("SELECT id, username, email, verification_reason, last_activity FROM users WHERE role = 'author' AND is_verified = FALSE")
            pending_authors = cursor.fetchall()
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file FROM books WHERE author_id = %s", (session['user_id'],))
            my_books = clean_book_data(cursor.fetchall())
            return render_template('dashboard.html', pending_authors=pending_authors, all_users=all_users, search_query=search_query, my_books=my_books, username_requests=username_requests, show_telegram_popup=show_telegram_popup, two_factor_enabled=two_factor_enabled)

        if role == 'author':
            cursor.execute("SELECT is_verified, razorpay_account_id FROM users WHERE id = %s", (session['user_id'],))
            author_data = cursor.fetchone()
            session['is_verified'] = author_data['is_verified']; razorpay_account_id = author_data['razorpay_account_id']
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file FROM books WHERE author_id = %s", (session['user_id'],))
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
            
        c_link = request.form.get('cover_link', '').strip(); p_link = request.form.get('pdf_link', '').strip()
        c_file = request.files.get('cover_image'); p_file = request.files.get('pdf_file')
        
        f_cov = book['cover_image']
        if c_link: f_cov = c_link
        elif c_file and c_file.filename: f_cov = secure_filename(c_file.filename); c_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'covers', f_cov))
            
        f_pdf = book['pdf_file']
        if p_link: f_pdf = p_link
        elif p_file and p_file.filename:
            f_pdf = secure_filename(p_file.filename); pdf_folder = app.config['PRIVATE_PDF_FOLDER'] if is_paid else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs'); p_file.save(os.path.join(pdf_folder, f_pdf))
            
        cursor.execute("UPDATE books SET title=%s, catalog=%s, cover_image=%s, pdf_file=%s, is_paid=%s, price_paise=%s, private_pdf=%s WHERE id=%s", (title, catalog, f_cov, f_pdf, is_paid, price_paise if is_paid else 0, is_paid, book_id))
        db.commit(); flash("Book updated!", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/update_front_page', methods=['POST'])
def update_front_page():
    if session.get('role') not in ['developer', 'official']: return redirect(url_for('dashboard'))
    title = request.form.get('hero_title'); subtitle = request.form.get('hero_subtitle'); font_color = request.form.get('font_color')
    logo_file = request.files.get('logo_image'); donation_active = request.form.get('donation_active') == 'on'; donation_qr_file = request.files.get('donation_qr')
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT logo_image, donation_qr FROM front_page_settings WHERE id=1")
        settings_data = cursor.fetchone()
        final_logo = settings_data['logo_image']; final_qr = settings_data['donation_qr']
        if logo_file and logo_file.filename: final_logo = secure_filename(logo_file.filename); logo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'logos', final_logo))
        if donation_qr_file and donation_qr_file.filename: final_qr = secure_filename(donation_qr_file.filename); donation_qr_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'logos', final_qr))
        cursor.execute("UPDATE front_page_settings SET hero_title=%s, hero_subtitle=%s, font_color=%s, logo_image=%s, donation_active=%s, donation_qr=%s WHERE id=1", (title, subtitle, font_color, final_logo, donation_active, final_qr))
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
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor()
        cursor.execute("UPDATE users SET role = 'reader' WHERE id = %s AND role = 'official'", (user_id,)); db.commit(); flash("Official privileges revoked.", "success")
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
        cursor.execute("SELECT target_user_id FROM deletion_requests WHERE id = %s", (req_id,)); req = cursor.fetchone()
        if action == 'approve':
            uid = req['target_user_id']; tables = ['personal_library', 'interactions', 'books', 'users']
            for table in tables: column = 'author_id' if table == 'books' else ('id' if table == 'users' else 'user_id'); cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (uid,))
            cursor.execute("UPDATE deletion_requests SET status = 'approved' WHERE id = %s", (req_id,))
        else: cursor.execute("UPDATE deletion_requests SET status = 'rejected' WHERE id = %s", (req_id,))
        db.commit()
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/admin_delete_user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if session.get('role') != 'developer': return redirect(url_for('dashboard'))
    db = None
    try:
        db = get_db_connection(); cursor = db.cursor(); tables = ['personal_library', 'interactions', 'books', 'users']
        for table in tables: column = 'author_id' if table == 'books' else ('id' if table == 'users' else 'user_id'); cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (user_id,))
        db.commit(); flash("User deleted.", "success")
    except Exception: flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/delete_book/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    if session.get('role') in ['author', 'official', 'developer']:
        db = None
        try:
            db = get_db_connection(); cursor = db.cursor()
            cursor.execute("DELETE FROM personal_library WHERE book_id = %s", (book_id,))
            cursor.execute("DELETE FROM books WHERE id = %s AND author_id = %s", (book_id, session['user_id']))
            db.commit()
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
