"""Tâches planifiées du serveur .

Chaque tâche a une planification (désactivée, toutes les N heures, chaque jour ou chaque semaine à une
heure donnée), des options, et garde le résultat de sa dernière exécution. Les tâches tournent une à la
fois dans un fil dédié ; les plus lourdes (lecture des fichiers) se mettent en pause pendant qu'on regarde
quelque chose, pour ne pas gêner la lecture sur le partage réseau.
"""
import json
import logging
import os
import shutil
import threading
import time

from . import introdetect
from .chapters import chapter_list
from .markers import from_chapters

log = logging.getLogger("seance.tasks")


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, task, reason):
        self.task = task
        self.reason = reason
        self.progress = 0.0
        self.message = ""
        self.started = time.time()
        self.cancel = threading.Event()
        self.paused = False

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()


# ------------------------------------------------------------------ options
def opt_bool(key, label, default, hint=None):
    return {"key": key, "type": "bool", "label": label, "default": default, "hint": hint}


def opt_int(key, label, default, lo, hi, unit="", hint=None):
    return {"key": key, "type": "int", "label": label, "default": default, "min": lo, "max": hi, "unit": unit, "hint": hint}


def opt_select(key, label, default, choices, hint=None):
    return {"key": key, "type": "select", "label": label, "default": default, "choices": choices, "hint": hint}


def opt_libraries(kind=None):
    return {"key": "libraries", "type": "libraries", "label": "Bibliothèques", "default": [], "kind": kind,
            "hint": "Aucune cochée = toutes"}


PAUSE = opt_bool("pause_playback", "Se mettre en pause pendant les lectures", True,
                 "Libère le partage réseau et le processeur pendant les lectures.")
AFTER_SCAN = opt_bool("after_scan", "Lancer aussi après chaque ajout de fichiers", True,
                      "Les nouveaux épisodes et films sont traités dès qu'ils sont scannés.")


class Task:
    id = ""
    name = ""
    description = ""
    category = ""
    heavy = False
    default = {"mode": "off"}
    options = []

    def available(self, ctx):
        return True, None

    def run(self, ctx, job, opts):  # -> résumé (texte)
        raise NotImplementedError


def lib_clause(opts, column="i.library_id"):
    libs = [int(x) for x in opts.get("libraries") or [] if str(x).isdigit()]
    if not libs:
        return "", []
    return f" AND {column} IN ({','.join('?' * len(libs))})", libs


# ------------------------------------------------------------------ tâches
class ScanTask(Task):
    id = "scan"
    name = "Analyser les bibliothèques"
    description = ("Analyse complète des dossiers : fichiers ajoutés, supprimés ou remplacés. Les ajouts sont "
                   "normalement détectés en continu ; ce passage complet sert de contrôle.")
    category = "Bibliothèques"
    default = {"mode": "interval", "hours": 24}
    options = [opt_libraries()]

    def run(self, ctx, job, opts):
        libs = [int(x) for x in opts.get("libraries") or [] if str(x).isdigit()] or None
        while not ctx.scanner.start(libs):
            job.message = "Un scan est déjà en cours, en attente…"
            job.check()
            time.sleep(3)
        time.sleep(0.3)
        while True:
            st = ctx.scanner.status()
            if not st["running"]:
                break
            job.message = f"{st.get('library') or ''} · {st['phase']}".strip(" ·")
            job.progress = (st["done"] / st["total"]) if st["total"] else job.progress
            time.sleep(1)
        st = ctx.scanner.status()
        out = f"{st['added']} ajout(s), {st['removed']} suppression(s)"
        if st["errors"]:
            out += f", {len(st['errors'])} erreur(s)"
        return out


