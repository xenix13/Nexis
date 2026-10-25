package fr.nexis.tv;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Nexis pour Android TV : demande l'adresse du serveur au premier lancement, puis affiche l'interface web de
 * Nexis en plein écran. Toute l'application vit sur le serveur (mise à jour en même temps que lui) ; la
 * télécommande est gérée par l'interface (flèches, OK), la touche Retour lui est transmise.
 */
public class MainActivity extends Activity {
    private static final int BG = Color.rgb(11, 11, 14);
    private static final int ACCENT = Color.rgb(255, 164, 58);

    private SharedPreferences prefs;
    private FrameLayout root;
    private WebView web;
    private View fullscreenView;
    private WebChromeClient.CustomViewCallback fullscreenCallback;
    private final Handler main = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("nexis", MODE_PRIVATE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        setContentView(root);
        immersive();
        String server = prefs.getString("server", null);
        if (server == null) showSetup(null);
        else showWeb(server);
    }

    // ------------------------------------------------------------------ premier lancement : adresse du serveur
    private void showSetup(String error) {
        destroyWeb();
        root.removeAllViews();
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        int pad = dp(32);
        box.setPadding(pad, pad, pad, pad);

        ImageView logo = new ImageView(this);
        logo.setImageResource(R.mipmap.ic_launcher);
        box.addView(logo, new LinearLayout.LayoutParams(dp(96), dp(96)));

        TextView title = text("Adresse de ton serveur Nexis", 26, Color.WHITE);
        title.setPadding(0, dp(16), 0, dp(6));
        box.addView(title);
        TextView hint = text("Par exemple https://nexis.mondomaine.fr ou 192.168.1.20:8420", 15, Color.rgb(170, 170, 180));
        hint.setPadding(0, 0, 0, dp(18));
        box.addView(hint);

        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setImeOptions(EditorInfo.IME_ACTION_GO);
        input.setTextColor(Color.WHITE);
        input.setHintTextColor(Color.rgb(120, 120, 130));
        input.setHint("https://…");
        input.setText(prefs.getString("server", ""));
        input.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
        input.setBackground(rounded(Color.rgb(28, 28, 33), ACCENT));
        input.setPadding(dp(16), dp(12), dp(16), dp(12));
        box.addView(input, new LinearLayout.LayoutParams(dp(560), ViewGroup.LayoutParams.WRAP_CONTENT));

        final TextView status = text(error == null ? "" : error, 16, Color.rgb(255, 120, 110));
        status.setPadding(0, dp(12), 0, dp(12));
        box.addView(status);

        final Button go = new Button(this);
        go.setText("Se connecter");
        go.setAllCaps(false);
        go.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        go.setTextColor(Color.BLACK);
        go.setBackground(rounded(ACCENT, ACCENT));
        go.setPadding(dp(28), dp(10), dp(28), dp(10));
        box.addView(go);

        final Runnable submit = () -> connect(input.getText().toString(), status, go);
        go.setOnClickListener(v -> submit.run());
        input.setOnEditorActionListener((v, actionId, ev) -> {
            submit.run();
            return true;
        });

        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        root.addView(box, lp);
        input.requestFocus();
    }

    /** Vérifie que l'adresse répond comme un serveur Nexis (/api/status), puis l'enregistre. */
    private void connect(String raw, TextView status, Button go) {
        final String url = normalize(raw);
        if (url == null) {
            status.setText("Saisis l'adresse du serveur.");
            return;
        }
        status.setTextColor(Color.rgb(170, 170, 180));
        status.setText("Connexion à " + url + "…");
        go.setEnabled(false);
        new Thread(() -> {
            String err = probe(url);
            main.post(() -> {
                go.setEnabled(true);
                if (err != null) {
                    status.setTextColor(Color.rgb(255, 120, 110));
                    status.setText(err);
                    return;
                }
                prefs.edit().putString("server", url).apply();
                showWeb(url);
            });
        }).start();
    }

    private static String normalize(String raw) {
        String s = raw == null ? "" : raw.trim();
        if (s.isEmpty()) return null;
        if (!s.startsWith("http://") && !s.startsWith("https://")) {
            // adresse locale sans protocole : http ; nom de domaine : https (reverse proxy)
            s = (s.matches("^[0-9.]+(:\\d+)?(/.*)?$") || s.contains(":") ? "http://" : "https://") + s;
        }
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }

