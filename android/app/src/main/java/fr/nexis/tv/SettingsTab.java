package fr.nexis.tv;

import android.graphics.Color;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * Paramètres de la TV réduits au strict nécessaire : profil, langue de l'interface, déconnexion et serveur.
 * Les réglages de lecture (langues audio et sous-titres, qualité, épisode suivant…) suivent le compte : ils se
 * règlent depuis l'interface web, sur le téléphone ou l'ordinateur, et valent aussi ici.
 */
final class SettingsTab extends BrowseScreen.Tab {
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
        section(Tr.t("Compte"));
        item(Tr.t("Profil"), Session.user.optString("display_name"), Tr.t("Changer"), () ->
                Api.arr("/api/profiles", profiles -> act.push(new ProfilesScreen(act, profiles)),
                        e -> Ui.toast(act, e.getMessage())));
        // langue de l'interface, propre au compte (la même que dans l'interface web)
        String[] langKeys = {"fr", "en"};
        String[] langNames = {"Français", "English"};
        item(Tr.t("Langue"), "en".equals(Tr.lang) ? "English" : "Français", Tr.t("Changer"), () -> Ui.dialog(act)
                .setTitle("Langue / Language").setSingleChoiceItems(langNames, "en".equals(Tr.lang) ? 1 : 0, (d, which) -> {
                    d.dismiss();
                    Api.send("PUT", "/api/me/language", Api.json("language", langKeys[which]), r -> {
                        Tr.use(langKeys[which]);
                        act.openBrowse(); // écrans reconstruits dans la nouvelle langue
                    }, e -> Ui.toast(act, e.getMessage()));
                }).show());
        item(Tr.t("Se déconnecter"), "", null, () -> Ui.dialog(act).setTitle(Tr.t("Se déconnecter ?"))
                .setPositiveButton(Tr.t("Se déconnecter"), (d, w) -> act.logout())
                .setNegativeButton(Tr.t("Annuler"), null).show());

        section(Tr.t("Serveur"));
        item(Tr.t("Adresse"), Api.server(), Tr.t("Changer"), () -> Ui.dialog(act).setTitle(Tr.t("Changer de serveur ?"))
                .setMessage(Tr.t("Vous devrez vous reconnecter sur le nouveau serveur."))
                .setPositiveButton(Tr.t("Changer"), (d, w) -> act.reset(new SetupScreen(act, null)))
                .setNegativeButton(Tr.t("Annuler"), null).show());
        TextView hint = Ui.text(act, Tr.t("Langues audio et sous-titres, qualité, épisode suivant : ces réglages suivent votre compte et se changent depuis le téléphone ou l'ordinateur."), 15, Ui.MUTED);
        hint.setPadding(dp(4), dp(28), 0, 0);
        list.addView(hint, new LinearLayout.LayoutParams(dp(820), ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView version = Ui.text(act, Tr.t("Nexis pour Android TV, version ") + BuildConfig.VERSION_NAME, 14, Ui.MUTED);
        version.setPadding(dp(4), dp(12), 0, 0);
        list.addView(version);
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

    @Override
    View entry() {
        return first;
    }
}