class MetadataTask(Task):
    id = "metadata"
    name = "Actualiser les métadonnées"
    description = ("Recharge résumés, affiches, distribution et titres d'épisodes depuis TMDB (et les .nfo). Les "
                   "identifications faites à la main sont conservées.")
    category = "Bibliothèques"
    default = {"mode": "weekly", "weekday": 6, "time": "04:00"}
    options = [
        opt_select("scope", "Quoi actualiser", "missing", [
            ["missing", "Seulement ce qui est incomplet (non identifié, sans résumé ni affiche)"],
            ["stale", "Ce qui n'a pas été actualisé depuis un certain temps"],
            ["all", "Tout (long : une requête TMDB par film et par saison)"]]),
        opt_int("days", "Ancienneté minimale (si « pas actualisé depuis »)", 30, 1, 365, "jours"),
        opt_libraries(),
    ]

    def available(self, ctx):
        return (True, None) if ctx.db.setting("tmdb_key") else (True, "Sans clé TMDB, seuls les .nfo sont relus.")

    def run(self, ctx, job, opts):
        db = ctx.db
        where, args = lib_clause(opts)
        scope = opts.get("scope", "missing")
        if scope == "missing":
            where += (" AND (i.meta_state IN ('pending','nomatch','error') OR i.summary IS NULL OR i.poster IS NULL"
                      " OR i.backdrop IS NULL)")
        elif scope == "stale":
            where += " AND i.updated_at < ?"
            args.append(int(time.time()) - int(opts.get("days", 30)) * 86400)
        rows = db.all(f"SELECT i.* FROM items i WHERE i.type IN ('movie','show'){where} ORDER BY i.id", args)
        tmdb = ctx.metadata.tmdb()
        ok = err = 0
        for n, r in enumerate(rows, 1):
            job.check()
            job.progress = (n - 1) / max(1, len(rows))
            job.message = r["title"]
            try:
                ctx.metadata.refresh(r, tmdb)
                ok += 1
            except Exception as e:
                err += 1
                log.warning("Métadonnées de %s : %s", r["title"], e)
                if "401" in str(e):
                    raise RuntimeError("Clé TMDB refusée")
            finally:
                db.release()
        return f"{ok} élément(s) actualisé(s)" + (f", {err} erreur(s)" if err else "") if rows else "Rien à actualiser"


def analysis_done(db, item, kind):
    r = db.one("SELECT file_mtime FROM media_analysis WHERE item_id=? AND kind=?", (item["id"], kind))
    return bool(r) and r["file_mtime"] == item["file_mtime"]


def mark_analysis(db, item, kind, result=None):
    db.run("INSERT INTO media_analysis(item_id,kind,file_mtime,done_at,result) VALUES(?,?,?,?,?) "
           "ON CONFLICT(item_id,kind) DO UPDATE SET file_mtime=excluded.file_mtime, done_at=excluded.done_at, "
           "result=excluded.result", (item["id"], kind, item["file_mtime"], int(time.time()),
                                      json.dumps(result) if result is not None else None))


class ChapterImagesTask(Task):
    id = "chapters"
    name = "Extraire les images des chapitres"
    description = ("Une image par chapitre, affichée dans la sélection de chapitre du lecteur et sur la fiche. Seuls "
                   "les fichiers qui ont au moins deux chapitres sont concernés.")
    category = "Lecture"
    heavy = True
    default = {"mode": "daily", "time": "02:00"}
    options = [AFTER_SCAN, PAUSE, opt_libraries()]

    def run(self, ctx, job, opts):
        db = ctx.db
        where, args = lib_clause(opts)
        rows = db.all("SELECT i.id, i.key, i.probe, i.file_size, i.file_mtime, i.duration_ms, i.title FROM items i "
                      f"WHERE i.type IN ('movie','episode') AND i.probe LIKE '%\"chapters\": [{{%'{where} "
                      "ORDER BY i.added_at DESC", args)
        todo = [r for r in rows if len(chapter_list(r)) >= 2 and not analysis_done(db, r, "chapters")]
        db.release()
        made = files = failed = 0
        for n, r in enumerate(todo, 1):
            job.wait_idle()
            job.progress = (n - 1) / max(1, len(todo))
            job.message = r["title"]
            try:
                made += ctx.chapter_images.extract_all(r, cancelled=job.cancel.is_set)
            except Exception as e:  # un fichier en défaut ne doit pas arrêter la tâche
                log.warning("Images de chapitres de %s : %s", r["title"], e)
                failed += 1
            job.check()
            if ctx.chapter_images.has_all(r):
                mark_analysis(db, r, "chapters")
                files += 1
            db.release()
        if not todo:
            return "Toutes les images sont prêtes"
        return f"{made} image(s) pour {files} fichier(s)" + (f", {failed} fichier(s) en échec" if failed else "")