    private static String probe(String url) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(url + "/api/status").openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            if (c.getResponseCode() != 200) return "Ce serveur a répondu " + c.getResponseCode() + " : est-ce bien Nexis ?";
            try (InputStream in = c.getInputStream()) {
                byte[] buf = new byte[4096];
                int n = in.read(buf);
                String body = n > 0 ? new String(buf, 0, n, StandardCharsets.UTF_8) : "";
                if (!body.contains("\"version\"")) return "Ce n'est pas un serveur Nexis.";
            }
            return null;
        } catch (Exception e) {
            return "Impossible de joindre le serveur (" + e.getClass().getSimpleName() + "). Vérifie l'adresse et le réseau.";
        } finally {
            if (c != null) c.disconnect();
        }
    }

    // ------------------------------------------------------------------ l'interface de Nexis
    private void showWeb(final String server) {
        root.removeAllViews();
        destroyWeb();
        web = new WebView(this);
        web.setBackgroundColor(BG);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        // « NexisApp » : l'interface passe en mode télécommande (voir static/js/remote.js)
        s.setUserAgentString(s.getUserAgentString() + " NexisApp/" + BuildConfig.VERSION_NAME + " (Android TV)");
        CookieManager.getInstance().setAcceptCookie(true);
        if (Build.VERSION.SDK_INT >= 21) CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);

        final String host = Uri.parse(server).getHost();
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // on reste sur le serveur Nexis ; le reste (liens externes) n'a rien à faire sur la TV
                String h = request.getUrl().getHost();
                return h != null && !h.equals(host);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showSetup("Le serveur " + server + " ne répond pas. Vérifie le réseau, ou change d'adresse.");
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            // plein écran d'une vidéo (bandes-annonces YouTube…)
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                fullscreenView = view;
                fullscreenCallback = callback;
                root.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
            }

            @Override
            public void onHideCustomView() {
                hideFullscreen();
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        web.loadUrl(server + "/");
        web.requestFocus();
    }

    private void hideFullscreen() {
        if (fullscreenView == null) return;
        root.removeView(fullscreenView);
        fullscreenView = null;
        if (fullscreenCallback != null) fullscreenCallback.onCustomViewHidden();
        fullscreenCallback = null;
    }

    private void destroyWeb() {
        if (web == null) return;
        root.removeView(web);
        web.destroy();
        web = null;
    }

    // ------------------------------------------------------------------ touche Retour de la télécommande
    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (fullscreenView != null) {
            hideFullscreen();
            return;
        }
        if (web == null) {
            super.onBackPressed();
            return;
        }
        // Nexis ferme d'abord ce qui est ouvert (menu, fenêtre, lecteur) ou revient à la page précédente
        web.evaluateJavascript("window.nexisBack ? String(window.nexisBack()) : 'false'", value -> {
            if (value != null && value.contains("true")) return;
            new AlertDialog.Builder(this)
                    .setTitle("Quitter Nexis ?")
                    .setPositiveButton("Quitter", (d, w) -> finish())
                    .setNeutralButton("Changer de serveur", (d, w) -> showSetup(null))
                    .setNegativeButton("Rester", null)
                    .show();
        });
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        // certaines télécommandes envoient « Retour » sous forme de touche Échap / BACK : même traitement
        if (event.getKeyCode() == KeyEvent.KEYCODE_BACK && event.getAction() == KeyEvent.ACTION_UP) {
            onBackPressed();
            return true;
        }
        if (event.getKeyCode() == KeyEvent.KEYCODE_BACK) return true;
        return super.dispatchKeyEvent(event);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush(); // rester connecté après redémarrage de la TV
        if (web != null) web.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        immersive();
        if (web != null) web.onResume();
    }

    @Override
    protected void onDestroy() {
        destroyWeb();
        super.onDestroy();
    }

    // ------------------------------------------------------------------ petits outils d'affichage
    @SuppressWarnings("deprecation")
    private void immersive() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }

    private TextView text(String s, int sp, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        t.setGravity(Gravity.CENTER);
        return t;
    }

    private GradientDrawable rounded(int fill, int stroke) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(fill);
        d.setCornerRadius(dp(10));
        d.setStroke(dp(2), stroke);
        return d;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
