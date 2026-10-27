package fr.nexis.tv;

import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Navigation principale : barre du haut (Accueil, une entrée par bibliothèque, TV en direct, Recherche,
 * Paramètres) et la page choisie en dessous. Une entrée sélectionnée à la télécommande s'ouvre d'elle-même.
 */
final class BrowseScreen extends Screen {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final List<View> tabViews = new ArrayList<>();
    private final List<Tab> tabs = new ArrayList<>();
    private LinearLayout bar;
    private FrameLayout content;
    private int selected = -1;
    private Runnable pending;

    BrowseScreen(MainActivity act) {
        super(act);
    }

    /** Une page de la navigation (accueil, bibliothèque…). Sa vue est créée à la première ouverture. */
    abstract static class Tab {
        final BrowseScreen browse;
        final MainActivity act;
        View view;

        Tab(BrowseScreen b) {
            browse = b;
            act = b.act;
        }

        abstract View create();

        /** Ouverture (et retour sur la page) : rafraîchir ce qui peut avoir changé. */
        void onShow() {}

        /** Élément à sélectionner quand on descend depuis la barre du haut (null : le plus proche). */
        View entry() {
            return null;
        }

        int dp(float v) {
            return Ui.dp(act, v);
        }
    }

    @Override
    View create() {
        FrameLayout root = new FrameLayout(act);
        root.setBackgroundColor(Ui.BG);
        LinearLayout page = new LinearLayout(act) {
            @Override
            public View focusSearch(View focused, int direction) {
                View result = super.focusSearch(focused, direction);
                // monter depuis la page : retour sur l'entrée ouverte de la barre, pas sur la plus proche
                if (direction == View.FOCUS_UP && inside(focused, content) && inside(result, bar) && selected >= 0) {
                    return tabViews.get(selected);
                }
                if (direction == View.FOCUS_DOWN && inside(focused, bar) && selected >= 0) {
                    View entry = tabs.get(selected).entry();
                    if (entry != null && entry.isShown()) return entry;
                }
                return result;
            }
        };
        page.setOrientation(LinearLayout.VERTICAL);
        root.addView(page, new FrameLayout.LayoutParams(-1, -1));

        bar = Ui.row(act);
        bar.setPadding(dp(40), dp(18), dp(40), dp(6));
        ImageView logo = new ImageView(act);
        logo.setImageResource(R.mipmap.ic_launcher);
        bar.addView(logo, new LinearLayout.LayoutParams(dp(34), dp(34)));
        View gap = new View(act);
        bar.addView(gap, new LinearLayout.LayoutParams(dp(18), 1));

        addTab("Accueil", new HomeTab(this));
        for (int i = 0; i < Session.libraries.length(); i++) {
            JSONObject lib = Session.libraries.optJSONObject(i);
            if (lib != null) addTab(lib.optString("title"), new LibraryTab(this, lib));
        }
        if (Session.live()) addTab("TV en direct", new LiveTab(this));
        View spacer = new View(act);
        bar.addView(spacer, new LinearLayout.LayoutParams(0, 1, 1f));
        addIcon(Icons.SEARCH, "Rechercher", new SearchTab(this));
        addIcon(Icons.SETTINGS, "Paramètres", new SettingsTab(this));
        page.addView(bar, new LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT));

        content = new FrameLayout(act);
        content.setClipChildren(false);
        page.addView(content, new LinearLayout.LayoutParams(-1, 0, 1f));
        select(0);
        View first = tabViews.get(0);
        first.post(first::requestFocus);
        return root;
    }

    private void addTab(String label, Tab tab) {
        TextView t = Ui.oneLine(Ui.text(act, label, 18, Ui.MUTED));
        t.setPadding(dp(16), dp(8), dp(16), dp(8));
        t.setGravity(Gravity.CENTER);
        t.setMaxWidth(dp(260));
        register(t, tab);
        bar.addView(t, Ui.margins(act, 0, 0, 6, 0));
    }

    private void addIcon(int kind, String label, Tab tab) {
        ImageView v = new ImageView(act);
        Icons icon = new Icons(kind, Ui.MUTED);
        v.setImageDrawable(icon);
        v.setPadding(dp(9), dp(9), dp(9), dp(9));
        v.setContentDescription(label);
        v.setTag(icon);
        register(v, tab);
        bar.addView(v, new LinearLayout.LayoutParams(dp(44), dp(44)));
        ((LinearLayout.LayoutParams) v.getLayoutParams()).leftMargin = dp(8);
    }

    private void register(View v, Tab tab) {
        int index = tabs.size();
        tabs.add(tab);
        tabViews.add(v);
        v.setFocusable(true);
        v.setOnFocusChangeListener((view, has) -> {
            paint(index);
            if (has && index != selected) {
                // on ouvre la page quand la sélection s'arrête sur l'entrée (pas à chaque passage)
                if (pending != null) handler.removeCallbacks(pending);
                pending = () -> select(index);
                handler.postDelayed(pending, 260);
            }
        });
        v.setOnClickListener(view -> {
            select(index);
            View entry = tabs.get(index).entry();
            if (entry != null) entry.requestFocus();
        });
    }

    /** Apparence d'une entrée : ouverte (soulignée), sélectionnée (fond clair) ou normale. */
    private void paint(int index) {
        View v = tabViews.get(index);
        boolean focus = v.hasFocus() || v.isFocused();
        boolean open = index == selected;
        int fg = focus ? Color.BLACK : open ? Ui.TEXT : Ui.MUTED;
        v.setBackground(focus ? Ui.rounded(act, Color.WHITE, 22) : open ? Ui.rounded(act, Ui.SURFACE_2, 22) : null);
        if (v instanceof TextView) {
            ((TextView) v).setTextColor(fg);
            ((TextView) v).setTypeface(open || focus ? Typeface.DEFAULT_BOLD : Typeface.DEFAULT);
        } else if (v.getTag() instanceof Icons) {
            ((Icons) v.getTag()).setColor(fg);
        }
    }

    void select(int index) {
        if (index == selected) return;
        int old = selected;
        selected = index;
        if (old >= 0) paint(old);
        paint(index);
        content.removeAllViews();
        Tab tab = tabs.get(index);
        if (tab.view == null) tab.view = tab.create();
        content.addView(tab.view, new FrameLayout.LayoutParams(-1, -1));
        tab.onShow();
    }

    /** Ouvre une page de la barre par son type (ex. la recherche depuis un raccourci). */
    void focusBar() {
        if (selected >= 0) tabViews.get(selected).requestFocus();
    }

    @Override
    void onResume() {
        if (selected >= 0) tabs.get(selected).onShow();
    }

    @Override
    boolean onBack() {
        View f = act.getCurrentFocus();
        if (f != null && inside(f, content)) {
            focusBar();
            return true;
        }
        if (selected != 0) {
            select(0);
            tabViews.get(0).requestFocus();
            return true;
        }
        Ui.dialog(act).setTitle("Quitter Nexis ?")
                .setPositiveButton("Quitter", (d, w) -> act.finish())
                .setNegativeButton("Rester", null)
                .show();
        return true;
    }

    @Override
    void onDestroy() {
        if (pending != null) handler.removeCallbacks(pending);
    }

    static boolean inside(View v, View parent) {
        if (v == null || parent == null) return false;
        if (v == parent) return true;
        ViewParent p = v.getParent();
        while (p instanceof View) {
            if (p == parent) return true;
            p = p.getParent();
        }
        return false;
    }
}
