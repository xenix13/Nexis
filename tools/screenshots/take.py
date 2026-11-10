"""Documentation screenshots, taken on the demo library (see demo_library.py).

python3 tools/screenshots/take.py <demo_media_folder> <output_folder>

Starts a throwaway Noxa server on a temporary data folder, creates an English-speaking admin and a few
profiles, adds the demo libraries, seeds some viewing history, then drives Chromium (Playwright) through the
pages shown in the docs. Images are written as JPEG (docs/images).
"""
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from seance.api import Ctx, build_app  # noqa: E402
from seance.web import Server, make_handler  # noqa: E402
from tests.test_api import STATIC, Client, wait_scan  # noqa: E402
from tests.ui_smoke_stub import STUB, serve_webm  # noqa: E402

media, out = sys.argv[1], sys.argv[2]
# Shown in the Libraries page: point a short path (e.g. /srv/media) at the demo folder for tidier screenshots.
# Run as a regular user: as root every folder looks writable and the page warns about it.
os.makedirs(out, exist_ok=True)
work = tempfile.mkdtemp()
webm = os.path.join(work, "player.webm")
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-t", "20", "-i",
                "mandelbrot=s=1280x720:r=24:maxiter=400:start_scale=1.2:end_scale=0.3", "-c:v", "libvpx-vp9",
                "-b:v", "1M", "-deadline", "realtime", "-cpu-used", "8", webm], check=True)

ctx = Ctx(os.path.join(work, "data"), STATIC)
server = Server(("127.0.0.1", 0), make_handler(build_app(ctx)))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"

admin = Client(base)
st, r = admin.call("POST", "/api/setup", {"username": "alex", "password": "demo-password-1", "display_name": "Alex",
                                          "language": "en"}, headers={"X-Setup-Code": ctx.setup_code})
assert st == 200, r
libs = {}
for name, kind, sub in (("Movies", "movie", "Movies"), ("Shows", "show", "Shows"), ("Photos", "photo", "Photos")):
    st, r = admin.call("POST", "/api/admin/libraries", {"name": name, "kind": kind, "paths": [os.path.join(media, sub)]})
    assert st == 200, r
    libs[name] = r.get("id") or r.get("library", {}).get("id")
    wait_scan(admin, timeout=900)
st, lst = admin.call("GET", "/api/admin/libraries")
libs = {lib["name"]: lib["id"] for lib in (lst["libraries"] if isinstance(lst, dict) else lst)}
admin.call("PUT", "/api/me/prefs", {"audio_lang": "eng", "sub_lang": "eng"})
for name, display in (("sam", "Sam"), ("kids", "Kids")):
    st, r = admin.call("POST", "/api/admin/users", {"username": name, "display_name": display, "managed": True,
                                                    "language": "en"})
    assert st == 200, r

db = ctx.db
# the scan and the .nfo metadata finish in the background: wait until every demo title is there
for _ in range(300):
    if (db.scalar("SELECT COUNT(*) FROM items WHERE type='movie'") >= 12
            and db.scalar("SELECT COUNT(*) FROM items WHERE type='show' AND title IN "
                          "('Harbor Lights', 'Signal Lost', 'Kitchen Atlas')") == 3):
        break
    time.sleep(1)
movies = db.all("SELECT id, title, duration_ms FROM items WHERE type='movie' ORDER BY title")
by_title = {m["title"]: m for m in movies}
episodes = db.all("SELECT id, title, duration_ms, parent_id FROM items WHERE type='episode' ORDER BY id")
shows = db.all("SELECT id, title FROM items WHERE type='show' ORDER BY title")
show_by_title = {s["title"]: s for s in shows}
uid = db.scalar("SELECT id FROM users WHERE username='alex'")
now = int(time.time())

# continue watching: a few movies and an episode in progress
for title, frac in (("The Quiet Orbit", 0.42), ("Glass Tide", 0.67), ("Velvet Circuit", 0.18)):
    m = by_title[title]
    admin.call("POST", "/api/progress", {"id": str(m["id"]), "position": int(m["duration_ms"] * frac),
                                         "duration": m["duration_ms"], "state": "stopped"})
for e in episodes[:2]:
    admin.call("POST", "/api/progress", {"id": str(e["id"]), "position": e["duration_ms"], "duration": e["duration_ms"],
                                         "state": "stopped", "ended": True})
