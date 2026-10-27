package fr.nexis.tv;

import android.graphics.Rect;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.GridLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONObject;

/** Une bibliothèque : toutes les affiches en grille, chargées par pages au fil du défilement. */
final class LibraryTab extends BrowseScreen.Tab {
    private static final String[] SORT_KEYS = {"title", "added", "year", "rating"};
    private static final String[] SORT_LABELS = {"Titre", "Ajouts récents", "Année", "Note"};

    private final JSONObject lib;
    private RecyclerView grid;
    private Cards.Adapter adapter;
    private TextView count, message;
    private Button sort;
    private int sortIndex;
    private int total = -1;
    private boolean loading;
    private int generation;

    LibraryTab(BrowseScreen b, JSONObject lib) {
        super(b);
        this.lib = lib;
        String saved = Api.store().getString("sort:" + lib.optString("id"), "title");
        for (int i = 0; i < SORT_KEYS.length; i++) if (SORT_KEYS[i].equals(saved)) sortIndex = i;
    }

    @Override
    View create() {
        LinearLayout root = Ui.column(act);
        root.setClipChildren(false);
        LinearLayout head = Ui.row(act);
        head.setPadding(dp(48), dp(10), dp(48), dp(2));
        TextView title = Ui.title(act, lib.optString("title"), 24);
        head.addView(title);
        count = Ui.text(act, "", 16, Ui.MUTED);
        count.setPadding(dp(14), 0, 0, 0);
        head.addView(count, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        sort = Ui.button(act, Tr.t("Trier : ") + Tr.t(SORT_LABELS[sortIndex]), false);
        sort.setTextSize(15);
        sort.setOnClickListener(v -> chooseSort());
        head.addView(sort);
        root.addView(head);

        FrameLayout body = new FrameLayout(act);
        body.setClipChildren(false);
        int cardW = Cards.width(act, Cards.POSTER) + dp(16);
        int avail = act.getResources().getDisplayMetrics().widthPixels - dp(80);
        int span = Math.max(2, avail / cardW);
        grid = new RecyclerView(act);
        grid.setLayoutManager(new GridLayoutManager(act, span));
        grid.setClipToPadding(false);
        grid.setClipChildren(false);
        grid.setPadding(dp(40), dp(12), dp(40), dp(40));
        grid.setItemAnimator(null);
        grid.addItemDecoration(new RecyclerView.ItemDecoration() {
            @Override
            public void getItemOffsets(@NonNull Rect out, @NonNull View v, @NonNull RecyclerView p, @NonNull RecyclerView.State s) {
                out.bottom = dp(18);
            }
        });
        adapter = new Cards.Adapter(Cards.POSTER, item -> Detail.open(act, item));
        adapter.nearEnd = () -> grid.post(this::more);
        grid.setAdapter(adapter);
        body.addView(grid, new FrameLayout.LayoutParams(-1, -1));
        message = Ui.text(act, Tr.t("Chargement…"), 18, Ui.MUTED);
        message.setGravity(Gravity.CENTER);
        body.addView(message, new FrameLayout.LayoutParams(-1, -1));
        root.addView(body, new LinearLayout.LayoutParams(-1, 0, 1f));
        return root;
    }

    @Override
    void onShow() {
        if (total < 0) reload();
        else refreshVisible();
    }

    private void reload() {
        generation++;
        total = -1;
        loading = false;
        adapter.set(new JSONArray());
        message.setVisibility(View.VISIBLE);
        message.setText(Tr.t("Chargement…"));
        more();
    }

    private void more() {
        if (loading || (total >= 0 && adapter.items.size() >= total)) return;
        loading = true;
        int gen = generation;
        int start = adapter.items.size();
        Api.obj("/api/libraries/" + lib.optString("id") + "/items?start=" + start + "&size=100&sort=" + SORT_KEYS[sortIndex], r -> {
            if (gen != generation) return;
            loading = false;
            total = r.optInt("total", 0);
            count.setText(total + (total > 1 ? " titres" : " titre"));
            adapter.add(r.optJSONArray("items") != null ? r.optJSONArray("items") : new JSONArray());
            message.setVisibility(adapter.items.isEmpty() ? View.VISIBLE : View.GONE);
            message.setText(Tr.t("Cette bibliothèque est vide."));
        }, e -> {
            if (gen != generation) return;
            loading = false;
            if (adapter.items.isEmpty()) message.setText(e.getMessage());
        });
    }

    /** Retour sur la page : « vu » et progression des affiches déjà chargées remis à jour. */
    private void refreshVisible() {
        if (adapter.items.isEmpty()) return;
        int gen = generation;
        int size = Math.min(200, adapter.items.size());
        Api.obj("/api/libraries/" + lib.optString("id") + "/items?start=0&size=" + size + "&sort=" + SORT_KEYS[sortIndex], r -> {
            if (gen != generation) return;
            JSONArray items = r.optJSONArray("items");
            if (items == null) return;
            for (int i = 0; i < items.length() && i < adapter.items.size(); i++) {
                JSONObject fresh = items.optJSONObject(i);
                if (fresh != null && fresh.optString("id").equals(adapter.items.get(i).optString("id"))) {
                    adapter.items.set(i, fresh);
                }
            }
            adapter.notifyItemRangeChanged(0, Math.min(items.length(), adapter.items.size()));
        }, null);
    }

    private void chooseSort() {
        Ui.dialog(act).setTitle(Tr.t("Trier par")).setSingleChoiceItems(Tr.all(SORT_LABELS), sortIndex, (d, which) -> {
            d.dismiss();
            if (which == sortIndex) return;
            sortIndex = which;
            Api.store().edit().putString("sort:" + lib.optString("id"), SORT_KEYS[which]).apply();
            sort.setText(Tr.t("Trier : ") + Tr.t(SORT_LABELS[which]));
            reload();
        }).show();
    }

    @Override
    View entry() {
        return grid != null && grid.getChildCount() > 0 ? grid.getChildAt(0) : sort;
    }
}
