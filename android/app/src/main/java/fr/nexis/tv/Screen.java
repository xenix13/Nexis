package fr.nexis.tv;

import android.view.View;

/** Un écran de l'application (connexion, profils, navigation, fiche…), empilé par MainActivity. */
abstract class Screen {
    final MainActivity act;
    View view;
    View lastFocus;

    Screen(MainActivity act) {
        this.act = act;
    }

    abstract View create();

    /** Revenu au premier plan (après la lecture, une fiche…) : l'écran peut se mettre à jour. */
    void onResume() {}

    /** Touche Retour : true si l'écran l'a traitée lui-même. */
    boolean onBack() {
        return false;
    }

    void onDestroy() {}

    int dp(float v) {
        return Ui.dp(act, v);
    }
}
