-- ==============================================================================
-- PustakVerse - Cloudflare D1 SQLite Database Schema (100% Free Tier Compatible)
-- ==============================================================================

-- 1. Users Table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'reader' CHECK(role IN ('reader', 'author', 'official', 'developer')),
    is_verified INTEGER DEFAULT 0,
    security_question TEXT NOT NULL,
    security_answer TEXT NOT NULL,
    verification_reason TEXT,
    two_factor_enabled INTEGER DEFAULT 0,
    failed_attempts INTEGER DEFAULT 0,
    locked_until DATETIME NULL,
    official_designation TEXT DEFAULT 'Official Moderator',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_activity DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Books Table
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    catalog TEXT NOT NULL,
    cover_image TEXT NOT NULL,
    pdf_file TEXT NOT NULL,
    is_paid INTEGER NOT NULL DEFAULT 0,
    price_paise INTEGER NOT NULL DEFAULT 0,
    private_pdf INTEGER NOT NULL DEFAULT 0,
    preview_pages INTEGER NOT NULL DEFAULT 5,
    rp_key_id TEXT DEFAULT NULL,
    rp_key_secret TEXT DEFAULT NULL,
    rp_verified INTEGER NOT NULL DEFAULT 0,
    rp_verify_message TEXT DEFAULT NULL,
    rp_verified_at DATETIME DEFAULT NULL,
    description TEXT,
    is_quarantined INTEGER NOT NULL DEFAULT 0,
    is_featured INTEGER NOT NULL DEFAULT 0,
    sbin_no TEXT DEFAULT NULL,
    isbn TEXT DEFAULT NULL,
    video_trailer_url TEXT DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. Purchases Table (Razorpay Payments)
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    razorpay_order_id TEXT NOT NULL UNIQUE,
    razorpay_payment_id TEXT UNIQUE,
    amount_paise INTEGER NOT NULL,
    donation_paise INTEGER DEFAULT 0,
    fee_paise INTEGER NOT NULL DEFAULT 0,
    author_earning_paise INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'paid', 'failed', 'refunded')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    paid_at DATETIME NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);

-- 4. Personal Library Table
CREATE TABLE IF NOT EXISTS personal_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    UNIQUE(user_id, book_id)
);

-- 5. Interactions Table (Ratings & Reviews)
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),
    review TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);

-- 6. Front Page Settings
CREATE TABLE IF NOT EXISTS front_page_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hero_title TEXT DEFAULT 'PustakVerse',
    hero_subtitle TEXT DEFAULT 'Every Book. Every Mind. Free.',
    logo_image TEXT DEFAULT 'PustakVerse.png',
    font_color TEXT DEFAULT '#ffffff',
    donation_qr TEXT DEFAULT NULL,
    donation_active INTEGER DEFAULT 0,
    rp_key_id TEXT DEFAULT NULL,
    rp_key_secret TEXT DEFAULT NULL,
    intro_tagline TEXT DEFAULT 'Every Book. Every Mind. Free.',
    intro_sub_tagline TEXT DEFAULT 'Prepare to explore the universe of knowledge...',
    gemini_api_key TEXT DEFAULT NULL,
    checkout_donation_active INTEGER DEFAULT 1,
    donation_default_inr INTEGER DEFAULT 10,
    maintenance_mode INTEGER DEFAULT 0,
    upload_freeze INTEGER DEFAULT 0
);
INSERT OR IGNORE INTO front_page_settings (id) VALUES (1);

-- 7. Catalogs Table
CREATE TABLE IF NOT EXISTS catalogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
INSERT OR IGNORE INTO catalogs (name) VALUES ('Fiction'), ('Non-Fiction'), ('Educational'), ('History'), ('Poetry');

-- 8. Deletion Requests (User Account Deletion)
CREATE TABLE IF NOT EXISTS deletion_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_user_id INTEGER NOT NULL,
    requested_by INTEGER NOT NULL,
    reason TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE CASCADE
);

-- 9. Book Deletion Requests
CREATE TABLE IF NOT EXISTS book_deletion_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    requested_by INTEGER NOT NULL,
    reason TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE CASCADE
);

