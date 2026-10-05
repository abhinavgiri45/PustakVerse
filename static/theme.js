/**
 * PustakVerse Universal System & Manual Theme Engine
 * Controls Light, Dark, and System (Auto) modes with zero flicker (FOUC).
 */
(function() {
    function getStoredTheme() {
        return localStorage.getItem('pustakverse_theme') || 'system';
    }

    function updateToggleButtons(themeName, isDark) {
        document.querySelectorAll('.theme-toggle, .theme-toggle-pill, #globalThemeToggleBtn, #themeToggle').forEach(btn => {
            if (themeName === 'system') {
                btn.innerHTML = isDark ? '💻 Auto (Dark)' : '💻 Auto (Light)';
                btn.title = 'Current Theme: System Auto. Click to toggle Dark / Light.';
            } else if (themeName === 'dark') {
                btn.innerHTML = '🌙 Dark';
                btn.title = 'Current Theme: Dark Mode. Click to toggle Light Mode.';
            } else {
                btn.innerHTML = '☀️ Light';
                btn.title = 'Current Theme: Light Mode. Click to toggle Auto Mode.';
            }
        });
    }

    function applyTheme(themeName) {
        if (!themeName) themeName = getStoredTheme();
        const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        let isDark = false;

        if (themeName === 'dark') {
            isDark = true;
        } else if (themeName === 'light') {
            isDark = false;
        } else {
            themeName = 'system';
            isDark = prefersDark;
        }

        const doc = document.documentElement;
        const body = document.body;

        if (isDark) {
            doc.classList.add('dark-theme');
            doc.classList.remove('light-theme', 'sepia-theme');
            if (body) {
                body.classList.add('dark-theme');
                body.classList.remove('light-theme', 'sepia-theme');
            }
        } else {
            doc.classList.add('light-theme');
            doc.classList.remove('dark-theme', 'sepia-theme');
            if (body) {
                body.classList.add('light-theme');
                body.classList.remove('dark-theme', 'sepia-theme');
            }
        }

        if (themeName === 'system') {
            localStorage.setItem('pustakverse_theme', 'system');
        } else {
            localStorage.setItem('pustakverse_theme', themeName);
        }

        updateToggleButtons(themeName, isDark);
    }

    function toggleTheme() {
        const cur = getStoredTheme();
        let next = 'dark';
        if (cur === 'dark') next = 'light';
        else if (cur === 'light') next = 'system';
        else next = 'dark';
        applyTheme(next);
    }

    window.applyTheme = applyTheme;
    window.toggleTheme = toggleTheme;
    window.applySystemTheme = function() { applyTheme(getStoredTheme()); };

    // Execute immediately to prevent FOUC / flickering
    applyTheme(getStoredTheme());

    // Listen for OS scheme adjustments
    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
            if (getStoredTheme() === 'system') {
                applyTheme('system');
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            applyTheme(getStoredTheme());
        });
    } else {
        applyTheme(getStoredTheme());
    }
})();
