package fr.nexis.tv;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Langue de l'appli, comme dans l'interface web : le texte du code est en français et sert de clé,
 * Tr.t("Paramètres") renvoie la traduction de la langue choisie (ou le français s'il n'y en a pas).
 * La langue est celle du compte (réponse de /api/me), gardée pour l'écran suivant le prochain lancement ;
 * avant la première connexion, c'est celle de l'appareil.
 */
final class Tr {
    static String lang = "fr";
    private static final Map<String, String> EN = new HashMap<>();

    private Tr() {}

    /** Au lancement : la langue du dernier compte connecté, sinon celle de l'appareil. */
    static void init() {
        String saved = Api.store().getString("lang", null);
        lang = saved != null ? saved : "fr".equals(Locale.getDefault().getLanguage()) ? "fr" : "en";
    }

    /** Langue du compte connecté. */
    static void use(String l) {
        if (!"fr".equals(l) && !"en".equals(l)) return;
        lang = l;
        Api.store().edit().putString("lang", l).apply();
    }

    /** Un même mot français peut demander deux traductions : la clé porte alors un contexte (« réglages|Lecture »). */
    static String t(String fr) {
        String s = "en".equals(lang) ? EN.get(fr) : null;
        return s != null ? s : fr.substring(fr.indexOf('|') + 1);
    }

    static String[] all(String[] fr) {
        String[] out = new String[fr.length];
        for (int i = 0; i < fr.length; i++) out[i] = t(fr[i]);
        return out;
    }

    private static void en(String fr, String en) {
        EN.put(fr, en);
    }

