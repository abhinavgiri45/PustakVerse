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
from decimal import Decimal, InvalidOperation
import mysql.connector
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth
from PyPDF2 import PdfReader, PdfWriter
import requests
from datetime import datetime, timezone, timedelta

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    pytesseract = None
    HAS_TESSERACT = False

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
    logging.warning("Pillow is not installed. Image compression is disabled.")

# ==========================================
# APPLICATION SETUP & LOGGING CONFIGURATION
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__, static_folder='static', template_folder='templates')

app.secret_key = os.environ.get('FLASK_SECRET_KEY')

UPLOAD_FOLDER = 'static/uploads'
PRIVATE_PDF_FOLDER = 'private_uploads/pdfs'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PRIVATE_PDF_FOLDER'] = PRIVATE_PDF_FOLDER
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000 
payment_schema_ready = False

os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'covers'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'logos'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'ai_screenshots'), exist_ok=True)
os.makedirs(os.path.join(app.config['PRIVATE_PDF_FOLDER']), exist_ok=True)

# --- SEO & PERFORMANCE CACHING HEADER ---
@app.after_request
def add_header(response):
    if 'Cache-Control' not in response.headers:
        response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

# ==========================================
# TIMEZONE FORMATTING (UTC TO IST)
# ==========================================
@app.template_filter('to_ist')
def to_ist_filter(dt):
    if not dt:
        return "Never"
    
    if isinstance(dt, str):
        try:
            dt = datetime.strptime(str(dt).split('.')[0], '%Y-%m-%d %H:%M:%S')
        except Exception:
            return dt
            
    if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    ist = timezone(timedelta(hours=5, minutes=30))
    return dt.astimezone(ist).strftime('%Y-%m-%d %I:%M %p')

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
    if not books: 
        return []
    for b in books:
        b['cover_image'] = str(b.get('cover_image') or "")
        b['pdf_file'] = str(b.get('pdf_file') or "")
        b['author_name'] = str(b.get('author_name') or "Unknown")
        b['description'] = str(b.get('description') or "")
    return books

STOP_WORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'has', 'have', 'in', 'is', 'it',
    'its', 'of', 'on', 'or', 'that', 'the', 'their', 'this', 'to', 'was', 'were', 'with', 'your', 'about',
    'into', 'them', 'then', 'than', 'through', 'using', 'you', 'yourself', 'will', 'how', 'what', 'when',
    'why', 'who', 'which', 'while', 'also', 'after', 'before', 'between', 'within', 'without'
}


