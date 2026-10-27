package fr.nexis.tv;

import android.app.AlertDialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/** Écrans de démarrage : attente, adresse du serveur, connexion (avec double authentification), profils. */
final class StartScreens {
    private StartScreens() {}

    /** Colonne centrée sur un fond sombre, avec le logo en tête. */
    static LinearLayout centered(Screen s, FrameLayout[] outer) {
        FrameLayout root = new FrameLayout(s.act);
        root.setBackgroundColor(Ui.BG);
        LinearLayout box = Ui.column(s.act);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        int pad = s.dp(32);
        box.setPadding(pad, pad, pad, pad);
        root.addView(box, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER));
        outer[0] = root;
        return box;
    }

    static void logo(Screen s, LinearLayout box) {
        ImageView logo = new ImageView(s.act);
        logo.setImageResource(R.mipmap.ic_launcher);
        box.addView(logo, new LinearLayout.LayoutParams(s.dp(84), s.dp(84)));
    }

    /** Pastille de profil : photo si le profil en a une, sinon initiale sur sa couleur. */
    static FrameLayout avatar(Screen s, JSONObject u, int sizeDp) {
        FrameLayout f = new FrameLayout(s.act);
        GradientDrawable circle = new GradientDrawable();
        circle.setShape(GradientDrawable.OVAL);
        int color;
        try {
            color = Color.parseColor(u.optString("avatar_color", "#ffa43a"));
        } catch (IllegalArgumentException e) {
            color = Ui.ACCENT;
        }
        circle.setColor(color);
        f.setBackground(circle);
        f.setClipToOutline(true);
        String name = u.optString("display_name", u.optString("username", "?"));
        TextView initial = Ui.title(s.act, name.isEmpty() ? "?" : name.substring(0, 1).toUpperCase(), sizeDp * 0.42f);
        initial.setGravity(Gravity.CENTER);
        f.addView(initial, new FrameLayout.LayoutParams(-1, -1));
        String url = u.optString("avatar", "");
        if (!url.isEmpty() && !"null".equals(url)) {
            ImageView img = new ImageView(s.act);
            img.setScaleType(ImageView.ScaleType.CENTER_CROP);
            Images.load(img, url);
            f.addView(img, new FrameLayout.LayoutParams(-1, -1));
        }
        StateListDrawable ring = new StateListDrawable();
        GradientDrawable on = new GradientDrawable();
        on.setShape(GradientDrawable.OVAL);
        on.setStroke(s.dp(4), Ui.ACCENT);
        ring.addState(new int[]{android.R.attr.state_selected}, on);
        ring.addState(new int[]{}, new GradientDrawable());
        f.setForeground(ring);
        return f;
    }
}

/** Attente (vérification de la session au lancement). */
final class LoadingScreen extends Screen {
    private final String message;

    LoadingScreen(MainActivity act, String message) {
        super(act);
        this.message = message;
    }

    @Override
    View create() {
        FrameLayout[] root = new FrameLayout[1];
        LinearLayout box = StartScreens.centered(this, root);
        StartScreens.logo(this, box);
        ProgressBar p = new ProgressBar(act);
        box.addView(p, Ui.margins(act, 0, 24, 0, 12));
        TextView t = Ui.text(act, message, 16, Ui.MUTED);
        t.setGravity(Gravity.CENTER);
        box.addView(t);
        return root[0];
    }
}

/** Adresse du serveur (premier lancement, ou serveur injoignable). */
final class SetupScreen extends Screen {
    private final String error;

    SetupScreen(MainActivity act, String error) {
        super(act);
        this.error = error;
    }

    @Override
    View create() {
        FrameLayout[] root = new FrameLayout[1];
        LinearLayout box = StartScreens.centered(this, root);
        StartScreens.logo(this, box);
        TextView title = Ui.title(act, "Adresse du serveur Nexis", 26);
        title.setPadding(0, dp(16), 0, dp(6));
        box.addView(title);
        TextView hint = Ui.text(act, "Exemple : https://nexis.mondomaine.fr ou 192.168.1.20:8420", 15, Ui.MUTED);
        hint.setPadding(0, 0, 0, dp(18));
        box.addView(hint);

        EditText input = Ui.input(act, "https://…", false);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setImeOptions(EditorInfo.IME_ACTION_GO);
        input.setText(Api.server() == null ? "" : Api.server());
        box.addView(input, new LinearLayout.LayoutParams(dp(560), ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView status = Ui.text(act, error == null ? "" : error, 16, Ui.ERROR);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, dp(12), 0, dp(12));
        box.addView(status, new LinearLayout.LayoutParams(dp(560), ViewGroup.LayoutParams.WRAP_CONTENT));

        Button go = Ui.button(act, "Continuer", true);
        box.addView(go);
        Runnable submit = () -> {
            String url = Api.normalize(input.getText().toString());
            if (url == null) {
                status.setText("Saisissez l'adresse du serveur.");
                return;
            }
            status.setTextColor(Ui.MUTED);
            status.setText("Connexion à " + url + "…");
            go.setEnabled(false);
            Api.run(() -> Api.probe(url), err -> {
                go.setEnabled(true);
                if (err != null) {
                    status.setTextColor(Ui.ERROR);
                    status.setText(err);
                    return;
                }
                Api.setServer(url);
                act.start();
            }, null);
        };
        go.setOnClickListener(v -> submit.run());
        input.setOnEditorActionListener((v, id, ev) -> {
            submit.run();
            return true;
        });
        input.requestFocus();
        return root[0];
    }
}

/** Connexion : identifiant et mot de passe, puis code de double authentification si le compte en a une. */
final class LoginScreen extends Screen {
    private LinearLayout box;
    private TextView status;