class IntroTask(Task):
    id = "intros"
    name = "Détecter les intros et génériques"
    description = ("Pour les épisodes sans chapitres nommés : compare l'empreinte audio des épisodes d'une même saison "
                   "pour trouver l'opening (bouton « Passer l'intro ») et le générique de fin (carte « Épisode "
                   "suivant »). Il faut au moins deux épisodes dans la saison.")
    category = "Lecture"
    heavy = True
    default = {"mode": "daily", "time": "03:00"}
    options = [
        AFTER_SCAN,
        opt_int("intro_minutes", "Chercher l'intro dans les premières", 10, 3, 25, "minutes"),
        opt_bool("credits", "Détecter aussi le générique de fin", True),
        opt_int("credits_minutes", "Chercher le générique dans les dernières", 6, 2, 15, "minutes"),
        opt_int("min_seconds", "Durée minimale d'une intro / d'un générique", 15, 5, 60, "secondes"),
        opt_int("max_seconds", "Durée maximale", 130, 60, 300, "secondes"),
        opt_bool("skip_chaptered", "Ignorer les épisodes dont les chapitres indiquent déjà l'intro", True),
        PAUSE,
        opt_libraries("show"),
    ]

    def available(self, ctx):
        if not introdetect.supported():
            return False, "ffmpeg n'a pas été compilé avec Chromaprint (empreinte audio) : installe le paquet ffmpeg de la distribution."
        return True, None

    def run(self, ctx, job, opts):
        db = ctx.db
        where, args = lib_clause(opts)
        kinds = ["intro"] + (["credits"] if opts.get("credits", True) else [])
        seasons = db.all(f"SELECT DISTINCT i.parent_id AS sid FROM items i WHERE i.type='episode'{where}", args)
        found = analysed = 0
        todo = []
        for s in seasons:
            eps = db.all("SELECT id, key, probe, file_size, file_mtime, duration_ms, idx FROM items WHERE parent_id=? "
                         "AND type='episode' AND duration_ms > 120000 ORDER BY idx, id", (s["sid"],))
            need = {k: [e for e in eps if not analysis_done(db, e, k)] for k in kinds}
            if len(eps) >= 2 and any(need.values()):
                todo.append((eps, need))
        db.release()
        for n, (eps, need) in enumerate(todo, 1):
            job.progress = (n - 1) / max(1, len(todo))
            for kind in kinds:
                if not need[kind]:
                    continue
                job.wait_idle()
                job.check()
                skip = set()
                if opts.get("skip_chaptered", True):
                    for e in need[kind]:
                        probe = json.loads(e["probe"] or "{}")
                        m = from_chapters(probe.get("chapters") or [], probe.get("duration") or 0).get(kind)
                        if m and m["source"] == "chapters":
                            skip.add(e["id"])
                if all(e["id"] in skip for e in need[kind]):
                    for e in need[kind]:
                        mark_analysis(db, e, kind, {"skipped": "chapters"})
                    continue
                job.message = f"{os.path.basename(os.path.dirname(eps[0]['key']))} · {'intro' if kind == 'intro' else 'générique'}"
                minutes = int(opts.get("intro_minutes", 10) if kind == "intro" else opts.get("credits_minutes", 6))
                lo, hi = int(opts.get("min_seconds", 15)), int(opts.get("max_seconds", 130))
                res = introdetect.detect_season(eps, ctx.fingerprints, "head" if kind == "intro" else "tail", minutes,
                                                lo, hi if kind == "intro" else max(hi, 180),
                                                cancelled=lambda: job.cancel.is_set() or ctx.subtitles._streaming() and
                                                bool(opts.get("pause_playback", True)))
                job.check()
                if ctx.subtitles._streaming() and opts.get("pause_playback", True):
                    continue  # interrompu par une lecture : on reprendra cette saison
                for e in need[kind]:
                    if e["id"] in skip:
                        mark_analysis(db, e, kind, {"skipped": "chapters"})
                        continue
                    seg = res.get(e["id"])
                    analysed += 1
                    if seg:
                        found += 1
                        start, end = int(seg[0] * 1000), int(seg[1] * 1000)
                        if kind == "credits":
                            end = max(end, int(e["duration_ms"] or end))
                        db.run("INSERT INTO markers(rating_key,kind,start_ms,end_ms,source) VALUES(?,?,?,?,'audio') "
                               "ON CONFLICT(rating_key,kind) DO UPDATE SET start_ms=excluded.start_ms, "
                               "end_ms=excluded.end_ms, source='audio' WHERE markers.source<>'manual'",
                               (str(e["id"]), kind, start, end))
                    mark_analysis(db, e, kind, {"start": seg[0], "end": seg[1]} if seg else {"found": False})
                db.release()
        if not todo:
            return "Tous les épisodes sont déjà analysés"
        return f"{found} repère(s) trouvé(s) sur {analysed} analyse(s)"