def suggest_concept(book_title, description='', book_text=''):
    source = ' '.join(filter(None, [book_title, description, book_text])).lower()
    keyword_map = {
        'loop': ['loop', 'loops', 'iteration', 'repetition'],
        'function': ['function', 'method', 'routine', 'procedure'],
        'variable': ['variable', 'constant', 'data', 'parameter'],
        'algorithm': ['algorithm', 'logic', 'process', 'steps'],
        'theme': ['theme', 'message', 'moral', 'idea'],
        'character': ['character', 'hero', 'villain', 'narrator'],
        'grammar': ['grammar', 'sentence', 'verb', 'noun', 'pronoun'],
        'market': ['market', 'sales', 'customer', 'demand', 'pricing'],
        'strategy': ['strategy', 'plan', 'approach', 'model'],
        'history': ['history', 'war', 'empire', 'revolution', 'civilization'],
        'economics': ['economics', 'money', 'trade', 'income', 'growth'],
        'science': ['science', 'experiment', 'theory', 'discovery'],
        'motivation': ['motivation', 'focus', 'discipline', 'growth', 'success'],
        'story': ['story', 'plot', 'chapter', 'narrative']
    }

    for concept, terms in keyword_map.items():
        if any(term in source for term in terms):
            return concept

    words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", source)
    freq = {}
    for word in words:
        clean_word = word.lower()
        if clean_word in STOP_WORDS or len(clean_word) <= 3:
            continue
        freq[clean_word] = freq.get(clean_word, 0) + 1

    if freq:
        return sorted(freq.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return 'core concept'


def build_ai_learning_response(book_title, book_description='', concept_query='', book_text=''):
    concept = (concept_query or suggest_concept(book_title, book_description, book_text) or 'core concept').strip()
    concept_label = concept.strip().capitalize()
    title_text = book_title or 'this book'
    context = (book_description or book_text or '').strip()
    context_snippet = context[:600]

    if 'loop' in concept.lower() or 'iteration' in concept.lower():
        explanation = (
            f"{concept_label} is the idea of repeating a task until a condition is met. In {title_text}, this usually helps you avoid writing the same instruction over and over while still processing many items. "
            "Think of it like a repeated instruction: do the task, check the condition, and repeat only when needed."
        )
        key_points = [
            'A loop repeats actions using a clear condition.',
            'It saves time and reduces repeated code.',
            'It is useful when you need to process multiple values or steps.'
        ]
        example = 'If you are reading a list of books, a loop can visit each one, check a rule, and move to the next one automatically.'
        questions = [
            'What is the main job of a loop in simple words?',
            'How is a loop different from writing the same instructions manually?',
            'Can you describe one real-world example where looping is useful?'
        ]
    elif 'function' in concept.lower() or 'method' in concept.lower() or 'routine' in concept.lower():
        explanation = (
            f"{concept_label} means creating a reusable block of logic that performs one job. In {title_text}, this helps keep ideas organized so the same process can be used again without rewriting it. "
            "A function is like a mini-tool: you define it once and call it whenever needed."
        )
        key_points = [
            'Functions group related instructions together.',
            'They make code or explanations easier to reuse and maintain.',
            'A good function usually does one focused job.'
        ]
        example = 'A study helper function might summarize a chapter, extract key ideas, and give quiz questions using the same steps each time.'
        questions = [
            'Why is a function useful when solving a problem more than once?',
            'What happens when a function has a clear goal?',
            'Can you think of a function you already use in daily learning or work?'
        ]
    elif 'variable' in concept.lower() or 'data' in concept.lower():
        explanation = (
            f"{concept_label} is a labeled container for information. In {title_text}, it lets the reader keep track of values, inputs, or facts that can change or be reused. "
            "Instead of memorizing everything as plain text, you store it under a meaningful name and use it when needed."
        )
        key_points = [
            'A variable stores meaningful information.',
            'It can be updated or reused in different situations.',
            'Naming matters because clear names make learning easier.'
        ]
        example = 'A student can store a chapter title in a variable called chapter_name and reuse it in summaries, notes, or flashcards.'
        questions = [
            'What does a variable help you remember?',
            'Why do meaningful names make understanding easier?',
            'How is a variable different from static text in a book?'
        ]
    elif 'algorithm' in concept.lower() or 'logic' in concept.lower() or 'process' in concept.lower():
        explanation = (
            f"{concept_label} is simply a step-by-step way to solve a problem. In {title_text}, it gives structure: understand the goal, take the next step, and repeat or adjust until you reach the result. "
            "This is how complex ideas become easier to think through."
        )
        key_points = [
            'Algorithms break big tasks into manageable steps.',
            'They help you understand order and sequence.',
            'Good logic makes difficult ideas easier to follow.'
        ]
        example = 'To understand a chapter, you can first read the summary, then note the main points, then compare them with examples and finally explain them in your own words.'
        questions = [
            'What makes an algorithm easier to follow?',
            'Why does step-by-step thinking help learning?',
            'Can you outline a simple process for understanding this chapter?'
        ]
    elif 'theme' in concept.lower() or 'story' in concept.lower() or 'character' in concept.lower():
        explanation = (
            f"{concept_label} in {title_text} helps explain the deeper meaning behind the events or ideas. A theme is the central message, while a character or plot helps show that message in action. "
            "When you understand the theme, you can connect the book's details to the bigger idea."
        )
        key_points = [
            'Themes show the main message or lesson.',
            'Stories use characters and events to express that message.',
            'Looking for patterns makes understanding clearer.'
        ]
        example = 'If a story focuses on courage, the plot may show a character making hard decisions to reveal that bigger idea.'
        questions = [
            'What message does the book seem to carry?',
            'Which character or scene best shows that message?',
            'How does the story support the book’s bigger idea?'
        ]
    else:
        explanation = (
            f"{concept_label} is a key idea in {title_text}. In simple language, it is the main building block that helps you understand the subject clearly. "
            "Instead of trying to memorize everything at once, focus on what it means, why it matters, and how it connects to the examples in the book."
        )
        key_points = [
            'Focus on the meaning before the details.',
            'Connect the idea to examples from the book or real life.',
            'Try to explain it in your own words to test understanding.'
        ]
        example = f'If you are learning {concept_label}, write a short explanation using one real example from {title_text} and one everyday example.'
        questions = [
            f'How would you explain {concept_label} in one sentence?',
            'Why does this idea matter in the context of the book?',
            'Can you give one example that makes this idea easier to understand?'
        ]

    if context_snippet:
        supported_hint = (
            f"From the book context: {context_snippet[:240]}"
            + ('...' if len(context_snippet) > 240 else '')
        )
    else:
        supported_hint = 'No detailed description was found, so this explanation is based on the topic and general learning principles.'

    return {
        'concept': concept_label,
        'explanation': explanation,
        'key_points': key_points,
        'example': example,
        'practice_questions': questions,
        'book_context': supported_hint,
        'study_tip': 'Read the idea once, explain it in your own words, then test yourself with the practice questions.'
    }


def save_uploaded_screenshot(file_obj):
    if not file_obj or not getattr(file_obj, 'filename', None):
        return ''

    allowed_exts = {'.png', '.jpg', '.jpeg', '.webp'}
    filename = secure_filename(file_obj.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_exts:
        return ''

    folder = os.path.join(app.config['UPLOAD_FOLDER'], 'ai_screenshots')
    os.makedirs(folder, exist_ok=True)
    safe_name = f"{secrets.token_hex(8)}{ext}"
    save_path = os.path.join(folder, safe_name)
    file_obj.save(save_path)
    return safe_name


def extract_text_from_uploaded_image(image_path):
    if not image_path or not os.path.exists(image_path):
        return ''

    if not HAS_TESSERACT or not pytesseract:
        return ''

    try:
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(image_path))
        cleaned = ' '.join(text.split())
        return cleaned[:3000]
    except Exception:
        logging.exception('Failed to OCR uploaded screenshot for the AI tutor.')
        return ''


def generate_free_ai_response(prompt):
    provider = (os.environ.get('FREE_AI_PROVIDER') or os.environ.get('AI_PROVIDER') or 'openrouter').strip().lower()
    ai_timeout = max(5, min(int(os.environ.get('AI_TIMEOUT_SECONDS', '15')), 30))
    max_tokens = max(100, min(int(os.environ.get('AI_MAX_TOKENS', '450')), 800))
    key = (os.environ.get('OPENROUTER_API_KEY') or os.environ.get('FREE_AI_API_KEY') or '').strip()

    if provider == 'openrouter':
        key = (os.environ.get('OPENROUTER_API_KEY') or os.environ.get('FREE_AI_API_KEY') or '').strip()
        if key:
            model = (os.environ.get('FREE_AI_MODEL') or os.environ.get('AI_MODEL') or 'meta-llama/llama-3.1-8b-instruct:free').strip()
            try:
                response = requests.post(
                    'https://openrouter.ai/api/v1/chat/completions',
                    headers={
                        'Authorization': f'Bearer {key}',
                        'Content-Type': 'application/json',
                        'HTTP-Referer': 'https://pustakverse.onrender.com',
                        'X-Title': 'PustakVerse AI Tutor'
                    },
                    json={
                        'model': model,
                        'messages': [{'role': 'user', 'content': prompt}],
                        'temperature': 0.35,
                        'max_tokens': max_tokens
                    },
                    timeout=ai_timeout
                )
                if response.status_code == 200:
                    data = response.json()
                    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                    if content:
                        return str(content).strip()
                else:
                    logging.warning('OpenRouter free AI request failed: %s - %s', response.status_code, response.text[:300])
            except Exception:
                logging.exception('OpenRouter free AI request error.')

    if provider == 'huggingface':
        key = (os.environ.get('HUGGINGFACE_API_KEY') or os.environ.get('FREE_AI_API_KEY') or '').strip()
        if key:
            model = (os.environ.get('HUGGINGFACE_MODEL') or 'google/flan-t5-base').strip()
            try:
                response = requests.post(
                    f'https://api-inference.huggingface.co/models/{model}',
                    headers={'Authorization': f'Bearer {key}'},
                    json={'inputs': prompt, 'parameters': {'max_new_tokens': min(max_tokens, 350), 'temperature': 0.35}},
                    timeout=ai_timeout
                )
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        text = data[0].get('generated_text') or data[0].get('summary_text')
                        if text:
                            return str(text).strip()
                    if isinstance(data, dict):
                        text = data.get('generated_text') or data.get('summary_text')
                        if text:
                            return str(text).strip()
                else:
                    logging.warning('Hugging Face request failed: %s - %s', response.status_code, response.text[:300])
            except Exception:
                logging.exception('Hugging Face free AI request error.')

    if provider == 'groq':
        key = (os.environ.get('GROQ_API_KEY') or os.environ.get('FREE_AI_API_KEY') or '').strip()
        if key:
            model = (os.environ.get('GROQ_MODEL') or 'llama-3.3-70b-versatile').strip()
            try:
                response = requests.post(
                    'https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.35, 'max_tokens': max_tokens},
                    timeout=ai_timeout
                )
                if response.status_code == 200:
                    data = response.json()
                    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                    if content:
                        return str(content).strip()
                else:
                    logging.warning('Groq request failed: %s - %s', response.status_code, response.text[:300])
            except Exception:
                logging.exception('Groq free AI request error.')

    return ''


def build_ai_free_response(question, book_title='', book_description='', screenshot_text='', book_text='', chat_history=None):
    cleaned_question = (question or '').strip()
    if not cleaned_question:
        return 'Please ask a question about this book or a concept you want explained.'

    context = ""
    if book_title:
        context += f"Book title: {book_title}. "
    if book_description:
        context += f"Book description: {book_description[:500]}. "
    if screenshot_text:
        context += f"Screenshot text: {screenshot_text[:3000]}. "
    if book_text:
        context += f"Relevant passages from the book: {book_text[:6000]}. "

    recent_messages = (chat_history or [])[-6:]
    conversation = '\n'.join(
        f"{'Student' if item.get('role') == 'user' else 'Tutor'}: {item.get('text', '')[:1000]}"
        for item in recent_messages
        if item.get('text')
    )

    prompt = (
        "You are PustakVerse's careful AI tutor. Answer the student's latest question using the provided book "
        "context whenever it is relevant. Do not invent facts, quotations, page numbers, or details not supported by the "
        "context. If the context does not contain the answer, say so clearly and give only general guidance. "
        "Use this exact readable format: a short direct answer, then headings `Key points` and `Example` with concise bullets. "
        "For math, wrap inline expressions in `$...$` and displayed equations in `$$...$$`; use LaTex commands such as `\\frac{a}{b}` and `x^{2}` inside those delimiters. "
        "Use simple language, but keep subject-specific terms accurate. "
        f"Book context: {context}\n"
        f"Recent conversation:\n{conversation or 'No earlier messages.'}\n"
        f"Student's latest question: {cleaned_question}"
    )

    free_answer = generate_free_ai_response(prompt)
    if free_answer:
        return free_answer

    if os.environ.get('OLLAMA_ENABLED', '').strip().lower() in ('1', 'true', 'yes'):
        model_name = os.environ.get('OLLAMA_MODEL', 'llama3.2').strip() or 'llama3.2'
        try:
            response = requests.post(
                'http://localhost:11434/api/generate',
                json={'model': model_name, 'prompt': prompt, 'stream': False},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                answer = (data.get('response') or '').strip()
                if answer:
                    return answer
        except Exception:
            logging.warning('Ollama free model not available; using local tutor fallback.')

    answer = (
        f"Here is the easiest way to understand this: {cleaned_question}. "
        "Break the idea into small parts, look for the main goal, and connect it to a real example. "
        "If the concept is unfamiliar, learn the definition first, then understand why it matters in the book."
    )
    if screenshot_text:
        answer += " The uploaded screenshot is being treated as visual context, so look at the labels, arrows, and key words first."
    if book_title:
        answer += f" For {book_title}, focus on the main idea and explain it in your own words before moving on."
    return answer


def extract_pdf_text_for_learning(pdf_name, private_pdf=False):
    if not pdf_name or pdf_name.startswith('http'):
        return ''

    folder = app.config['PRIVATE_PDF_FOLDER'] if private_pdf else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs')
    full_path = os.path.join(folder, pdf_name)
    if not os.path.exists(full_path):
        return ''

    try:
        reader = PdfReader(full_path)
        text_chunks = []
        for page in reader.pages[:6]:
            page_text = page.extract_text() or ''
            if page_text:
                text_chunks.append(page_text)
        return ' '.join(text_chunks)[:4000]
    except Exception:
        logging.exception('Failed to extract PDF text for learning assistant')
        return ''


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
        except Exception:
            pass
        finally:
            if db:
                try: db.close()
                except: pass
    return dict(site_settings=global_cache['settings'], site_catalogs=global_cache['catalogs'])

@app.template_filter('drive_img')
def drive_img(url):
    if url and 'drive.google.com' in url:
        match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        if not match: 
            match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
        if match: 
            return f"https://drive.google.com/thumbnail?id={match.group(1)}&sz=w300"
    return url

def compress_cover_image(file_obj, upload_folder):
    if not HAS_PILLOW:
        safe_name = secure_filename(file_obj.filename)
        file_obj.save(os.path.join(upload_folder, 'covers', safe_name))
        return safe_name
    try:
        img = Image.open(file_obj)
        if img.mode in ("RGBA", "P"): 
            img = img.convert("RGB")
        img.thumbnail((800, 1200), Image.Resampling.LANCZOS)
        filename = secure_filename(file_obj.filename)
        base_name, _ = os.path.splitext(filename)
        webp_filename = f"{base_name}_{secrets.token_hex(4)}.webp"
        save_path = os.path.join(upload_folder, 'covers', webp_filename)
        img.save(save_path, format="WEBP", quality=75, optimize=True)
        return webp_filename
    except Exception:
        safe_name = secure_filename(file_obj.filename)
        file_obj.seek(0)
        file_obj.save(os.path.join(upload_folder, 'covers', safe_name))
        return safe_name

# ==========================================
# GOOGLE OAUTH & GMAIL API 
# ==========================================
oauth = OAuth(app)
google = oauth.register(
    name='google', 
    client_id=os.environ.get('GOOGLE_CLIENT_ID', '').strip(), 
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', '').strip(),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration', 
    client_kwargs={'scope': 'openid email profile'}
)

def send_email_wrapper(to_email, subject, body_html):
    client_id = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()
    refresh_token = os.environ.get('GOOGLE_REFRESH_TOKEN', '').strip()
    email_password = os.environ.get('EMAIL_PASSWORD', '').replace(' ', '').strip()

    from_email = os.environ.get('EMAIL_FROM', 'noreply.pustakverse@gmail.com').strip()
    smtp_username = os.environ.get('EMAIL_SMTP_USERNAME', from_email).strip()
    delivery_errors = []

    if client_id and refresh_token and client_secret:
        try:
            token_url = "https://oauth2.googleapis.com/token"
            token_data = {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token, "grant_type": "refresh_token"}
            r = requests.post(token_url, data=token_data, timeout=5)
            access_token = r.json().get("access_token")

            if access_token:
                message = EmailMessage()
                message.set_content("Please enable HTML to view this email.")
                message.add_alternative(body_html, subtype='html')
                message['To'] = to_email
                message['Subject'] = subject
                message['From'] = from_email
                encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
                send_url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
                headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
                send_res = requests.post(send_url, json={"raw": encoded_message}, headers=headers, timeout=5)
                if send_res.status_code in [200, 201]:
                    return True
                delivery_errors.append(f'Gmail API returned {send_res.status_code}: {send_res.text[:200]}')
        except Exception as error:
            delivery_errors.append(f'Gmail API error: {error}')

    if email_password:
        try:
            msg = MIMEText(body_html, 'html')
            msg['Subject'] = subject
            msg['From'] = from_email
            msg['To'] = to_email
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=5) as server:
                server.login(smtp_username, email_password)
                server.send_message(msg)
            return True
        except Exception as error:
            delivery_errors.append(f'SMTP error: {error}')

    if not delivery_errors:
        delivery_errors.append('No email provider is configured.')
    logging.error('Email delivery to %s failed. %s', to_email, ' | '.join(delivery_errors))
    return False
def send_registration_otp(to_email, otp): 
    return send_email_wrapper(to_email, 'PustakVerse - Verify Your Email', generate_html_email("Account Verification", f"<p>Your registration verification code is: <strong style='font-size: 24px; color: #38a169;'>{otp}</strong></p>"))
def send_pending_author(to_email, username):
    subject = "PustakVerse - Author Account Under Review"
    content = (
        f"<p>Hello <strong>{username}</strong>,</p>"
        f"<p>Thank you for verifying your email! Your Author account is currently <strong>under review</strong> by our administrative team.</p>"
        f"<p>We will notify you as soon as you are approved so you can start publishing.</p>"
    )
    return send_email_wrapper(to_email, subject, generate_html_email("Account Under Review", content))

def send_author_approved(to_email, username, approver_name):
    subject = "PustakVerse - Author Account Approved!"
    content = (
        f"<p>Congratulations <strong>{username}</strong>!</p>"
        f"<p>Your Author application has been officially <strong>approved by {approver_name}</strong>.</p>"
        f"<p>You can now log into your dashboard and publish your first book to the Global Library.</p>"
    )
    return send_email_wrapper(to_email, subject, generate_html_email("Account Approved", content))

def send_author_rejected(to_email, username, rejector_name, reason):
    subject = "PustakVerse - Author Application Update"
    content = (
        f"<p>Hello <strong>{username}</strong>,</p>"
        f"<p>We have reviewed your application to become an author on PustakVerse. Unfortunately, your request has been <strong>declined by {rejector_name}</strong>.</p>"
        f"<p><strong>Reason provided:</strong> {reason}</p>"
        f"<p>Your account has been switched to a standard Reader account so you can still enjoy the Global Library. If you have any questions, please contact support.</p>"
    )
    return send_email_wrapper(to_email, subject, generate_html_email("Application Declined", content))
    
def generate_html_email(title, content):
    return f'<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px; background-color: #ffffff;"><h2 style="color: #2d3748; border-bottom: 2px solid #f66d2f; padding-bottom: 10px;">{title}</h2><div style="color: #4a5568; font-size: 16px; line-height: 1.6;">{content}</div><p style="color: #718096; font-size: 12px; margin-top: 30px; border-top: 1px solid #edf2f7; padding-top: 10px;">This is an automated message from PustakVerse.</p></div>'

def send_otp_email(to_email, otp): 
    return send_email_wrapper(to_email, 'PustakVerse - Password Reset OTP', generate_html_email("Password Reset", f"<p>Your password reset code is: <strong style='font-size: 24px; color: #f66d2f;'>{otp}</strong></p>"))

def send_account_deletion_otp(to_email, otp): 
    return send_email_wrapper(to_email, 'PustakVerse - Account Deletion OTP', generate_html_email("Account Deletion Request", f"<p>Your deletion verification code is: <strong style='font-size: 24px; color: #e53e3e;'>{otp}</strong></p>"))

def send_2fa_email(to_email, otp): 
    return send_email_wrapper(to_email, 'PustakVerse - Login Verification', generate_html_email("Security Verification", f"<p>Your 2-Step Login Verification code is: <strong style='font-size: 24px; color: #38a169;'>{otp}</strong></p>"))

def send_welcome_reader(to_email, username): 
    return send_email_wrapper(to_email, 'Welcome to PustakVerse!', generate_html_email("Welcome to the Library", f"<p>Hello <strong>{username}</strong>,</p><p>Welcome to PustakVerse! Dive into our extensive Global Library today.</p>"))

def send_pending_author(to_email, username): 
    return send_email_wrapper(to_email, 'PustakVerse - Author Account Under Review', generate_html_email("Author Application Received", f"<p>Hello <strong>{username}</strong>,</p><p>Thank you for registering as an Author! Your account is currently under review.</p>"))

def send_approved_author(to_email, username): 
    return send_email_wrapper(to_email, 'Your PustakVerse Author Account is Approved!', generate_html_email("Account Approved", f"<p>Hello <strong>{username}</strong>,</p><p>Congratulations! Your Author account is officially approved.</p>"))

def send_official_welcome(to_email, username, password): 
    return send_email_wrapper(to_email, 'Welcome to the PustakVerse Official Team', generate_html_email("Official Privileges Granted", f"<p>Hello <strong>{username}</strong>,</p><p>Welcome to the administrative team!</p><p>Username: <strong>{username}</strong><br>Temporary Password: <strong>{password}</strong></p>"))

def send_warning_email(to_email, username, warning_message): 
    return send_email_wrapper(to_email, 'URGENT: Official Warning from PustakVerse', generate_html_email("Account Warning", f"<p>Hello <strong>{username}</strong>,</p><blockquote style='background: #fff5f5; border-left: 4px solid #e53e3e; padding: 10px; color: #c53030;'>{warning_message}</blockquote>"))

def send_promotion_notification(to_email, username): 
    return send_email_wrapper(to_email, 'PustakVerse - Promoted to Official', generate_html_email("Promotion Notice", f"<p>Hello <strong>{username}</strong>,</p><p>Congratulations! You have been officially promoted to an Administrator on PustakVerse.</p>"))

def send_mass_message(to_emails, subject, message, role_target):
    content = f"<p><strong>Official Broadcast to {role_target.capitalize()}s:</strong></p><p>{message}</p>"
    for email in to_emails: 
        send_email_wrapper(email, f'PustakVerse Notice: {subject}', generate_html_email(subject, content))

def send_revoked_official_email(to_email, username, reason): 
    return send_email_wrapper(to_email, 'PustakVerse - Administrative Privileges Revoked', generate_html_email("Privileges Revoked", f"<p>Hello {username},</p><p>Your official administrative privileges have been revoked.</p><p><strong>Reason:</strong> {reason}</p>"))

def send_account_deleted_email(to_email, username, reason): 
    return send_email_wrapper(to_email, 'PustakVerse - Account Deletion Notice', generate_html_email("Account Terminated", f"<p>Hello {username},</p><p>Your PustakVerse account has been permanently deleted.</p><p><strong>Reason:</strong> {reason}</p>"))

def send_author_rejected_email(to_email, username, reason): 
    return send_email_wrapper(to_email, 'PustakVerse - Author Application Status', generate_html_email("Application Rejected", f"<p>Hello {username},</p><p>Your application for an Author account has been rejected.</p><p><strong>Reason:</strong> {reason}</p>"))

def send_book_deleted_email(to_email, username, book_title, reason): 
    return send_email_wrapper(to_email, 'PustakVerse - Book Removal Notice', generate_html_email("Content Removed", f"<p>Hello {username},</p><p>Your book titled '{book_title}' has been removed from PustakVerse.</p><p><strong>Reason:</strong> {reason}</p>"))

# ==========================================
# SECURE TiDB (MYSQL) DATABASE CONNECTION
# ==========================================
def get_db_connection(retries=2, delay=1.0):
    last_exception = None
    db_host = os.environ.get('DB_HOST')
    db_port = int(os.environ.get('DB_PORT', 4000))
    db_user = os.environ.get('DB_USER')
    db_pass = os.environ.get('DB_PASSWORD')
    db_name = os.environ.get('DB_NAME')

    for attempt in range(retries):
        try:
            conn = mysql.connector.connect(
                host=db_host, 
                port=db_port, 
                user=db_user, 
                password=db_pass, 
                database=db_name, 
                ssl_verify_cert=False, 
                ssl_verify_identity=False, 
                connection_timeout=8
            )
            if conn.is_connected(): 
                return conn
        except mysql.connector.Error as err:
            last_exception = err
            time.sleep(delay)
    raise last_exception

def ensure_payment_schema():
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        
        cursor.execute("CREATE TABLE IF NOT EXISTS users (id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(100) NOT NULL UNIQUE, email VARCHAR(150) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, role ENUM('reader', 'author', 'official', 'developer') DEFAULT 'reader', is_verified BOOLEAN DEFAULT FALSE, security_question VARCHAR(255) NOT NULL, security_answer VARCHAR(255) NOT NULL, verification_reason TEXT, payout_details VARCHAR(255) DEFAULT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)")
        try:
            cursor.execute("SHOW COLUMNS FROM users LIKE 'two_factor_enabled'")
            if not cursor.fetchone(): 
                cursor.execute("ALTER TABLE users ADD COLUMN two_factor_enabled BOOLEAN DEFAULT FALSE")
        except Exception: pass
        
        cursor.execute("CREATE TABLE IF NOT EXISTS username_requests (id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, new_username VARCHAR(100) NOT NULL, reason TEXT NOT NULL, status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE)")
        cursor.execute("CREATE TABLE IF NOT EXISTS books (id INT AUTO_INCREMENT PRIMARY KEY, title VARCHAR(255) NOT NULL, author_id INT NOT NULL, catalog VARCHAR(100) NOT NULL, cover_image VARCHAR(1000) NOT NULL, pdf_file VARCHAR(1000) NOT NULL, is_paid BOOLEAN NOT NULL DEFAULT FALSE, price_paise INT NOT NULL DEFAULT 0, private_pdf BOOLEAN NOT NULL DEFAULT FALSE, preview_pages INT NOT NULL DEFAULT 5, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE)")
        
        try:
            cursor.execute("SHOW COLUMNS FROM books LIKE 'preview_pages'")
            if not cursor.fetchone(): 
                cursor.execute("ALTER TABLE books ADD COLUMN preview_pages INT NOT NULL DEFAULT 5")
        except Exception: pass

        try:
            cursor.execute("SHOW COLUMNS FROM books LIKE 'rp_key_id'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE books ADD COLUMN rp_key_id VARCHAR(255) DEFAULT NULL")
                cursor.execute("ALTER TABLE books ADD COLUMN rp_key_secret VARCHAR(255) DEFAULT NULL")
        except Exception: pass
        
        try:
            cursor.execute("SHOW COLUMNS FROM books LIKE 'description'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE books ADD COLUMN description TEXT")
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
        cursor.execute("CREATE TABLE IF NOT EXISTS ai_chat_messages (id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, book_id INT NULL, role ENUM('user', 'assistant') NOT NULL, message_text TEXT NOT NULL, screenshot VARCHAR(255) DEFAULT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE, INDEX idx_ai_chat_user_book (user_id, book_id, id))")
        
        db.commit()
        return True
    except Exception as error:
        if db: 
            db.rollback()
        return False
    finally:
        if db:
            try: db.close()
            except: pass

def log_official_activity(official_id, action_desc):
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT INTO official_activities (official_id, action) VALUES (%s, %s)", (official_id, action_desc))
        db.commit()
    except Exception: 
        pass
    finally:
        if db:
            try: db.close()
            except: pass


def get_ai_chat_history(user_id, book_id):
    """Return a user's saved tutor conversation for one book (or the general tutor)."""
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT role, message_text AS text, COALESCE(screenshot, '') AS screenshot "
            "FROM ai_chat_messages WHERE user_id = %s AND book_id <=> %s ORDER BY id ASC LIMIT 100",
            (user_id, book_id)
        )
        return cursor.fetchall()
    except Exception:
        logging.exception('Could not load AI chat history.')
        return []
    finally:
        if db:
            try: db.close()
            except: pass


