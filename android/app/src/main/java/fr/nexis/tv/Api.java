package fr.nexis.tv;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Accès à l'API du serveur Nexis : adresse, cookie de session (gardé entre deux lancements), requêtes JSON.
 * Les appels réseau passent par un petit groupe de fils ; les réponses reviennent sur le fil principal.
 */
final class Api {
    static final String VERSION_HEADER = "NexisApp/" + BuildConfig.VERSION_NAME + " (Android TV)";
    private static final ExecutorService IO = Executors.newFixedThreadPool(4);
    static final Handler MAIN = new Handler(Looper.getMainLooper());

    private static SharedPreferences store;
    private static String server;
    private static final Map<String, String> cookies = new LinkedHashMap<>();

    interface Ok<T> {
        void run(T value);
    }

    interface Fail {
        void run(Error e);
    }

    /** Erreur renvoyée par le serveur (code HTTP + message en français) ou réseau (code 0). */
    static final class Error extends Exception {
        final int code;

        Error(int code, String message) {
            super(message);
            this.code = code;
        }
    }

    private Api() {}

    static synchronized void init(Context c) {
        if (store != null) return;
        store = c.getApplicationContext().getSharedPreferences("nexis", Context.MODE_PRIVATE);
        server = store.getString("server", null);
        String raw = store.getString("cookies", "");
        for (String part : raw.split(";")) {
            int eq = part.indexOf('=');
            if (eq > 0) cookies.put(part.substring(0, eq).trim(), part.substring(eq + 1).trim());
        }
    }

    static String server() {
        return server;
    }

    static synchronized void setServer(String url) {
        if (url != null && !url.equals(server)) cookies.clear();
        server = url;
        store.edit().putString("server", url).putString("cookies", cookieHeader()).apply();
    }

    static SharedPreferences store() {
        return store;
    }

    static synchronized String cookieHeader() {
        StringBuilder b = new StringBuilder();
        for (Map.Entry<String, String> e : cookies.entrySet()) {
            if (b.length() > 0) b.append("; ");
            b.append(e.getKey()).append('=').append(e.getValue());
        }
        return b.toString();
    }

    static synchronized void forgetSession() {
        cookies.clear();
        store.edit().putString("cookies", "").apply();
    }

    private static synchronized void keepCookies(HttpURLConnection c) {
        boolean changed = false;
        for (Map.Entry<String, List<String>> h : c.getHeaderFields().entrySet()) {
            if (h.getKey() == null || !h.getKey().equalsIgnoreCase("Set-Cookie")) continue;
            for (String v : h.getValue()) {
                String first = v.split(";", 2)[0];
                int eq = first.indexOf('=');
                if (eq <= 0) continue;
                String name = first.substring(0, eq).trim(), value = first.substring(eq + 1).trim();
                boolean expired = value.isEmpty() || v.toLowerCase().contains("max-age=0");
                if (expired) cookies.remove(name);
                else cookies.put(name, value);
                changed = true;
            }
        }
        if (changed) store.edit().putString("cookies", cookieHeader()).apply();
    }

    static String url(String path) {
        return path.startsWith("http") ? path : server + path;
    }