class SubtitlesTask(Task):
    id = "subtitles"
    name = "Préparer les sous-titres et les aperçus"
    description = ("Met en file les fichiers dont les sous-titres intégrés ou les vignettes de la barre de lecture ne "
                   "sont pas encore prêts. La préparation s'effectue en dehors des lectures.")
    category = "Lecture"
    default = {"mode": "daily", "time": "01:30"}

    def run(self, ctx, job, opts):
        before = len(ctx.subtitles.urgent) + len(ctx.subtitles.idle)
        ctx.prefetch_missing_subtitles()
        after = len(ctx.subtitles.urgent) + len(ctx.subtitles.idle)
        return f"{after} fichier(s) en file" if after else ("Tout est prêt" if not before else f"{before} fichier(s) en file")


class LiveTask(Task):
    id = "livetv"
    name = "Actualiser les chaînes et le guide TV"
    description = "Recharge les listes de chaînes (M3U) et les programmes (XMLTV) des sources de TV en direct."
    category = "TV en direct"
    default = {"mode": "interval", "hours": 12}
    options = [opt_bool("check", "Vérifier que chaque chaîne répond", True,
                        "Une chaîne sans réponse deux fois de suite est masquée, puis réaffichée dès qu'elle répond à "
                        "nouveau. Les chaînes sont testées une par une.")]

    def available(self, ctx):
        if not ctx.db.scalar("SELECT COUNT(*) FROM live_sources WHERE enabled=1"):
            return True, "Aucune source configurée (Administration → TV en direct)."
        return True, None

    def run(self, ctx, job, opts):
        def progress(done, total, label):
            job.check()
            job.progress = done / max(1, total)
            job.message = label
        summary = ctx.live.refresh_all(lambda d, t, label: progress(d, t * 2 if opts.get("check", True) else t, label))
        if not opts.get("check", True):
            return summary
        checked = ctx.live.check_channels(
            progress=lambda d, t: progress(t + d, t * 2, "Vérification des chaînes"), cancelled=job.cancel.is_set)
        job.check()
        return f"{summary} — {checked}"


