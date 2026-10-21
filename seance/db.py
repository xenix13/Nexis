"""SQLite storage: one connection per request thread, schema migrations."""
import contextlib
import sqlite3
import threading

MIGRATIONS = [
    """
    CREATE TABLE users(
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        all_libraries INTEGER NOT NULL DEFAULT 1,
        disabled INTEGER NOT NULL DEFAULT 0,
        avatar_color TEXT NOT NULL DEFAULT '#e0a84f',
        created_at INTEGER NOT NULL
    );
    CREATE TABLE sessions(
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        user_agent TEXT,
        ip TEXT
    );
    CREATE TABLE user_libraries(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        section_id TEXT NOT NULL,
        PRIMARY KEY(user_id, section_id)
    );
    CREATE TABLE prefs(
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        data TEXT NOT NULL
    );
    CREATE TABLE progress(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        rating_key TEXT NOT NULL,
        position_ms INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        watched INTEGER NOT NULL DEFAULT 0,
        view_count INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        last_watched_at INTEGER,
        type TEXT,
        show_key TEXT,
        season_key TEXT,
        section_id TEXT,
        meta TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(user_id, rating_key)
    );
    CREATE INDEX progress_show ON progress(user_id, show_key);
    CREATE INDEX progress_season ON progress(user_id, season_key);
    CREATE INDEX progress_updated ON progress(user_id, updated_at DESC);
    CREATE TABLE history(
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        rating_key TEXT NOT NULL,
        watched_at INTEGER NOT NULL,
        section_id TEXT,
        meta TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX history_user ON history(user_id, watched_at DESC);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE markers(
        rating_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        source TEXT NOT NULL DEFAULT 'manual',
        PRIMARY KEY(rating_key, kind)
    );
    CREATE TABLE streams(
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        plex_session TEXT NOT NULL,
        rating_key TEXT NOT NULL,
        params TEXT NOT NULL,
        title TEXT,
        quality TEXT,
        position_ms INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'playing',
        created_at INTEGER NOT NULL,
        last_seen INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE libraries(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        paths TEXT NOT NULL DEFAULT '[]',
        position INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        scanned_at INTEGER
    );
    CREATE TABLE items(
        id INTEGER PRIMARY KEY,
        library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
        type TEXT NOT NULL,
        parent_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
        show_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
        key TEXT,
        title TEXT NOT NULL,
        sort_title TEXT NOT NULL,
        search TEXT NOT NULL DEFAULT '',
        original_title TEXT,
        year INTEGER,
        idx INTEGER,
        summary TEXT,
        tagline TEXT,
        rating REAL,
        content_rating TEXT,
        studio TEXT,
        released TEXT,
        genres TEXT NOT NULL DEFAULT '[]',
        people TEXT NOT NULL DEFAULT '{}',
        duration_ms INTEGER,
        added_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        file_size INTEGER,
        file_mtime INTEGER,
        probe TEXT,
        poster TEXT,
        backdrop TEXT,
        still TEXT,
        tmdb_id INTEGER,
        meta_state TEXT NOT NULL DEFAULT 'pending',
        locked INTEGER NOT NULL DEFAULT 0,
        img_version INTEGER NOT NULL DEFAULT 1
    );
    CREATE UNIQUE INDEX items_key ON items(library_id, type, key);
    CREATE INDEX items_lib ON items(library_id, type, sort_title);
    CREATE INDEX items_parent ON items(parent_id, idx);
    CREATE INDEX items_show ON items(show_id, type);
    CREATE INDEX items_added ON items(library_id, type, added_at DESC);
    CREATE INDEX items_meta ON items(meta_state);
    DROP TABLE IF EXISTS user_libraries;
    CREATE TABLE user_libraries(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        section_id TEXT NOT NULL,
        PRIMARY KEY(user_id, section_id)
    );
    DELETE FROM streams;
    DELETE FROM progress;
    DELETE FROM history;
    DELETE FROM markers;
    DELETE FROM settings WHERE key IN ('plex_url', 'plex_token', 'plex_verify_tls');
    """,
    """
    CREATE TABLE track_prefs(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        scope_id TEXT NOT NULL,
        audio_lang TEXT,
        sub_lang TEXT,
        sub_forced INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, scope_id)
    );
    """,
]


class DB:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        self._migrate()

    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=5000")
            self._local.c = c
        return c

    def release(self):
        c = getattr(self._local, "c", None)
        if c is not None:
            c.close()
            self._local.c = None

    def _migrate(self):
        c = self.conn()
        c.execute("PRAGMA journal_mode=WAL")
        version = c.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            c.executescript("BEGIN;" + script + f"PRAGMA user_version={i};COMMIT;")
        self.release()

    def all(self, sql, args=()):
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    def scalar(self, sql, args=()):
        r = self.conn().execute(sql, args).fetchone()
        return r[0] if r else None

    def run(self, sql, args=()):
        return self.conn().execute(sql, args)

    @contextlib.contextmanager
    def tx(self):
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    # settings helpers
    def setting(self, key, default=None):
        v = self.scalar("SELECT value FROM settings WHERE key=?", (key,))
        return default if v is None else v

    def set_setting(self, key, value):
        if value is None:
            self.run("DELETE FROM settings WHERE key=?", (key,))
        else:
            self.run("INSERT INTO settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
