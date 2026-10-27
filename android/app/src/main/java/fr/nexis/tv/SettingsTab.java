package fr.nexis.tv;

import android.graphics.Color;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/** Paramètres réduits à l'essentiel : langues, sous-titres, qualité, enchaînement des épisodes, profil, serveur. */
final class SettingsTab extends BrowseScreen.Tab {
    private static final String[] LANG_KEYS = {"fra", "eng", "jpn", "spa", "deu", "ita", "por", "kor"};
    private static final String[] LANG_LABELS = {"Français", "Anglais", "Japonais", "Espagnol", "Allemand", "Italien",
            "Portugais", "Coréen"};
    private LinearLayout list;
    private View first;

    SettingsTab(BrowseScreen b) {
        super(b);
    }

    @Override
    View create() {
        ScrollView scroll = new ScrollView(act);
        list = Ui.column(act);
        list.setPadding(dp(48), dp(8), dp(48), dp(48));
        scroll.addView(list);
        return scroll;
    }

    @Override
    void onShow() {
        build();
    }

    private void build() {
        list.removeAllViews();
        first = null;
        section("Lecture");
        String[] audioKeys = concat(LANG_KEYS, "");
        String[] audioLabels = concat(LANG_LABELS, "Version originale");
        choice("Langue audio", "audio_lang", "fra", audioKeys, audioLabels);
        choice("Sous-titres", "sub_mode", "foreign", new String[]{"foreign", "always", "never"},
                new String[]{"Si l'audio est dans une autre langue", "Toujours", "Jamais"});
        choice("Langue des sous-titres", "sub_lang", "fra", LANG_KEYS, LANG_LABELS);
        choice("Qualité", "quality", "original", new String[]{"original", "1080p", "720p", "480p"},
                new String[]{"Originale (recommandé)", "1080p", "720p", "480p (connexion lente)"});
        toggle("Épisode suivant automatique", "autoplay_next", true);
        toggle("Passer les génériques de début", "auto_skip_intro", false);

        section("Compte");
        item("Profil", Session.user.optString("display_name"), "Changer", () ->
                Api.arr("/api/profiles", profiles -> act.push(new ProfilesScreen(act, profiles)),
                        e -> Ui.toast(act, e.getMessage())));
        item("Se déconnecter", "", null, () -> Ui.dialog(act).setTitle("Se déconnecter ?")
                .setPositiveButton("Se déconnecter", (d, w) -> act.logout())
                .setNegativeButton("Annuler", null).show());

        section("Serveur");
        item("Adresse", Api.server(), "Changer", () -> Ui.dialog(act).setTitle("Changer de serveur ?")
                .setMessage("Vous devrez vous reconnecter sur le nouveau serveur.")
                .setPositiveButton("Changer", (d, w) -> act.reset(new SetupScreen(act, null)))
                .setNegativeButton("Annuler", null).show());
        TextView version = Ui.text(act, "Nexis pour Android TV, version " + BuildConfig.VERSION_NAME, 14, Ui.MUTED);
        version.setPadding(0, dp(28), 0, 0);
        list.addView(version);
    }

    private static String[] concat(String[] a, String extra) {
        String[] out = new String[a.length + 1];
        System.arraycopy(a, 0, out, 0, a.length);
        out[a.length] = extra;
        return out;
    }

    private void section(String title) {
        TextView t = Ui.title(act, title, 15);
        t.setTextColor(Ui.ACCENT);
        t.setAllCaps(true);
        t.setLetterSpacing(0.08f);
        t.setPadding(dp(4), dp(22), 0, dp(8));
        list.addView(t);
    }

    /** Ligne de réglage : libellé, valeur actuelle, action au clic. */
    private TextView[] item(String label, String value, String action, Runnable click) {
        LinearLayout row = Ui.row(act);
        row.setFocusable(true);
        row.setBackground(Ui.focusable(act, Ui.SURFACE, Color.WHITE, 10));
        row.setPadding(dp(18), dp(14), dp(18), dp(14));
        TextView l = Ui.oneLine(Ui.text(act, label, 18, Ui.TEXT));
        row.addView(l, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView v = Ui.oneLine(Ui.text(act, value == null ? "" : value + (action != null ? "  ›" : ""), 17, Ui.MUTED));
        row.addView(v);
        row.setOnFocusChangeListener((view, has) -> {
            l.setTextColor(has ? Color.BLACK : Ui.TEXT);
            v.setTextColor(has ? Color.rgb(60, 60, 70) : Ui.MUTED);
        });
        row.setOnClickListener(x -> click.run());
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(820), ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(6);
        list.addView(row, lp);
        if (first == null) first = row;
        return new TextView[]{l, v};
    }

    private void choice(String label, String key, String def, String[] keys, String[] labels) {
        String cur = Session.pref(key, def);
        int idx = 0;
        for (int i = 0; i < keys.length; i++) if (keys[i].equals(cur)) idx = i;
        final int selectedIndex = idx;
        item(label, labels[idx], "", () -> Ui.dialog(act).setTitle(label)
                .setSingleChoiceItems(labels, selectedIndex, (d, which) -> {
                    d.dismiss();
                    save(key, keys[which]);
                }).show());
    }

    private void toggle(String label, String key, boolean def) {
        boolean on = Session.flag(key, def);
        item(label, on ? "Activé" : "Désactivé", null, () -> save(key, !on));
    }

    private void save(String key, Object value) {
        Api.send("PUT", "/api/me/prefs", Api.json(key, value), prefs -> {
            Session.prefs = prefs;
            View focus = act.getCurrentFocus();
            int index = focus == null ? -1 : list.indexOfChild(focus);
            build();
            if (index >= 0 && index < list.getChildCount()) list.getChildAt(index).requestFocus();
        }, e -> Ui.toast(act, e.getMessage()));
    }

    @Override
    View entry() {
        return first;
    }
}
