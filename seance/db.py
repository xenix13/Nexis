"""SQLite storage: one connection per request thread, schema migrations."""
import contextlib
import queue
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
    """
    ALTER TABLE items ADD COLUMN age INTEGER;
    ALTER TABLE users ADD COLUMN pin_hash TEXT;
    ALTER TABLE users ADD COLUMN managed INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN home INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE users ADD COLUMN max_age INTEGER;
    ALTER TABLE users ADD COLUMN allow_unrated INTEGER NOT NULL DEFAULT 1;
    """,
    """
    CREATE TABLE collections(
        id INTEGER PRIMARY KEY,
        library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        sort_title TEXT NOT NULL DEFAULT '',
        summary TEXT,
        tmdb_id INTEGER,
        poster TEXT,
        backdrop TEXT,
        kind TEXT NOT NULL DEFAULT 'auto',
        img_version INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL
    );
    CREATE UNIQUE INDEX collections_tmdb ON collections(library_id, tmdb_id);
    CREATE TABLE collection_items(
        collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        PRIMARY KEY(collection_id, item_id)
    );
    CREATE INDEX collection_items_item ON collection_items(item_id);
    CREATE TABLE playlists(
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE playlist_items(
        id INTEGER PRIMARY KEY,
        playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        position REAL NOT NULL
    );
    CREATE INDEX playlist_items_pl ON playlist_items(playlist_id, position);
    CREATE TABLE watchlist(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        added_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, item_id)
    );
    ALTER TABLE items ADD COLUMN trailer TEXT;
    ALTER TABLE items ADD COLUMN extra_kind TEXT;
    """,
    # 6 : identifiants externes, envies hors bibliothèque (et une ancienne compatibilité retirée en 10)
    """
    ALTER TABLE items ADD COLUMN imdb_id TEXT;
    ALTER TABLE items ADD COLUMN tvdb_id INTEGER;
    CREATE INDEX items_tmdb ON items(tmdb_id, type);
    CREATE TABLE jf_tokens(
        token_hash TEXT PRIMARY KEY,
        kind TEXT NOT NULL,              -- session | apikey
        secret TEXT,                     -- clé d'API (ancienne compatibilité, retirée en 10)
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        app TEXT NOT NULL DEFAULT '',
        device TEXT NOT NULL DEFAULT '',
        ip TEXT,
        created_at INTEGER NOT NULL,
        last_used INTEGER NOT NULL
    );
    CREATE TABLE wishlist(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        media_type TEXT NOT NULL,        -- movie | tv
        tmdb_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        year INTEGER,
        poster TEXT,
        added_at INTEGER NOT NULL,
        requested_at INTEGER,
        PRIMARY KEY(user_id, media_type, tmdb_id)
    );
    """,
    # 7 : tâches planifiées (analyses des fichiers), TV en direct (IPTV)
    """
    CREATE TABLE media_analysis(
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,              -- chapters (images) | intro | credits (empreinte audio)
        file_mtime INTEGER,
        done_at INTEGER NOT NULL,
        result TEXT,
        PRIMARY KEY(item_id, kind)
    );
    CREATE TABLE live_sources(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        m3u_url TEXT NOT NULL,
        epg_url TEXT,
        user_agent TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        position INTEGER NOT NULL DEFAULT 0,
        refreshed_at INTEGER,
        epg_at INTEGER,
        error TEXT,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE live_channels(
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES live_sources(id) ON DELETE CASCADE,
        uid TEXT NOT NULL,
        name TEXT NOT NULL,
        number INTEGER,
        grp TEXT NOT NULL DEFAULT '',
        logo TEXT,
        tvg_id TEXT,
        epg_key TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL,
        user_agent TEXT,
        referrer TEXT,
        position INTEGER NOT NULL DEFAULT 0,
        search TEXT NOT NULL DEFAULT '',
        UNIQUE(source_id, uid)
    );
    CREATE INDEX live_channels_pos ON live_channels(source_id, position);
    CREATE TABLE live_hidden_groups(
        source_id INTEGER NOT NULL REFERENCES live_sources(id) ON DELETE CASCADE,
        grp TEXT NOT NULL,
        PRIMARY KEY(source_id, grp)
    );
    CREATE TABLE live_programmes(
        source_id INTEGER NOT NULL REFERENCES live_sources(id) ON DELETE CASCADE,
        epg_key TEXT NOT NULL,
        start INTEGER NOT NULL,
        stop INTEGER NOT NULL,
        title TEXT NOT NULL,
        subtitle TEXT,
        descr TEXT,
        category TEXT,
        PRIMARY KEY(source_id, epg_key, start)
    );
    CREATE INDEX live_programmes_stop ON live_programmes(source_id, epg_key, stop);
    CREATE TABLE live_favorites(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        channel_id INTEGER NOT NULL REFERENCES live_channels(id) ON DELETE CASCADE,
        PRIMARY KEY(user_id, channel_id)
    );
    """,
    # 8 : index pour le guide TV (programmes d'une période) et l'historique « vu » des séries
    """
    CREATE INDEX live_programmes_time ON live_programmes(stop, start);
    CREATE INDEX progress_watched ON progress(user_id, watched, last_watched_at);
    """,
    # 9 : anciens clients externes (retirés en 10)
    """
    DELETE FROM jf_tokens;
    ALTER TABLE jf_tokens ADD COLUMN device_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE jf_tokens ADD COLUMN version TEXT NOT NULL DEFAULT '';
    CREATE INDEX jf_tokens_user ON jf_tokens(user_id, device_id);
    CREATE TABLE jf_prefs(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        client TEXT NOT NULL,
        pref_id TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY(user_id, client, pref_id)
    );
    """,
    # 10 : ancienne compatibilité avec des clients externes retirée (remplacée par les applications Nexis)
    """
    DROP TABLE IF EXISTS jf_prefs;
    DROP TABLE IF EXISTS jf_tokens;
    DELETE FROM settings WHERE key IN ('jellyfin_api', 'jf_server_id', 'jf_secret');
    """,
    # 11 : TV en direct, chaînes vérifiées (ok : NULL = pas encore vérifiée, 0 = hors service, 1 = répond)
    """
    ALTER TABLE live_channels ADD COLUMN ok INTEGER;
    ALTER TABLE live_channels ADD COLUMN fails INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE live_channels ADD COLUMN checked_at INTEGER;
    """,
    # 12 : avatar en image (numéro de version de l'image, NULL = initiale sur fond de couleur)
    """
    ALTER TABLE users ADD COLUMN avatar_ver INTEGER;
    """,
    # 13 : avis par utilisateur sur un film ou une série (1 = j'aime, -1 = je n'aime pas, 0 = ne plus suggérer)
    """
    CREATE TABLE ratings(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        value INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, item_id)
    );
    """,
    # 14 : interface enfant (choisie par l'administrateur) ; date de la dernière lecture des notifications
    """
    ALTER TABLE users ADD COLUMN kids INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN notif_seen INTEGER NOT NULL DEFAULT 0;
    """,
    # 15 : double authentification (secret TOTP, secret en attente de confirmation, codes de secours hachés)
    """
    ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN totp_secret TEXT;
    ALTER TABLE users ADD COLUMN totp_pending TEXT;
    ALTER TABLE users ADD COLUMN totp_last_step INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN totp_recovery TEXT;
    """,
    # 16 : langue de l'interface choisie par chaque utilisateur (NULL : langue par défaut du serveur)
    """
    ALTER TABLE users ADD COLUMN language TEXT;
    """,
    # 17 : fédération (serveurs liés). Chaque compte et chaque bibliothèque a un identifiant commun à tous les
    # serveurs (uid). Les bibliothèques et éléments venus d'un serveur lié gardent son identifiant (remote_id).
    # fseq : numéro de changement, pour n'envoyer aux serveurs liés que ce qui a changé depuis leur dernier passage.
    """
    ALTER TABLE users ADD COLUMN uid TEXT;
    ALTER TABLE users ADD COLUMN rev INTEGER;
    ALTER TABLE users ADD COLUMN fseq INTEGER;
    UPDATE users SET uid=lower(hex(randomblob(16))), rev=created_at*1000;
    CREATE UNIQUE INDEX users_uid ON users(uid);
    ALTER TABLE libraries ADD COLUMN uid TEXT;
    ALTER TABLE libraries ADD COLUMN peer_id INTEGER REFERENCES peers(id) ON DELETE CASCADE;
    ALTER TABLE libraries ADD COLUMN remote_id INTEGER;
    UPDATE libraries SET uid=lower(hex(randomblob(16)));
    ALTER TABLE items ADD COLUMN peer_id INTEGER;
    ALTER TABLE items ADD COLUMN remote_id INTEGER;
    ALTER TABLE items ADD COLUMN fseq INTEGER;
    CREATE UNIQUE INDEX items_remote ON items(peer_id, remote_id) WHERE peer_id IS NOT NULL;
    CREATE INDEX items_fseq ON items(fseq) WHERE peer_id IS NULL;
    ALTER TABLE progress ADD COLUMN fseq INTEGER;
    CREATE INDEX progress_fseq ON progress(fseq);
    CREATE TABLE peers(
        id INTEGER PRIMARY KEY,
        uid TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        address TEXT,
        fingerprint TEXT NOT NULL,
        secret TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_seen INTEGER,
        version TEXT,
        error TEXT,
        users_cursor INTEGER NOT NULL DEFAULT 0,
        catalog_cursor INTEGER NOT NULL DEFAULT 0,
        progress_cursor INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE fed_invites(token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
    CREATE TABLE fed_gone(kind TEXT NOT NULL, ref TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(kind, ref));
    CREATE TABLE fed_pending_libs(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        uid TEXT NOT NULL,
        PRIMARY KEY(user_id, uid)
    );
    CREATE TABLE fed_clock(n INTEGER NOT NULL, applying INTEGER NOT NULL DEFAULT 0);
    INSERT INTO fed_clock(n) VALUES(0);
    CREATE TRIGGER users_fed_ins AFTER INSERT ON users BEGIN
        UPDATE fed_clock SET n=n+1;
        UPDATE users SET uid=COALESCE(NEW.uid, lower(hex(randomblob(16)))),
            rev=COALESCE(NEW.rev, CAST((julianday('now')-2440587.5)*86400000 AS INTEGER)),
            fseq=(SELECT n FROM fed_clock) WHERE id=NEW.id;
    END;
    CREATE TRIGGER users_fed_upd AFTER UPDATE OF username, display_name, password_hash, is_admin, all_libraries,
        disabled, avatar_color, pin_hash, managed, home, max_age, allow_unrated, avatar_ver, kids, totp_enabled,
        totp_secret, totp_recovery, language, rev ON users WHEN NEW.fseq IS OLD.fseq BEGIN
        UPDATE fed_clock SET n=n+1;
        UPDATE users SET fseq=(SELECT n FROM fed_clock),
            rev=CASE WHEN NEW.rev IS OLD.rev THEN CAST((julianday('now')-2440587.5)*86400000 AS INTEGER) ELSE NEW.rev END
            WHERE id=NEW.id;
    END;
    CREATE TRIGGER users_fed_del AFTER DELETE ON users BEGIN
        UPDATE fed_clock SET n=n+1;
        INSERT OR REPLACE INTO fed_gone(kind, ref, seq) VALUES('user', OLD.uid, (SELECT n FROM fed_clock));
    END;
    CREATE TRIGGER user_libraries_fed_ins AFTER INSERT ON user_libraries WHEN (SELECT applying FROM fed_clock)=0 BEGIN
        UPDATE users SET rev=CAST((julianday('now')-2440587.5)*86400000 AS INTEGER) WHERE id=NEW.user_id;
    END;
    CREATE TRIGGER user_libraries_fed_del AFTER DELETE ON user_libraries WHEN (SELECT applying FROM fed_clock)=0 BEGIN
        UPDATE users SET rev=CAST((julianday('now')-2440587.5)*86400000 AS INTEGER) WHERE id=OLD.user_id;
    END;
    CREATE TRIGGER libraries_fed_ins AFTER INSERT ON libraries WHEN NEW.uid IS NULL BEGIN
        UPDATE libraries SET uid=lower(hex(randomblob(16))) WHERE id=NEW.id;
    END;
    CREATE TRIGGER items_fed_ins AFTER INSERT ON items WHEN NEW.peer_id IS NULL AND EXISTS (SELECT 1 FROM peers) BEGIN
        UPDATE fed_clock SET n=n+1;
        UPDATE items SET fseq=(SELECT n FROM fed_clock) WHERE id=NEW.id;
    END;
    CREATE TRIGGER items_fed_upd AFTER UPDATE ON items WHEN NEW.peer_id IS NULL AND NEW.fseq IS OLD.fseq
        AND EXISTS (SELECT 1 FROM peers) BEGIN
        UPDATE fed_clock SET n=n+1;
        UPDATE items SET fseq=(SELECT n FROM fed_clock) WHERE id=NEW.id;
    END;
    CREATE TRIGGER items_fed_del AFTER DELETE ON items WHEN OLD.peer_id IS NULL AND EXISTS (SELECT 1 FROM peers) BEGIN
        UPDATE fed_clock SET n=n+1;
        INSERT OR REPLACE INTO fed_gone(kind, ref, seq) VALUES('item', OLD.id, (SELECT n FROM fed_clock));
    END;
    CREATE TRIGGER progress_fed_ins AFTER INSERT ON progress WHEN EXISTS (SELECT 1 FROM peers) BEGIN
        UPDATE fed_clock SET n=n+1;
        UPDATE progress SET fseq=(SELECT n FROM fed_clock) WHERE user_id=NEW.user_id AND rating_key=NEW.rating_key;
    END;
    CREATE TRIGGER progress_fed_upd AFTER UPDATE ON progress WHEN NEW.fseq IS OLD.fseq AND EXISTS (SELECT 1 FROM peers) BEGIN
        UPDATE fed_clock SET n=n+1;
        UPDATE progress SET fseq=(SELECT n FROM fed_clock) WHERE user_id=NEW.user_id AND rating_key=NEW.rating_key;
    END;
    """,
    # 18 : bibliothèques d'un serveur lié fusionnées avec une bibliothèque de ce serveur. src_* : place d'origine
    # d'un élément reçu (sa bibliothèque et ses parents chez lui) ; library_id/parent_id/show_id : place affichée
    # (dans la bibliothèque fusionnée, sous la série locale). alias_of : même titre que cet élément local (masqué,
    # sert de seconde copie à la lecture). Un élément local supprimé rend leur place d'origine aux éléments reçus
    # qui en dépendaient, au lieu de les emporter avec lui.
    """
    ALTER TABLE libraries ADD COLUMN merge_into INTEGER REFERENCES libraries(id) ON DELETE SET NULL;
    ALTER TABLE peers ADD COLUMN online INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE items ADD COLUMN src_lib INTEGER;
    ALTER TABLE items ADD COLUMN src_parent INTEGER;
    ALTER TABLE items ADD COLUMN src_show INTEGER;
    ALTER TABLE items ADD COLUMN alias_of INTEGER REFERENCES items(id) ON DELETE SET NULL;
    UPDATE items SET src_lib=library_id, src_parent=parent_id, src_show=show_id WHERE peer_id IS NOT NULL;
    CREATE INDEX items_alias ON items(alias_of) WHERE alias_of IS NOT NULL;
    CREATE INDEX items_src ON items(src_lib) WHERE src_lib IS NOT NULL;
    CREATE TRIGGER items_fed_rehome BEFORE DELETE ON items WHEN OLD.peer_id IS NULL AND EXISTS (SELECT 1 FROM peers) BEGIN
        UPDATE items SET library_id=src_lib, parent_id=src_parent, show_id=src_show, alias_of=NULL
            WHERE peer_id IS NOT NULL AND (parent_id=OLD.id OR show_id=OLD.id OR alias_of=OLD.id);
    END;
    CREATE TRIGGER libraries_fed_rehome BEFORE DELETE ON libraries WHEN OLD.peer_id IS NULL BEGIN
        UPDATE items SET library_id=src_lib, parent_id=src_parent, show_id=src_show, alias_of=NULL
            WHERE peer_id IS NOT NULL AND library_id=OLD.id;
    END;
    """,
    # 19 : notes personnelles (1 à 5 étoiles), titres masqués pour soi, playlists partagées avec tout le foyer
    """
    CREATE TABLE stars(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        stars INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, item_id)
    );
    CREATE TABLE hidden_items(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        added_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, item_id)
    );
    ALTER TABLE playlists ADD COLUMN shared INTEGER NOT NULL DEFAULT 0;
    """,
    # 20 : notifications push (appareils inscrits, épisodes déjà signalés) ; photothèque : favoris, albums partagés par lien
    """
    CREATE TABLE push_subs(
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        endpoint TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL,
        last_ok INTEGER,
        failures INTEGER NOT NULL DEFAULT 0
    );
    ALTER TABLE users ADD COLUMN push_seen INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE photo_favs(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        added_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, item_id)
    );
    CREATE TABLE photo_shares(
        token TEXT PRIMARY KEY,
        album_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    );
    """,
    # 21 : sous-titres OpenSubtitles (recherches automatiques sans résultat, ou sous-titre retiré à la main)
    """
    CREATE TABLE subs_attempts(
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        lang TEXT NOT NULL,
        at INTEGER NOT NULL,
        result TEXT NOT NULL,
        PRIMARY KEY(item_id, lang)
    );
    """,
    # 22 : alertes pour les administrateurs ; limites par compte (lectures simultanées, débit hors du réseau local) ;
    # temps d'écran des profils (minutes par jour, heure du coucher) et temps regardé par jour
    """
    CREATE TABLE alerts(
        key TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '#/admin/dashboard',
        created_at INTEGER NOT NULL,
        notified_at INTEGER NOT NULL,
        resolved_at INTEGER
    );
    ALTER TABLE users ADD COLUMN max_streams INTEGER;
    ALTER TABLE users ADD COLUMN remote_kbps INTEGER;
    ALTER TABLE users ADD COLUMN daily_minutes INTEGER;
    ALTER TABLE users ADD COLUMN bedtime TEXT;
    CREATE TABLE screen_time(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        seconds INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(user_id, day)
    );
    DROP TRIGGER users_fed_upd;
    CREATE TRIGGER users_fed_upd AFTER UPDATE OF username, display_name, password_hash, is_admin, all_libraries,
        disabled, avatar_color, pin_hash, managed, home, max_age, allow_unrated, avatar_ver, kids, totp_enabled,
        totp_secret, totp_recovery, language, max_streams, remote_kbps, daily_minutes, bedtime, rev ON users
        WHEN NEW.fseq IS OLD.fseq BEGIN
        UPDATE fed_clock SET n=n+1;
        UPDATE users SET fseq=(SELECT n FROM fed_clock),
            rev=CASE WHEN NEW.rev IS OLD.rev THEN CAST((julianday('now')-2440587.5)*86400000 AS INTEGER) ELSE NEW.rev END
            WHERE id=NEW.id;
    END;
    """,
    # 23 : comptes Trakt reliés
    """
    CREATE TABLE trakt_links(
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        access_token TEXT NOT NULL,
        refresh_token TEXT NOT NULL DEFAULT '',
        expires_at INTEGER NOT NULL,
        username TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        last_sync INTEGER,
        last_error TEXT,
        last_result TEXT
    );
    """,
    # 24 : podcasts (flux, épisodes, abonnements et écoute de chacun)
    """
    CREATE TABLE podcasts(
        id INTEGER PRIMARY KEY,
        url TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        author TEXT NOT NULL DEFAULT '',
        image TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        link TEXT NOT NULL DEFAULT '',
        etag TEXT,
        private INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        checked_at INTEGER,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE podcast_episodes(
        id INTEGER PRIMARY KEY,
        podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
        guid TEXT NOT NULL,
        title TEXT NOT NULL,
        published INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER,
        url TEXT NOT NULL,
        mime TEXT NOT NULL DEFAULT 'audio/mpeg',
        image TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        added_at INTEGER NOT NULL,
        UNIQUE(podcast_id, guid)
    );
    CREATE INDEX podcast_episodes_pub ON podcast_episodes(podcast_id, published DESC);
    CREATE TABLE podcast_subs(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, podcast_id)
    );
    CREATE TABLE podcast_progress(
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        episode_id INTEGER NOT NULL REFERENCES podcast_episodes(id) ON DELETE CASCADE,
        position_ms INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        played INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(user_id, episode_id)
    );
    """,
    # 25 : préférences répliquées avec les comptes (une modification relance l'envoi du compte aux serveurs liés)
    """
    CREATE TRIGGER prefs_fed_ins AFTER INSERT ON prefs WHEN (SELECT applying FROM fed_clock)=0 BEGIN
        UPDATE users SET rev=CAST((julianday('now')-2440587.5)*86400000 AS INTEGER) WHERE id=NEW.user_id;
    END;
    CREATE TRIGGER prefs_fed_upd AFTER UPDATE ON prefs WHEN (SELECT applying FROM fed_clock)=0 BEGIN
        UPDATE users SET rev=CAST((julianday('now')-2440587.5)*86400000 AS INTEGER) WHERE id=NEW.user_id;
    END;
    CREATE TRIGGER prefs_fed_del AFTER DELETE ON prefs WHEN (SELECT applying FROM fed_clock)=0 BEGIN
        UPDATE users SET rev=CAST((julianday('now')-2440587.5)*86400000 AS INTEGER) WHERE id=OLD.user_id;
    END;
    """,
    # 26 : podcasts retirés (tables, et leur trace dans les préférences : menu, accueil, notifications)
    """
    DROP TABLE IF EXISTS podcast_progress;
    DROP TABLE IF EXISTS podcast_subs;
    DROP INDEX IF EXISTS podcast_episodes_pub;
    DROP TABLE IF EXISTS podcast_episodes;
    DROP TABLE IF EXISTS podcasts;
    UPDATE fed_clock SET applying=1;
    UPDATE prefs SET data=json_remove(data, '$.notify_podcasts') WHERE json_valid(data);
    UPDATE prefs SET data=json_set(data, '$.nav_order', json((SELECT json_group_array(value) FROM json_each(prefs.data, '$.nav_order')
        WHERE value<>'podcasts'))) WHERE json_valid(data) AND json_type(data, '$.nav_order')='array';
    UPDATE prefs SET data=json_set(data, '$.nav_hidden', json((SELECT json_group_array(value) FROM json_each(prefs.data, '$.nav_hidden')
        WHERE value<>'podcasts'))) WHERE json_valid(data) AND json_type(data, '$.nav_hidden')='array';
    UPDATE prefs SET data=json_set(data, '$.start_page', 'home') WHERE json_valid(data) AND json_extract(data, '$.start_page')='podcasts';
    UPDATE fed_clock SET applying=0;
    """,
    # 27 : journal des lectures (statistiques de l'administration) : une ligne à la fin de chaque lecture de plus
    # de 30 s, quelle qu'en soit la raison (arrêt, abandon, lecture remplacée), avec le débit estimé
    """
    CREATE TABLE play_log(
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        rating_key TEXT NOT NULL,
        title TEXT,
        mode TEXT,
        kbps INTEGER,
        started INTEGER NOT NULL,
        ended INTEGER NOT NULL,
        seconds INTEGER NOT NULL,
        relayed INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX play_log_time ON play_log(ended);
    CREATE INDEX play_log_user ON play_log(user_id, ended);
    CREATE TRIGGER streams_log AFTER DELETE ON streams WHEN OLD.last_seen - OLD.created_at >= 30 BEGIN
        INSERT INTO play_log(user_id, rating_key, title, mode, kbps, started, ended, seconds, relayed) VALUES(
            OLD.user_id,
            -- mode privé : la durée compte, le titre n'est pas gardé
            CASE WHEN (SELECT json_extract(data, '$.private_mode') FROM prefs WHERE user_id = OLD.user_id) = 1 THEN 'private' ELSE OLD.rating_key END,
            CASE WHEN (SELECT json_extract(data, '$.private_mode') FROM prefs WHERE user_id = OLD.user_id) = 1 THEN NULL ELSE OLD.title END,
            json_extract(OLD.params, '$.mode'),
            CASE WHEN json_extract(OLD.params, '$.mode') = 'transcode' THEN json_extract(OLD.params, '$.kbps')
                 ELSE COALESCE(NULLIF(json_extract(OLD.params, '$.src_kbps'), 0), json_extract(OLD.params, '$.kbps')) END,
            OLD.created_at, OLD.last_seen, OLD.last_seen - OLD.created_at, json_extract(OLD.params, '$.peer') IS NOT NULL);
    END;
    """,
    # 28 : stockage froid (copies des fichiers sur d'autres stockages, file d'archivage)
    """
    CREATE TABLE cold_stores(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        conf TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        position INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE cold_copies(
        id INTEGER PRIMARY KEY,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        store_id INTEGER NOT NULL REFERENCES cold_stores(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT,
        extras TEXT NOT NULL DEFAULT '[]',
        archived_at INTEGER NOT NULL,
        verified_at INTEGER,
        UNIQUE(item_id, store_id)
    );
    CREATE INDEX cold_copies_store ON cold_copies(store_id, name);
    CREATE TABLE cold_jobs(
        id INTEGER PRIMARY KEY,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        store_id INTEGER NOT NULL REFERENCES cold_stores(id) ON DELETE CASCADE,
        state TEXT NOT NULL DEFAULT 'queued',
        error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        UNIQUE(item_id, store_id)
    );
    """,
]


class DB:
    """Une connexion par fil d'exécution, rendue à une réserve à la fin de chaque requête.

    Le serveur crée un fil par requête : sans réserve, chaque requête ouvrirait (et configurerait) une
    nouvelle connexion SQLite. Une connexion n'est remise en réserve que hors transaction."""
    POOL_SIZE = 16

    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        self._pool = queue.LifoQueue(self.POOL_SIZE)
        self._migrate()

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA synchronous=NORMAL")  # sûr en mode WAL, beaucoup moins d'écritures disque
        return c

    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            try:
                c = self._pool.get_nowait()
            except queue.Empty:
                c = self._connect()
            self._local.c = c
        return c

    def release(self):
        c = getattr(self._local, "c", None)
        if c is None:
            return
        self._local.c = None
        if c.in_transaction:
            c.rollback()
        try:
            self._pool.put_nowait(c)
        except queue.Full:
            c.close()

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
