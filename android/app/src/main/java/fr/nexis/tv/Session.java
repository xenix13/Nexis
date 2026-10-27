package fr.nexis.tv;

import org.json.JSONArray;
import org.json.JSONObject;

/** Profil connecté, ses préférences et ses bibliothèques (réponse de /api/me), partagés par les écrans. */
final class Session {
    static JSONObject user = new JSONObject();
    static JSONObject prefs = new JSONObject();
    static JSONArray libraries = new JSONArray();
    static JSONObject features = new JSONObject();
    /** Chaînes de la TV en direct, pour passer de l'une à l'autre dans le lecteur. */
    static JSONArray channels = new JSONArray();

    private Session() {}

    static void load(JSONObject me) {
        user = me.optJSONObject("user") != null ? me.optJSONObject("user") : new JSONObject();
        prefs = me.optJSONObject("prefs") != null ? me.optJSONObject("prefs") : new JSONObject();
        libraries = me.optJSONArray("libraries") != null ? me.optJSONArray("libraries") : new JSONArray();
        features = me.optJSONObject("features") != null ? me.optJSONObject("features") : new JSONObject();
        Tr.use(user.optString("language", Tr.lang));
    }

    static String pref(String key, String def) {
        return prefs.optString(key, def);
    }

    static boolean flag(String key, boolean def) {
        return prefs.optBoolean(key, def);
    }

    static boolean live() {
        return features.optBoolean("live", false);
    }
}
