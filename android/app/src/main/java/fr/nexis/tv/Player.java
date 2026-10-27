package fr.nexis.tv;

import android.app.Activity;
import android.content.Intent;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Lancement du lecteur et choix des pistes (mêmes règles que le lecteur web : static/js/tracks.js). */
final class Player {
    private Player() {}

    static void play(Activity act, JSONObject item, long offsetMs) {
        Intent i = new Intent(act, PlayerActivity.class);
        i.putExtra("id", item.optString("id"));
        i.putExtra("offset", offsetMs);
        act.startActivity(i);
    }

    static List<JSONObject> list(JSONArray a) {
        List<JSONObject> out = new ArrayList<>();
        if (a != null) for (int i = 0; i < a.length(); i++) if (a.optJSONObject(i) != null) out.add(a.optJSONObject(i));
        return out;
    }

    /** Piste audio : langue retenue pour ce film / cette série, sinon langue préférée, sinon piste par défaut. */
    static JSONObject pickAudio(List<JSONObject> audio, JSONObject trackPrefs) {
        if (audio.isEmpty()) return null;
        String want = trackPrefs != null && !trackPrefs.optString("audio_lang").isEmpty()
                && !"null".equals(trackPrefs.optString("audio_lang")) ? trackPrefs.optString("audio_lang")
                : Session.pref("audio_lang", "");
        if (!want.isEmpty()) for (JSONObject a : audio) if (want.equals(a.optString("lang"))) return a;
        for (JSONObject a : audio) if (a.optBoolean("default")) return a;
        return audio.get(0);
    }

    static JSONObject pickSubtitle(List<JSONObject> subs, JSONObject audio, JSONObject trackPrefs) {
        if (subs.isEmpty()) return null;
        String audioLang = audio == null ? "" : audio.optString("lang");
        String tpLang = trackPrefs == null || trackPrefs.isNull("sub_lang") ? "" : trackPrefs.optString("sub_lang");
        if (!tpLang.isEmpty()) {
            if ("off".equals(tpLang)) return null;
            boolean forced = trackPrefs.optBoolean("sub_forced") && tpLang.equals(audioLang);
            JSONObject pick = null;
            for (JSONObject s : subs) {
                if (!tpLang.equals(s.optString("lang"))) continue;
                if (s.optBoolean("forced") == forced && s.optBoolean("overlay")) { pick = s; break; }
            }
            if (pick == null) for (JSONObject s : subs) {
                if (tpLang.equals(s.optString("lang")) && s.optBoolean("forced") == forced) { pick = s; break; }
            }
            if (pick == null) for (JSONObject s : subs) if (tpLang.equals(s.optString("lang"))) { pick = s; break; }
            if (pick != null) return fixForced(pick, audio, subs);
        }
        String mode = Session.pref("sub_mode", "foreign");
        if ("never".equals(mode)) return null;
        String lang = Session.pref("sub_lang", "");
        JSONObject best = null;
        for (JSONObject s : subs) {
            if (!lang.isEmpty() && !lang.equals(s.optString("lang"))) continue;
            if (!s.optBoolean("forced") && s.optBoolean("overlay")) { best = s; break; }
        }
        if (best == null) for (JSONObject s : subs) {
            if ((lang.isEmpty() || lang.equals(s.optString("lang"))) && !s.optBoolean("forced")) { best = s; break; }
        }
        if (best == null) for (JSONObject s : subs) if (lang.isEmpty() || lang.equals(s.optString("lang"))) { best = s; break; }
        if ("always".equals(mode)) return best;
        String prefAudio = Session.pref("audio_lang", "");
        if (audio != null && !prefAudio.isEmpty() && !audioLang.isEmpty() && !audioLang.equals(prefAudio)) return best;
        for (JSONObject s : subs) if (s.optBoolean("forced") && audio != null && s.optString("lang").equals(audioLang)) return s;
        return null;
    }

    /** Piste « forcée » (panneaux seulement) avec un audio d'une autre langue -> piste complète de la même langue. */
    static JSONObject fixForced(JSONObject sub, JSONObject audio, List<JSONObject> subs) {
        if (sub == null || !signsOnly(sub, subs) || (audio != null && audio.optString("lang").equals(sub.optString("lang")))) return sub;
        for (JSONObject x : subs) if (!x.optBoolean("forced") && x.optString("lang").equals(sub.optString("lang")) && x.optBoolean("overlay")) return x;
        for (JSONObject x : subs) if (!x.optBoolean("forced") && x.optString("lang").equals(sub.optString("lang"))) return x;
        return sub;
    }

    static boolean signsOnly(JSONObject s, List<JSONObject> subs) {
        if (!s.optBoolean("forced")) return false;
        for (JSONObject x : subs) if (!x.optBoolean("forced") && x.optString("lang").equals(s.optString("lang"))) return true;
        return false;
    }

    /** Sous-titres proposés dans le menu : les pistes « panneaux seulement » d'une autre langue sont masquées. */
    static List<JSONObject> menuSubs(List<JSONObject> subs, JSONObject audio) {
        List<JSONObject> out = new ArrayList<>();
        String audioLang = audio == null ? "" : audio.optString("lang");
        for (JSONObject s : subs) if (!signsOnly(s, subs) || (!audioLang.isEmpty() && audioLang.equals(s.optString("lang")))) out.add(s);
        return out;
    }
}
