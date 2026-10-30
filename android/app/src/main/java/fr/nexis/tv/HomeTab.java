package fr.nexis.tv;

import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Accueil : « Reprendre », une rangée par bibliothèque (derniers ajouts) et « Ma liste » si elle n'est pas vide. */
final class HomeTab extends BrowseScreen.Tab {
    private ScrollView scroll;
    private Nav.Column list;
    private TextView message;
    private final List<RecyclerView> rows = new ArrayList<>();
    private final List<String> keys = new ArrayList<>();
    private boolean loading;

    HomeTab(BrowseScreen b) {
        super(b);
    }

    @Override
    View create() {
        FrameLayout root = new FrameLayout(act);
        scroll = new ScrollView(act);
        scroll.setClipChildren(false);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.setSmoothScrollingEnabled(true);
        list = new Nav.Column(act);  // haut / bas : de rangée en rangée, en retrouvant la carte quittée
        list.setClipChildren(false);
        list.setPadding(0, dp(4), 0, dp(48));
        scroll.addView(list);
        root.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        message = Ui.text(act, Tr.t("Chargement…"), 18, Ui.MUTED);
        message.setGravity(Gravity.CENTER);
        root.addView(message, new FrameLayout.LayoutParams(-1, -1));
        return root;
    }

    @Override
    void onShow() {
        if (loading) return;
        loading = true;
        Api.obj("/api/home", h -> {
            loading = false;
            render(h);
        }, e -> {
            loading = false;
            if (rows.isEmpty()) {
                message.setVisibility(View.VISIBLE);
                message.setText(e.getMessage());
            }
        });
    }

    private void render(JSONObject h) {
        // on retient la carte sélectionnée pour la retrouver après la mise à jour (retour de lecture)
        String focusKey = null;
        int focusPos = -1;
        View f = act.getCurrentFocus();
        for (int i = 0; i < rows.size(); i++) {
            RecyclerView r = rows.get(i);
            if (f != null && BrowseScreen.inside(f, r)) {
                View item = r.findContainingItemView(f);
                focusKey = keys.get(i);
                focusPos = item == null ? 0 : r.getChildAdapterPosition(item);
            }
        }
        list.removeAllViews();
        rows.clear();
        keys.clear();
        addRow("continue", Tr.t("Reprendre"), h.optJSONArray("continue"), Cards.WIDE, true);
        JSONArray libs = h.optJSONArray("rows");
        if (libs != null) {
            for (int i = 0; i < libs.length(); i++) {
                JSONObject r = libs.optJSONObject(i);
                if (r == null) continue;
                JSONObject lib = r.optJSONObject("library");
                String name = lib == null ? "" : lib.optString("title");
                addRow("lib:" + (lib == null ? i : lib.optString("id")), name,
                        r.optJSONArray("items"), Cards.POSTER, false);
            }
        }
        addRow("watchlist", Tr.t("Ma liste"), h.optJSONArray("watchlist"), Cards.POSTER, false);
        message.setVisibility(rows.isEmpty() ? View.VISIBLE : View.GONE);
        message.setText(Tr.t("Rien à afficher pour l'instant : les bibliothèques sont peut-être vides."));
        if (focusKey != null) {
            int idx = keys.indexOf(focusKey);
            if (idx < 0) idx = 0;
            if (idx < rows.size()) restoreFocus(rows.get(idx), Math.max(0, focusPos));
        }
    }

    private void restoreFocus(RecyclerView r, int pos) {
        int p = Math.min(pos, Math.max(0, r.getAdapter().getItemCount() - 1));
        r.scrollToPosition(p);
        r.post(() -> {
            RecyclerView.ViewHolder vh = r.findViewHolderForAdapterPosition(p);
            if (vh != null) vh.itemView.requestFocus();
        });
    }

    private void addRow(String key, String title, JSONArray items, int style, boolean resume) {
        if (items == null || items.length() == 0) return;
        LinearLayout box = Ui.column(act);
        box.setClipChildren(false);
        box.setClipToPadding(false);
        TextView t = Ui.title(act, title, 26);
        t.setPadding(dp(48), dp(14), dp(48), 0);
        box.addView(t);
        Cards.Adapter adapter = new Cards.Adapter(style, item -> {
            if (resume) Player.play(act, item, item.optLong("position", 0));
            else Detail.open(act, item);
        });
        adapter.focus = (v, has) -> {
            if (has) scroll.post(() -> scroll.smoothScrollTo(0, Math.max(0, box.getTop() - dp(6))));
        };
        adapter.add(items);
        RecyclerView r = Cards.row(act, adapter);
        box.addView(r, new LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT));
        list.addView(box, new LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT));
        rows.add(r);
        keys.add(key);
    }

    @Override
    View entry() {
        return list == null || list.getChildCount() == 0 ? null : list.entryOf(list.getChildAt(0));
    }
}
