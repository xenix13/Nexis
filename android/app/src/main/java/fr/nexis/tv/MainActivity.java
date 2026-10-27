package fr.nexis.tv;

import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;

import java.util.ArrayDeque;

/**
 * Nexis pour Android TV : application native (écrans dessinés par l'appli, lecture par ExoPlayer).
 * Au lancement : adresse du serveur, puis connexion, puis « Qui regarde ? », puis l'accueil.
 */
public class MainActivity extends Activity {
    private FrameLayout root;
    private final ArrayDeque<Screen> stack = new ArrayDeque<>();
    private boolean started;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Api.init(this);
        Tr.init();
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        root = new FrameLayout(this);
        root.setBackgroundColor(Ui.BG);
        setContentView(root);
        immersive();
        start();
    }

    /** Point de départ : serveur connu ? session encore valide ? */
    void start() {
        if (Api.server() == null) {
            reset(new SetupScreen(this, null));
            return;
        }
        reset(new LoadingScreen(this, Tr.t("Connexion à ") + Api.server() + "…"));
        Api.obj("/api/me", me -> {
            Session.load(me);
            afterLogin(true);
        }, e -> {
            if (e.code == 401) reset(new LoginScreen(this));
            else reset(new SetupScreen(this, e.code == 0 ? Tr.t("Le serveur ") + Api.server() + Tr.t(" ne répond pas. ")
                    + Tr.t("Vérifiez la connexion réseau ou modifiez l'adresse.") : e.getMessage()));
        });
    }

    /** Connecté : choix du profil s'il y en a plusieurs dans le foyer, sinon accueil. */
    void afterLogin(boolean askProfile) {
        if (!askProfile) {
            openBrowse();
            return;
        }
        Api.arr("/api/profiles", list -> {
            if (list.length() > 1) reset(new ProfilesScreen(this, list));
            else openBrowse();
        }, e -> openBrowse());
    }

    /** Recharge le profil (/api/me) puis ouvre la navigation. */
    void openBrowse() {
        Api.obj("/api/me", me -> {
            Session.load(me);
            reset(new BrowseScreen(this));
        }, e -> {
            if (e.code == 401) reset(new LoginScreen(this));
            else Ui.toast(this, e.getMessage());
        });
    }

    void logout() {
        Api.send("POST", "/api/logout", null, r -> {
            Api.forgetSession();
            reset(new LoginScreen(this));
        }, e -> {
            Api.forgetSession();
            reset(new LoginScreen(this));
        });
    }

    // ------------------------------------------------------------------ pile d'écrans
    void push(Screen s) {
        Screen top = stack.peek();
        if (top != null) {
            top.lastFocus = getCurrentFocus();
            root.removeView(top.view);
        }
        stack.push(s);
        show(s, true);
    }

    void reset(Screen s) {
        while (!stack.isEmpty()) {
            Screen old = stack.pop();
            root.removeView(old.view);
            old.onDestroy();
        }
        stack.push(s);
        show(s, true);
    }

    void pop() {
        if (stack.size() <= 1) {
            finish();
            return;
        }
        Screen old = stack.pop();
        root.removeView(old.view);
        old.onDestroy();
        Screen top = stack.peek();
        show(top, false);
        top.onResume();
    }

    private void show(Screen s, boolean fresh) {
        if (fresh || s.view == null) s.view = s.create();
        root.addView(s.view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        View f = s.lastFocus;
        if (!fresh && f != null && f.isAttachedToWindow()) f.requestFocus();
    }

    Screen top() {
        return stack.peek();
    }

    // ------------------------------------------------------------------ télécommande
    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        Screen top = stack.peek();
        if (top != null && top.onBack()) return;
        pop();
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        // certaines télécommandes envoient Échap au lieu de Retour : même traitement
        if (event.getKeyCode() == KeyEvent.KEYCODE_ESCAPE) {
            if (event.getAction() == KeyEvent.ACTION_UP) onBackPressed();
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        immersive();
        if (started) {
            // retour du lecteur : reprise et « vu » à jour (le temps que la dernière position arrive au serveur)
            root.postDelayed(() -> {
                Screen top = stack.peek();
                if (top != null && top.view != null && top.view.isAttachedToWindow()) top.onResume();
            }, 700);
        }
        started = true;
    }

    @SuppressWarnings("deprecation")
    void immersive() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }
}
