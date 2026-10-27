package fr.nexis.tv;

import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Cartes du catalogue : affiches (films, séries) ou vignettes larges (reprise, épisodes).
 * L'élément sélectionné grossit légèrement et reçoit un cadre orange ; la rangée laisse assez de marge
 * autour pour que ce cadre ne soit jamais coupé.
 */
final class Cards {
    static final int POSTER = 0, WIDE = 1;

    interface Click {
        void open(JSONObject item);
    }

    private Cards() {}

    static int width(Context c, int style) {
        return Ui.dp(c, style == WIDE ? 248 : 132);
    }

    static int imageHeight(Context c, int style) {
        return style == WIDE ? Ui.dp(c, 140) : Ui.dp(c, 198);
    }

    static final class Adapter extends RecyclerView.Adapter<Holder> {
        final List<JSONObject> items = new ArrayList<>();
        final int style;
        final Click click;
        Runnable nearEnd;
        View.OnFocusChangeListener focus;

        Adapter(int style, Click click) {
            this.style = style;
            this.click = click;
        }

        void add(JSONArray a) {
            int start = items.size();
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.optJSONObject(i);
                if (o != null) items.add(o);
            }
            notifyItemRangeInserted(start, items.size() - start);
        }

        void set(JSONArray a) {
            items.clear();
            notifyDataSetChanged();
            add(a);
        }