    static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (java.io.UnsupportedEncodingException e) {
            return s;
        }
    }

    /** Requête synchrone (à appeler hors du fil principal). -> JSONObject, JSONArray ou null. */
    static Object call(String method, String path, JSONObject body) throws Error {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(url(path)).openConnection();
            c.setRequestMethod(method);
            c.setConnectTimeout(10000);
            c.setReadTimeout(60000);
            c.setRequestProperty("Accept", "application/json");
            c.setRequestProperty("User-Agent", VERSION_HEADER);
            c.setRequestProperty("X-Seance", "1");  // exigé par le serveur pour toute requête qui modifie
            c.setRequestProperty("X-Lang", Tr.lang);  // messages d'erreur du serveur dans la langue de l'appli
            String ck = cookieHeader();
            if (!ck.isEmpty()) c.setRequestProperty("Cookie", ck);
            if (body != null || !"GET".equals(method)) {
                byte[] data = (body == null ? "{}" : body.toString()).getBytes(StandardCharsets.UTF_8);
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setFixedLengthStreamingMode(data.length);
                try (OutputStream out = c.getOutputStream()) {
                    out.write(data);
                }
            }
            int status = c.getResponseCode();
            keepCookies(c);
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            String text = in == null ? "" : read(in);
            Object json = text.isEmpty() ? null : parse(text);
            if (status >= 400) {
                String msg = json instanceof JSONObject ? ((JSONObject) json).optString("error", "") : "";
                if (msg.isEmpty()) msg = status == 401 ? Tr.t("Session expirée : reconnectez-vous") : Tr.t("Erreur du serveur (") + status + ")";
                throw new Error(status, msg);
            }
            return json;
        } catch (IOException e) {
            throw new Error(0, Tr.t("Le serveur ne répond pas. Vérifiez la connexion réseau."));
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private static Object parse(String text) {
        try {
            return new JSONTokener(text).nextValue();
        } catch (JSONException e) {
            return null;
        }
    }

    static String read(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        in.close();
        return out.toString("UTF-8");
    }

    /** Travail en arrière-plan, résultat (ou erreur) rendu sur le fil principal. */
    static <T> void run(Callable<T> work, Ok<T> ok, Fail fail) {
        IO.execute(() -> {
            try {
                T v = work.call();
                MAIN.post(() -> ok.run(v));
            } catch (Error e) {
                if (fail != null) MAIN.post(() -> fail.run(e));
            } catch (Exception e) {
                if (fail != null) MAIN.post(() -> fail.run(new Error(0, Tr.t("Réponse inattendue du serveur"))));
            }
        });
    }

    static void get(String path, Ok<Object> ok, Fail fail) {
        run(() -> call("GET", path, null), ok, fail);
    }

    static void obj(String path, Ok<JSONObject> ok, Fail fail) {
        run(() -> asObj(call("GET", path, null)), ok, fail);
    }

    static void arr(String path, Ok<JSONArray> ok, Fail fail) {
        run(() -> asArr(call("GET", path, null)), ok, fail);
    }

    static void send(String method, String path, JSONObject body, Ok<JSONObject> ok, Fail fail) {
        run(() -> asObj(call(method, path, body)), ok, fail);
    }

    /** Envoi sans attendre la réponse (position de lecture, arrêt d'un flux). */
    static void fire(String method, String path, JSONObject body) {
        IO.execute(() -> {
            try {
                call(method, path, body);
            } catch (Error ignored) {
                // sans conséquence : la prochaine mise à jour rattrapera
            }
        });
    }

    static JSONObject asObj(Object o) {
        return o instanceof JSONObject ? (JSONObject) o : new JSONObject();
    }

    static JSONArray asArr(Object o) {
        return o instanceof JSONArray ? (JSONArray) o : new JSONArray();
    }

    static JSONObject json(Object... kv) {
        JSONObject o = new JSONObject();
        try {
            for (int i = 0; i + 1 < kv.length; i += 2) o.put((String) kv[i], kv[i + 1] == null ? JSONObject.NULL : kv[i + 1]);
        } catch (JSONException ignored) {
            // clés littérales : impossible
        }
        return o;
    }

    /** Vérifie qu'une adresse répond comme un serveur Nexis. -> null si oui, sinon le message à afficher. */
    static String probe(String url) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(url + "/api/status").openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            if (c.getResponseCode() != 200) return Tr.t("Ce serveur a répondu ") + c.getResponseCode() + Tr.t(" : s'agit-il bien d'un serveur Nexis ?");
            String body = read(c.getInputStream());
            if (!body.contains("\"version\"")) return Tr.t("Ce n'est pas un serveur Nexis.");
            return null;
        } catch (Exception e) {
            return Tr.t("Impossible de joindre le serveur. Vérifiez l'adresse et la connexion réseau.");
        } finally {
            if (c != null) c.disconnect();
        }
    }

    static String normalize(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.isEmpty()) return null;
        if (!s.startsWith("http://") && !s.startsWith("https://")) {
            // adresse locale sans protocole : http ; nom de domaine : https (reverse proxy)
            s = (s.matches("^[0-9.]+(:\\d+)?(/.*)?$") || s.contains(":") ? "http://" : "https://") + s;
        }
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }
}
