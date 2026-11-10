package fr.nexis.tv;

import android.os.SystemClock;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Débit entre la TV et le serveur, pour la qualité automatique : quelques mégaoctets téléchargés au premier
 * lancement d'une vidéo, et la mesure gardée 10 minutes. Le serveur en déduit l'original ou une qualité convertie.
 */
final class Bandwidth {
    private static final long KEEP_MS = 10 * 60 * 1000;
    private static final long MAX_MS = 3000;
    private static final long WARMUP = 1 << 20;
    private static long kbps, at;

    private Bandwidth() {}

    /** Débit connu (kbit/s), sinon mesuré maintenant ; 0 si la mesure échoue (le serveur garde la dernière). */
    static void get(Api.Ok<Long> done) {
        if (!"auto".equals(Session.pref("quality", "auto"))) {
            done.run(0L);  // qualité fixée par le compte : rien à mesurer
            return;
        }
        if (kbps > 0 && SystemClock.elapsedRealtime() - at < KEEP_MS) {
            done.run(kbps);
            return;
        }
        Api.run(Bandwidth::measure, v -> {
            if (v > 0) {
                kbps = v;
                at = SystemClock.elapsedRealtime();
            }
            done.run(v);
        }, e -> done.run(0L));
    }

    static void lower(long value) {
        kbps = value;
        at = SystemClock.elapsedRealtime();
    }

    private static long measure() throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(Api.url("/api/bandwidth?size=" + (32 << 20))).openConnection();
        try {
            c.setConnectTimeout(5000);
            c.setReadTimeout(5000);
            c.setRequestProperty("User-Agent", Api.VERSION_HEADER);
            String ck = Api.cookieHeader();
            if (!ck.isEmpty()) c.setRequestProperty("Cookie", ck);
            if (c.getResponseCode() != 200) return 0;
            // le premier mégaoctet n'est pas compté : la connexion TCP monte encore en régime (sinon une
            // connexion rapide paraît lente, et un fichier à fort débit est converti sans raison)
            long got = 0, timed = 0, t1 = SystemClock.elapsedRealtime(), t0 = 0;
            byte[] buf = new byte[65536];
            try (InputStream in = c.getInputStream()) {
                int n;
                while ((n = in.read(buf)) > 0) {
                    got += n;
                    long now = SystemClock.elapsedRealtime();
                    if (t0 == 0 && got >= WARMUP) t0 = now;
                    else if (t0 > 0) timed += n;
                    if (now - t1 > MAX_MS) break;
                }
            }
            long end = SystemClock.elapsedRealtime();
            if (t0 > 0 && timed >= 512 * 1024) return timed * 8 / Math.max(1, end - t0);
            return got < 256 * 1024 ? 0 : got * 8 / Math.max(1, end - t1);
        } finally {
            c.disconnect();
        }
    }
}