        @NonNull
        @Override
        public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            return new Holder(parent.getContext(), style);
        }

        @Override
        public void onBindViewHolder(@NonNull Holder h, int position) {
            JSONObject it = items.get(position);
            h.bind(it, style);
            h.itemView.setOnClickListener(v -> click.open(it));
            h.extraFocus = focus;
            if (nearEnd != null && position >= items.size() - 12) nearEnd.run();
        }

        @Override
        public int getItemCount() {
            return items.size();
        }
    }

    static final class Holder extends RecyclerView.ViewHolder {
        final FrameLayout frame;
        final ImageView image;
        final TextView title, sub, badge, placeholder;
        final View progress, progressTrack;
        View.OnFocusChangeListener extraFocus;

        Holder(Context c, int style) {
            super(new LinearLayout(c));
            LinearLayout root = (LinearLayout) itemView;
            root.setOrientation(LinearLayout.VERTICAL);
            root.setFocusable(true);
            root.setClipChildren(false);
            root.setClipToPadding(false);
            int w = width(c, style), ih = imageHeight(c, style);
            RecyclerView.LayoutParams lp = new RecyclerView.LayoutParams(w, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.setMargins(Ui.dp(c, 8), 0, Ui.dp(c, 8), 0);
            root.setLayoutParams(lp);

            frame = new FrameLayout(c);
            frame.setBackground(Ui.rounded(c, Ui.SURFACE, 8));
            frame.setClipToOutline(true);
            frame.setForeground(Ui.focusRing(c, 8));
            placeholder = Ui.text(c, "", 15, Ui.MUTED);
            placeholder.setGravity(Gravity.CENTER);
            placeholder.setPadding(Ui.dp(c, 8), 0, Ui.dp(c, 8), 0);
            frame.addView(placeholder, new FrameLayout.LayoutParams(-1, -1));
            image = new ImageView(c);
            image.setScaleType(ImageView.ScaleType.CENTER_CROP);
            frame.addView(image, new FrameLayout.LayoutParams(-1, -1));

            progressTrack = new View(c);
            progressTrack.setBackgroundColor(Color.argb(150, 0, 0, 0));
            FrameLayout.LayoutParams tp = new FrameLayout.LayoutParams(-1, Ui.dp(c, 4), Gravity.BOTTOM);
            frame.addView(progressTrack, tp);
            progress = new View(c);
            progress.setBackgroundColor(Ui.ACCENT);
            frame.addView(progress, new FrameLayout.LayoutParams(0, Ui.dp(c, 4), Gravity.BOTTOM | Gravity.START));

            badge = Ui.text(c, "", 12, Color.BLACK);
            badge.setGravity(Gravity.CENTER);
            GradientDrawable dot = new GradientDrawable();
            dot.setShape(GradientDrawable.OVAL);
            dot.setColor(Ui.ACCENT);
            badge.setBackground(dot);
            FrameLayout.LayoutParams bp = new FrameLayout.LayoutParams(Ui.dp(c, 24), Ui.dp(c, 24), Gravity.TOP | Gravity.END);
            bp.setMargins(0, Ui.dp(c, 6), Ui.dp(c, 6), 0);
            frame.addView(badge, bp);
            root.addView(frame, new LinearLayout.LayoutParams(w, ih));

            title = Ui.oneLine(Ui.text(c, "", 15, Ui.TEXT));
            title.setPadding(0, Ui.dp(c, 8), 0, 0);
            root.addView(title);
            sub = Ui.oneLine(Ui.text(c, "", 13, Ui.MUTED));
            root.addView(sub);

            root.setOnFocusChangeListener((v, has) -> {
                frame.animate().scaleX(has ? 1.06f : 1f).scaleY(has ? 1.06f : 1f).setDuration(140).start();
                frame.setElevation(has ? Ui.dp(c, 8) : 0);
                title.setTextColor(has ? Ui.ACCENT : Ui.TEXT);
                frame.setSelected(has);  // cadre orange (état « sélectionné » de l'image)
                if (extraFocus != null) extraFocus.onFocusChange(v, has);
            });
        }

        void bind(JSONObject it, int style) {
            Context c = itemView.getContext();
            String type = it.optString("type");
            boolean episode = "episode".equals(type);
            String t, s;
            if (episode) {
                t = it.optString("show", it.optString("title"));
                s = "S" + it.optInt("season") + " · É" + it.optInt("episode") + "  " + it.optString("title");
            } else if ("season".equals(type)) {
                t = it.optString("title");
                s = it.optString("show");
            } else {
                t = it.optString("title");
                int y = it.optInt("year", 0);
                s = y > 0 ? String.valueOf(y) : "";
            }
            title.setText(t);
            sub.setText(s);
            placeholder.setText(t);
            String img;
            if (style == WIDE) {
                String src = episode ? it.optString("thumb") : it.optString("art", "");
                if (src.isEmpty() || "null".equals(src)) src = it.optString(episode ? "show_art" : "thumb");
                img = Images.library(src, 496, 280);
            } else {
                String src = episode ? it.optString("show_thumb", it.optString("thumb")) : it.optString("thumb");
                img = Images.library(src, 264, 396);
            }
            Images.load(image, img);

            long pos = it.optLong("position", 0), dur = it.optLong("duration", 0);
            boolean watched = it.optBoolean("watched", false);
            if (pos > 0 && dur > 0 && !watched) {
                progressTrack.setVisibility(View.VISIBLE);
                progress.setVisibility(View.VISIBLE);
                ViewGroup.LayoutParams p = progress.getLayoutParams();
                p.width = (int) (width(c, style) * Math.min(1.0, pos / (double) dur));
                progress.setLayoutParams(p);
            } else {
                progressTrack.setVisibility(View.GONE);
                progress.setVisibility(View.GONE);
            }
            int unwatched = it.has("leaves") ? it.optInt("leaves", 0) - it.optInt("watched_count", 0) : 0;
            if (watched) {
                badge.setVisibility(View.VISIBLE);
                badge.setText("✓");
            } else if (!episode && unwatched > 0 && unwatched < 100) {
                badge.setVisibility(View.VISIBLE);
                badge.setText(String.valueOf(unwatched));
            } else {
                badge.setVisibility(View.GONE);
            }
        }
    }

    /** Rangée horizontale (titre + cartes) : marges internes pour que la carte agrandie reste entière. */
    static RecyclerView row(Context c, Adapter adapter) {
        RecyclerView r = new RecyclerView(c);
        r.setLayoutManager(new LinearLayoutManager(c, LinearLayoutManager.HORIZONTAL, false));
        r.setAdapter(adapter);
        r.setClipChildren(false);
        r.setClipToPadding(false);
        r.setPadding(Ui.dp(c, 40), Ui.dp(c, 12), Ui.dp(c, 40), Ui.dp(c, 12));
        r.setItemAnimator(null);
        r.setHasFixedSize(true);
        r.setFocusable(false);
        r.setDescendantFocusability(ViewGroup.FOCUS_AFTER_DESCENDANTS);
        return r;
    }
}