class CleanupTask(Task):
    id = "cleanup"
    name = "Nettoyer les caches"
    description = ("Supprime les images redimensionnées inutilisées depuis longtemps, et les sous-titres, aperçus, "
                   "images de chapitres et empreintes de fichiers qui ne sont plus dans les bibliothèques.")
    category = "Maintenance"
    default = {"mode": "daily", "time": "05:00"}
    options = [opt_int("days", "Garder les images vues dans les", 60, 7, 365, "derniers jours")]

    def run(self, ctx, job, opts):
        now = time.time()
        removed, freed = 0, 0

        def rm(path):
            nonlocal removed, freed
            try:
                if os.path.isdir(path):
                    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs)
                    shutil.rmtree(path)
                else:
                    size = os.path.getsize(path)
                    os.remove(path)
                removed += 1
                freed += size
            except OSError:
                pass
        img = os.path.join(ctx.data_dir, "cache", "img")
        days = int(opts.get("days", 60))
        for name in os.listdir(img):
            p = os.path.join(img, name)
            try:
                if now - os.path.getatime(p) > days * 86400:
                    rm(p)
            except OSError:
                pass
        job.progress = 0.3
        job.check()
        keys = set()
        for r in ctx.db.all("SELECT id, file_size, file_mtime FROM items WHERE type IN ('movie','episode','extra')"):
            keys.add(f"f{r['file_size']}_{r['file_mtime'] or 0}" if r["file_size"] else f"{r['id']}_{r['file_mtime'] or 0}")
        ctx.db.release()
        for sub in ("subs", "trickplay", "chapters"):
            base = os.path.join(ctx.data_dir, "cache", sub)
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                p = os.path.join(base, name)
                if name.startswith(".") or not os.path.isdir(p) or name in keys:
                    continue
                try:
                    if now - os.path.getmtime(p) < 7 * 86400:
                        continue  # fichier peut-être seulement déplacé, pas encore rescanné
                except OSError:
                    continue
                rm(p)
        fp = ctx.fingerprints.base
        for name in os.listdir(fp):
            stem = name.split("_head")[0].split("_tail")[0]
            if stem not in keys:
                rm(os.path.join(fp, name))
        job.progress = 1
        return f"{removed} élément(s) supprimé(s), {freed / 1e6:.1f} Mo libérés"


TASKS = [ScanTask(), MetadataTask(), ChapterImagesTask(), IntroTask(), SubtitlesTask(), LiveTask(), CleanupTask()]


# ------------------------------------------------------------------ planification
def _at(day_struct, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return time.mktime((day_struct.tm_year, day_struct.tm_mon, day_struct.tm_mday, h, m, 0, 0, 0, -1))


def next_run(schedule, last, anchor):
    """Prochaine exécution (timestamp) ou None si désactivée."""
    mode = schedule.get("mode")
    ref = last or anchor
    if mode == "interval":
        return ref + max(1, int(schedule.get("hours") or 24)) * 3600
    if mode in ("daily", "weekly"):
        hhmm = schedule.get("time") or "03:00"
        t = _at(time.localtime(ref), hhmm)
        step = 0
        while True:
            cand = _at(time.localtime(t + step * 86400), hhmm)
            if cand > ref and (mode == "daily" or time.localtime(cand).tm_wday == int(schedule.get("weekday", 6))):
                return cand
            step += 1
            if step > 8:
                return None
    return None


def valid_schedule(s):
    if not isinstance(s, dict) or s.get("mode") not in ("off", "interval", "daily", "weekly"):
        raise ValueError("Planification invalide")
    out = {"mode": s["mode"]}
    if s["mode"] == "interval":
        hours = int(s.get("hours") or 0)
        if not 1 <= hours <= 24 * 30:
            raise ValueError("Intervalle : entre 1 heure et 30 jours")
        out["hours"] = hours
    if s["mode"] in ("daily", "weekly"):
        t = str(s.get("time") or "")
        parts = t.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) or not (0 <= int(parts[0]) < 24 and 0 <= int(parts[1]) < 60):
            raise ValueError("Heure invalide (HH:MM)")
        out["time"] = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    if s["mode"] == "weekly":
        wd = int(s.get("weekday", 6))
        if not 0 <= wd <= 6:
            raise ValueError("Jour invalide")
        out["weekday"] = wd
    return out