def save_ai_chat_message(user_id, book_id, role, text, screenshot=''):
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO ai_chat_messages (user_id, book_id, role, message_text, screenshot) VALUES (%s, %s, %s, %s, %s)",
            (user_id, book_id, role, text, screenshot or None)
        )
        db.commit()
    except Exception:
        if db:
            db.rollback()
        logging.exception('Could not save AI chat message.')
    finally:
        if db:
            try: db.close()
            except: pass

@app.before_request
def ensure_payment_schema_before_request():
    global payment_schema_ready
    if not payment_schema_ready: 
        payment_schema_ready = ensure_payment_schema()

@app.before_request
def update_last_activity():
    if 'user_id' in session:
        last_update = session.get('last_activity_update')
        current_time = time.time()
        
        if not last_update or (current_time - last_update > 300):
            db = None
            try:
                db = get_db_connection()
                cursor = db.cursor()
                cursor.execute("UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE id = %s", (session['user_id'],))
                db.commit()
                session['last_activity_update'] = current_time
            except Exception: 
                pass
            finally:
                if db:
                    try: db.close()
                    except: pass

def create_master_developer():
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE username = 'abhinavgiri45'")
        if not cursor.fetchone():
            hashed_pw = generate_password_hash(os.environ.get('MASTER_ADMIN_PASSWORD'))
            cursor.execute("INSERT IGNORE INTO users (username, email, password_hash, role, is_verified, security_question, security_answer, verification_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", ('abhinavgiri45', 'abhinavgiri370@gmail.com', hashed_pw, 'developer', True, 'What is your favorite book?', 'gita', 'Master Admin'))
            db.commit()
    except Exception as e: 
        pass
    finally:
        if db:
            try: db.close()
            except: pass