e = episodes[2]
admin.call("POST", "/api/progress", {"id": str(e["id"]), "position": int(e["duration_ms"] * 0.3),
                                     "duration": e["duration_ms"], "state": "stopped"})
for t in ("Halcyon Drift", "Low Light Requiem", "Paper Lanterns"):
    db.run("INSERT OR IGNORE INTO watchlist(user_id, item_id, added_at) VALUES(?,?,?)", (uid, by_title[t]["id"], now))
db.run("INSERT OR IGNORE INTO watchlist(user_id, item_id, added_at) VALUES(?,?,?)",
       (uid, show_by_title["Signal Lost"]["id"], now))
for t, v in (("Northbound Static", 5), ("Salt & Ember", 4), ("Pinewatch", 3)):
    db.run("INSERT OR REPLACE INTO ratings(user_id, item_id, value, updated_at) VALUES(?,?,?,?)",
           (uid, by_title[t]["id"], v, now))

# cold storage: a "USB disk" folder holding copies of three movies
usb = os.path.join(work, "usb-disk")
os.makedirs(usb)
st, r = admin.call("POST", "/api/admin/cold/stores", {"kind": "local", "name": "USB disk", "conf": {"path": usb}})
assert st == 200, r
admin.call("POST", "/api/admin/cold/archive", {"ids": [by_title[t]["id"] for t in ("Pinewatch", "Paper Lanterns",
                                                                                   "Seven Summers South")],
                                               "store": r["id"]})
for _ in range(600):
    st, c = admin.call("GET", "/api/admin/cold")
    if not c.get("jobs"):
        break
    time.sleep(1)

# statistics: 30 days of plausible plays for three accounts
rnd = random.Random(7)
users = [r["id"] for r in db.all("SELECT id FROM users")]
pool = [(m["id"], m["title"], m["duration_ms"]) for m in movies] + [(e["id"], e["title"], e["duration_ms"]) for e in episodes]
for day in range(30):
    for _ in range(rnd.randint(1, 5)):
        item = rnd.choice(pool)
        secs = int(item[2] / 1000 * rnd.uniform(0.3, 1.0))
        end = now - day * 86400 - rnd.randint(0, 6 * 3600)
        db.run("INSERT INTO play_log(user_id, rating_key, title, mode, kbps, started, ended, seconds) "
               "VALUES(?,?,?,?,?,?,?,?)", (rnd.choice(users), str(item[0]), item[1],
                                           rnd.choice(("direct", "direct", "remux", "transcode")),
                                           rnd.choice((4000, 8000, 12000)), end - secs, end, secs))


def save(page, name, full=False, clip=None):
    png = page.screenshot(full_page=full, clip=clip)
    Image.open(io.BytesIO(png)).convert("RGB").save(os.path.join(out, name), quality=86, optimize=True)
    print("saved", name, flush=True)


