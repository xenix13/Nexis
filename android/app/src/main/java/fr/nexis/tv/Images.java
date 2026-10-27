package fr.nexis.tv;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.LruCache;
import android.widget.ImageView;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Affiches, vignettes, avatars et logos : chargés en arrière-plan avec le cookie de session, gardés en mémoire. */
final class Images {
    private static final LruCache<String, Bitmap> CACHE = new LruCache<String, Bitmap>(
            (int) Math.min(64L * 1024 * 1024, Runtime.getRuntime().maxMemory() / 6)) {
        @Override
        protected int sizeOf(String key, Bitmap b) {
            return b.getByteCount();
        }
    };
    private static final ExecutorService POOL = Executors.newFixedThreadPool(4);

    private Images() {}

    /** Image du catalogue (« /library/metadata/… ») redimensionnée par le serveur. */
    static String library(String path, int w, int h) {
        if (path == null || path.isEmpty() || "null".equals(path)) return null;
        return "/api/img?p=" + Api.enc(path) + "&w=" + w + "&h=" + h;
    }

    static void load(ImageView view, String path) {
        load(view, path, 0);
    }

    /** maxSide : réduit les images trop grandes (logos de chaînes), 0 pour garder la taille d'origine. */
    static void load(ImageView view, String path, int maxSide) {
        view.setTag(R.id.nexis_image, path);
        if (path == null) {
            view.setImageDrawable(null);
            return;
        }
        Bitmap hit = CACHE.get(path);
        if (hit != null) {
            view.setImageBitmap(hit);
            return;
        }
        view.setImageDrawable(null);
        POOL.execute(() -> {
            Bitmap b = fetch(path, maxSide);
            if (b == null) return;
            CACHE.put(path, b);
            Api.MAIN.post(() -> {
                if (path.equals(view.getTag(R.id.nexis_image))) view.setImageBitmap(b);
            });
        });
    }

    private static Bitmap fetch(String path, int maxSide) {
        HttpURLConnection c = null;
        try {
            String url = Api.url(path);
            c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(10000);
            c.setReadTimeout(30000);
            c.setRequestProperty("User-Agent", Api.VERSION_HEADER);
            String ck = Api.cookieHeader();
            // cookie de session : uniquement vers le serveur Nexis, jamais vers une adresse d'image extérieure
            if (!ck.isEmpty() && url.startsWith(Api.server() + "/")) c.setRequestProperty("Cookie", ck);
            if (c.getResponseCode() != 200) return null;
            byte[] data;
            try (InputStream in = c.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                data = out.toByteArray();
            }
            BitmapFactory.Options o = new BitmapFactory.Options();
            if (maxSide > 0) {
                o.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(data, 0, data.length, o);
                int sample = 1;
                while (Math.max(o.outWidth, o.outHeight) / (sample * 2) >= maxSide) sample *= 2;
                o = new BitmapFactory.Options();
                o.inSampleSize = sample;
            }
            return BitmapFactory.decodeByteArray(data, 0, data.length, o);
        } catch (Exception e) {
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }
}
