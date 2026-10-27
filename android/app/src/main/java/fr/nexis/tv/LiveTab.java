package fr.nexis.tv;

import android.content.Context;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** TV en direct : liste des chaînes avec le programme en cours ; OK lance la chaîne en plein écran. */
final class LiveTab extends BrowseScreen.Tab {
    private RecyclerView list;
    private TextView message;
    private JSONArray channels = new JSONArray();
    private long now;
    private final ChannelAdapter adapter = new ChannelAdapter();

    LiveTab(BrowseScreen b) {
        super(b);
    }

    @Override
    View create() {
        LinearLayout root = Ui.column(act);
        TextView title = Ui.title(act, Tr.t("TV en direct"), 24);
        title.setPadding(dp(48), dp(10), dp(48), dp(8));
        root.addView(title);
        FrameLayout body = new FrameLayout(act);
        list = new RecyclerView(act);
        list.setLayoutManager(new LinearLayoutManager(act));
        list.setAdapter(adapter);
        list.setItemAnimator(null);
        list.setClipToPadding(false);
        list.setPadding(dp(40), dp(4), dp(40), dp(40));
        body.addView(list, new FrameLayout.LayoutParams(-1, -1));
        message = Ui.text(act, Tr.t("Chargement…"), 18, Ui.MUTED);
        message.setGravity(Gravity.CENTER);
        body.addView(message, new FrameLayout.LayoutParams(-1, -1));
        root.addView(body, new LinearLayout.LayoutParams(-1, 0, 1f));
        return root;
    }

    @Override
    void onShow() {
        Api.obj("/api/live", r -> {
            channels = r.optJSONArray("channels") != null ? r.optJSONArray("channels") : new JSONArray();
            now = r.optLong("now", System.currentTimeMillis() / 1000);
            Session.channels = channels;
            adapter.notifyDataSetChanged();
            message.setVisibility(channels.length() == 0 ? View.VISIBLE : View.GONE);
            message.setText(Tr.t("Aucune chaîne disponible."));
        }, e -> {
            if (channels.length() == 0) message.setText(e.getMessage());
        });
    }

    @Override
    View entry() {
        return list.getChildCount() > 0 ? list.getChildAt(0) : null;
    }

    static String hour(long epochSeconds) {
        return new SimpleDateFormat("HH:mm", Locale.FRANCE).format(new Date(epochSeconds * 1000));
    }

    private final class ChannelAdapter extends RecyclerView.Adapter<ChannelHolder> {
        @NonNull
        @Override
        public ChannelHolder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            return new ChannelHolder(parent.getContext());
        }

        @Override
        public void onBindViewHolder(@NonNull ChannelHolder h, int position) {
            JSONObject c = channels.optJSONObject(position);
            if (c == null) return;
            h.bind(c, now);
            h.itemView.setOnClickListener(v -> LiveActivity.open(act, position));
        }

        @Override
        public int getItemCount() {
            return channels.length();
        }
    }

    private static final class ChannelHolder extends RecyclerView.ViewHolder {
        final ImageView logo;
        final TextView number, name, prog, next;
        final ProgressBar bar;

        ChannelHolder(Context c) {
            super(new LinearLayout(c));
            LinearLayout row = (LinearLayout) itemView;
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setFocusable(true);
            row.setBackground(Ui.focusable(c, Color.TRANSPARENT, Ui.SURFACE_2, 10));
            int pad = Ui.dp(c, 10);
            row.setPadding(pad, pad, pad, pad);
            RecyclerView.LayoutParams lp = new RecyclerView.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.bottomMargin = Ui.dp(c, 4);
            row.setLayoutParams(lp);

            number = Ui.text(c, "", 17, Ui.MUTED);
            number.setGravity(Gravity.CENTER);
            row.addView(number, new LinearLayout.LayoutParams(Ui.dp(c, 54), ViewGroup.LayoutParams.WRAP_CONTENT));
            FrameLayout logoBox = new FrameLayout(c);
            logoBox.setBackground(Ui.rounded(c, Ui.SURFACE, 6));
            logo = new ImageView(c);
            logo.setScaleType(ImageView.ScaleType.FIT_CENTER);
            logo.setPadding(Ui.dp(c, 4), Ui.dp(c, 4), Ui.dp(c, 4), Ui.dp(c, 4));
            logoBox.addView(logo, new FrameLayout.LayoutParams(-1, -1));
            LinearLayout.LayoutParams llp = new LinearLayout.LayoutParams(Ui.dp(c, 88), Ui.dp(c, 52));
            llp.rightMargin = Ui.dp(c, 18);
            row.addView(logoBox, llp);

            LinearLayout text = Ui.column(c);
            name = Ui.oneLine(Ui.title(c, "", 18));
            text.addView(name);
            prog = Ui.oneLine(Ui.text(c, "", 15, Ui.TEXT));
            text.addView(prog);
            bar = new ProgressBar(c, null, android.R.attr.progressBarStyleHorizontal);
            bar.setMax(1000);
            bar.setProgressTintList(android.content.res.ColorStateList.valueOf(Ui.ACCENT));
            LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(Ui.dp(c, 220), Ui.dp(c, 4));
            bp.topMargin = Ui.dp(c, 6);
            text.addView(bar, bp);
            row.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            next = Ui.oneLine(Ui.text(c, "", 14, Ui.MUTED));
            next.setGravity(Gravity.END);
            row.addView(next, new LinearLayout.LayoutParams(Ui.dp(c, 300), ViewGroup.LayoutParams.WRAP_CONTENT));
        }

        void bind(JSONObject ch, long now) {
            number.setText(ch.isNull("number") ? "" : ch.optString("number"));
            name.setText(ch.optString("name"));
            String logoUrl = ch.isNull("logo") ? null : ch.optString("logo", null);
            Images.load(logo, logoUrl, Ui.dp(itemView.getContext(), 120));
            JSONObject cur = ch.optJSONObject("now");
            if (cur != null) {
                long start = cur.optLong("start"), stop = cur.optLong("stop");
                prog.setText(hour(start) + "  " + cur.optString("title"));
                bar.setVisibility(View.VISIBLE);
                bar.setProgress(stop > start ? (int) (1000 * Math.max(0, Math.min(1, (now - start) / (double) (stop - start)))) : 0);
            } else {
                prog.setText("");
                bar.setVisibility(View.INVISIBLE);
            }
            JSONObject nx = ch.optJSONObject("next");
            next.setText(nx != null ? Tr.t("Ensuite : ") + hour(nx.optLong("start")) + "  " + nx.optString("title") : "");
        }
    }
}