def valid_options(task, data):
    out = {}
    for o in task.options:
        if o["key"] not in data:
            continue
        v = data[o["key"]]
        if o["type"] == "bool":
            out[o["key"]] = bool(v)
        elif o["type"] == "int":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{o['label']} : nombre attendu")
            if not o["min"] <= v <= o["max"]:
                raise ValueError(f"{o['label']} : entre {o['min']} et {o['max']}")
            out[o["key"]] = v
        elif o["type"] == "select":
            if v not in [c[0] for c in o["choices"]]:
                raise ValueError(f"{o['label']} : choix invalide")
            out[o["key"]] = v
        elif o["type"] == "libraries":
            if not isinstance(v, list) or not all(str(x).isdigit() for x in v):
                raise ValueError("Bibliothèques invalides")
            out[o["key"]] = [str(x) for x in v][:100]
    return out


class TaskManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.tasks = {t.id: t for t in TASKS}
        self.lock = threading.Lock()
        self.queue = []          # [(id, raison)]
        self.job = None
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.anchor = time.time()
        self._migrate()

    def _migrate(self):
        db = self.ctx.db
        if db.setting("task_conf:scan") is None and db.setting("scan_interval") is not None:
            hours = self.ctx.setting_int("scan_interval", 24)
            self._save_conf("scan", {"schedule": {"mode": "interval", "hours": hours} if hours else {"mode": "off"}})
            db.set_setting("scan_interval", None)
        db.release()

    # ------------------------------------------------------------ état
    def conf(self, tid):
        task = self.tasks[tid]
        raw = self.ctx.db.setting(f"task_conf:{tid}")
        c = json.loads(raw) if raw else {}
        opts = {o["key"]: o["default"] for o in task.options}
        opts.update(c.get("options") or {})
        return {"schedule": c.get("schedule") or dict(task.default), "options": opts}

    def _save_conf(self, tid, c):
        self.ctx.db.set_setting(f"task_conf:{tid}", json.dumps(c))

    def last(self, tid):
        raw = self.ctx.db.setting(f"task_last:{tid}")
        return json.loads(raw) if raw else None

    def update(self, tid, schedule=None, options=None):
        c = self.conf(tid)
        if schedule is not None:
            c["schedule"] = valid_schedule(schedule)
        if options is not None:
            c["options"].update(valid_options(self.tasks[tid], options))
        self._save_conf(tid, c)
        self.wake.set()
        return c

    def view(self, tid):
        task = self.tasks[tid]
        c = self.conf(tid)
        last = self.last(tid)
        ok, note = task.available(self.ctx)
        nxt = next_run(c["schedule"], last and last.get("start"), self.anchor) if ok else None
        job = self.job if self.job and self.job.task.id == tid else None
        return {
            "id": tid, "name": task.name, "description": task.description, "category": task.category,
            "heavy": task.heavy, "schedule": c["schedule"], "options": c["options"], "option_defs": task.options,
            "available": ok, "note": note, "last": last, "next": int(nxt) if nxt else None,
            "queued": any(q[0] == tid for q in self.queue),
            "running": {"progress": round(job.progress, 3), "message": job.message, "started": int(job.started),
                        "paused": job.paused, "reason": job.reason} if job else None,
        }

    def views(self):
        return [self.view(t) for t in self.tasks]

    # ------------------------------------------------------------ exécution
    def run_now(self, tid, reason="manuel"):
        task = self.tasks[tid]
        ok, note = task.available(self.ctx)
        if not ok:
            raise ValueError(note)
        with self.lock:
            if (self.job and self.job.task.id == tid) or any(q[0] == tid for q in self.queue):
                return False
            if reason == "manuel":
                self.queue.insert(0, (tid, reason))
            else:
                self.queue.append((tid, reason))
        self.wake.set()
        return True

    def cancel(self, tid):
        with self.lock:
            self.queue = [q for q in self.queue if q[0] != tid]
            if self.job and self.job.task.id == tid:
                self.job.cancel.set()

    def after_scan(self, added):
        """Des fichiers viennent d'être ajoutés : les tâches qui le demandent passent sur les nouveautés."""
        if not added:
            return
        for tid, task in self.tasks.items():
            if any(o["key"] == "after_scan" for o in task.options) and self.conf(tid)["options"].get("after_scan"):
                if task.available(self.ctx)[0]:
                    self.run_now(tid, "nouveaux fichiers")

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        with self.lock:
            if self.job:
                self.job.cancel.set()

    def _due(self):
        now = time.time()
        for tid, task in self.tasks.items():
            c = self.conf(tid)
            if c["schedule"].get("mode") == "off":
                continue
            last = self.last(tid)
            nxt = next_run(c["schedule"], last and last.get("start"), self.anchor)
            if nxt and now >= nxt and task.available(self.ctx)[0]:
                yield tid

    def _loop(self):
        while not self.stopped.is_set():
            self.wake.wait(20)
            self.wake.clear()
            try:
                for tid in list(self._due()):
                    self.run_now(tid, "planifié")
            except Exception:
                log.exception("Planification des tâches")
            finally:
                self.ctx.db.release()
            while not self.stopped.is_set():
                with self.lock:
                    if not self.queue:
                        break
                    tid, reason = self.queue.pop(0)
                    job = self.job = Job(self.tasks[tid], reason)
                self._execute(job)
                with self.lock:
                    self.job = None

    def _execute(self, job):
        task = job.task
        opts = self.conf(task.id)["options"]
        ctx = self.ctx

        def wait_idle():
            job.check()
            if not (task.heavy and opts.get("pause_playback", True)):
                return
            while ctx.subtitles._streaming():
                job.paused = True
                job.message = "En pause : une lecture est en cours"
                if job.cancel.wait(10):
                    raise Cancelled()
            job.paused = False
        job.wait_idle = wait_idle
        started = time.time()
        status, summary = "ok", ""
        log.info("Tâche « %s » (%s)", task.name, job.reason)
        try:
            summary = task.run(ctx, job, opts) or ""
        except Cancelled:
            status, summary = "cancelled", "Arrêtée"
        except Exception as e:
            log.exception("Tâche « %s »", task.name)
            status, summary = "error", str(e) or e.__class__.__name__
        finally:
            ctx.db.set_setting(f"task_last:{task.id}", json.dumps({
                "start": int(started), "end": int(time.time()), "status": status, "summary": summary[:300],
                "reason": job.reason}))
            ctx.db.release()