    static {
        en("Session expirée : reconnectez-vous", "Session expired: sign in again");
        en("Erreur du serveur (", "Server error (");
        en("Le serveur ne répond pas. Vérifiez la connexion réseau.", "The server is not responding. Check the network connection.");
        en("Réponse inattendue du serveur", "Unexpected response from the server");
        en("Ce serveur a répondu ", "This server responded ");
        en(" : s'agit-il bien d'un serveur Nexis ?", ": is it really a Nexis server?");
        en("Ce n'est pas un serveur Nexis.", "This is not a Nexis server.");
        en("Impossible de joindre le serveur. Vérifiez l'adresse et la connexion réseau.",
                "Cannot reach the server. Check the address and the network connection.");
        en("Accueil", "Home");
        en("Plus", "More");
        en("Quitter Nexis ?", "Quit Nexis?");
        en("Quitter", "Quit");
        en("Rester", "Stay");
        en("Chargement…", "Loading…");
        en("Reprendre", "Resume");
        en("Lecture", "Play");
        en("Reprendre à ", "Resume at ");
        en("Depuis le début", "From the beginning");
        en("   ·   reprise à ", "   ·   resume at ");
        en("Ma liste", "My list");
        en("Rien à afficher pour l'instant : les bibliothèques sont peut-être vides.",
                "Nothing to show yet: the libraries may be empty.");
        en("Titre", "Title");
        en("Ajouts récents", "Recently added");
        en("Année", "Year");
        en("Note", "Rating");
        en("Trier : ", "Sort: ");
        en("Trier par", "Sort by");
        en("Cette bibliothèque est vide.", "This library is empty.");
        en("La chaîne ne répond pas (", "The channel is not responding (");
        en("Ensuite : ", "Next: ");
        en("\nHaut / bas pour changer de chaîne, Retour pour quitter.", "\nUp / down to change channel, Back to quit.");
        en("TV en direct", "Live TV");
        en("Aucune chaîne disponible.", "No channels available.");
        en("Connexion à ", "Connecting to ");
        en("Le serveur ", "The server ");
        en(" ne répond pas. ", " is not responding. ");
        en("Vérifiez la connexion réseau ou modifiez l'adresse.", "Check the network connection or change the address.");
        en("Rechercher", "Search");
        en("Paramètres", "Settings");
        en("Épisode suivant dans ", "Next episode in ");
        en("Audio et sous-titres", "Audio and subtitles");
        en("Épisode suivant", "Next episode");
        en("Passer le générique", "Skip credits");
        en("Réessayer", "Retry");
        en("Retour", "Back");
        en("Saison ", "Season ");
        en(" · Épisode ", " · Episode ");
        en("connexion au serveur", "connecting to the server");
        en("lecture directe du fichier", "direct play of the file");
        en("flux préparé par le serveur (remux)", "stream prepared by the server (remux)");
        en("conversion de la vidéo par le serveur", "video converted by the server");
        en("Lecture directe trop lente : passage par le serveur", "Direct play too slow: switching to the server");
        en("La lecture ne démarre pas (", "Playback does not start (");
        en("Le serveur ou le partage réseau est peut-être lent. Réessayez, ou attendez encore un peu.",
                "The server or the network share may be slow. Retry, or wait a little longer.");
        en("Aucun", "None");
        en("Audio : ", "Audio: ");
        en("Sous-titres : ", "Subtitles: ");
        en("Audio", "Audio");
        en("Flux interrompu, reconnexion…", "Stream interrupted, reconnecting…");
        en("La lecture s'est interrompue (", "Playback stopped (");
        en("Titre d'un film, d'une série, d'un épisode…", "Title of a movie, a show, an episode…");
        en("Saisissez au moins deux lettres.", "Type at least two letters.");
        en("Films", "Movies");
        en("Séries", "Shows");
        en("Épisodes", "Episodes");
        en("Aucun résultat pour « ", "No results for “");
        en(" ».", "”.");
        en("réglages|Lecture", "Playback");
        en(" · É", " · E");
        en("Langue", "Language");
        en("Français", "French");
        en("Anglais", "English");
        en("Japonais", "Japanese");
        en("Espagnol", "Spanish");
        en("Allemand", "German");
        en("Italien", "Italian");
        en("Portugais", "Portuguese");
        en("Coréen", "Korean");
        en("Version originale", "Original version");
        en("Langue audio", "Audio language");
        en("Sous-titres", "Subtitles");
        en("Si l'audio est dans une autre langue", "If the audio is in another language");
        en("Toujours", "Always");
        en("Jamais", "Never");
        en("Langue des sous-titres", "Subtitle language");
        en("Qualité", "Quality");
        en("Originale (recommandé)", "Original (recommended)");
        en("480p (connexion lente)", "480p (slow connection)");
        en("Épisode suivant automatique", "Play next episode automatically");
        en("Passer les génériques de début", "Skip opening credits");
        en("Compte", "Account");
        en("Profil", "Profile");
        en("Changer", "Change");
        en("Langue / Language", "Langue / Language");
        en("Se déconnecter", "Sign out");
        en("Se déconnecter ?", "Sign out?");
        en("Annuler", "Cancel");
        en("Serveur", "Server");
        en("Adresse", "Address");
        en("Changer de serveur ?", "Change server?");
        en("Vous devrez vous reconnecter sur le nouveau serveur.", "You will have to sign in again on the new server.");
        en("Nexis pour Android TV, version ", "Nexis for Android TV, version ");
        en("Activé", "On");
        en("Désactivé", "Off");
        en("Adresse du serveur Nexis", "Nexis server address");
        en("Exemple : https://nexis.mondomaine.fr ou 192.168.1.20:8420", "Example: https://nexis.mydomain.com or 192.168.1.20:8420");
        en("Continuer", "Continue");
        en("Saisissez l'adresse du serveur.", "Enter the server address.");
        en("Connexion", "Sign in");
        en("Identifiant", "Username");
        en("Mot de passe", "Password");
        en("Se connecter", "Sign in");
        en("Changer de serveur", "Change server");
        en("Double authentification", "Two-factor authentication");
        en("Saisissez le code à 6 chiffres de votre application d'authentification,\n",
                "Enter the 6-digit code from your authenticator app,\n");
        en("ou l'un de vos codes de secours.", "or one of your recovery codes.");
        en("Code", "Code");
        en("Valider", "Confirm");
        en("Code de secours utilisé. Il en reste ", "Recovery code used. Remaining: ");
        en("Délai", "Timed out");
        en("Qui regarde ?", "Who's watching?");
        en("Code PIN", "PIN code");
        en("Code de double authentification", "Two-factor authentication code");
    }
}
