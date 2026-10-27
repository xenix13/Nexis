package fr.nexis.tv;

import android.os.Handler;
import android.os.Looper;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONObject;

/** Recherche dans toutes les bibliothèques : films, séries et épisodes. */
final class SearchTab extends BrowseScreen.Tab {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private EditText input;
    private LinearLayout results;
    private ScrollView scroll;
    private TextView message;
    private Runnable pending;
    private int seq;

    SearchTab(BrowseScreen b) {
        super(b);
    }

    @Override
    View create() {
        LinearLayout root = Ui.column(act);
        root.setClipChildren(false);
        input = Ui.input(act, "Titre d'un film, d'une série, d'un épisode…", false);
        input.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        LinearLayout.LayoutParams ip = new LinearLayout.LayoutParams(dp(640), ViewGroup.LayoutParams.WRAP_CONTENT);
        ip.setMargins(dp(48), dp(12), dp(48), dp(4));
        root.addView(input, ip);
        input.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {}

            @Override
            public void onTextChanged(CharSequence s, int a, int b, int c) {}

            @Override
            public void afterTextChanged(Editable s) {
                if (pending != null) handler.removeCallbacks(pending);
                pending = () -> search(s.toString());
                handler.postDelayed(pending, 450);
            }
        });
        input.setOnEditorActionListener((v, id, ev) -> {
            if (pending != null) handler.removeCallbacks(pending);
            search(input.getText().toString());
            return false;
        });
        message = Ui.text(act, "Saisissez au moins deux lettres.", 17, Ui.MUTED);
        message.setPadding(dp(48), dp(18), dp(48), 0);
        root.addView(message);
        scroll = new ScrollView(act);
        scroll.setClipChildren(false);
        results = Ui.column(act);
        results.setClipChildren(false);
        results.setPadding(0, 0, 0, dp(40));
        scroll.addView(results);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));
        return root;
    }

    private void search(String q) {
        int mine = ++seq;
        String query = q.trim();
        if (query.length() < 2) {
            results.removeAllViews();
            message.setVisibility(View.VISIBLE);
            message.setText("Saisissez au moins deux lettres.");
            return;
        }
        Api.obj("/api/search?q=" + Api.enc(query), r -> {
            if (mine != seq) return;
            results.removeAllViews();
            int n = addRow("Films", r.optJSONArray("movies"), Cards.POSTER)
                    + addRow("Séries", r.optJSONArray("shows"), Cards.POSTER)
                    + addRow("Épisodes", r.optJSONArray("episodes"), Cards.WIDE);
            message.setVisibility(n == 0 ? View.VISIBLE : View.GONE);
            message.setText("Aucun résultat pour « " + query + " ».");
        }, e -> {
            if (mine == seq) message.setText(e.getMessage());
        });
    }

    private int addRow(String title, JSONArray items, int style) {
        if (items == null || items.length() == 0) return 0;
        LinearLayout box = Ui.column(act);
        box.setClipChildren(false);
        TextView t = Ui.title(act, title, 20);
        t.setPadding(dp(48), dp(12), dp(48), 0);
        box.addView(t);
        Cards.Adapter adapter = new Cards.Adapter(style, item -> Detail.open(act, item));
        adapter.focus = (v, has) -> {
            if (has) scroll.post(() -> scroll.smoothScrollTo(0, Math.max(0, box.getTop() - dp(6))));
        };
        adapter.add(items);
        RecyclerView r = Cards.row(act, adapter);
        box.addView(r, new LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT));
        results.addView(box, new LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT));
        return items.length();
    }

    @Override
    View entry() {
        return input;
    }
}