    LoginScreen(MainActivity act) {
        super(act);
    }

    @Override
    View create() {
        FrameLayout[] root = new FrameLayout[1];
        box = StartScreens.centered(this, root);
        credentials();
        return root[0];
    }

    private void header(String title, String hint) {
        box.removeAllViews();
        StartScreens.logo(this, box);
        TextView t = Ui.title(act, title, 26);
        t.setPadding(0, dp(16), 0, dp(6));
        box.addView(t);
        TextView h = Ui.text(act, hint, 15, Ui.MUTED);
        h.setGravity(Gravity.CENTER);
        h.setPadding(0, 0, 0, dp(18));
        box.addView(h);
    }

    private TextView status() {
        status = Ui.text(act, "", 16, Ui.ERROR);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, dp(10), 0, dp(10));
        return status;
    }

    private void credentials() {
        header("Connexion", Api.server());
        EditText user = Ui.input(act, "Identifiant", false);
        EditText pass = Ui.input(act, "Mot de passe", true);
        pass.setImeOptions(EditorInfo.IME_ACTION_GO);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(460), ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(12);
        box.addView(user, lp);
        box.addView(pass, new LinearLayout.LayoutParams(dp(460), ViewGroup.LayoutParams.WRAP_CONTENT));
        box.addView(status(), new LinearLayout.LayoutParams(dp(460), ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout buttons = Ui.row(act);
        Button go = Ui.button(act, "Se connecter", true);
        Button server = Ui.button(act, "Changer de serveur", false);
        buttons.addView(go, Ui.margins(act, 0, 0, 12, 0));
        buttons.addView(server);
        box.addView(buttons);
        Runnable submit = () -> {
            status.setText("");
            go.setEnabled(false);
            Api.send("POST", "/api/login", Api.json("username", user.getText().toString(), "password",
                    pass.getText().toString()), r -> {
                go.setEnabled(true);
                if (r.optBoolean("two_factor")) secondFactor(r.optString("ticket"));
                else act.afterLogin(true);
            }, e -> {
                go.setEnabled(true);
                status.setText(e.getMessage());
            });
        };
        go.setOnClickListener(v -> submit.run());
        pass.setOnEditorActionListener((v, id, ev) -> {
            submit.run();
            return true;
        });
        server.setOnClickListener(v -> act.reset(new SetupScreen(act, null)));
        user.requestFocus();
    }

    private void secondFactor(String ticket) {
        header("Double authentification", "Saisissez le code à 6 chiffres de votre application d'authentification,\n"
                + "ou l'un de vos codes de secours.");
        EditText code = Ui.input(act, "Code", false);
        code.setImeOptions(EditorInfo.IME_ACTION_GO);
        code.setGravity(Gravity.CENTER);
        code.setTextSize(24);
        box.addView(code, new LinearLayout.LayoutParams(dp(320), ViewGroup.LayoutParams.WRAP_CONTENT));
        box.addView(status(), new LinearLayout.LayoutParams(dp(460), ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout buttons = Ui.row(act);
        Button go = Ui.button(act, "Valider", true);
        Button back = Ui.button(act, "Retour", false);
        buttons.addView(go, Ui.margins(act, 0, 0, 12, 0));
        buttons.addView(back);
        box.addView(buttons);
        Runnable submit = () -> {
            go.setEnabled(false);
            Api.send("POST", "/api/login/2fa", Api.json("ticket", ticket, "code", code.getText().toString().replace(" ", "")), r -> {
                go.setEnabled(true);
                if (r.optBoolean("recovery_used")) {
                    Ui.toast(act, "Code de secours utilisé. Il en reste " + r.optInt("recovery_left") + ".");
                }
                act.afterLogin(true);
            }, e -> {
                go.setEnabled(true);
                status.setText(e.getMessage());
                if (e.code == 401 && e.getMessage().startsWith("Délai")) credentials();
            });
        };
        go.setOnClickListener(v -> submit.run());
        code.setOnEditorActionListener((v, id, ev) -> {
            submit.run();
            return true;
        });
        back.setOnClickListener(v -> credentials());
        code.requestFocus();
    }
}

/** « Qui regarde ? » : profils du foyer, avec code PIN, mot de passe ou code de double authentification si besoin. */
final class ProfilesScreen extends Screen {
    private final JSONArray profiles;

    ProfilesScreen(MainActivity act, JSONArray profiles) {
        super(act);
        this.profiles = profiles;
    }

    @Override
    View create() {
        FrameLayout root = new FrameLayout(act);
        root.setBackgroundColor(Ui.BG);
        LinearLayout box = Ui.column(act);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        TextView t = Ui.title(act, "Qui regarde ?", 34);
        t.setPadding(0, 0, 0, dp(36));
        box.addView(t);
        HorizontalScrollView scroll = new HorizontalScrollView(act);
        scroll.setHorizontalScrollBarEnabled(false);
        scroll.setClipChildren(false);
        LinearLayout row = Ui.row(act);
        row.setPadding(dp(24), dp(16), dp(24), dp(16));
        row.setClipChildren(false);
        View first = null, current = null;
        for (int i = 0; i < profiles.length(); i++) {
            JSONObject p = profiles.optJSONObject(i);
            if (p == null) continue;
            View tile = tile(p);
            row.addView(tile, Ui.margins(act, 18, 0, 18, 0));
            if (first == null) first = tile;
            if (p.optBoolean("current")) current = tile;
        }
        scroll.addView(row);
        box.addView(scroll);
        root.addView(box, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER));
        View focus = current != null ? current : first;
        if (focus != null) focus.post(focus::requestFocus);
        return root;
    }

    private View tile(JSONObject p) {
        LinearLayout tile = Ui.column(act);
        tile.setGravity(Gravity.CENTER_HORIZONTAL);
        tile.setFocusable(true);
        tile.setClipChildren(false);
        FrameLayout av = StartScreens.avatar(this, p, 128);
        tile.addView(av, new LinearLayout.LayoutParams(dp(128), dp(128)));
        TextView name = Ui.oneLine(Ui.text(act, p.optString("display_name"), 18, Ui.MUTED));
        name.setGravity(Gravity.CENTER);
        name.setPadding(0, dp(12), 0, 0);
        tile.addView(name, new LinearLayout.LayoutParams(dp(150), ViewGroup.LayoutParams.WRAP_CONTENT));
        tile.setOnFocusChangeListener((v, has) -> {
            av.animate().scaleX(has ? 1.08f : 1f).scaleY(has ? 1.08f : 1f).setDuration(140).start();
            av.setSelected(has);
            name.setTextColor(has ? Ui.TEXT : Ui.MUTED);
            name.setTypeface(has ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
        });
        tile.setOnClickListener(v -> choose(p));
        return tile;
    }

    private void choose(JSONObject p) {
        if (p.optBoolean("current")) {
            act.openBrowse();
            return;
        }
        String needs = p.optString("needs", "none");
        boolean code = p.optBoolean("needs_code");
        if ("none".equals(needs) && !code) {
            switchTo(p, Api.json());
            return;
        }
        LinearLayout form = Ui.column(act);
        form.setPadding(dp(24), dp(12), dp(24), 0);
        EditText secret = null;
        if (!"none".equals(needs)) {
            secret = Ui.input(act, "pin".equals(needs) ? "Code PIN" : "Mot de passe", true);
            if ("pin".equals(needs)) {
                secret.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
            }
            form.addView(secret, Ui.margins(act, 0, 0, 0, 12));
        }
        EditText otp = null;
        if (code) {
            otp = Ui.input(act, "Code de double authentification", false);
            form.addView(otp);
        }
        final EditText s = secret, o = otp;
        AlertDialog d = Ui.dialog(act).setTitle(p.optString("display_name")).setView(form)
                .setPositiveButton("Valider", (dlg, w) -> {
                    JSONObject body = Api.json();
                    try {
                        if (s != null) body.put("pin".equals(needs) ? "pin" : "password", s.getText().toString());
                        if (o != null) body.put("code", o.getText().toString().replace(" ", ""));
                    } catch (org.json.JSONException ignored) {
                        // clés littérales
                    }
                    switchTo(p, body);
                })
                .setNegativeButton("Annuler", null).create();
        d.show();
        View focus = s != null ? s : o;
        if (focus != null) focus.requestFocus();
    }

    private void switchTo(JSONObject p, JSONObject body) {
        Api.send("POST", "/api/profiles/" + p.optString("id") + "/switch", body, r -> act.openBrowse(),
                e -> Ui.toast(act, e.getMessage()));
    }
}
