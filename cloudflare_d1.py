"""
Cloudflare D1 Database Adapter for PustakVerse (DB-API 2.0 Compatible)
Connects Python Flask directly to Cloudflare D1 Serverless SQL via Cloudflare REST API.
Automatically captures and persists all new rows created via the website interface.
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
        converted = sql
        
        # 1. SHOW COLUMNS FROM <table> LIKE '<col>' -> SELECT name FROM pragma_table_info('<table>') WHERE name LIKE '<col>'
        show_cols_match = re.search(r"SHOW\s+COLUMNS\s+FROM\s+([`\w]+)\s+LIKE\s+(['\"]\w+['\"])", converted, re.IGNORECASE)
        if show_cols_match:
            table = show_cols_match.group(1).replace('`', '')
            col = show_cols_match.group(2)
            return f"SELECT name FROM pragma_table_info('{table}') WHERE name LIKE {col}", []

        # 2. INSERT IGNORE INTO -> INSERT OR IGNORE INTO
        converted = re.sub(r"\bINSERT\s+IGNORE\s+INTO\b", "INSERT OR IGNORE INTO", converted, flags=re.IGNORECASE)

        # 3. SELECT LAST_INSERT_ID() -> SELECT last_insert_rowid()
        converted = re.sub(r"LAST_INSERT_ID\(\)", "last_insert_rowid()", converted, flags=re.IGNORECASE)

        # 4. Parameter placeholders: replace %s with ?
        converted = re.sub(r"%s", "?", converted)

        return converted, None

    def execute(self, sql, params=None):
        sql, alt_params = self._convert_sql(sql)
        if alt_params:
            params = alt_params

        param_list = []
        if params is not None:
            if isinstance(params, (list, tuple)):
                for p in params:
                    if isinstance(p, bool):
                        param_list.append(1 if p else 0)
                    elif hasattr(p, 'strftime'):
                        param_list.append(p.strftime('%Y-%m-%d %H:%M:%S'))
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
                logger.error(f"[D1 Query Error] {err_msg} | SQL: {sql[:150]}")
                if "UNIQUE constraint failed" in err_msg or "constraint failed" in err_msg.lower():
                    raise IntegrityError(f"Duplicate entry: {err_msg}")
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
    def __init__(self, account_id, database_id, api_token, timeout=12):
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
    return None
