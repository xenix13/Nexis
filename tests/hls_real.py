"""Lecture réelle avec hls.js dans Chromium (VP9 + Opus, que Chromium décode sans codecs propriétaires).

Vérifie le flux HLS de bout en bout : playlist VOD complète, segments fMP4 à la demande,
reprise au milieu, saut loin devant (relance de ffmpeg), retour en arrière.

python3 tests/hls_real.py <chemin vers hls.min.js> [dossier_captures]
"""
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fixtures import FakeTMDB, ff  # noqa: E402

TMDB = FakeTMDB()
os.environ["SEANCE_TMDB_API"] = TMDB.url
os.environ["SEANCE_TMDB_IMG"] = TMDB.url + "/img"

from playwright.sync_api import sync_playwright  # noqa: E402

from seance import scanner  # noqa: E402
from seance.api import Ctx, build_app  # noqa: E402
from seance.web import Server, make_handler  # noqa: E402
from tests.test_api import STATIC, Client, wait_scan  # noqa: E402

hls_src = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp()
os.makedirs(out, exist_ok=True)
vendor = os.path.join(STATIC, "vendor", "hls.min.js")
placed = not os.path.exists(vendor)
if placed:
    shutil.copy(hls_src, vendor)
work = tempfile.mkdtemp()
d = os.path.join(work, "films", "Essai VP9 (2020)")
os.makedirs(d)
from tests.pgs import make_sup  # noqa: E402
make_sup(os.path.join(work, "s.sup"), start=50, end=59, size=(640, 360), box=(160, 40), pos=(240, 290))
ff("-copyts", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25", "-f", "lavfi", "-i", "sine=f=330:sample_rate=48000",
   "-i", os.path.join(work, "s.sup"), "-map", "0:v", "-map", "1:a", "-map", "2:s",
   "-t", "120", "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "600k", "-g", "50",
   "-c:a", "libopus", "-b:a", "96k", "-c:s", "copy", "-metadata:s:s:0", "language=fre",
   os.path.join(d, "Essai VP9 (2020).mkv"))
scanner.RECENT_SECONDS = 0
ctx = Ctx(os.path.join(work, "data"), STATIC)
server = Server(("127.0.0.1", 0), make_handler(build_app(ctx)))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"
admin = Client(base)
admin.call("POST", "/api/setup", {"username": "colas", "password": "motdepasse1"},
           headers={"X-Setup-Code": ctx.setup_code})
wait_scan(admin)
st, lib = admin.call("POST", "/api/admin/libraries", {"name": "Films", "kind": "movie", "paths": [os.path.join(work, "films")]})
wait_scan(admin)
st, items = admin.call("GET", f"/api/libraries/{lib['id']}/items")
movie = items["items"][0]["id"]
failures, logs = [], []


def check(cond, msg):
    print(("OK    " if cond else "ÉCHEC ") + msg)
    if not cond:
        failures.append(msg)


try:
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = b.new_context(viewport={"width": 1280, "height": 720}, bypass_csp=True).new_page()
        page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
        page.goto(base + "/#/login")
        page.fill("input[autocomplete=username]", "colas")
        page.fill("input[type=password]", "motdepasse1")
        page.click("button[type=submit]")
        page.wait_for_selector(".profile-tile, .hero-title, .empty", timeout=20000)
        # reprise au milieu : la position est absolue, le flux démarre au bon segment
        admin.call("POST", "/api/progress", {"id": movie, "position": 50000, "duration": 120000, "state": "stopped"})
        st, det = admin.call("GET", f"/api/items/{movie}")
        sub = det["subtitles"][0]["id"]
        page.goto(base + f"/#/play/{movie}?resume=1&s={sub}")
        page.wait_for_function("document.querySelector('video').currentTime > 51", timeout=60000)
        info = page.evaluate("({t: document.querySelector('video').currentTime, d: document.querySelector('video').duration})")
        check(abs(info["d"] - 120) < 1.5, f"durée complète annoncée ({info['d']:.1f} s)")
        check(50 <= info["t"] < 60, f"reprise à 50 s ({info['t']:.1f} s)")
        page.wait_for_timeout(1500)
        from PIL import Image
        import io
        shot = Image.open(io.BytesIO(page.screenshot(path=os.path.join(out, "pgs-navigateur.png")))).convert("RGB")
        box = shot.crop((500, 590, 780, 650))
        red = sum(1 for px in box.getdata() if px[0] > 150 and px[1] < 50 and px[2] < 50)
        check(red > 5000, f"sous-titre PGS dessiné par le navigateur ({red} pixels rouges)")
        st, streams = admin.call("GET", "/api/admin/streams")
        check(streams and streams[0]["mode"] == "copy", f"VP9 + Opus recopiés tels quels ({streams and streams[0]['mode']})")
        # saut loin devant : ffmpeg relancé, la lecture repart au bon endroit
        page.evaluate("document.querySelector('video').currentTime = 100")
        page.wait_for_function("document.querySelector('video').currentTime > 101 && !document.querySelector('video').paused",
                               timeout=60000)
        check(True, "saut à 100 s puis lecture")
        # retour en arrière hors tampon
        page.evaluate("document.querySelector('video').currentTime = 10")
        page.wait_for_function("(() => { const v = document.querySelector('video'); return v.currentTime > 11 && v.currentTime < 30 && !v.paused; })()",
                               timeout=60000)
        check(True, "retour à 10 s puis lecture")
        page.wait_for_timeout(3000)
        q = page.evaluate("(() => { const v = document.querySelector('video'); const q = v.getVideoPlaybackQuality(); return {t: v.currentTime, dropped: q.droppedVideoFrames, total: q.totalVideoFrames}; })()")
        check(q["t"] > 13, f"lecture continue après le retour ({q['t']:.1f} s)")
        page.screenshot(path=os.path.join(out, "hls-reel.png"))
        b.close()
finally:
    errs = [l for l in logs if l.startswith("error")]
    print("Erreurs console :", errs or "aucune")
    ctx.transcoder.stop_all()
    server.shutdown()
    if placed:
        os.remove(vendor)
    shutil.rmtree(work, ignore_errors=True)
print("Échecs :", failures or "aucun")
sys.exit(1 if failures else 0)