# ==========================================
# PUBLIC ROUTES
# ==========================================
@app.route('/')
def index():
    show_telegram_popup = session.pop('show_telegram_popup', False)
    db = None
    books = []
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, books.description, users.username as author_name, users.role as author_role 
            FROM books JOIN users ON books.author_id = users.id ORDER BY books.created_at DESC""")
        books = clean_book_data(cursor.fetchall())
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('index.html', books=books, show_telegram_popup=show_telegram_popup)

@app.route('/category/<name>')
def category_view(name):
    db = None
    books = []
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, books.description, users.username as author_name, users.role as author_role 
            FROM books JOIN users ON books.author_id = users.id WHERE books.catalog = %s ORDER BY books.created_at DESC""", (name,))
        books = clean_book_data(cursor.fetchall())
    except Exception: 
        flash("Experiencing high traffic. Please refresh to load books.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('category.html', books=books, page_title=name)

@app.route('/archives')
def archives_view():
    db = None
    books = []
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("""SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, books.description, users.username as author_name, users.role as author_role 
            FROM books JOIN users ON books.author_id = users.id WHERE books.catalog = 'Archives' ORDER BY books.created_at ASC""")
        books = clean_book_data(cursor.fetchall())
    except Exception: 
        flash("Experiencing high traffic. Please refresh to load books.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('category.html', books=books, page_title="Archives (Free Classics)")

@app.route('/book/<int:book_id>')
def view_book(book_id):
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT b.*, u.username as author_name FROM books b JOIN users u ON b.author_id = u.id WHERE b.id = %s", (book_id,))
        book = cursor.fetchone()
        if not book: 
            abort(404)

        cursor.execute("SELECT i.*, u.username FROM interactions i JOIN users u ON i.user_id = u.id WHERE i.book_id = %s ORDER BY i.created_at DESC", (book_id,))
        reviews = cursor.fetchall()
        
        # SMART SORTING: If the logged-in user wrote a review, move it to the very top!
        user_id = session.get('user_id')
        if user_id:
            reviews.sort(key=lambda x: x['user_id'] != user_id)
            
        # AVERAGE RATING MATH
        review_count = len(reviews)
        avg_rating = 0.0
        rating_counts = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        
        if review_count > 0:
            total_stars = sum(r['rating'] for r in reviews)
            avg_rating = round(total_stars / review_count, 1)
            for r in reviews:
                if r['rating'] in rating_counts:
                    rating_counts[r['rating']] += 1
        
        can_read = False
        if 'user_id' in session:
            if not book['is_paid'] or session['user_id'] == book['author_id'] or session.get('role') == 'developer':
                can_read = True
            else:
                cursor.execute("SELECT id FROM purchases WHERE user_id = %s AND book_id = %s AND status = 'paid'", (session['user_id'], book_id))
                can_read = bool(cursor.fetchone())
                
        return render_template('book.html', book=book, reviews=reviews, can_read=can_read, avg_rating=avg_rating, review_count=review_count, rating_counts=rating_counts)
    except Exception as e:
        flash("Error loading book details.", "error")
        return redirect(url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass

@app.route('/book/<int:book_id>/learn', methods=['GET', 'POST'])
def learn_book(book_id):
    db = None
    book = None
    concept = request.form.get('concept', '').strip() or request.args.get('concept', '').strip()

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT b.*, u.username AS author_name FROM books b JOIN users u ON b.author_id = u.id WHERE b.id = %s", (book_id,))
        book = cursor.fetchone()
    except Exception:
        flash("AI study assistant could not load this book right now.", "error")
        return redirect(url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass

    if not book:
        abort(404)

    book_text = extract_pdf_text_for_learning(book.get('pdf_file') or '', bool(book.get('private_pdf')))
    if not concept:
        concept = suggest_concept(book.get('title') or '', book.get('description') or '', book_text)

    ai_response = build_ai_learning_response(
        book_title=book.get('title') or 'This book',
        book_description=book.get('description') or '',
        concept_query=concept,
        book_text=book_text
    )

    return render_template('learn_book.html', book=book, concept=concept, ai_response=ai_response)

@app.route('/ask_ai', methods=['GET', 'POST'])
def ask_ai():
    if 'user_id' not in session:
        flash('Please log in to access the AI tutor.', 'error')
        return redirect(url_for('login'))

    book_id = request.args.get('book_id', type=int)
    question = request.form.get('question', '').strip() or request.args.get('question', '').strip()
    book = None

    if book_id:
        db = None
        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT b.*, u.username AS author_name FROM books b JOIN users u ON b.author_id = u.id WHERE b.id = %s", (book_id,))
            book = cursor.fetchone()
        except Exception:
            book = None
        finally:
            if db:
                try: db.close()
                except: pass

    chat_history = get_ai_chat_history(session['user_id'], book_id)
    screenshot_path = ''
    screenshot_text = ''

    if request.method == 'POST':
        uploaded_file = request.files.get('screenshot')
        if uploaded_file and uploaded_file.filename:
            screenshot_name = save_uploaded_screenshot(uploaded_file)
            if screenshot_name:
                screenshot_path = os.path.join(app.config['UPLOAD_FOLDER'], 'ai_screenshots', screenshot_name)
                screenshot_text = extract_text_from_uploaded_image(screenshot_path)

        if not question and not screenshot_text:
            message = 'Ask a question or upload a screenshot to continue the conversation.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': message}), 400
            flash(message, 'error')
            return render_template('ask_ai.html', book=book, question='', answer='', chat_history=chat_history)

        prompt_text = question or 'Explain this screenshot in the simplest possible way.'
        book_text = extract_pdf_text_for_learning(
            (book or {}).get('pdf_file') or '',
            bool((book or {}).get('private_pdf'))
        ) if book else ''
        answer = build_ai_free_response(
            prompt_text,
            book_title=(book or {}).get('title') or '',
            book_description=(book or {}).get('description') or '',
            screenshot_text=screenshot_text,
            book_text=book_text,
            chat_history=chat_history
        )

        screenshot_name = os.path.basename(screenshot_path) if screenshot_path else ''
        save_ai_chat_message(session['user_id'], book_id, 'user', prompt_text, screenshot_name)
        save_ai_chat_message(session['user_id'], book_id, 'assistant', answer)
        chat_history = get_ai_chat_history(session['user_id'], book_id)

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': True,
                'answer': answer,
                'history': chat_history
            })

        return render_template('ask_ai.html', book=book, question='', answer=answer, chat_history=chat_history)

    return render_template('ask_ai.html', book=book, question=question, answer='', chat_history=chat_history)

@app.route('/clear_ai_chat', methods=['POST'])
def clear_ai_chat():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401

    book_id = request.args.get('book_id', type=int)
    if book_id == 0:
        book_id = None
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "DELETE FROM ai_chat_messages WHERE user_id = %s AND book_id <=> %s",
            (session['user_id'], book_id)
        )
        db.commit()
    except Exception:
        if db:
            db.rollback()
        logging.exception('Could not clear AI chat history.')
        return jsonify({'success': False, 'message': 'Could not clear this chat. Please try again.'}), 500
    finally:
        if db:
            try: db.close()
            except: pass
    return jsonify({'success': True})

