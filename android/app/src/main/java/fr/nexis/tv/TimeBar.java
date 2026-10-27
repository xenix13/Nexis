package fr.nexis.tv;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.KeyEvent;
import android.view.View;

/**
 * Barre de progression du lecteur, pilotée à la télécommande : gauche / droite reculent ou avancent
 * (de plus en plus vite si la touche reste enfoncée), le saut part quand on relâche.
 */
final class TimeBar extends View {
    interface Listener {
        void preview(long positionMs);

        void seek(long positionMs);
    }

    private final Paint track = new Paint(Paint.ANTI_ALIAS_FLAG), done = new Paint(Paint.ANTI_ALIAS_FLAG),
            buffer = new Paint(Paint.ANTI_ALIAS_FLAG), knob = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private long position, buffered, duration;
    private long target = -1;
    Listener listener;

    TimeBar(Context c) {
        super(c);
        setFocusable(true);
        track.setColor(Color.argb(90, 255, 255, 255));
        buffer.setColor(Color.argb(140, 255, 255, 255));
        done.setColor(Ui.ACCENT);
        knob.setColor(Color.WHITE);
    }

    void update(long pos, long buf, long dur) {
        position = pos;
        buffered = buf;
        duration = dur;
        invalidate();
    }

    boolean seeking() {
        return target >= 0;
    }

    /** Pas du saut selon le temps passé touche enfoncée. */
    static long step(int repeat) {
        return repeat > 24 ? 120000 : repeat > 10 ? 60000 : repeat > 3 ? 30000 : 10000;
    }

    @Override
    public boolean onKeyDown(int code, KeyEvent e) {
        if ((code == KeyEvent.KEYCODE_DPAD_LEFT || code == KeyEvent.KEYCODE_DPAD_RIGHT) && duration > 0) {
            long base = target >= 0 ? target : position;
            long delta = step(e.getRepeatCount()) * (code == KeyEvent.KEYCODE_DPAD_LEFT ? -1 : 1);
            target = Math.max(0, Math.min(duration - 2000, base + delta));
            if (listener != null) listener.preview(target);
            invalidate();
            return true;
        }
        return super.onKeyDown(code, e);
    }

    @Override
    public boolean onKeyUp(int code, KeyEvent e) {
        if ((code == KeyEvent.KEYCODE_DPAD_LEFT || code == KeyEvent.KEYCODE_DPAD_RIGHT) && target >= 0) {
            long t = target;
            target = -1;
            position = t;
            if (listener != null) listener.seek(t);
            invalidate();
            return true;
        }
        return super.onKeyUp(code, e);
    }

    @Override
    protected void onDraw(Canvas c) {
        float h = getHeight(), w = getWidth();
        float r = Ui.dp(getContext(), isFocused() ? 9 : 6);
        float th = Ui.dp(getContext(), isFocused() ? 6 : 4);
        float left = r, right = w - r, cy = h / 2f;
        rect.set(left, cy - th / 2, right, cy + th / 2);
        c.drawRoundRect(rect, th, th, track);
        if (duration > 0) {
            float bx = left + (right - left) * Math.min(1f, buffered / (float) duration);
            rect.set(left, cy - th / 2, bx, cy + th / 2);
            c.drawRoundRect(rect, th, th, buffer);
            long shown = target >= 0 ? target : position;
            float px = left + (right - left) * Math.min(1f, shown / (float) duration);
            rect.set(left, cy - th / 2, px, cy + th / 2);
            c.drawRoundRect(rect, th, th, done);
            if (isFocused()) c.drawCircle(px, cy, r, knob);
        }
    }

    @Override
    protected void onFocusChanged(boolean gain, int direction, android.graphics.Rect previous) {
        super.onFocusChanged(gain, direction, previous);
        invalidate();
    }
}
