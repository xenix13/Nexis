package fr.nexis.tv;

import android.app.AlertDialog;
import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.text.InputType;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/** Couleurs, tailles et petits composants partagés par tous les écrans (pensés pour la télécommande). */
final class Ui {
    static final int BG = Color.rgb(11, 11, 14);
    static final int SURFACE = Color.rgb(24, 24, 29);
    static final int SURFACE_2 = Color.rgb(36, 36, 43);
    static final int TEXT = Color.WHITE;
    static final int MUTED = Color.rgb(165, 165, 176);
    static final int ACCENT = Color.rgb(255, 164, 58);
    static final int ERROR = Color.rgb(255, 120, 110);

    private Ui() {}

    static int dp(Context c, float v) {
        return Math.round(v * c.getResources().getDisplayMetrics().density);
    }

    static TextView text(Context c, CharSequence s, float sp, int color) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        return t;
    }

    static TextView title(Context c, CharSequence s, float sp) {
        TextView t = text(c, s, sp, TEXT);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    static TextView oneLine(TextView t) {
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        return t;
    }

    static GradientDrawable rounded(Context c, int fill, float radiusDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(fill);
        d.setCornerRadius(dp(c, radiusDp));
        return d;
    }

    static GradientDrawable outline(Context c, int stroke, float radiusDp, float widthDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(Color.TRANSPARENT);
        d.setCornerRadius(dp(c, radiusDp));
        d.setStroke(dp(c, widthDp), stroke);
        return d;
    }

    /** Fond qui change quand l'élément a le focus de la télécommande. */
    static Drawable focusable(Context c, int normal, int focused, float radiusDp) {
        StateListDrawable s = new StateListDrawable();
        s.addState(new int[]{android.R.attr.state_focused}, rounded(c, focused, radiusDp));
        s.addState(new int[]{android.R.attr.state_pressed}, rounded(c, focused, radiusDp));
        s.addState(new int[]{}, rounded(c, normal, radiusDp));
        return s;
    }

    /** Cadre orange affiché par-dessus un élément sélectionné. */
    static Drawable focusRing(Context c, float radiusDp) {
        StateListDrawable s = new StateListDrawable();
        s.addState(new int[]{android.R.attr.state_focused}, outline(c, ACCENT, radiusDp, 3));
        s.addState(new int[]{android.R.attr.state_selected}, outline(c, ACCENT, radiusDp, 3));
        s.addState(new int[]{}, new ColorDrawable(Color.TRANSPARENT));
        return s;
    }

    /** Bouton principal (orange) ou secondaire (gris), lisible avec la télécommande. */
    static Button button(Context c, String label, boolean primary) {
        Button b = new Button(c);
        b.setText(label);
        b.setAllCaps(false);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
        b.setTypeface(Typeface.DEFAULT_BOLD);
        b.setMinWidth(0);
        b.setMinimumWidth(0);
        b.setMinHeight(0);
        b.setMinimumHeight(0);
        b.setStateListAnimator(null);
        b.setPadding(dp(c, 22), dp(c, 11), dp(c, 22), dp(c, 11));
        if (primary) {
            b.setTextColor(Color.BLACK);
            b.setBackground(focusable(c, ACCENT, Color.rgb(255, 196, 120), 10));
        } else {
            b.setTextColor(TEXT);
            b.setBackground(focusable(c, SURFACE_2, Color.rgb(70, 70, 82), 10));
        }
        b.setFocusable(true);
        b.setOnFocusChangeListener((v, has) -> {
            if (!primary) ((Button) v).setTextColor(has ? Color.BLACK : TEXT);
            if (!primary) v.setBackground(has ? rounded(c, Color.WHITE, 10) : rounded(c, SURFACE_2, 10));
        });
        return b;
    }

    static EditText input(Context c, String hint, boolean password) {
        EditText e = new EditText(c);
        e.setSingleLine(true);
        e.setHint(hint);
        e.setTextColor(TEXT);
        e.setHintTextColor(Color.rgb(120, 120, 130));
        e.setTextSize(TypedValue.COMPLEX_UNIT_SP, 19);
        e.setInputType(password ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
                : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        StateListDrawable bg = new StateListDrawable();
        GradientDrawable on = rounded(c, SURFACE_2, 10);
        on.setStroke(dp(c, 2), ACCENT);
        bg.addState(new int[]{android.R.attr.state_focused}, on);
        bg.addState(new int[]{}, rounded(c, SURFACE_2, 10));
        e.setBackground(bg);
        e.setPadding(dp(c, 16), dp(c, 12), dp(c, 16), dp(c, 12));
        return e;
    }

    static LinearLayout column(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.VERTICAL);
        return l;
    }

    static LinearLayout row(Context c) {
        LinearLayout l = new LinearLayout(c);
        l.setOrientation(LinearLayout.HORIZONTAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        return l;
    }

    static LinearLayout.LayoutParams wrap() {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    static LinearLayout.LayoutParams margins(Context c, int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = wrap();
        p.setMargins(dp(c, l), dp(c, t), dp(c, r), dp(c, b));
        return p;
    }

    static AlertDialog.Builder dialog(Context c) {
        return new AlertDialog.Builder(c, AlertDialog.THEME_DEVICE_DEFAULT_DARK);
    }

    static void toast(Context c, String msg) {
        Toast.makeText(c, msg, Toast.LENGTH_LONG).show();
    }

    /** 3725000 ms -> « 1:02:05 », 125000 -> « 2:05 ». */
    static String clock(long ms) {
        long s = Math.max(0, ms / 1000);
        long h = s / 3600, m = (s % 3600) / 60, sec = s % 60;
        return h > 0 ? String.format(java.util.Locale.ROOT, "%d:%02d:%02d", h, m, sec)
                : String.format(java.util.Locale.ROOT, "%d:%02d", m, sec);
    }

    /** 5400000 ms -> « 1 h 30 », 2700000 -> « 45 min ». */
    static String duration(long ms) {
        long min = Math.round(ms / 60000.0);
        if (min <= 0) return "";
        if (min < 60) return min + " min";
        long m = min % 60;
        return (min / 60) + " h" + (m > 0 ? String.format(java.util.Locale.ROOT, " %02d", m) : "");
    }

    static void hide(View v) {
        v.setVisibility(View.GONE);
    }
}