@app.route('/submit_review/<int:book_id>', methods=['POST'])
def submit_review(book_id):
    if 'user_id' not in session:
        flash("Please log in to leave a review.", "error")
        return redirect(url_for('login'))
    
    rating = request.form.get('rating', type=int)
    review_text = request.form.get('review', '').strip()
    
    if not rating or rating < 1 or rating > 5:
        flash("Please provide a star rating.", "error")
        return redirect(url_for('view_book', book_id=book_id))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("SELECT id FROM interactions WHERE user_id = %s AND book_id = %s", (session['user_id'], book_id))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("UPDATE interactions SET rating = %s, review = %s, created_at = CURRENT_TIMESTAMP WHERE id = %s", (rating, review_text, existing[0]))
        else:
            cursor.execute("INSERT INTO interactions (user_id, book_id, rating, review) VALUES (%s, %s, %s, %s)", (session['user_id'], book_id, rating, review_text))
        db.commit()
        flash("Review successfully posted!", "success")
    except Exception as e:
        flash("Database error posting review.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('view_book', book_id=book_id))

@app.route('/delete_review/<int:review_id>/<int:book_id>', methods=['POST'])
def delete_review(review_id, book_id):
    if 'user_id' not in session:
        flash("Please log in.", "error")
        return redirect(url_for('login'))
    
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM interactions WHERE id = %s AND user_id = %s", (review_id, session['user_id']))
        db.commit()
        flash("Your review has been successfully deleted.", "success")
    except Exception as e:
        flash("Database error deleting review.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('view_book', book_id=book_id))

@app.route('/check_username', methods=['POST'])
def check_username():
    username = request.form.get('username', '').strip()
    if not username: 
        return jsonify({'available': False, 'message': ''})
    if not re.match(r'^[a-zA-Z0-9_]+$', username): 
        return jsonify({'available': False, 'message': 'Username cannot contain spaces or special characters.'})
    
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if user: 
            return jsonify({'available': False, 'message': 'Username is already taken'})
        return jsonify({'available': True, 'message': 'Username is available!'})
    except Exception: 
        return jsonify({'available': False, 'message': 'Checking...'})
    finally:
        if db:
            try: db.close()
            except: pass

@app.route('/contact')
def contact(): 
    return render_template('contact.html')

@app.route('/terms')
def terms():
    role = request.args.get('role', 'reader')
    return render_template('terms.html', role=role)

# ==========================================
# AUTHENTICATION
# ==========================================
@app.route('/register', methods=['POST'])
def register():
    action = request.form.get('action', 'register')

    # STEP 1: Process initial form and send OTP
    if action == 'register':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form['role']
        sec_question = request.form['security_question']
        sec_answer = request.form['security_answer'].lower().strip()
        verification_reason = request.form.get('verification_reason', '')
        
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            flash("Username can only contain letters, numbers, and underscores.", "error")
            return redirect(url_for('login'))

        if role not in ['reader', 'author']: 
            role = 'reader'

        # Check if user already exists BEFORE sending OTP
        db = None
        try:
            db = get_db_connection()
            cursor = db.cursor()
            cursor.execute("SELECT id FROM users WHERE username = %s OR email = %s", (username, email))
            if cursor.fetchone():
                flash("Username or Email already exists. Please choose another.", "error")
                return redirect(url_for('login'))
        except Exception:
            flash("Database connection error. Please try again.", "error")
            return redirect(url_for('login'))
        finally:
            if db:
                try: db.close()
                except: pass

        # Generate OTP and store user data temporarily in session
        otp = str(random.randint(100000, 999999))
        session['reg_otp'] = otp
        session['reg_data'] = {
            'username': username,
            'email': email,
            'password_hash': generate_password_hash(password),
            'role': role,
            'sec_question': sec_question,
            'sec_answer': sec_answer,
            'verification_reason': verification_reason
        }

        # Send the verification email
        if send_registration_otp(email, otp):
            flash("An OTP has been sent to your email. Please verify to complete registration.", "info")
            # Render the login page but tell it to show the OTP form
            return render_template('login.html', show_reg_otp_form=True, reg_email=email)
        else:
            flash("Failed to send OTP email. Please check your email address.", "error")
            return redirect(url_for('login'))

    # STEP 2: Verify the OTP and create the account
    elif action == 'verify_otp':
        user_otp = request.form.get('otp', '').replace(' ', '').strip()
        correct_otp = session.get('reg_otp')
        reg_data = session.get('reg_data')

        if user_otp and correct_otp and user_otp == correct_otp and reg_data:
            db = None
            try:
                db = get_db_connection()
                cursor = db.cursor()
                is_verified = (reg_data['role'] == 'reader')
                
                cursor.execute("INSERT INTO users (username, email, password_hash, role, is_verified, security_question, security_answer, verification_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", 
                               (reg_data['username'], reg_data['email'], reg_data['password_hash'], reg_data['role'], is_verified, reg_data['sec_question'], reg_data['sec_answer'], reg_data['verification_reason']))
                db.commit()

                if reg_data['role'] == 'reader': 
                    send_welcome_reader(reg_data['email'], reg_data['username'])
                    flash("Account created and verified successfully! Please sign in.", "success")
                elif reg_data['role'] == 'author': 
                    send_pending_author(reg_data['email'], reg_data['username'])
                    flash("Author Account verified! Please wait for admin approval.", "success")
                
                # Clear the temporary session data
                session.pop('reg_otp', None)
                session.pop('reg_data', None)

            except mysql.connector.IntegrityError: 
                flash("Username or Email was taken while you were verifying. Please start over.", "error")
            except Exception: 
                flash("Network error occurred during registration. Please try again.", "error")
            finally:
                if db:
                    try: db.close()
                    except: pass
            
            return redirect(url_for('login'))
        else:
            flash("Invalid Verification Code. Please try again.", "error")
            return render_template('login.html', show_reg_otp_form=True, reg_email=reg_data.get('email', ''))

    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action', 'login')
        
        if action == 'login':
            login_portal = request.form.get('login_portal', 'reader')
            db = None
            user = None
            try:
                db = get_db_connection()
                cursor = db.cursor(dictionary=True)
                cursor.execute("SELECT * FROM users WHERE username = %s", (request.form['username'].strip(),))
                user = cursor.fetchone()
            except Exception:
                flash("Secure connection to the database timed out. Please try logging in again.", "error")
                return render_template('login.html', active_tab=login_portal)
            finally:
                if db:
                    try: db.close()
                    except: pass
            
            if user and check_password_hash(user['password_hash'], request.form['password']):
                if login_portal == 'reader' and user['role'] != 'reader': 
                    flash("Please use the 'Author / Official' tab to log in to your account.", "error")
                    return render_template('login.html', active_tab='reader')
                if login_portal == 'author_official' and user['role'] not in ['author', 'official', 'developer']: 
                    flash("Readers must log in using the 'Reader Login' tab.", "error")
                    return render_template('login.html', active_tab='official')

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
                
                flash(f"Welcome back, {user['username']}!", "success")
                return redirect(url_for('index'))
            
            flash("Invalid username or password.", "error")
            return render_template('login.html', active_tab=login_portal)
            
        elif action == 'verify_2fa':
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
                return redirect(url_for('index'))
            else: 
                flash("Invalid Verification Code. Please try again.", "error")
                return render_template('login.html', show_2fa_form=True, email=pending_user.get('email', ''))
                
    return render_template('login.html', active_tab='reader')

@app.route('/login/google')
def google_login(): 
    return google.authorize_redirect(url_for('google_authorize', _external=True))

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
        if not base_username: 
            base_username = f"user_{secrets.randbelow(9999)}"
        
        db = None
        user = None
        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            if not user:
                cursor.execute("SELECT * FROM users WHERE username = %s", (base_username,))
                if cursor.fetchone(): 
                    base_username = f"{base_username}{secrets.randbelow(9999)}"
                cursor.execute("INSERT INTO users (username, email, password_hash, role, is_verified, security_question, security_answer) VALUES (%s, %s, %s, 'reader', TRUE, 'Google', 'Google')", (base_username, email, generate_password_hash(secrets.token_urlsafe(16))))
                db.commit()
                send_welcome_reader(email, base_username)
                
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()
        except Exception: 
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
        
        flash(f"Welcome back, {user['username']}!", "success")
        return redirect(url_for('index'))
    except Exception: 
        flash("Google Authentication failed. Please try again.", "error")
        return redirect(url_for('login'))

@app.route('/logout')
def logout(): 
    session.clear()
    return redirect(url_for('index'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        action = request.form.get('action')
        db = None
        
        if action == 'send_otp':
            email = request.form.get('email')
            try:
                db = get_db_connection()
                cursor = db.cursor(dictionary=True)
                cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
                user = cursor.fetchone()
            except Exception: 
                flash("Database connection timeout. Please try again.", "error")
                return render_template('forgot_password.html', show_otp_form=False)
            finally:
                if db:
                    try: db.close()
                    except: pass
                    
            if user and user['role'] == 'developer':
                flash("Developer accounts cannot be reset via this page. ", "error")
                return render_template('forgot_password.html', show_otp_form=False)
            elif user:
                otp = str(random.randint(100000, 999999))
                session['reset_otp'] = otp
                session['reset_email'] = email
                if send_otp_email(email, otp):
                    flash("An OTP has been sent.", "success")
                    return render_template('forgot_password.html', show_otp_form=True, email=email)
            else:
                flash("If this email exists, an OTP will be sent.", "info")
        elif action == 'verify_otp':
            user_otp = request.form.get('otp')
            new_password = request.form.get('new_password')
            email = session.get('reset_email')
            
            if user_otp and user_otp == session.get('reset_otp'):
                hashed_pw = generate_password_hash(new_password)
                try:
                    db = get_db_connection()
                    cursor = db.cursor()
                    cursor.execute("UPDATE users SET password_hash = %s WHERE email = %s", (hashed_pw, email))
                    db.commit()
                except Exception: 
                    flash("Database connection timeout. Please try again.", "error")
                    return render_template('forgot_password.html', show_otp_form=True, email=email)
                finally:
                    if db:
                        try: db.close()
                        except: pass
                        
                session.pop('reset_otp', None)
                session.pop('reset_email', None)
                flash("Password changed successfully. You may now log in.", "success")
                return redirect(url_for('login'))
            else: 
                flash("Invalid OTP. Please try again.", "error")
                return render_template('forgot_password.html', show_otp_form=True, email=email)
                
    return render_template('forgot_password.html', show_otp_form=False)

# ==========================================
# ACCOUNT DELETION (OTP VERIFIED)
# ==========================================
@app.route('/send_delete_account_otp', methods=['POST'])
def send_delete_account_otp():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT email FROM users WHERE id = %s", (session['user_id'],))
        user = cursor.fetchone()
        
        if user:
            otp = str(random.randint(100000, 999999))
            session['delete_account_otp'] = otp
            send_account_deletion_otp(user['email'], otp)
            flash("An OTP has been sent to your email to confirm account deletion.", "info")
            session['show_delete_otp_form'] = True
    except Exception: 
        flash("Database Error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/delete_my_account', methods=['POST'])
def delete_my_account():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
        
    user_otp = request.form.get('otp', '').strip()
    valid_otp = session.pop('delete_account_otp', None)
    session.pop('show_delete_otp_form', None)
    
    if user_otp and valid_otp and user_otp == valid_otp:
        db = None
        try:
            db = get_db_connection()
            cursor = db.cursor()
            user_id = session['user_id']
            tables = ['personal_library', 'interactions', 'books', 'users']
            
            for table in tables:
                column = 'author_id' if table == 'books' else ('id' if table == 'users' else 'user_id')
                cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (user_id,))
            db.commit()
            
            session.clear()
            flash("Your account and all associated data have been permanently deleted.", "success")
            return redirect(url_for('index'))
        except Exception: 
            flash("Database Error during deletion.", "error")
        finally:
            if db:
                try: db.close()
                except: pass
    else: 
        flash("Invalid OTP. Account deletion aborted.", "error")
    return redirect(url_for('dashboard'))

# ==========================================
# DEVELOPER WARNING, PROMOTION & BROADCAST
# ==========================================
@app.route('/warn_user/<int:user_id>', methods=['POST'])
def warn_user(user_id):
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    warning_msg = request.form.get('warning_message', '').strip()
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT username, email FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if user and warning_msg: 
            send_warning_email(user['email'], user['username'], warning_msg)
            flash(f"Official warning sent to {user['username']}.", "success")
    except Exception: 
        flash("Database Error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/promote_user/<int:user_id>', methods=['POST'])
def promote_user(user_id):
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT username, email FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        
        if user:
            cursor.execute("UPDATE users SET role = 'official' WHERE id = %s", (user_id,))
            db.commit()
            send_promotion_notification(user['email'], user['username'])
            flash(f"{user['username']} has been promoted to Official!", "success")
    except Exception: 
        flash("Database Error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/mass_message', methods=['POST'])
def mass_message():
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    target_role = request.form.get('target_role')
    subject = request.form.get('subject', 'Official Notice')
    message_body = request.form.get('message_body', '').strip()
    
    if not message_body: 
        flash("Message body cannot be empty.", "error")
        return redirect(url_for('dashboard'))

    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        
        if target_role == 'all': 
            cursor.execute("SELECT email FROM users")
        else: 
            cursor.execute("SELECT email FROM users WHERE role = %s", (target_role,))
            
        emails = [row['email'] for row in cursor.fetchall()]
        
        if emails: 
            send_mass_message(emails, subject, message_body, target_role)
            flash(f"Mass broadcast sent successfully to {len(emails)} users.", "success")
        else: 
            flash("No users found for that specific role.", "info")
    except Exception: 
        flash("Database Error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

# ==========================================
# CHANGE USERNAME LOGIC
# ==========================================
@app.route('/change_username', methods=['POST'])
def change_username():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
        
    new_username = request.form.get('new_username', '').strip()
    reason = request.form.get('reason', '').strip()
    role = session.get('role')

    if not new_username: 
        flash("New username cannot be empty.", "error")
        return redirect(url_for('dashboard'))
        
    if not re.match(r'^[a-zA-Z0-9_]+$', new_username): 
        flash("Username can only contain letters, numbers, and underscores (no spaces or special characters).", "error")
        return redirect(url_for('dashboard'))

    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE username = %s", (new_username,))
        
        if cursor.fetchone(): 
            flash("Username is already taken.", "error")
            return redirect(url_for('dashboard'))

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
    except Exception: 
        flash("Database connection error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/handle_username_request/<int:req_id>/<action>', methods=['POST'])
def handle_username_request(req_id, action):
    role = session.get('role')
    
    if role not in ['official', 'developer']: 
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM username_requests WHERE id = %s", (req_id,))
        req = cursor.fetchone()
        
        if not req or req['status'] != 'pending': 
            flash("Invalid or already processed request.", "error")
            return redirect(url_for('dashboard'))

        cursor.execute("SELECT role FROM users WHERE id = %s", (req['user_id'],))
        target_user = cursor.fetchone()
        
        if target_user['role'] == 'official' and role != 'developer': 
            flash("Only developers can approve official username changes.", "error")
            return redirect(url_for('dashboard'))

        if action == 'approve':
            cursor.execute("SELECT id FROM users WHERE username = %s", (req['new_username'],))
            if cursor.fetchone():
                flash("That username was taken by someone else while pending.", "error")
                cursor.execute("UPDATE username_requests SET status = 'rejected' WHERE id = %s", (req_id,))
                db.commit()
                return redirect(url_for('dashboard'))
                
            cursor.execute("UPDATE users SET username = %s WHERE id = %s", (req['new_username'], req['user_id']))
            cursor.execute("UPDATE username_requests SET status = 'approved' WHERE id = %s", (req_id,))
            db.commit()
            flash("Username change approved.", "success")
            
        elif action == 'reject': 
            cursor.execute("UPDATE username_requests SET status = 'rejected' WHERE id = %s", (req_id,))
            db.commit()
            flash("Username change rejected.", "info")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/send_change_password_otp', methods=['POST'])
def send_change_password_otp():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
        
    db = None
    user = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT email FROM users WHERE id = %s", (session['user_id'],))
        user = cursor.fetchone()
    except Exception: 
        flash("Database connection error. Please try again.", "error")
        return redirect(url_for('dashboard'))
    finally:
        if db:
            try: db.close()
            except: pass

    if user:
        otp = str(random.randint(100000, 999999))
        session['change_pw_otp'] = otp
        if send_otp_email(user['email'], otp): 
            flash("An OTP has been sent to your registered email.", "info")
        else: 
            flash("Failed to send OTP. Please check the email server.", "error")
            
    return redirect(url_for('dashboard'))

@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
        
    user_otp = request.form.get('otp')
    old_password = request.form.get('old_password')
    new_password = request.form.get('new_password')
    valid_otp = session.pop('change_pw_otp', None)
    
    if not user_otp or not valid_otp or user_otp != valid_otp: 
        flash("Invalid or expired OTP. Please request a new one.", "error")
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT password_hash FROM users WHERE id = %s", (session['user_id'],))
        user = cursor.fetchone()
        
        if not user or not check_password_hash(user['password_hash'], old_password): 
            flash("Incorrect current password.", "error")
            return redirect(url_for('dashboard'))
            
        hashed_pw = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed_pw, session['user_id']))
        db.commit()
        flash("Your password has been securely updated!", "success")
    except Exception: 
        flash("Database connection error. Please try again.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
            
    return redirect(url_for('dashboard'))

@app.route('/cancel_password_change')
def cancel_password_change(): 
    session.pop('change_pw_otp', None)
    return redirect(url_for('dashboard'))

# ==========================================
# E-COMMERCE & BUY NOW LOGIC 
# ==========================================
@app.route('/buy_book/<int:book_id>', methods=['POST'])
def buy_book(book_id):
    if 'user_id' not in session: 
        flash('Please sign in or register before purchasing a book.', 'error')
        return redirect(url_for('login'))
        
    db = None
    book = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT b.id, b.title, b.is_paid, b.price_paise, b.cover_image, b.rp_key_id as author_key_id, b.rp_key_secret as author_key_secret, u.username as author_name FROM books b JOIN users u ON b.author_id = u.id WHERE b.id = %s", (book_id,))
        book = cursor.fetchone()
        
        if book: 
            book['cover_image'] = book.get('cover_image') or ""
        if not book: 
            abort(404)
            
        if not book['is_paid'] or not book['price_paise']:
            cursor.execute('INSERT IGNORE INTO personal_library (user_id, book_id) VALUES (%s, %s)', (session['user_id'], book_id))
            db.commit()
            return redirect(url_for('read_book', book_id=book_id))
            
        cursor.execute("SELECT id FROM purchases WHERE user_id = %s AND book_id = %s AND status = 'paid'", (session['user_id'], book_id))
        if cursor.fetchone(): 
            return redirect(url_for('read_book', book_id=book_id))
            
    except Exception: 
        flash("Database connection error. Please try again.", "error")
        return redirect(request.referrer or url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass

    author_key_id = book.get('author_key_id')
    author_key_secret = book.get('author_key_secret')
    
    if not author_key_id or not author_key_secret: 
        flash('The author has not configured their payment gateway for this specific book. Purchases are temporarily disabled.', 'error')
        return redirect(request.referrer or url_for('index'))

    total_paise = book['price_paise']
    db = None
    try:
        client = razorpay.Client(auth=(author_key_id, author_key_secret))
        order_data = {'amount': total_paise, 'currency': 'INR', 'receipt': f"pv-{session['user_id']}-{book_id}-{secrets.token_hex(4)}"}
        order = client.order.create(order_data)
        
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT INTO purchases (user_id, book_id, razorpay_order_id, amount_paise, fee_paise, status) VALUES (%s, %s, %s, %s, %s, 'pending')", (session['user_id'], book_id, order['id'], book['price_paise'], 0))
        db.commit()
    except Exception: 
        flash('Unable to connect to the payment gateway. The author keys may be invalid.', 'error')
        return redirect(request.referrer or url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass
            
    return render_template('checkout.html', book=book, order_id=order['id'], total_paise=total_paise, fee_paise=0, base_price=book['price_paise'], razorpay_key=author_key_id)

@app.route('/payment/verify', methods=['POST'])
def verify_payment():
    if 'user_id' not in session: 
        abort(401)
        
    order_id = request.form.get('razorpay_order_id', '')
    payment_id = request.form.get('razorpay_payment_id', '')
    signature = request.form.get('razorpay_signature', '')
    
    if not all([order_id, payment_id, signature]): 
        abort(400)
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT p.id, p.book_id, b.rp_key_id, b.rp_key_secret FROM purchases p JOIN books b ON p.book_id = b.id WHERE p.razorpay_order_id = %s AND p.user_id = %s', (order_id, session['user_id']))
        purchase = cursor.fetchone()
        
        if purchase:
            key_id = purchase['rp_key_id']
            key_secret = purchase['rp_key_secret']
            
            if key_id and key_secret:
                client = razorpay.Client(auth=(key_id, key_secret))
                client.utility.verify_payment_signature({'razorpay_order_id': order_id, 'razorpay_payment_id': payment_id, 'razorpay_signature': signature})
                
                cursor.execute("UPDATE purchases SET razorpay_payment_id = %s, status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE id = %s", (payment_id, purchase['id']))
                cursor.execute('INSERT IGNORE INTO personal_library (user_id, book_id) VALUES (%s, %s)', (session['user_id'], purchase['book_id']))
                db.commit()
                
                flash('Payment successful! Book has been saved to My Library and unlocked.', 'success')
                return redirect(url_for('read_book', book_id=purchase['book_id']))
            else: 
                flash('Payment verification failed. Keys missing on this book.', 'error')
                return redirect(url_for('my_library'))
        else: 
            flash('Payment verification failed. Order not found.', 'error')
            return redirect(url_for('my_library'))
    except Exception: 
        flash('Payment verification failed.', 'error')
        return redirect(url_for('my_library'))
    finally:
        if db:
            try: db.close()
            except: pass

@app.route('/payment_history')
def payment_history():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
        
    db = None
    payments = []
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT p.razorpay_order_id, p.amount_paise, p.status, p.paid_at, b.title as book_title FROM purchases p JOIN books b ON p.book_id = b.id WHERE p.user_id = %s ORDER BY p.created_at DESC", (session['user_id'],))
        payments = cursor.fetchall()
    except Exception: 
        flash("Could not load payment history.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return render_template('payment_history.html', payments=payments)

@app.route('/book_sales/<int:book_id>')
def book_sales(book_id):
    if session.get('role') not in ['author', 'developer', 'official']: 
        return redirect(url_for('login'))
        
    db = None
    sales = []
    book = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT title, author_id, price_paise FROM books WHERE id = %s", (book_id,))
        book = cursor.fetchone()
        
        if not book or (book['author_id'] != session['user_id'] and session['role'] not in ['developer', 'official']): 
            flash("Unauthorized access to book sales.", "error")
            return redirect(url_for('dashboard'))
            
        cursor.execute("SELECT p.razorpay_order_id, p.amount_paise, p.status, p.paid_at, u.username as buyer_name, u.email as buyer_email FROM purchases p JOIN users u ON p.user_id = u.id WHERE p.book_id = %s AND p.status = 'paid' ORDER BY p.paid_at DESC", (book_id,))
        sales = cursor.fetchall()
    except Exception: 
        flash("Could not load sales history.", "error")
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
        
    db = None
    can_read = False
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT id, title, author_id, pdf_file, is_paid, private_pdf, preview_pages FROM books WHERE id = %s', (book_id,))
        book = cursor.fetchone()
        if not book: 
            abort(404)
            
        can_read = not book['is_paid'] or session.get('user_id') == book['author_id'] or session.get('role') == 'developer'
        
        if book['is_paid'] and not can_read and session.get('user_id'):
            cursor.execute("SELECT id FROM purchases WHERE user_id = %s AND book_id = %s AND status = 'paid'", (session['user_id'], book_id))
            can_read = bool(cursor.fetchone())
    except Exception: 
        flash("Database error.", "error")
        return redirect(url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass
            
    return render_template('viewer.html', book=book, can_read=can_read)

@app.route('/serve_secure_pdf/<int:book_id>')
def serve_secure_pdf(book_id):
    if 'user_id' not in session: 
        abort(401)
        
    db = None
    book = None
    can_read = False
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute('SELECT author_id, pdf_file, is_paid, private_pdf, preview_pages FROM books WHERE id = %s', (book_id,))
        book = cursor.fetchone()
        if not book: 
            abort(404)
            
        user_id = session.get('user_id')
        user_role = session.get('role')
        can_read = not book['is_paid'] or user_id == book['author_id'] or user_role == 'developer'
        
        if book['is_paid'] and not can_read and user_id: 
            cursor.execute("SELECT id FROM purchases WHERE user_id = %s AND book_id = %s AND status = 'paid'", (user_id, book_id))
            can_read = bool(cursor.fetchone())
    except Exception: 
        abort(500)
    finally:
        if db:
            try: db.close()
            except: pass

    if book['pdf_file'].startswith('http'): 
        abort(400)
        
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
    if 'user_id' not in session: 
        flash("Please sign in or register first.", "error")
        return redirect(url_for('login'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT IGNORE INTO personal_library (user_id, book_id) VALUES (%s, %s)", (session['user_id'], book_id))
        db.commit()
        flash("Book saved to My Library!", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(request.referrer or url_for('index'))

@app.route('/my-library')
def my_library():
    if 'user_id' not in session: 
        flash("Please log in.", "error")
        return redirect(url_for('login'))
        
    db = None
    saved_books = []
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        role = session.get('role')
        
        if role == 'author': 
            cursor.execute("SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, books.description, users.username as author_name, users.role as author_role FROM books JOIN users ON books.author_id = users.id WHERE books.author_id = %s ORDER BY books.created_at DESC", (session['user_id'],))
        elif role in ['official', 'developer']: 
            cursor.execute("SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, books.description, users.username as author_name, users.role as author_role FROM books JOIN users ON books.author_id = users.id ORDER BY books.created_at DESC")
        else: 
            cursor.execute("SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, books.description, users.username as author_name, users.role as author_role FROM personal_library JOIN books ON personal_library.book_id = books.id JOIN users ON books.author_id = users.id WHERE personal_library.user_id = %s ORDER BY personal_library.added_at DESC", (session['user_id'],))
            
        saved_books = clean_book_data(cursor.fetchall())
    except Exception: 
        flash("Database error.", "error")
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
    if 'user_id' not in session: 
        return redirect(url_for('login'))
        
    db = None
    show_delete_otp_form = session.get('show_delete_otp_form', False)
    
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
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

        if request.method == 'POST' and 'title' in request.form:
            catalog = request.form.get('catalog', '')
            if role == 'author':
                cursor.execute("SELECT is_verified FROM users WHERE id = %s", (session['user_id'],))
                if not cursor.fetchone()['is_verified']:
                    flash("Must be verified to publish.", "error")
                    return redirect(url_for('dashboard'))
                if catalog.lower() == 'archives':
                    flash("Cannot publish to Archives.", "error")
                    return redirect(url_for('dashboard'))

            description = request.form.get('description', '').strip()
            c_link = request.form.get('cover_link', '').strip()
            p_link = request.form.get('pdf_link', '').strip()
            c_file = request.files.get('cover_image')
            p_file = request.files.get('pdf_file')
            is_paid = request.form.get('is_paid') == 'on'

            if catalog.lower() == 'archives':
                is_paid = False

            try:
                price_paise = int((Decimal(request.form.get('price_inr', '0').strip() or '0') * 100).quantize(Decimal('1')))
            except (InvalidOperation, ValueError):
                price_paise = -1

            raw_preview = int(request.form.get('preview_pages', 5) or 5)
            preview_pages = min(max(1, raw_preview), 10)

            if is_paid and price_paise <= 0:
                flash('Paid books need a valid price.', 'error')
                return redirect(url_for('dashboard'))

            book_key_id = request.form.get('rp_key_id', '').strip() if is_paid else None
            book_key_secret = request.form.get('rp_key_secret', '').strip() if is_paid else None

            f_cov = c_link if c_link else ""
            if c_file and c_file.filename and not c_link:
                f_cov = compress_cover_image(c_file, app.config['UPLOAD_FOLDER'])

            f_pdf = p_link if p_link else (secure_filename(p_file.filename) if p_file and p_file.filename else "")
            if p_file and not p_link:
                pdf_folder = app.config['PRIVATE_PDF_FOLDER'] if is_paid else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs')
                p_file.save(os.path.join(pdf_folder, f_pdf))

            if f_cov and f_pdf:
                cursor.execute("INSERT INTO books (title, author_id, catalog, cover_image, pdf_file, is_paid, price_paise, private_pdf, preview_pages, rp_key_id, rp_key_secret, description) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (request.form['title'], session['user_id'], request.form['catalog'], f_cov, f_pdf, is_paid, price_paise if is_paid else 0, is_paid, preview_pages, book_key_id, book_key_secret, description))
                db.commit()
                flash("Book published successfully!", "success")
                return redirect(url_for('dashboard'))

        if role in ['developer', 'official'] and request.method == 'POST':
            if 'approve_author_id' in request.form: 
                auth_id = request.form['approve_author_id']
                cursor.execute("UPDATE users SET is_verified = TRUE WHERE id = %s", (auth_id,))
                db.commit()
                cursor.execute("SELECT username, email, role FROM users WHERE id = %s", (auth_id,))
                author_data = cursor.fetchone()
                
                if author_data: 
                    send_approved_author(author_data['email'], author_data['username'])
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
                
                if role == 'official': 
                    log_official_activity(session['user_id'], f"Rejected & deleted author: {author_name}. Reason: {reason}")
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
            
            cursor.execute("SELECT bdr.id, b.title as book_title, u.username as author_name, o.username as official_name, bdr.reason FROM book_deletion_requests bdr JOIN books b ON bdr.book_id = b.id JOIN users u ON b.author_id = u.id JOIN users o ON bdr.requested_by = o.id WHERE bdr.status = 'pending'")
            book_del_requests = cursor.fetchall()
            
            cursor.execute("SELECT id, username, email, verification_reason, last_activity FROM users WHERE role = 'author' AND is_verified = FALSE")
            pending_authors = cursor.fetchall()
            
            cursor.execute("SELECT oa.action, oa.timestamp, u.username FROM official_activities oa JOIN users u ON oa.official_id = u.id WHERE oa.timestamp >= NOW() - INTERVAL 30 DAY ORDER BY oa.timestamp DESC LIMIT 200")
            official_logs = cursor.fetchall()
            
            cursor.execute("SELECT books.id, books.title, books.catalog, books.cover_image, books.pdf_file, books.is_paid, books.price_paise, books.private_pdf, books.description, books.rp_key_id, books.rp_key_secret, users.username as author_name, users.role as author_role FROM books JOIN users ON books.author_id = users.id WHERE books.catalog = 'Archives' ORDER BY books.created_at DESC")
            archive_books = clean_book_data(cursor.fetchall())
            
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file, preview_pages, rp_key_id, rp_key_secret, description FROM books WHERE author_id = %s", (session['user_id'],))
            my_books = clean_book_data(cursor.fetchall())
            
            return render_template('dashboard.html', archive_books=archive_books, searched_users=searched_users, del_requests=del_requests, book_del_requests=book_del_requests, search_query=search_query, pending_authors=pending_authors, official_logs=official_logs, my_books=my_books, username_requests=username_requests, show_delete_otp_form=show_delete_otp_form, two_factor_enabled=two_factor_enabled)

        if role == 'official':
            if search_query: 
                cursor.execute("SELECT id, username, email, role, last_activity FROM users WHERE role IN ('reader', 'author') AND (username LIKE %s OR email LIKE %s)", (f"%{search_query}%", f"%{search_query}%"))
            else: 
                cursor.execute("SELECT id, username, email, role, last_activity FROM users WHERE role IN ('reader', 'author') ORDER BY last_activity DESC")
                
            all_users = cursor.fetchall()
            
            cursor.execute("SELECT id, username, email, verification_reason, last_activity FROM users WHERE role = 'author' AND is_verified = FALSE")
            pending_authors = cursor.fetchall()
            
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file, preview_pages, rp_key_id, rp_key_secret, description FROM books WHERE author_id = %s", (session['user_id'],))
            my_books = clean_book_data(cursor.fetchall())
            
            return render_template('dashboard.html', pending_authors=pending_authors, all_users=all_users, search_query=search_query, my_books=my_books, username_requests=username_requests, show_delete_otp_form=show_delete_otp_form, two_factor_enabled=two_factor_enabled)

        if role == 'author':
            cursor.execute("SELECT is_verified FROM users WHERE id = %s", (session['user_id'],))
            author_data = cursor.fetchone()
            session['is_verified'] = author_data['is_verified']
            
            cursor.execute("SELECT id, title, catalog, is_paid, price_paise, cover_image, pdf_file, preview_pages, rp_key_id, rp_key_secret, description FROM books WHERE author_id = %s", (session['user_id'],))
            my_books = clean_book_data(cursor.fetchall())
            
            return render_template('dashboard.html', my_books=my_books, show_delete_otp_form=show_delete_otp_form, two_factor_enabled=two_factor_enabled)

        return render_template('dashboard.html', show_delete_otp_form=show_delete_otp_form, two_factor_enabled=two_factor_enabled)
        
    except Exception as e:
        flash(f"System Notice: Database schema updating. {str(e)}", "error")
        return redirect(url_for('index'))
    finally:
        if db:
            try: db.close()
            except: pass

@app.route('/edit_book/<int:book_id>', methods=['POST'])
def edit_book(book_id):
    if 'user_id' not in session: 
        return redirect(url_for('login'))
        
    db = None
    try:
        role = session.get('role')
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM books WHERE id = %s", (book_id,))
        book = cursor.fetchone()
        
        if not book: 
            flash("Book not found.", "error")
            return redirect(url_for('dashboard'))
            
        if book['author_id'] != session['user_id'] and role not in ['official', 'developer']: 
            flash("Unauthorized.", "error")
            return redirect(url_for('dashboard'))
            
        title = request.form.get('title', book['title'])
        catalog = request.form.get('catalog', book['catalog'])
        description = request.form.get('description', '').strip()
        is_paid = request.form.get('is_paid') == 'on'
        
        if catalog.lower() == 'archives': 
            is_paid = False
        
        try: 
            price_paise = int((Decimal(request.form.get('price_inr', '0').strip() or '0') * 100).quantize(Decimal('1')))
        except (InvalidOperation, ValueError): 
            price_paise = book['price_paise'] if is_paid else 0
            
        raw_preview = int(request.form.get('preview_pages', book.get('preview_pages', 5)) or 5)
        preview_pages = min(max(1, raw_preview), 10)
        
        c_link = request.form.get('cover_link', '').strip()
        p_link = request.form.get('pdf_link', '').strip()
        c_file = request.files.get('cover_image')
        p_file = request.files.get('pdf_file')
        
        book_key_id = request.form.get('rp_key_id', '').strip() if is_paid else None
        book_key_secret = request.form.get('rp_key_secret', '').strip() if is_paid else None
        
        f_cov = book['cover_image']
        if c_link: 
            f_cov = c_link
        elif c_file and c_file.filename: 
            f_cov = compress_cover_image(c_file, app.config['UPLOAD_FOLDER'])
            
        f_pdf = book['pdf_file']
        if p_link: 
            f_pdf = p_link
        elif p_file and p_file.filename:
            f_pdf = secure_filename(p_file.filename)
            pdf_folder = app.config['PRIVATE_PDF_FOLDER'] if is_paid else os.path.join(app.config['UPLOAD_FOLDER'], 'pdfs')
            p_file.save(os.path.join(pdf_folder, f_pdf))
            
        cursor.execute("UPDATE books SET title=%s, catalog=%s, cover_image=%s, pdf_file=%s, is_paid=%s, price_paise=%s, private_pdf=%s, preview_pages=%s, rp_key_id=%s, rp_key_secret=%s, description=%s WHERE id=%s", (title, catalog, f_cov, f_pdf, is_paid, price_paise if is_paid else 0, is_paid, preview_pages, book_key_id, book_key_secret, description, book_id))
        db.commit()
        flash("Book updated!", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/update_front_page', methods=['POST'])
def update_front_page():
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    title = request.form.get('hero_title')
    subtitle = request.form.get('hero_subtitle')
    font_color = request.form.get('font_color')
    logo_file = request.files.get('logo_image')
    donation_active = request.form.get('donation_active') == 'on'
    donation_qr_file = request.files.get('donation_qr')
    rp_key_id = request.form.get('rp_key_id', '').strip()
    rp_key_secret = request.form.get('rp_key_secret', '').strip()
    
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT logo_image, donation_qr, rp_key_id, rp_key_secret FROM front_page_settings WHERE id=1")
        settings_data = cursor.fetchone()
        
        final_logo = settings_data['logo_image']
        final_qr = settings_data['donation_qr']
        
        if logo_file and logo_file.filename: 
            final_logo = secure_filename(logo_file.filename)
            logo_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'logos', final_logo))
            
        if donation_qr_file and donation_qr_file.filename: 
            final_qr = secure_filename(donation_qr_file.filename)
            donation_qr_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'logos', final_qr))
        
        final_rp_id = rp_key_id if rp_key_id else settings_data.get('rp_key_id', '')
        final_rp_secret = rp_key_secret if rp_key_secret else settings_data.get('rp_key_secret', '')
        
        cursor.execute("UPDATE front_page_settings SET hero_title=%s, hero_subtitle=%s, font_color=%s, logo_image=%s, donation_active=%s, donation_qr=%s, rp_key_id=%s, rp_key_secret=%s WHERE id=1", (title, subtitle, font_color, final_logo, donation_active, final_qr, final_rp_id, final_rp_secret))
        db.commit()
        invalidate_cache()
        flash("Platform settings updated!", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/add_catalog', methods=['POST'])
def add_catalog():
    if session.get('role') not in ['developer', 'official']: 
        return redirect(url_for('dashboard'))
        
    new_catalog = request.form['catalog_name'].strip()
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT IGNORE INTO catalogs (name) VALUES (%s)", (new_catalog,))
        db.commit()
        invalidate_cache()
        flash(f"Catalog '{new_catalog}' added!", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/delete_catalog/<int:cat_id>', methods=['POST'])
def delete_catalog(cat_id):
    if session.get('role') not in ['developer', 'official']: 
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM catalogs WHERE id = %s", (cat_id,))
        db.commit()
        invalidate_cache()
        flash("Catalog removed.", "success") 
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/create_official', methods=['POST'])
def create_official():
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        raw_password = request.form['password']
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("INSERT IGNORE INTO users (username, email, password_hash, role, is_verified, security_question, security_answer) VALUES (%s, %s, %s, 'official', TRUE, 'Dev', 'Dev')", (request.form['username'], request.form['email'], generate_password_hash(raw_password)))
        db.commit()
        send_official_welcome(request.form['email'], request.form['username'], raw_password)
        flash("Official created and welcome email sent!", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/revoke_official/<int:user_id>', methods=['POST'])
def revoke_official(user_id):
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    reason = request.form.get('reason', 'Administrative decision.')
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT username, email FROM users WHERE id = %s AND role = 'official'", (user_id,))
        user_data = cursor.fetchone()
        
        cursor.execute("UPDATE users SET role = 'reader' WHERE id = %s AND role = 'official'", (user_id,))
        db.commit()
        
        if user_data: 
            send_revoked_official_email(user_data['email'], user_data['username'], reason)
        flash("Official privileges revoked and email sent.", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/request_deletion/<int:user_id>', methods=['POST'])
def request_deletion(user_id):
    if session.get('role') != 'official': 
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("INSERT INTO deletion_requests (target_user_id, requested_by, reason) VALUES (%s, %s, %s)", (user_id, session['user_id'], request.form['reason']))
        db.commit()
        
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        target_name = cursor.fetchone()['username'] if cursor.rowcount > 0 else "Unknown"
        
        log_official_activity(session['user_id'], f"Requested deletion of user: {target_name}")
        flash("Deletion request sent.", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/handle_deletion/<int:req_id>/<action>', methods=['POST'])
def handle_deletion(req_id, action):
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
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
    except Exception as e: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/admin_delete_user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    reason = request.form.get('reason', 'Violation of platform terms.')
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT username, email FROM users WHERE id = %s", (user_id,))
        user_data = cursor.fetchone()
        
        tables = ['personal_library', 'interactions', 'books', 'users']
        for table in tables: 
            column = 'author_id' if table == 'books' else ('id' if table == 'users' else 'user_id')
            cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (user_id,))
            
        db.commit()
        if user_data: 
            send_account_deleted_email(user_data['email'], user_data['username'], reason)
        flash("User deleted and notification email sent.", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/request_book_deletion/<int:book_id>', methods=['POST'])
def request_book_deletion(book_id):
    if session.get('role') != 'official': 
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        reason = request.form.get('reason', 'Violates guidelines.')
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("INSERT INTO book_deletion_requests (book_id, requested_by, reason) VALUES (%s, %s, %s)", (book_id, session['user_id'], reason))
        db.commit()
        
        cursor.execute("SELECT title FROM books WHERE id = %s", (book_id,))
        book_title = cursor.fetchone()['title']
        log_official_activity(session['user_id'], f"Requested deletion of book: '{book_title}'")
        flash("Book deletion request sent to the Developer.", "success")
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/handle_book_deletion/<int:req_id>/<action>', methods=['POST'])
def handle_book_deletion(req_id, action):
    if session.get('role') != 'developer': 
        return redirect(url_for('dashboard'))
        
    db = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
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
    except Exception: 
        flash("Database error.", "error")
    finally:
        if db:
            try: db.close()
            except: pass
    return redirect(url_for('dashboard'))

@app.route('/delete_book/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    role = session.get('role')
    user_id = session.get('user_id')
    
    if role in ['author', 'developer']:
        db = None
        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)
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
        except Exception: 
            flash("Database error.", "error")
        finally:
            if db:
                try: db.close()
                except: pass
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    ensure_payment_schema()
    create_master_developer()
    app.run(debug=True)
