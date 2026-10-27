package fr.nexis.tv;

import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Fiche d'un film, d'une série (saisons et épisodes) ou d'un épisode. */
final class Detail extends Screen {
    private final String id;
    private FrameLayout root;
    private ImageView backdrop;
    private ScrollView scroll;
    private LinearLayout page;
    private LinearLayout episodes;
    private JSONObject item;
    private String seasonId;
    private final List<TextView> seasonTabs = new ArrayList<>();
    private boolean loaded;
    private Object wantFocus;  // épisode à resélectionner une fois la liste rechargée

    private Detail(MainActivity act, String id) {
        super(act);
        this.id = id;
    }

    /** Ouvre la fiche qui correspond à une carte (une saison ouvre sa série ; un bonus se lit directement). */
    static void open(MainActivity act, JSONObject card) {
        String type = card.optString("type");
        if ("extra".equals(type)) {
            Player.play(act, card, 0);
            return;
        }
        String target = "season".equals(type) ? card.optString("show_id", card.optString("id")) : card.optString("id");
        act.push(new Detail(act, target));
    }

    @Override
    View create() {
        root = new FrameLayout(act);
        root.setBackgroundColor(Ui.BG);
        backdrop = new ImageView(act);
        backdrop.setScaleType(ImageView.ScaleType.CENTER_CROP);
        backdrop.setAlpha(0.35f);
        root.addView(backdrop, new FrameLayout.LayoutParams(-1, -1));
        View shade = new View(act);
        shade.setBackground(new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{Color.argb(245, 11, 11, 14), Color.argb(170, 11, 11, 14), Color.argb(60, 11, 11, 14)}));
        root.addView(shade, new FrameLayout.LayoutParams(-1, -1));
        scroll = new ScrollView(act);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.setClipChildren(false);
        page = Ui.column(act);
        page.setClipChildren(false);
        page.setPadding(dp(56), dp(40), dp(56), dp(56));
        scroll.addView(page);
        root.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        TextView wait = Ui.text(act, "Chargement…", 18, Ui.MUTED);
        page.addView(wait);
        load(true);
        return root;
    }

    @Override
    void onResume() {
        if (loaded) load(false);  // retour de lecture : reprise et « vu » à jour
    }

    private void load(boolean first) {
        Api.obj("/api/items/" + id, d -> {
            item = d;
            loaded = true;
            render(first);
        }, e -> {
            if (first) {
                page.removeAllViews();
                page.addView(Ui.text(act, e.getMessage(), 18, Ui.ERROR));
            }
        });
    }

    private void render(boolean first) {
        View focus = act.getCurrentFocus();
        Object focusTag = focus == null ? null : focus.getTag();
        page.removeAllViews();
        seasonTabs.clear();
        String type = item.optString("type");
        boolean episode = "episode".equals(type), show = "show".equals(type);
        String art = item.optString(episode ? "show_art" : "art", "");
        if (art.isEmpty() || "null".equals(art)) art = item.optString("art");
        Images.load(backdrop, Images.library(art, 1280, 720));

        LinearLayout top = Ui.row(act);
        top.setGravity(Gravity.TOP);
        top.setClipChildren(false);
        FrameLayout posterBox = new FrameLayout(act);
        posterBox.setBackground(Ui.rounded(act, Ui.SURFACE, 10));
        posterBox.setClipToOutline(true);
        ImageView poster = new ImageView(act);
        poster.setScaleType(ImageView.ScaleType.CENTER_CROP);
        String thumb = item.optString(episode ? "show_thumb" : "thumb");
        Images.load(poster, Images.library(thumb, 400, 600));
        posterBox.addView(poster, new FrameLayout.LayoutParams(-1, -1));
        LinearLayout.LayoutParams pp = new LinearLayout.LayoutParams(dp(200), dp(300));
        pp.rightMargin = dp(40);
        top.addView(posterBox, pp);

        LinearLayout info = Ui.column(act);
        info.setClipChildren(false);
        if (episode) {
            TextView showName = Ui.oneLine(Ui.text(act, item.optString("show"), 20, Ui.ACCENT));
            info.addView(showName);
        }
        TextView title = Ui.title(act, episode ? "S" + item.optInt("season") + " · É" + item.optInt("episode") + "  "
                + item.optString("title") : item.optString("title"), 34);
        title.setMaxLines(2);
        title.setEllipsize(TextUtils.TruncateAt.END);
        info.addView(title);
        TextView meta = Ui.text(act, metaLine(), 16, Ui.MUTED);
        meta.setPadding(0, dp(8), 0, 0);
        info.addView(meta);
        String tagline = item.optString("tagline", "");
        if (!tagline.isEmpty() && !"null".equals(tagline)) {
            TextView tl = Ui.text(act, tagline, 17, Ui.TEXT);
            tl.setPadding(0, dp(14), 0, 0);
            tl.setTypeface(null, android.graphics.Typeface.ITALIC);
            info.addView(tl);
        }
        String summary = item.optString("summary", "");
        if (!summary.isEmpty() && !"null".equals(summary)) {
            TextView s = Ui.text(act, summary, 16, Color.rgb(215, 215, 222));
            s.setMaxLines(5);
            s.setEllipsize(TextUtils.TruncateAt.END);
            s.setLineSpacing(0, 1.15f);
            s.setPadding(0, dp(12), 0, 0);
            info.addView(s, new LinearLayout.LayoutParams(dp(760), ViewGroup.LayoutParams.WRAP_CONTENT));
        }
        LinearLayout buttons = Ui.row(act);
        buttons.setPadding(0, dp(22), 0, 0);
        View primary = addButtons(buttons);
        info.addView(buttons);
        top.addView(info, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        page.addView(top);

        boolean episodeFocus = focusTag instanceof String && ((String) focusTag).startsWith("ep:");
        wantFocus = episodeFocus ? focusTag : null;
        if (show) seasons();
        View restore = focusTag == null ? null : page.findViewWithTag(focusTag);
        View target = restore != null ? restore : primary;
        if (episodeFocus && seasonId != null) {
            // la liste d'épisodes arrive ensuite : en attendant, la saison ouverte garde la sélection
            target = page.findViewWithTag("season:" + seasonId);
        }
        if (target != null) target.requestFocus();
    }

    private String metaLine() {
        List<String> parts = new ArrayList<>();
        int year = item.optInt("year", 0);
        if (year > 0) parts.add(String.valueOf(year));
        String type = item.optString("type");
        if ("show".equals(type)) {
            int n = item.optInt("seasons", 0);
            if (n > 0) parts.add(n + (n > 1 ? " saisons" : " saison"));
        } else {
            String d = Ui.duration(item.optLong("duration", 0));
            if (!d.isEmpty()) parts.add(d);
        }
        String cr = item.optString("content_rating", "");
        if (!cr.isEmpty() && !"null".equals(cr)) parts.add(cr);
        double rating = item.optDouble("rating", 0);
        if (rating > 0) parts.add(String.format(java.util.Locale.FRANCE, "★ %.1f", rating));
        JSONArray genres = item.optJSONArray("genres");
        if (genres != null && genres.length() > 0) {
            List<String> g = new ArrayList<>();
            for (int i = 0; i < Math.min(3, genres.length()); i++) g.add(genres.optString(i));
            parts.add(TextUtils.join(", ", g));
        }
        JSONObject media = item.optJSONObject("media");
        if (media != null && !media.isNull("resolution")) parts.add(media.optString("resolution") + "p");
        return TextUtils.join("   ·   ", parts);
    }

    /** Boutons de lecture : -> le bouton principal (sélectionné à l'ouverture). */
    private View addButtons(LinearLayout row) {
        String type = item.optString("type");
        if ("show".equals(type)) {
            JSONObject target = item.optJSONObject("play_target");
            if (target == null) return null;
            long pos = target.optLong("position", 0);
            String label = (pos > 0 ? "Reprendre" : "Lecture") + "  S" + target.optInt("season") + " · É" + target.optInt("episode");
            Button play = Ui.button(act, label, true);
            play.setTag("play");
            play.setOnClickListener(v -> Player.play(act, target, pos));
            row.addView(play, Ui.margins(act, 0, 0, 12, 0));
            Button watched = Ui.button(act, item.optBoolean("watched") ? "Marquer comme non vue" : "Marquer comme vue", false);
            watched.setTag("watched");
            watched.setOnClickListener(v -> markWatched(!item.optBoolean("watched")));
            row.addView(watched);
            return play;
        }
        long pos = item.optLong("position", 0);
        boolean resume = pos > 0 && !item.optBoolean("watched");
        Button play = Ui.button(act, resume ? "Reprendre à " + Ui.clock(pos) : "Lecture", true);
        play.setTag("play");
        play.setOnClickListener(v -> Player.play(act, item, resume ? pos : 0));
        row.addView(play, Ui.margins(act, 0, 0, 12, 0));
        if (resume) {
            Button restart = Ui.button(act, "Depuis le début", false);
            restart.setTag("restart");
            restart.setOnClickListener(v -> Player.play(act, item, 0));
            row.addView(restart, Ui.margins(act, 0, 0, 12, 0));
        }
        Button watched = Ui.button(act, item.optBoolean("watched") ? "Marquer comme non vu" : "Marquer comme vu", false);
        watched.setTag("watched");
        watched.setOnClickListener(v -> markWatched(!item.optBoolean("watched")));
        row.addView(watched);
        return play;
    }

    private void markWatched(boolean watched) {
        Api.send("POST", "/api/items/" + id + "/watched", Api.json("watched", watched), r -> load(false),
                e -> Ui.toast(act, e.getMessage()));
    }

    // ------------------------------------------------------------------ séries : saisons et épisodes
    private void seasons() {
        JSONArray list = item.optJSONArray("seasons");
        if (list == null || list.length() == 0) return;
        JSONObject target = item.optJSONObject("play_target");
        if (seasonId == null) {
            seasonId = target != null ? target.optString("season_id") : list.optJSONObject(0).optString("id");
        }
        HorizontalScrollView hs = new HorizontalScrollView(act);
        hs.setHorizontalScrollBarEnabled(false);
        hs.setClipChildren(false);
        LinearLayout tabs = Ui.row(act);
        tabs.setPadding(0, dp(32), 0, dp(10));
        for (int i = 0; i < list.length(); i++) {
            JSONObject s = list.optJSONObject(i);
            if (s == null) continue;
            String sid = s.optString("id");
            TextView t = Ui.text(act, s.optString("title"), 17, Ui.MUTED);
            t.setPadding(dp(18), dp(8), dp(18), dp(8));
            t.setFocusable(true);
            t.setTag("season:" + sid);
            t.setOnFocusChangeListener((v, has) -> {
                paintSeason(t, sid, has);
                if (has && !sid.equals(seasonId)) {
                    seasonId = sid;
                    for (TextView o : seasonTabs) paintSeason(o, (String) o.getTag(R.id.nexis_image), o.isFocused());
                    loadEpisodes();
                }
            });
            t.setTag(R.id.nexis_image, sid);
            paintSeason(t, sid, false);
            seasonTabs.add(t);
            tabs.addView(t, Ui.margins(act, 0, 0, 8, 0));
        }
        hs.addView(tabs);
        page.addView(hs);
        episodes = Ui.column(act);
        page.addView(episodes, new LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT));
        loadEpisodes();
    }

    private void paintSeason(TextView t, String sid, boolean focus) {
        boolean open = sid.equals(seasonId);
        t.setBackground(focus ? Ui.rounded(act, Color.WHITE, 20) : open ? Ui.rounded(act, Ui.SURFACE_2, 20) : null);
        t.setTextColor(focus ? Color.BLACK : open ? Ui.TEXT : Ui.MUTED);
    }

    private void loadEpisodes() {
        String sid = seasonId;
        Api.arr("/api/items/" + sid + "/children", eps -> {
            if (!sid.equals(seasonId) || episodes == null) return;
            View focus = act.getCurrentFocus();
            Object focusTag = focus != null && BrowseScreen.inside(focus, episodes) ? focus.getTag() : wantFocus;
            wantFocus = null;
            episodes.removeAllViews();
            for (int i = 0; i < eps.length(); i++) {
                JSONObject ep = eps.optJSONObject(i);
                if (ep != null) episodes.addView(episodeRow(ep));
            }
            if (focusTag != null) {
                View again = episodes.findViewWithTag(focusTag);
                if (again != null) again.requestFocus();
            }
        }, e -> Ui.toast(act, e.getMessage()));
    }

    private View episodeRow(JSONObject ep) {
        Context c = act;
        LinearLayout row = Ui.row(c);
        row.setGravity(Gravity.TOP);
        row.setFocusable(true);
        row.setTag("ep:" + ep.optString("id"));
        row.setBackground(Ui.focusable(c, Color.TRANSPARENT, Ui.SURFACE_2, 12));
        row.setPadding(dp(10), dp(10), dp(10), dp(10));
        FrameLayout thumbBox = new FrameLayout(c);
        thumbBox.setBackground(Ui.rounded(c, Ui.SURFACE, 8));
        thumbBox.setClipToOutline(true);
        ImageView thumb = new ImageView(c);
        thumb.setScaleType(ImageView.ScaleType.CENTER_CROP);
        Images.load(thumb, Images.library(ep.optString("thumb"), 400, 224));
        thumbBox.addView(thumb, new FrameLayout.LayoutParams(-1, -1));
        long pos = ep.optLong("position", 0), dur = ep.optLong("duration", 0);
        boolean watched = ep.optBoolean("watched");
        if (pos > 0 && dur > 0 && !watched) {
            View bar = new View(c);
            bar.setBackgroundColor(Ui.ACCENT);
            thumbBox.addView(bar, new FrameLayout.LayoutParams((int) (dp(200) * Math.min(1, pos / (double) dur)), dp(4),
                    Gravity.BOTTOM | Gravity.START));
        }
        if (watched) {
            TextView check = Ui.text(c, "✓", 13, Color.BLACK);
            check.setGravity(Gravity.CENTER);
            GradientDrawable dot = new GradientDrawable();
            dot.setShape(GradientDrawable.OVAL);
            dot.setColor(Ui.ACCENT);
            check.setBackground(dot);
            FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(dp(24), dp(24), Gravity.TOP | Gravity.END);
            cp.setMargins(0, dp(6), dp(6), 0);
            thumbBox.addView(check, cp);
        }
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(dp(200), dp(112));
        tp.rightMargin = dp(20);
        row.addView(thumbBox, tp);
        LinearLayout text = Ui.column(c);
        TextView t = Ui.oneLine(Ui.title(c, ep.optInt("episode") + ". " + ep.optString("title"), 18));
        text.addView(t);
        String d = Ui.duration(dur);
        TextView meta = Ui.text(c, d + (pos > 0 && !watched ? "   ·   reprise à " + Ui.clock(pos) : ""), 14, Ui.MUTED);
        meta.setPadding(0, dp(4), 0, dp(6));
        text.addView(meta);
        String summary = ep.optString("summary", "");
        if (!summary.isEmpty() && !"null".equals(summary)) {
            TextView s = Ui.text(c, summary, 15, Color.rgb(200, 200, 208));
            s.setMaxLines(2);
            s.setEllipsize(TextUtils.TruncateAt.END);
            text.addView(s);
        }
        row.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        row.setOnFocusChangeListener((v, has) -> t.setTextColor(has ? Ui.ACCENT : Ui.TEXT));
        row.setOnClickListener(v -> Player.play(act, ep, watched ? 0 : pos));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(1000), ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(4);
        row.setLayoutParams(lp);
        return row;
    }
}
