"""
Cloudflare D1 Database Adapter for PustakVerse (DB-API 2.0 Compatible)
Connects Python Flask directly to Cloudflare D1 Serverless SQL via Cloudflare REST API.
Automatically translates MySQL syntax to SQLite for full compatibility.
"""

import os
import re
import json
import logging
import datetime
import requests

logger = logging.getLogger("cloudflare_d1")

class D1Error(Exception):
    pass

class IntegrityError(D1Error):
    pass

class D1Cursor:
    def __init__(self, connection, dictionary=True):
        self.connection = connection
        self.dictionary = dictionary
        self._results = []
        self._index = 0
        self.lastrowid = None
        self.rowcount = 0
        self.description = None

    def _convert_sql(self, sql):
        """Translate MySQL SQL to SQLite-compatible SQL for Cloudflare D1."""
        converted = sql.strip()

        # ── SHOW COLUMNS FROM <table> LIKE '<col>' ──
        show_cols_match = re.search(
            r"SHOW\s+COLUMNS\s+FROM\s+([`\w]+)\s+LIKE\s+(['\"][\w%]+['\"])",
            converted, re.IGNORECASE
        )
        if show_cols_match:
            table = show_cols_match.group(1).replace('`', '')
            col = show_cols_match.group(2).strip("'\"")
            return f"SELECT name FROM pragma_table_info('{table}') WHERE name = '{col}'", []

        # ── Skip ALTER TABLE ... MODIFY COLUMN (not supported in SQLite) ──
        if re.search(r"\bALTER\s+TABLE\s+\S+\s+MODIFY\s+COLUMN\b", converted, re.IGNORECASE):
            return "SELECT 1", []

        # ── ALTER TABLE ... ADD COLUMN: clean up MySQL types ──
        alter_match = re.search(r"\bALTER\s+TABLE\s+(\S+)\s+ADD\s+COLUMN\s+(\S+)\s+(.*)", converted, re.IGNORECASE)
        if alter_match:
            col_def = alter_match.group(3)
            col_def = self._convert_column_type(col_def)
            converted = f"ALTER TABLE {alter_match.group(1)} ADD COLUMN {alter_match.group(2)} {col_def}"

        # ── CREATE TABLE: convert MySQL DDL to SQLite ──
        if re.match(r"\s*CREATE\s+TABLE", converted, re.IGNORECASE):
            converted = self._convert_create_table(converted)

        # ── INSERT IGNORE INTO → INSERT OR IGNORE INTO ──
        converted = re.sub(r"\bINSERT\s+IGNORE\s+INTO\b", "INSERT OR IGNORE INTO", converted, flags=re.IGNORECASE)

        # ── LAST_INSERT_ID() → last_insert_rowid() ──
        converted = re.sub(r"LAST_INSERT_ID\(\)", "last_insert_rowid()", converted, flags=re.IGNORECASE)

        # ── DATE_SUB(NOW(), INTERVAL N HOUR) → datetime('now', '-N hours') ──
        converted = re.sub(
            r"DATE_SUB\s*\(\s*NOW\s*\(\s*\)\s*,\s*INTERVAL\s+(\d+)\s+HOUR\s*\)",
            lambda m: f"datetime('now', '-{m.group(1)} hours')",
            converted, flags=re.IGNORECASE
        )

        # ── DATE_SUB(NOW(), INTERVAL N DAY) → datetime('now', '-N days') ──
        converted = re.sub(
            r"DATE_SUB\s*\(\s*NOW\s*\(\s*\)\s*,\s*INTERVAL\s+(\d+)\s+DAY\s*\)",
            lambda m: f"datetime('now', '-{m.group(1)} days')",
            converted, flags=re.IGNORECASE
        )

        # ── NOW() → datetime('now') ──
        converted = re.sub(r"\bNOW\s*\(\s*\)", "datetime('now')", converted, flags=re.IGNORECASE)

        # ── CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP → CURRENT_TIMESTAMP ──
        converted = re.sub(
            r"CURRENT_TIMESTAMP\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP",
            "CURRENT_TIMESTAMP", converted, flags=re.IGNORECASE
        )

        # ── <=> (NULL-safe equality) → IS ──
        converted = converted.replace('<=>', 'IS')

        # ── Parameter placeholders: %s → ? ──
        converted = re.sub(r"%s", "?", converted)

        return converted, None

    def _convert_column_type(self, col_def):
        """Convert a MySQL column type definition to SQLite-compatible."""
        # ENUM('a','b','c') → TEXT
        col_def = re.sub(r"ENUM\s*\([^)]+\)", "TEXT", col_def, flags=re.IGNORECASE)
        # BOOLEAN → INTEGER
        col_def = re.sub(r"\bBOOLEAN\b", "INTEGER", col_def, flags=re.IGNORECASE)
        # VARCHAR(N) → TEXT
        col_def = re.sub(r"\bVARCHAR\s*\(\d+\)", "TEXT", col_def, flags=re.IGNORECASE)
        # INT AUTO_INCREMENT → INTEGER
        col_def = re.sub(r"\bINT\b\s*\bAUTO_INCREMENT\b", "INTEGER", col_def, flags=re.IGNORECASE)
        col_def = re.sub(r"\bAUTO_INCREMENT\b", "", col_def, flags=re.IGNORECASE)
        # FLOAT → REAL
        col_def = re.sub(r"\bFLOAT\b", "REAL", col_def, flags=re.IGNORECASE)
        # ON UPDATE CURRENT_TIMESTAMP → remove
        col_def = re.sub(r"\bON\s+UPDATE\s+CURRENT_TIMESTAMP\b", "", col_def, flags=re.IGNORECASE)
        return col_def.strip()

    def _convert_create_table(self, sql):
        """Convert a MySQL CREATE TABLE statement to SQLite-compatible DDL."""
        converted = sql

        # ENUM('a','b','c') → TEXT
        converted = re.sub(r"ENUM\s*\([^)]+\)", "TEXT", converted, flags=re.IGNORECASE)

        # BOOLEAN → INTEGER
        converted = re.sub(r"\bBOOLEAN\b", "INTEGER", converted, flags=re.IGNORECASE)

        # INT AUTO_INCREMENT PRIMARY KEY → INTEGER PRIMARY KEY AUTOINCREMENT
        converted = re.sub(
            r"\bINT\s+AUTO_INCREMENT\s+PRIMARY\s+KEY\b",
            "INTEGER PRIMARY KEY AUTOINCREMENT",
            converted, flags=re.IGNORECASE
        )

        # Remove standalone AUTO_INCREMENT
        converted = re.sub(r"\bAUTO_INCREMENT\b", "", converted, flags=re.IGNORECASE)

        # UNIQUE KEY name (cols) → UNIQUE(cols)
        converted = re.sub(
            r",?\s*UNIQUE\s+KEY\s+\w+\s*\(([^)]+)\)",
            r", UNIQUE(\1)",
            converted, flags=re.IGNORECASE
        )

        # Remove INDEX declarations (not supported inline in SQLite CREATE TABLE)
        converted = re.sub(
            r",?\s*INDEX\s+\w+\s*\([^)]+\)",
            "",
            converted, flags=re.IGNORECASE
        )

        # Remove KEY declarations (MySQL non-unique index)
        converted = re.sub(
            r",?\s*KEY\s+\w+\s*\([^)]+\)",
            "",
            converted, flags=re.IGNORECASE
        )

        # VARCHAR(N) → TEXT (optional, D1 doesn't strictly need this but safer)
        # Keep VARCHAR as D1/SQLite handles it fine, but convert for consistency
        converted = re.sub(r"\bVARCHAR\s*\(\d+\)", "TEXT", converted, flags=re.IGNORECASE)

        # ON UPDATE CURRENT_TIMESTAMP → remove (not supported in SQLite)
        converted = re.sub(
            r"\bCURRENT_TIMESTAMP\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP\b",
            "CURRENT_TIMESTAMP",
            converted, flags=re.IGNORECASE
        )

        # Clean up any trailing commas before closing paren
        converted = re.sub(r",\s*\)", ")", converted)

        return converted

    def execute(self, sql, params=None):
        sql, alt_params = self._convert_sql(sql)
        if alt_params is not None:
            params = alt_params

        param_list = []
        if params is not None:
            if isinstance(params, (list, tuple)):
                for p in params:
                    if isinstance(p, bool):
                        param_list.append(1 if p else 0)
                    elif hasattr(p, 'strftime'):
                        param_list.append(p.strftime('%Y-%m-%d %H:%M:%S'))
                    elif isinstance(p, (Decimal_type,)):
                        param_list.append(float(p))
                    else:
                        param_list.append(p)
            elif isinstance(params, dict):
                for p in params.values():
                    if isinstance(p, bool):
                        param_list.append(1 if p else 0)
                    elif hasattr(p, 'strftime'):
                        param_list.append(p.strftime('%Y-%m-%d %H:%M:%S'))
                    else:
                        param_list.append(p)
            else:
                param_list = [params]

        payload = {
            "sql": sql.strip().rstrip(';'),
            "params": param_list
        }

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.connection.account_id}/d1/database/{self.connection.database_id}/query"
        headers = {
            "Authorization": f"Bearer {self.connection.api_token}",
            "Content-Type": "application/json"
        }

        try:
            res = requests.post(url, json=payload, headers=headers, timeout=self.connection.timeout)
            data = res.json()

            if not data.get("success"):
                errors = data.get("errors", [])
                err_msg = errors[0].get("message", "Unknown D1 error") if errors else str(data)
                logger.error(f"[D1 Query Error] {err_msg} | SQL: {sql[:200]}")
                if "UNIQUE constraint failed" in err_msg or "constraint failed" in err_msg.lower():
                    raise IntegrityError(f"Duplicate entry: {err_msg}")
                # Silently ignore "duplicate column" errors during ALTER TABLE ADD COLUMN
                if "duplicate column name" in err_msg.lower():
                    self._results = []
                    self._index = 0
                    self.rowcount = 0
                    return self
                raise D1Error(f"Cloudflare D1 Error: {err_msg}")

            result_obj = data.get("result", [{}])[0]
            raw_results = result_obj.get("results", [])
            meta = result_obj.get("meta", {})

            self._results = raw_results if self.dictionary else [list(r.values()) for r in raw_results]
            self._index = 0
            self.rowcount = meta.get("changes", len(self._results))
            self.lastrowid = meta.get("last_row_id") or meta.get("lastrowid") or meta.get("last_insert_rowid")

            if raw_results:
                cols = list(raw_results[0].keys())
                self.description = [(c, None, None, None, None, None, None) for c in cols]
            else:
                self.description = None

            return self
        except requests.RequestException as e:
            logger.error(f"[D1 HTTP Request Error] {e}")
            raise D1Error(f"Cloudflare D1 Connection Error: {e}")

    def fetchone(self):
        if self._index < len(self._results):
            row = self._results[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self):
        rows = self._results[self._index:]
        self._index = len(self._results)
        return rows

    def fetchmany(self, size=None):
        if size is None:
            size = 1
        rows = self._results[self._index:self._index + size]
        self._index += len(rows)
        return rows

    def close(self):
        self._results = []

class D1Connection:
    def __init__(self, account_id, database_id, api_token, timeout=15):
        self.account_id = account_id
        self.database_id = database_id
        self.api_token = api_token
        self.timeout = timeout

    def cursor(self, dictionary=True, buffered=True):
        return D1Cursor(self, dictionary=dictionary)

    def commit(self):
        pass  # Cloudflare D1 auto-commits each statement

    def rollback(self):
        pass

    def close(self):
        pass

    def is_connected(self):
        return bool(self.account_id and self.database_id and self.api_token)

def get_d1_connection():
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID")
    database_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID") or os.environ.get("CF_DATABASE_ID") or os.environ.get("D1_DATABASE_ID")
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CF_API_TOKEN")

    if account_id and database_id and api_token:
        return D1Connection(account_id, database_id, api_token)
    raise D1Error("Cloudflare D1 credentials not configured. Set CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_D1_DATABASE_ID, and CLOUDFLARE_API_TOKEN environment variables.")


# Handle Decimal import for param conversion
try:
    from decimal import Decimal as Decimal_type
except ImportError:
    Decimal_type = type(None)
