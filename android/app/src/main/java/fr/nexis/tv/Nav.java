package fr.nexis.tv;

import android.content.Context;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;

import androidx.recyclerview.widget.RecyclerView;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;

/**
 * Navigation à la télécommande par rangées, comme sur les applis de TV : gauche / droite restent dans la rangée,
 * haut / bas passent à la rangée voisine et retrouvent l'élément qu'on y avait quitté. La recherche « au plus
 * proche » d'Android, elle, sautait d'une rangée à l'autre au bout d'une ligne et revenait rarement au même endroit.
 */
final class Nav {
    private Nav() {}

    /** Choix de l'élément d'arrivée dans un bloc (ex. : la saison ouverte plutôt que la première). */
    interface Entry {
        View get();
    }

    /** Rangée horizontale de cartes : on n'en sort pas par les côtés, et elle retient la dernière carte choisie. */
    static final class Row extends RecyclerView {
        private int last;

        Row(Context c) {
            super(c);
        }

        @Override
        public void requestChildFocus(View child, View focused) {
            super.requestChildFocus(child, focused);
            int p = getChildAdapterPosition(child);
            if (p != NO_POSITION) last = p;
        }

        @Override
        public View focusSearch(View focused, int direction) {
            View result = super.focusSearch(focused, direction);
            if ((direction == FOCUS_LEFT || direction == FOCUS_RIGHT) && !BrowseScreen.inside(result, this)) {
                return focused;  // bout de la rangée : on reste dessus
            }
            return result;
        }

        /** Carte à sélectionner en arrivant sur la rangée : celle qu'on y avait quittée. */
        View entry() {
            Adapter<?> a = getAdapter();
            if (a == null || a.getItemCount() == 0) return null;
            int p = Math.min(last, a.getItemCount() - 1);
            ViewHolder vh = findViewHolderForAdapterPosition(p);
            if (vh != null) return vh.itemView;
            scrollToPosition(p);
            return getChildCount() > 0 ? getChildAt(0) : null;
        }
    }

    /** Colonne de blocs (rangées, boutons, liste d'épisodes…) parcourue de bloc en bloc avec haut / bas. */
    static final class Column extends LinearLayout {
        private final Map<View, Entry> entries = new HashMap<>();
        private final Map<View, View> remembered = new HashMap<>();

        Column(Context c) {
            super(c);
            setOrientation(VERTICAL);
        }

        /** Bloc dont l'élément d'arrivée est choisi par le code (sinon : le dernier sélectionné, ou le premier). */
        void entry(View block, Entry e) {
            entries.put(block, e);
        }

        @Override
        public void removeAllViews() {
            super.removeAllViews();
            entries.clear();
            remembered.clear();
        }

        @Override
        public void requestChildFocus(View child, View focused) {
            super.requestChildFocus(child, focused);
            remembered.put(child, focused);
        }

        private int blockOf(View v) {
            for (int i = 0; i < getChildCount(); i++) if (BrowseScreen.inside(v, getChildAt(i))) return i;
            return -1;
        }

        /** Élément où arriver dans un bloc, ou null s'il n'a rien de sélectionnable. */
        View entryOf(View block) {
            if (block.getVisibility() != VISIBLE) return null;
            Entry e = entries.get(block);
            if (e != null) {
                View v = e.get();
                if (v != null && v.isShown()) return v;
            }
            Row row = findRow(block);
            if (row != null) return row.entry();
            View last = remembered.get(block);
            if (last != null && last.isShown() && BrowseScreen.inside(last, block)) return last;
            ArrayList<View> all = new ArrayList<>();
            block.addFocusables(all, FOCUS_DOWN);
            for (View v : all) if (v.isShown()) return v;
            return null;
        }

        private static Row findRow(View v) {
            if (v instanceof Row) return (Row) v;
            if (v instanceof ViewGroup) {
                ViewGroup g = (ViewGroup) v;
                for (int i = 0; i < g.getChildCount(); i++) {
                    Row r = findRow(g.getChildAt(i));
                    if (r != null) return r;
                }
            }
            return null;
        }

        @Override
        public View focusSearch(View focused, int direction) {
            View result = super.focusSearch(focused, direction);
            if (direction != FOCUS_UP && direction != FOCUS_DOWN) return result;
            int i = blockOf(focused);
            if (i < 0) return result;
            // dans un bloc qui est lui-même une liste verticale (épisodes) : on y reste tant qu'on peut
            if (result != null && BrowseScreen.inside(result, getChildAt(i)) && findRow(getChildAt(i)) == null) return result;
            int step = direction == FOCUS_DOWN ? 1 : -1;
            for (int j = i + step; j >= 0 && j < getChildCount(); j += step) {
                View e = entryOf(getChildAt(j));
                if (e != null) return e;
            }
            // plus rien en dessous : on reste ; plus rien au-dessus : la barre du haut (voir BrowseScreen)
            return direction == FOCUS_DOWN ? focused : result;
        }
    }
}