-- 10. Username Change Requests
CREATE TABLE IF NOT EXISTS username_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    new_username TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 11. Official Activities Audit Log
CREATE TABLE IF NOT EXISTS official_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    official_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (official_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 12. AI Chat Messages History
CREATE TABLE IF NOT EXISTS ai_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    message_text TEXT NOT NULL,
    screenshot TEXT DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);

-- 13. Leadership & Executive Team
CREATE TABLE IF NOT EXISTS leadership_team (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role_title TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT DEFAULT NULL,
    address TEXT DEFAULT NULL,
    photo TEXT DEFAULT 'PustakVerse.png',
    bio TEXT,
    is_founder INTEGER DEFAULT 0,
    display_order INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    instagram_id TEXT DEFAULT NULL,
    x_id TEXT DEFAULT NULL,
    linkedin_id TEXT DEFAULT NULL,
    github_id TEXT DEFAULT NULL,
    website_url TEXT DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 14. Book Custom Badges
CREATE TABLE IF NOT EXISTS book_custom_badges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    badge_label TEXT NOT NULL,
    badge_color TEXT DEFAULT 'gold',
    granted_by INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE CASCADE
);

-- 15. User Granted Licenses (Scholarships / Giveaways)
CREATE TABLE IF NOT EXISTS user_granted_licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    reason TEXT DEFAULT 'Community Contest Winner / Scholarship Access',
    granted_by INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, book_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE CASCADE
);

-- 16. Security Ban List
CREATE TABLE IF NOT EXISTS security_ban_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL CHECK(target_type IN ('ip', 'user_id', 'email')),
    target_value TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    banned_by INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (banned_by) REFERENCES users(id) ON DELETE CASCADE
);

-- 17. Author Coupons
CREATE TABLE IF NOT EXISTS author_coupons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id INTEGER NOT NULL,
    book_id INTEGER NULL,
    code TEXT NOT NULL UNIQUE,
    discount_percent INTEGER NOT NULL DEFAULT 20,
    max_uses INTEGER DEFAULT 100,
    times_used INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 18. Reader Custom Shelves
CREATE TABLE IF NOT EXISTS reader_custom_shelves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    shelf_name TEXT NOT NULL,
    shelf_icon TEXT DEFAULT '📚',
    description TEXT,
    is_public INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 19. Shelf Books (Many-to-Many)
CREATE TABLE IF NOT EXISTS shelf_books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shelf_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(shelf_id, book_id),
    FOREIGN KEY (shelf_id) REFERENCES reader_custom_shelves(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);

-- 20. Reader Reading Goals
CREATE TABLE IF NOT EXISTS reader_reading_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    daily_minutes_goal INTEGER DEFAULT 30,
    monthly_books_goal INTEGER DEFAULT 3,
    total_minutes_read INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 21. Reader Personal Notes & Highlights
CREATE TABLE IF NOT EXISTS user_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    note_text TEXT NOT NULL,
    page_number INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);

-- 22. Reader Multi-Bookmark System
CREATE TABLE IF NOT EXISTS user_bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    page_number INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);

-- 23. Reader Wishlist & Book Requests
CREATE TABLE IF NOT EXISTS book_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    catalog TEXT DEFAULT 'General',
    notes TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'acquired', 'rejected')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 24. AI Models & Provider Gateway
CREATE TABLE IF NOT EXISTS ai_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    model_id TEXT NOT NULL,
    api_key TEXT DEFAULT NULL,
    base_url TEXT DEFAULT NULL,
    temperature REAL DEFAULT 0.3,
    max_tokens INTEGER DEFAULT 2000,
    is_default INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 25. Reading Progress & Completion Tracker
CREATE TABLE IF NOT EXISTS reading_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    current_page INTEGER DEFAULT 1,
    max_page_reached INTEGER DEFAULT 1,
    total_pages INTEGER DEFAULT 1,
    percent_completed REAL DEFAULT 0.0,
    reading_seconds INTEGER DEFAULT 0,
    is_completed INTEGER DEFAULT 0,
    completed_at DATETIME NULL,
    last_read_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, book_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);
