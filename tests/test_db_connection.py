"""
=============================================================================
Module:        Test Helper — Database Connection
Location:      tests/test_db_connection.py
Description:   Smoke tests confirming the database is reachable and the
               products table is populated. Uses subprocess + mysql CLI,
               matching how test_luhn.py exports data — no extra drivers
               needed beyond what the OS already has installed.

Environment variables required:
    DB_HOST   — default: localhost
    DB_NAME   — default: trigzi
    DB_USER   — default: trigzi
    DB_PASS   — required
=============================================================================
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _mysql_query(sql: str) -> str:
    """Run a SQL query via the mysql CLI and return stdout."""
    host   = os.environ.get("DB_HOST", "localhost")
    name   = os.environ.get("DB_NAME", "trigzi")
    user   = os.environ.get("DB_USER", "trigzi")
    passwd = os.environ.get("DB_PASS", "")

    result = subprocess.run(
        ["mysql", f"-h{host}", f"-u{user}", f"-p{passwd}", name,
         "--skip-column-names", "-e", sql],
        capture_output=True,
        text=True,
        check=False,   # non-zero return handled by returncode check below
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


class TestDatabaseConnection(unittest.TestCase):
    """Smoke tests confirming the database is reachable and minimally populated."""

    def test_can_connect(self):
        """Verify a basic SELECT 1 reaches the database without error."""
        try:
            _mysql_query("SELECT 1")
        except Exception as e:
            self.fail(f"Database connection failed: {e}")

    def test_products_table_exists(self):
        """Verify the products table exists and contains at least one row."""
        count = _mysql_query("SELECT COUNT(*) FROM products")
        self.assertGreater(int(count), 0, "products table is empty or missing")

    def test_gtin_column_exists(self):
        """Verify the gtin column is present and non-empty."""
        result = _mysql_query("SELECT gtin FROM products LIMIT 1")
        self.assertTrue(result, "gtin column missing or empty")