def settle(page, ms=600):
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass  # the player keeps loading video segments
    try:
        page.wait_for_function("[...document.images].every((i) => i.complete)", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def go(page, route, sel=None, wait=600):
    """Hash navigation does not reload the app: reload so that new preferences and sessions are picked up."""
    page.goto(base + route)
    page.reload()
    if sel:
        try:
            page.wait_for_selector(sel)
        except Exception:
            page.screenshot(path=os.path.join(out, "debug-timeout.png"))
            raise
    settle(page, wait)


def login(page, user="alex", password="demo-password-1"):
    c = page.context
    st = c.request.post(base + "/api/login", data=json.dumps({"username": user, "password": password}),
                        headers={"Content-Type": "application/json", "X-Seance": "1"})
    assert st.ok, st.text()


with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
    bctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", reduced_motion="reduce",
                               bypass_csp=True)
    bctx.route("**/vendor/hls.min.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=STUB))
    bctx.route("**/__test.webm", serve_webm(webm))
    page = bctx.new_page()
    page.on("pageerror", lambda e: print("[pageerror]", e))

    page.goto(base + "/#/login")
    page.wait_for_selector("input[autocomplete='username']")
    settle(page)
    save(page, "login.jpg")
    login(page)
    page.reload()

    go(page, "/#/profiles", wait=800)
    save(page, "profiles.jpg")
    go(page, "/#/", ".hero-title", 1500)
    save(page, "home.jpg")
    go(page, f"/#/library/{libs['Movies']}", ".grid .card")
    save(page, "library.jpg")
    go(page, f"/#/item/{by_title['The Quiet Orbit']['id']}", ".detail-actions", 1200)
    save(page, "movie.jpg")
    go(page, f"/#/item/{show_by_title['Harbor Lights']['id']}", ".episode", 1500)
    save(page, "show.jpg", full=True, clip={"x": 0, "y": 0, "width": 1440, "height": 1400})

    go(page, f"/#/play/{by_title['Northbound Static']['id']}", "video", 6000)
    page.mouse.move(700, 500)
    page.mouse.move(720, 760)
    page.wait_for_timeout(800)
    save(page, "player.jpg")

    go(page, f"/#/library/{libs['Photos']}", None, 1000)
    page.locator("text=All photos").first.click()
    settle(page, 1500)
    save(page, "photos.jpg")

    for name, route, wait in (("settings-dashboard.jpg", "/#/admin/dashboard", 1500),
                              ("settings-libraries.jpg", "/#/admin/libraries", 800),
                              ("settings-stats.jpg", "/#/admin/stats", 1500),
                              ("settings-transcoding.jpg", "/#/admin/transcoding", 800),
                              ("settings-cold.jpg", "/#/admin/cold", 1000),
                              ("settings-tasks.jpg", "/#/admin/tasks", 800),
                              ("settings-users.jpg", "/#/admin/users", 800),
                              ("settings-appearance.jpg", "/#/settings/appearance", 800),
                              ("settings-themes.jpg", "/#/settings/themes", 1500)):
        go(page, route, None, wait)
        save(page, name)

    # a store theme with another layout, to show what personalization can do
    theme = json.load(open(os.path.join(ROOT, "static/themes/ville-neon.json")))
    for prefs in ({"my_themes": [theme]}, {"theme": "t:" + theme["id"], "ui_style": "sidebar"}):
        st = page.context.request.put(base + "/api/me/prefs", data=json.dumps(prefs),
                                      headers={"Content-Type": "application/json", "X-Seance": "1"})
        print("prefs", st.status, st.text()[:200])
    go(page, "/#/", ".hero-title", 1500)
    save(page, "home-neon-sidebar.jpg")
    for prefs in ({"theme": "light", "ui_style": "glass"},):
        page.context.request.put(base + "/api/me/prefs", data=json.dumps(prefs),
                                 headers={"Content-Type": "application/json", "X-Seance": "1"})
    go(page, "/#/", ".hero-title", 1500)
    save(page, "home-light-glass.jpg")
    page.context.request.put(base + "/api/me/prefs", data=json.dumps({"theme": "dark", "ui_style": "classic"}),
                             headers={"Content-Type": "application/json", "X-Seance": "1"})

    # phone
    phone = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                has_touch=True, locale="en-US", reduced_motion="reduce")
    p2 = phone.new_page()
    p2.goto(base + "/#/login")
    p2.fill("input[autocomplete='username']", "alex")
    p2.fill("input[type='password']", "demo-password-1")
    p2.click("button:has-text('Sign in')")
    p2.wait_for_selector("text=Alex")  # several profiles: the app asks "Who's watching?" first
    p2.click("text=Alex")
    p2.wait_for_timeout(800)
    shots = []
    for route, sel in (("/#/", ".hero-title"), (f"/#/item/{show_by_title['Signal Lost']['id']}", ".episode"),
                       (f"/#/library/{libs['Movies']}", ".grid .card")):
        go(p2, route, sel, 1200)
        shots.append(Image.open(io.BytesIO(p2.screenshot())).convert("RGB"))
    w, h = shots[0].size
    gap = 60
    sheet = Image.new("RGB", (len(shots) * w + (len(shots) + 1) * gap, h + 2 * gap), (14, 16, 22))
    for i, s in enumerate(shots):
        sheet.paste(s, (gap + i * (w + gap), gap))
    sheet = sheet.resize((sheet.width // 2, sheet.height // 2), Image.LANCZOS)
    sheet.save(os.path.join(out, "mobile.jpg"), quality=86, optimize=True)
    print("saved mobile.jpg")
    browser.close()
server.shutdown()
