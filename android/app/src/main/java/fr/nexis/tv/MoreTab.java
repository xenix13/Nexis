package fr.nexis.tv;

import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;

/**
 * « Plus » : recherche, TV en direct et paramètres. Ils sont rangés ici pour que la barre du haut reste réduite à
 * l'essentiel (Accueil et les bibliothèques). Chacun s'ouvre sur sa propre page ; Retour revient à ce menu.
 */
final class MoreTab extends BrowseScreen.Tab {
    private Button first;

    MoreTab(BrowseScreen b) {
        super(b);
    }

    private interface Make {
        BrowseScreen.Tab page();
    }

    @Override
    View create() {
        LinearLayout root = Ui.column(act);
        root.setPadding(dp(56), dp(40), dp(56), dp(24));
        add(root, Tr.t("Rechercher"), () -> new SearchTab(browse));
        if (Session.live()) add(root, Tr.t("TV en direct"), () -> new LiveTab(browse));
        add(root, Tr.t("Paramètres"), () -> new SettingsTab(browse));
        return root;
    }

    private void add(LinearLayout root, String label, Make make) {
        Button b = Ui.button(act, label, false);
        b.setTextSize(24);
        b.setPadding(dp(32), dp(18), dp(32), dp(18));
        b.setOnClickListener(v -> act.push(new Page(act, make.page())));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(420), ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(16);
        root.addView(b, lp);
        if (first == null) first = b;
    }

    @Override
    View entry() {
        return first;
    }

    /** Une page de la navigation affichée seule : Retour revient au menu « Plus ». */
    private static final class Page extends Screen {
        private final BrowseScreen.Tab tab;

        Page(MainActivity act, BrowseScreen.Tab tab) {
            super(act);
            this.tab = tab;
        }

        @Override
        View create() {
            FrameLayout root = new FrameLayout(act);
            root.setBackgroundColor(Ui.BG);
            tab.view = tab.create();
            root.addView(tab.view, new FrameLayout.LayoutParams(-1, -1));
            tab.onShow();
            root.post(() -> {
                View e = tab.entry();
                if (e != null) e.requestFocus();
            });
            return root;
        }

        @Override
        void onResume() {
            tab.onShow();
        }
    }
}