def register(app, ctx, x):
    from .web import HTTPError
    tm = ctx.tasks
    TID = r"(?P<tid>[a-z]{2,20})"

    def task_or_404(req):
        if req.params["tid"] not in tm.tasks:
            raise HTTPError(404, "Tâche inconnue")
        return req.params["tid"]

    @app.route("GET", "/api/admin/tasks", auth="admin")
    def admin_tasks(req):
        return {"tasks": tm.views(), "libraries": [{"id": l["id"], "name": l["title"], "kind": l["type"]}
                                                   for l in x.libraries_all()]}

    @app.route("PUT", f"/api/admin/tasks/{TID}", auth="admin")
    def admin_task_update(req):
        tid = task_or_404(req)
        b = req.json()
        try:
            tm.update(tid, schedule=b.get("schedule"), options=b.get("options"))
        except ValueError as e:
            raise HTTPError(400, str(e))
        return tm.view(tid)

    @app.route("POST", f"/api/admin/tasks/{TID}/run", auth="admin")
    def admin_task_run(req):
        tid = task_or_404(req)
        try:
            tm.run_now(tid)
        except ValueError as e:
            raise HTTPError(409, str(e))
        return tm.view(tid)

    @app.route("POST", f"/api/admin/tasks/{TID}/cancel", auth="admin")
    def admin_task_cancel(req):
        tid = task_or_404(req)
        tm.cancel(tid)
        return tm.view(tid)
