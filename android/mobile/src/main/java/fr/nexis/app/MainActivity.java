package fr.nexis.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.Manifest;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.app.NotificationManager;
import android.app.ProgressDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;

/**
 * Noxa pour téléphones et tablettes : affiche l'interface du serveur Noxa choisi (elle suit donc chaque mise à jour
 * du serveur), avec le plein écran vidéo, l'envoi de fichiers, les téléchargements de fichiers et la mise à jour de
 * l'application depuis le serveur (/api/apps, /apps/android).
 */
public class MainActivity extends Activity {
    private static final String PREFS = "nexis";
    private static final int PICK_FILE = 1;
    private static final int ASK_NOTIFY = 2;
    private static final long UPDATE_EVERY = 6 * 3600 * 1000L;

    private WebView web;
    private FrameLayout root;
    private View fullscreen;
    private WebChromeClient.CustomViewCallback fullscreenDone;
    private ValueCallback<Uri[]> pendingFiles;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private long lastUpdateCheck = 0;
    private boolean downloading = false; // service de téléchargement en arrière-plan lancé
    private boolean fr;

    private SharedPreferences prefs() { return getSharedPreferences(PREFS, MODE_PRIVATE); }
    private String server() { return prefs().getString("server", null); }

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        fr = Locale.getDefault().getLanguage().equals("fr");
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(11, 11, 14));
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(11, 11, 14));
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(true);
        s.setUserAgentString(s.getUserAgentString() + " NexisApp/" + BuildConfig.VERSION_NAME);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.addJavascriptInterface(new Bridge(), "NexisAndroid");
        // moteur de la page gardé prioritaire même application quittée (téléchargements en cours)
        web.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                String scheme = u.getScheme() == null ? "" : u.getScheme();
                if (scheme.equals("file") || sameOrigin(u)) return false;
                openOutside(u); // autre site, mailto:, intent: … : hors de l'application
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame() && server() != null) {
                    showSetup("offline=" + Uri.encode(server()) + "&error=" + Uri.encode(String.valueOf(err.getDescription())));
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            // vidéo en plein écran (bouton du lecteur) : la vue est posée par-dessus, barres système masquées
            @Override
            public void onShowCustomView(View view, CustomViewCallback done) {
                if (fullscreen != null) { done.onCustomViewHidden(); return; }
                fullscreen = view;
                fullscreenDone = done;
                root.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
                immersive(true);
            }

            @Override
            public void onHideCustomView() {
                if (fullscreen == null) return;
                root.removeView(fullscreen);
                fullscreen = null;
                if (fullscreenDone != null) fullscreenDone.onCustomViewHidden();
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
                immersive(false);
            }

            // <input type="file"> : photo du profil, fond d'écran, sauvegarde à restaurer, thème à importer
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingFiles != null) pendingFiles.onReceiveValue(null);
                pendingFiles = callback;
                try {
                    startActivityForResult(params.createIntent(), PICK_FILE);
                } catch (ActivityNotFoundException e) {
                    pendingFiles = null;
                    return false;
                }
                return true;
            }
        });

        // fichiers à enregistrer (export Letterboxd, sauvegarde…) : gestionnaire de téléchargements d'Android
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> {
            try {
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                String name = URLUtil.guessFileName(url, disposition, mime);
                r.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
                r.addRequestHeader("User-Agent", userAgent);
                r.setMimeType(mime);
                r.setTitle(name);
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
                Toast.makeText(this, R.string.download_started, Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                openOutside(Uri.parse(url));
            }
        });

        NotifyJob.channel(this);
        if (prefs().getString("push_token", null) != null) NotifyJob.schedule(this); // remise en route après une mise à jour

        String open = opened(getIntent());
        if (open != null && server() != null) web.loadUrl(server() + "/" + open);
        else if (saved != null) web.restoreState(saved);
        else if (server() != null) web.loadUrl(server());
        else showSetup("");
    }

    // notification touchée : la page qu'elle annonce (adresse de l'interface, « #/item/12 »)
    private static String opened(Intent i) {
        String u = i == null ? null : i.getStringExtra("open");
        return u != null && u.startsWith("#/") ? u : null;
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String open = opened(intent);
        if (open != null && server() != null) web.loadUrl(server() + "/" + open);
    }

    // ------------------------------------------------------------------ notifications
    private boolean notificationsAllowed() {
        return getSystemService(NotificationManager.class).areNotificationsEnabled();
    }

    private void answerPush(boolean ok) {
        web.evaluateJavascript("window.onNexisPush && onNexisPush(" + ok + ")", null);
    }

    @Override
    public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        if (request == ASK_NOTIFY) {
            answerPush(results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED && notificationsAllowed());
            return;
        }
        super.onRequestPermissionsResult(request, permissions, results);
    }

    private void showSetup(String query) {
        web.loadUrl("file:///android_asset/setup.html" + (query.isEmpty() ? "" : "?" + query));
    }

    private boolean sameOrigin(Uri u) {
        String srv = server();
        if (srv == null) return false;
        Uri s = Uri.parse(srv);
        return eq(s.getScheme(), u.getScheme()) && eq(s.getHost(), u.getHost()) && s.getPort() == u.getPort();
    }

    private static boolean eq(String a, String b) { return a == null ? b == null : a.equalsIgnoreCase(b); }

    private void openOutside(Uri u) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, R.string.no_browser, Toast.LENGTH_SHORT).show();
        }
    }

    private void immersive(boolean on) {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController c = getWindow().getInsetsController();
            if (c == null) return;
            if (on) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            } else {
                c.show(WindowInsets.Type.systemBars());
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(on
                    ? View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    : View.SYSTEM_UI_FLAG_VISIBLE);
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request == PICK_FILE && pendingFiles != null) {
            pendingFiles.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            pendingFiles = null;
            return;
        }
        super.onActivityResult(request, result, data);
    }

    @Override
    public void onBackPressed() {
        if (fullscreen != null) { web.getWebChromeClient().onHideCustomView(); return; }
        if (web.canGoBack()) { web.goBack(); return; }
        super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        if (server() != null && System.currentTimeMillis() - lastUpdateCheck > UPDATE_EVERY) checkUpdate(false);
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush(); // session gardée si Android ferme l'application
        if (!downloading) web.onPause(); // téléchargement en cours : la page continue de travailler
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        // sans la page, plus rien ne télécharge : la notification « Noxa télécharge » disparaît
        if (downloading) stopService(new Intent(this, DownloadService.class));
        downloading = false;
        super.onDestroy();
    }

    // ------------------------------------------------------------------ téléchargements en arrière-plan
    private void downloadsChanged(String json) {
        int count = 0, percent = 0;
        String title = "", text = "";
        try {
            JSONObject j = new JSONObject(json);
            count = j.optInt("active", 0);
            percent = j.optInt("percent", 0);
            title = j.optString("title", "");
            text = j.optString("text", "");
        } catch (Exception ignored) { }
        if (count <= 0) {
            if (downloading) stopService(new Intent(this, DownloadService.class));
            downloading = false;
            return;
        }
        if (downloading) { DownloadService.update(this, count, title, text, percent); return; }
        try {
            startForegroundService(new Intent(this, DownloadService.class)
                    .putExtra("count", count).putExtra("title", title).putExtra("text", text).putExtra("percent", percent));
            downloading = true;
        } catch (Exception e) {
            // Android refuse de lancer le service depuis l'arrière-plan : le téléchargement reprendra au retour
        }
    }

    // ------------------------------------------------------------------ choix du serveur
    private static String probe(String base) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/status").openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(8000);
        c.setRequestProperty("X-Seance", "1");
        try {
            if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode());
            JSONObject j = new JSONObject(read(c.getInputStream(), 65536));
            if (!j.has("version")) throw new Exception("not nexis");
            return j.getString("version");
        } finally {
            c.disconnect();
        }
    }

    private static String read(InputStream in, int max) throws Exception {
        byte[] buf = new byte[8192];
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        int n;
        while ((n = in.read(buf)) > 0 && out.size() < max) out.write(buf, 0, n);
        in.close();
        return out.toString("UTF-8");
    }

    private void setServer(String raw) {
        String s = raw == null ? "" : raw.trim().replaceAll("/+$", "");
        String[] candidates = s.matches("(?i)^https?://.*") ? new String[]{s} : new String[]{"https://" + s, "http://" + s};
        new Thread(() -> {
            String error = null;
            for (String c : candidates) {
                try {
                    probe(c);
                    prefs().edit().putString("server", c).apply();
                    ui.post(() -> { web.clearHistory(); web.loadUrl(c); checkUpdate(false); });
                    return;
                } catch (Exception e) {
                    error = "not nexis".equals(e.getMessage()) ? str("notNexis") : str("unreachable").replace("{error}", String.valueOf(e.getMessage()));
                }
            }
            final String msg = s.isEmpty() ? str("notNexis") : error;
            ui.post(() -> web.evaluateJavascript("window.onServerResult && onServerResult(false, " + JSONObject.quote(msg) + ")", null));
        }, "nexis-server").start();
    }

    // ------------------------------------------------------------------ mise à jour de l'application
    private void checkUpdate(boolean manual) {
        String srv = server();
        if (srv == null) return;
        lastUpdateCheck = System.currentTimeMillis();
        new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(srv + "/api/apps").openConnection();
                c.setConnectTimeout(10000);
                c.setReadTimeout(15000);
                JSONObject j = new JSONObject(read(c.getInputStream(), 1 << 20));
                c.disconnect();
                JSONObject a = j.optJSONObject("versions") == null ? null : j.getJSONObject("versions").optJSONObject("android");
                if (a != null && a.optInt("code", 0) > BuildConfig.VERSION_CODE) {
                    String version = a.optString("version", "");
                    ui.post(() -> offerUpdate(srv, version));
                } else if (manual) {
                    ui.post(() -> Toast.makeText(this, getString(R.string.up_to_date, BuildConfig.VERSION_NAME), Toast.LENGTH_LONG).show());
                }
            } catch (Exception ignored) {
                // hors ligne, ou serveur sans applications publiées : on réessaiera
            }
        }, "nexis-update").start();
    }

    private void offerUpdate(String srv, String version) {
        if (isFinishing()) return;
        new AlertDialog.Builder(this)
                .setTitle(getString(R.string.update_title, version))
                .setMessage(R.string.update_text)
                .setPositiveButton(R.string.update_now, (d, w) -> downloadUpdate(srv))
                .setNegativeButton(R.string.later, null)
                .show();
    }

    @SuppressWarnings("deprecation")
    private void downloadUpdate(String srv) {
        if (Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
            Toast.makeText(this, R.string.allow_install, Toast.LENGTH_LONG).show();
            startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getPackageName())));
            return;
        }
        ProgressDialog progress = new ProgressDialog(this);
        progress.setMessage(getString(R.string.update_downloading));
        progress.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);
        progress.setCancelable(false);
        progress.show();
        new Thread(() -> {
            try {
                File dir = new File(getCacheDir(), "updates");
                dir.mkdirs();
                File apk = new File(dir, "noxa-android.apk");
                HttpURLConnection c = (HttpURLConnection) new URL(srv + "/apps/android").openConnection();
                c.setInstanceFollowRedirects(true);
                c.setConnectTimeout(15000);
                c.setReadTimeout(60000);
                int code = c.getResponseCode();
                if (code / 100 == 3) { // le serveur renvoie vers GitHub tant qu'il n'a pas sa copie
                    String loc = c.getHeaderField("Location");
                    c.disconnect();
                    c = (HttpURLConnection) new URL(loc).openConnection();
                    code = c.getResponseCode();
                }
                if (code != 200) throw new Exception("HTTP " + code);
                long total = c.getContentLengthLong();
                try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(apk)) {
                    byte[] buf = new byte[65536];
                    long got = 0;
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        out.write(buf, 0, n);
                        got += n;
                        if (total > 0) { final int pct = (int) (got * 100 / total); ui.post(() -> progress.setProgress(pct)); }
                    }
                }
                c.disconnect();
                ui.post(() -> {
                    progress.dismiss();
                    Uri uri = FileProvider.getUriForFile(this, "fr.nexis.app.files", apk);
                    Intent install = new Intent(Intent.ACTION_VIEW);
                    install.setDataAndType(uri, "application/vnd.android.package-archive");
                    install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(install);
                });
            } catch (Exception e) {
                ui.post(() -> {
                    progress.dismiss();
                    Toast.makeText(this, getString(R.string.update_failed, String.valueOf(e.getMessage())), Toast.LENGTH_LONG).show();
                });
            }
        }, "nexis-apk").start();
    }

    // ------------------------------------------------------------------ textes de l'écran de choix du serveur
    private String str(String key) {
        try { return new JSONObject(strings()).getString(key); } catch (Exception e) { return key; }
    }

    private String strings() {
        try {
            JSONObject j = new JSONObject();
            j.put("connect", fr ? "Se connecter" : "Connect");
            j.put("checking", fr ? "Vérification…" : "Checking…");
            j.put("setupTitle", fr ? "Votre serveur Noxa" : "Your Noxa server");
            j.put("setupLead", fr ? "Adresse du serveur, telle que vous l’ouvrez dans un navigateur." : "The server address, as you open it in a browser.");
            j.put("setupPlaceholder", fr ? "https://noxa.mondomaine.fr ou 192.168.1.20:8420" : "https://noxa.example.com or 192.168.1.20:8420");
            j.put("notNexis", fr ? "Aucun serveur Noxa ne répond à cette adresse." : "No Noxa server answers at this address.");
            j.put("unreachable", fr ? "Serveur injoignable : {error}" : "Server unreachable: {error}");
            j.put("offlineTitle", fr ? "Serveur injoignable" : "Server unreachable");
            j.put("offlineLead", fr ? "Noxa n’a pas pu joindre {server}. Vérifiez la connexion, ou changez de serveur." : "Noxa could not reach {server}. Check the connection, or change server.");
            j.put("retry", fr ? "Réessayer" : "Retry");
            return j.toString();
        } catch (Exception e) {
            return "{}";
        }
    }

    // ------------------------------------------------------------------ passerelle vue par la page (window.NexisAndroid)
    private class Bridge {
        @JavascriptInterface
        public String info() {
            try {
                JSONObject j = new JSONObject();
                j.put("kind", "android");
                j.put("os", "android");
                j.put("version", BuildConfig.VERSION_NAME);
                j.put("code", BuildConfig.VERSION_CODE);
                j.put("server", server());
                return j.toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public String strings() { return MainActivity.this.strings(); }

        @JavascriptInterface
        public void setServer(String url) { MainActivity.this.setServer(url); }

        @JavascriptInterface
        public void changeServer() {
            ui.post(() -> showSetup("current=" + Uri.encode(server() == null ? "" : server())));
        }

        @JavascriptInterface
        public void retry() { ui.post(() -> { if (server() != null) web.loadUrl(server()); }); }

        @JavascriptInterface
        public void checkUpdate() { ui.post(() -> MainActivity.this.checkUpdate(true)); }

        // notifications : inscrit (jeton du serveur) et autorisées par Android ?
        /** État de la file de téléchargement (JSON : active, title, text, percent) ; active = 0 : plus rien en cours. */
        @JavascriptInterface
        public void downloads(String json) { ui.post(() -> downloadsChanged(json)); }

        @JavascriptInterface
        public String pushStatus() {
            try {
                JSONObject j = new JSONObject();
                j.put("registered", prefs().getString("push_token", null) != null);
                j.put("allowed", notificationsAllowed());
                return j.toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public void pushEnable(String token) {
            if (token == null || token.length() < 20) { ui.post(() -> answerPush(false)); return; }
            prefs().edit().putString("push_token", token).apply();
            ui.post(() -> {
                NotifyJob.schedule(MainActivity.this);
                if (Build.VERSION.SDK_INT >= 33
                        && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, ASK_NOTIFY);
                } else {
                    answerPush(notificationsAllowed());
                }
            });
        }

        /** Désinscrit cet appareil ; rend le jeton pour que la page le retire aussi du serveur. */
        @JavascriptInterface
        public String pushDisable() {
            String token = prefs().getString("push_token", null);
            prefs().edit().remove("push_token").apply();
            NotifyJob.cancel(MainActivity.this);
            return token;
        }

        // notification d'essai : on va la chercher tout de suite (le serveur l'a déjà en attente)
        @JavascriptInterface
        public void pushCheck() {
            new Thread(() -> {
                try { Thread.sleep(800); } catch (InterruptedException ignored) { }
                NotifyJob.poll(MainActivity.this);
            }, "nexis-notify-now").start();
        }

        @JavascriptInterface
        public void notificationSettings() {
            ui.post(() -> {
                try {
                    startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
                } catch (ActivityNotFoundException e) {
                    startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName())));
                }
            });
        }
    }
}
