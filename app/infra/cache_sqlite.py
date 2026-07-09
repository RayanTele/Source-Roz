"""
تنفيذ الكاش على SQLite (SQLiteCacheRepository).

مهم: يستخدم مخزناً مستقلاً تماماً (collectibles.db) خاصاً بالخدمة،
وليس store.db الخاص بالبوت. لا يمسّ قاعدة بيانات البوت إطلاقاً.

كل الجداول تُنشأ بـ IF NOT EXISTS (idempotent). لا ALTER على أي جدول قائم.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from app.core.models import CanonicalCollectible
from app.ports.cache_repository import CacheRepository

_ITEM_COLUMNS = [
    "provider", "provider_item_id", "external_id", "title", "collection",
    "model", "backdrop", "symbol", "number", "media_key", "thumbnail_url",
    "price_nano", "is_on_sale", "is_locked", "unlock_date", "raw", "synced_at",
]


class SQLiteCacheRepository(CacheRepository):
    """مستودع كاش مبني على SQLite، آمن للاستخدام عبر خيوط متعدّدة (قفل + اتصال لكل خيط)."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()

    # ── إدارة الاتصال ──
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return conn

    def init_schema(self) -> None:
        with self._lock:
            c = self._conn()
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS collectibles_items (
                    provider         TEXT    NOT NULL,
                    provider_item_id TEXT    NOT NULL,
                    external_id      TEXT    DEFAULT '',
                    title            TEXT    DEFAULT '',
                    collection       TEXT    DEFAULT '',
                    model            TEXT    DEFAULT '',
                    backdrop         TEXT    DEFAULT '',
                    symbol           TEXT    DEFAULT '',
                    number           INTEGER,
                    media_key        TEXT    DEFAULT '',
                    thumbnail_url    TEXT    DEFAULT '',
                    price_nano       INTEGER DEFAULT 0,
                    is_on_sale       INTEGER DEFAULT 0,
                    is_locked        INTEGER DEFAULT 0,
                    unlock_date      TEXT,
                    raw              TEXT    DEFAULT '{}',
                    synced_at        REAL    DEFAULT 0,
                    first_seen       REAL    DEFAULT 0,
                    PRIMARY KEY (provider, provider_item_id)
                );
                CREATE INDEX IF NOT EXISTS idx_items_sale
                    ON collectibles_items (is_on_sale, price_nano);
                CREATE INDEX IF NOT EXISTS idx_items_synced
                    ON collectibles_items (synced_at);

                CREATE TABLE IF NOT EXISTS collectibles_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS collectibles_rates (
                    currency TEXT PRIMARY KEY,
                    rate     REAL NOT NULL,
                    updated_at REAL DEFAULT 0
                );
                """
            )
            # هجرة إضافية آمنة: first_seen للنسخ القديمة من collectibles.db
            existing = {r[1] for r in c.execute("PRAGMA table_info(collectibles_items)")}
            if "first_seen" not in existing:
                c.execute("ALTER TABLE collectibles_items ADD COLUMN first_seen REAL DEFAULT 0")
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_firstseen "
                "ON collectibles_items (first_seen)"
            )
            c.commit()

    # ── عناصر المقتنيات ──
    def upsert_items(self, items: Sequence[CanonicalCollectible]) -> int:
        if not items:
            return 0
        cols = _ITEM_COLUMNS + ["first_seen"]
        placeholders = ",".join("?" for _ in cols)
        # first_seen يُضبط عند الإدراج فقط ولا يُحدَّث لاحقاً
        updates = ",".join(
            f"{col}=excluded.{col}" for col in _ITEM_COLUMNS
            if col not in ("provider", "provider_item_id")
        )
        sql = (
            f"INSERT INTO collectibles_items ({','.join(cols)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(provider, provider_item_id) DO UPDATE SET {updates}"
        )
        rows = []
        for it in items:
            r = it.to_row()
            values = [r[col] for col in _ITEM_COLUMNS]
            values.append(it.synced_at or 0.0)  # first_seen = وقت أول ظهور
            rows.append(tuple(values))
        with self._lock:
            c = self._conn()
            c.executemany(sql, rows)
            c.commit()
        return len(rows)

    def get_item(self, provider: str, provider_item_id: str) -> Optional[CanonicalCollectible]:
        with self._lock:
            c = self._conn()
            row = c.execute(
                "SELECT * FROM collectibles_items WHERE provider=? AND provider_item_id=?",
                (provider, provider_item_id),
            ).fetchone()
        return CanonicalCollectible.from_row(dict(row)) if row else None

    def _build_filter_sql(self, filters: Optional[Dict[str, Any]]):
        clauses = ["is_on_sale=1"]
        params: List[Any] = []
        if filters:
            for col in ("provider", "collection", "model", "backdrop", "symbol"):
                val = filters.get(col)
                if val:
                    clauses.append(f"{col}=?")
                    params.append(val)
            # بحث نصّي: الاسم/المجموعة أو الرقم
            q = filters.get("q")
            if q:
                like = f"%{q}%"
                sub = "(title LIKE ? OR collection LIKE ?"
                params.extend([like, like])
                if str(q).strip().isdigit():
                    sub += " OR number=?"
                    params.append(int(str(q).strip()))
                sub += ")"
                clauses.append(sub)
            # مقفول/غير مقفول
            locked = filters.get("locked")
            if locked is not None:
                clauses.append("is_locked=?")
                params.append(1 if locked else 0)
            # نطاق السعر (بوحدة nano بعد التحويل من عملة العميل)
            pmin = filters.get("price_nano_min")
            if pmin is not None:
                clauses.append("price_nano>=?")
                params.append(int(pmin))
            pmax = filters.get("price_nano_max")
            if pmax is not None:
                clauses.append("price_nano<=?")
                params.append(int(pmax))
        return " AND ".join(clauses), params

    _SORTS = {
        "cheapest": "price_nano ASC, provider_item_id ASC",
        "expensive": "price_nano DESC, provider_item_id ASC",
        "newest": "first_seen DESC, provider_item_id ASC",
        "oldest": "first_seen ASC, provider_item_id ASC",
    }

    def list_for_sale(
        self,
        limit: int = 50,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
    ) -> List[CanonicalCollectible]:
        where, params = self._build_filter_sql(filters)
        order = self._SORTS.get(sort or "newest", self._SORTS["newest"])
        sql = (
            f"SELECT * FROM collectibles_items WHERE {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?"
        )
        with self._lock:
            c = self._conn()
            rows = c.execute(sql, (*params, int(limit), int(offset))).fetchall()
        return [CanonicalCollectible.from_row(dict(r)) for r in rows]

    def distinct_values(self, field: str, provider: Optional[str] = None) -> List[str]:
        """قيم متمايزة لحقل عرض (للفلاتر/التصفّح). محصور بأعمدة مسموحة."""
        if field not in ("collection", "model", "backdrop", "symbol"):
            return []
        clauses = ["is_on_sale=1", f"{field}!=''"]
        params: List[Any] = []
        if provider:
            clauses.append("provider=?")
            params.append(provider)
        sql = (
            f"SELECT DISTINCT {field} AS v FROM collectibles_items "
            f"WHERE {' AND '.join(clauses)} ORDER BY {field} COLLATE NOCASE"
        )
        with self._lock:
            c = self._conn()
            rows = c.execute(sql, tuple(params)).fetchall()
        return [r["v"] for r in rows if r["v"]]

    def count_for_sale(self, filters: Optional[Dict[str, Any]] = None) -> int:
        where, params = self._build_filter_sql(filters)
        with self._lock:
            c = self._conn()
            row = c.execute(
                f"SELECT COUNT(*) AS n FROM collectibles_items WHERE {where}", tuple(params)
            ).fetchone()
        return int(row["n"]) if row else 0

    def prune_stale(self, before_ts: float, provider: Optional[str] = None) -> int:
        with self._lock:
            c = self._conn()
            if provider is None:
                cur = c.execute(
                    "DELETE FROM collectibles_items WHERE synced_at < ?", (float(before_ts),)
                )
            else:
                cur = c.execute(
                    "DELETE FROM collectibles_items WHERE synced_at < ? AND provider = ?",
                    (float(before_ts), provider),
                )
            c.commit()
            return cur.rowcount if cur.rowcount is not None else 0

    def count_items(self, provider: Optional[str] = None) -> int:
        """إجمالي العناصر المخزّنة (اختيارياً لمزوّد) — للصحة."""
        with self._lock:
            c = self._conn()
            if provider is None:
                row = c.execute("SELECT COUNT(*) AS n FROM collectibles_items").fetchone()
            else:
                row = c.execute(
                    "SELECT COUNT(*) AS n FROM collectibles_items WHERE provider=?", (provider,)
                ).fetchone()
        return int(row["n"]) if row else 0

    # ── meta ──
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            c = self._conn()
            row = c.execute(
                "SELECT value FROM collectibles_meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            c = self._conn()
            c.execute(
                "INSERT INTO collectibles_meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            c.commit()

    # ── أسعار الصرف (نسخة قراءة) ──
    def get_rate(self, currency: str) -> Optional[float]:
        with self._lock:
            c = self._conn()
            row = c.execute(
                "SELECT rate FROM collectibles_rates WHERE currency=?", (currency,)
            ).fetchone()
        return float(row["rate"]) if row else None

    def upsert_rates(self, rates: Dict[str, float]) -> int:
        if not rates:
            return 0
        now = time.time()
        with self._lock:
            c = self._conn()
            c.executemany(
                "INSERT INTO collectibles_rates(currency,rate,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(currency) DO UPDATE SET rate=excluded.rate, updated_at=excluded.updated_at",
                [(cur, float(r), now) for cur, r in rates.items()],
            )
            c.commit()
        return len(rates)
