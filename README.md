# PustakVerse 📚✨

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-Flask-black.svg)](https://flask.palletsprojects.com/)
[![Security](https://img.shields.io/badge/security-Two--Step%20Verification-success.svg)](https://github.com/abhinavgiri45/PustakVerse)
[![AI Integration](https://img.shields.io/badge/AI-Google%20Gemini-orange.svg)](https://deepmind.google/technologies/gemini/)
[![Payments](https://img.shields.io/badge/payments-Razorpay%20Direct-blueviolet.svg)](https://razorpay.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**PustakVerse** is a modern, full-featured digital library, publishing platform, and AI-powered study companion. Designed for readers, authors, and educational organizations, PustakVerse combines an immersive reading experience with enterprise-grade security and creator monetization.

---

## 🌟 Key Highlights & Features

### 📖 1. Immersive Digital Reading Experience
- **Interactive In-Browser PDF Reader**: Built-in distraction-free viewer with zoom, width-fitting, and full-screen modes (<kbd>F</kbd> shortcut).
- **Ambient Color Themes**: One-click switching between **🌙 Dark Night**, **📜 Warm Sepia**, and **☀️ Day Light** modes.
- **Reading Progress & Auto-Bookmarks**: Remembers reading sessions and automatically resumes where the reader left off.
- **In-Viewer Notes & Journal**: Slide-out notes drawer to jot down takeaways, chapter summaries, and thoughts that persist continuously in browser storage with 1-click **.txt export**.
- **🎧 Audio Voice Narrator (TTS)**: Web Speech Synthesis integration that reads book synopses aloud with estimated reading time calculations.
- **🔗 QR Code & Instant Book Sharing**: Dynamic QR code generator for instant mobile reading, plus one-tap sharing to WhatsApp, Telegram, and X (Twitter).

---

### 🔐 2. Enterprise-Grade Security Architecture
- **Two-Step Verification**: Email-based one-time security passcodes for logins, with permanent enforcement for administrative users.
- **Universal SMTP Delivery**: Multi-protocol direct SMTP (Port 587 STARTTLS / Port 465 SSL) with RFC anti-spam headers for instant worldwide delivery of OTPs.
- **Dynamic Account Security Health Shield (0–100%)**: Live score evaluating verified emails, Two-Step Verification, password hashing, and recovery question status.
- **Active Device Management & Remote Session Revocation**: Real-time IP address and user-agent tracking with one-click **"Log Out of All Other Devices"**.
- **Magic-Byte File Signature Validation**: Binary inspection for book (`%PDF-`) and image (JPEG, PNG, WebP) uploads, protecting against disguised malicious files.
- **Live Password Strength & Entropy Meters**: Real-time password feedback (*Weak &rarr; Moderate &rarr; Strong &rarr; Optimal*) across Registration, Google Password Setup, and Change Password forms.
- **Brute-Force Attack Prevention**: Automatic 15-minute account lockout after 5 consecutive failed login attempts.

---

### 🌐 3. Google OAuth & Custom Password Authentication
- **Sign in with Google**: One-click verified authentication with Google OAuth 2.0.
- **Custom Password Setup for Google Users**: Allows Google-registered users to set their own master password and security questions for flexible multi-channel login.

---

### 🧠 4. GranthMind™ AI Study Companion
- **Multi-Tier Gemini AI Engine**: Intelligent tutoring, Socratic dialogue, and instant multi-perspective book answers.
- **5 Specialized Study Modes**: *⚡ Fast Answer*, *📝 Chapter Summary*, *🃏 Flashcards & Quizzes*, *💡 Explain Simply (ELI5)*, and *🔬 Deep Breakdown*.
- **KaTeX Mathematical Derivations & Code Explanations**: Rich inline and displayed LaTeX math formatting for complex technical subjects.
- **Text-to-Speech Voice Narration & Study Notes Export**: Listen to GranthMind's explanations aloud or export your full study sessions to structured `.txt` notes.

---

### 💰 5. Author & Creator Monetization
- **Direct Razorpay Integration**: Authors can link their Razorpay keys for instant, direct payouts upon book purchases.
- **Live Royalty & Revenue Calculator**: Real-time earnings estimator projecting author income across 25, 100, and 500 sales.
- **Comprehensive Sales History**: Dedicated analytics dashboard for tracking transactions and buyer activity.

---

### 📚 6. Personal Library & Shelves
- **Reading Shelves Manager**: Organize your collection into **📚 All Books**, **📖 Currently Reading**, and **✅ Completed**.
- **Spotlight Search & Voice Recognition**: Quick search with keyboard shortcut (<kbd>Ctrl</kbd> + <kbd>K</kbd> or <kbd>/</kbd>) and speech-to-text voice input.

---

## 🏛️ Platform Roles & Permissions

| Feature / Capability | Reader | Author | Official | Developer |
| :--- | :---: | :---: | :---: | :---: |
| Browse Global Library & Search | ✅ | ✅ | ✅ | ✅ |
| Save Books to Personal Shelves | ✅ | ✅ | ✅ | ✅ |
| In-Viewer Notes, Themes & Bookmarks | ✅ | ✅ | ✅ | ✅ |
| Audio Narrator & QR Sharing | ✅ | ✅ | ✅ | ✅ |
| Write Reviews & Ratings | ✅ | ✅ | ✅ | ✅ |
| Publish Free & Premium Books | ❌ | ✅ | ✅ | ✅ |
| Direct Razorpay Payouts | ❌ | ✅ | ✅ | ✅ |
| Author Application Review | ❌ | ❌ | ✅ | ✅ |
| Content Moderation & Reporting | ❌ | ❌ | ✅ | ✅ |
| Two-Step Verification Toggle | Optional | Optional | Enforced | Enforced |
| Appoint Officials & System Config | ❌ | ❌ | ❌ | ✅ |

---

## 🛠️ Technology Stack

- **Backend**: Python 3.10+, Flask, SQLite (with WAL mode), Werkzeug
- **Frontend**: Semantic HTML5, Vanilla CSS3 (Custom Design System), JavaScript (ES6+)
- **APIs & Integrations**:
  - Google Gemini AI API
  - Google OAuth 2.0
  - Razorpay Payment Gateway
  - Web Speech Synthesis API
  - QR Code Server API
- **Email Delivery**: Python `smtplib` / `email.mime` with TLS/SSL direct routing

---

## 🚀 Quick Start & Installation

### 1. Clone the Repository
```bash
git clone https://github.com/abhinavgiri45/PustakVerse.git
cd PustakVerse
```

### 2. Create and Activate Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the root directory:
```env
FLASK_SECRET_KEY="your-super-secret-key"
EMAIL_ADDRESS="your-email@gmail.com"
EMAIL_PASSWORD="your-gmail-app-password"

# Google OAuth (Optional for Google Sign-In)
GOOGLE_CLIENT_ID="your-google-client-id"
GOOGLE_CLIENT_SECRET="your-google-client-secret"

# Gemini AI (Optional for AI Tutor)
GEMINI_API_KEY="your-gemini-api-key"
```

### 5. Run the Application
```bash
python app.py
```
Open your browser and navigate to: `http://127.0.0.1:5000`

---

## 📁 Directory Structure

```text
PustakVerse/
├── app.py                     # Main application entry point & API controllers
├── database.db                # SQLite platform database
├── requirements.txt           # Python dependencies
├── static/
│   ├── PustakVerse.png        # Official brand logo
│   └── uploads/
│       ├── covers/            # Uploaded book cover images
│       ├── logos/             # Custom site logos
│       └── pdfs/              # Uploaded PDF documents
└── templates/
    ├── ask_ai.html            # AI chat & study companion
    ├── base.html              # Core navigation, search, and layout template
    ├── book.html              # Book detail, TTS audio narrator & QR share modal
    ├── category.html          # Categorized book collections
    ├── checkout.html          # Secure Razorpay checkout
    ├── contact.html           # Feedback & inquiry portal
    ├── dashboard.html         # User, Author & Admin management center
    ├── error.html             # Error handling page
    ├── forgot_password.html   # Password recovery portal
    ├── google_set_password.html # Google sign-up password setup
    ├── index.html             # Global library storefront
    ├── intro.html             # Anime-style onboarding experience
    ├── learn_book.html        # Interactive AI tutor & flashcards
    ├── login.html             # Multi-role login & Two-Step Verification
    ├── my_library.html        # Personal library shelves & progress manager
    ├── payment_history.html   # User transaction history
    ├── register.html          # Account registration
    ├── sales_history.html     # Author revenue & sales analytics
    ├── terms.html             # Terms of service & copyright policies
    └── viewer.html            # Protected PDF reader with themes & notes
```

---

## 📄 License
This project is licensed under the [MIT License](LICENSE).
