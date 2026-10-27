package fr.nexis.tv;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;

/** Icônes dessinées (loupe, réglages, lecture…) : nettes à toutes les tailles, sans fichier d'image. */
final class Icons extends Drawable {
    static final int SEARCH = 0, SETTINGS = 1, PLAY = 2, PAUSE = 3, CHECK = 4;

    private final int kind;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

    Icons(int kind, int color) {
        this.kind = kind;
        paint.setColor(color);
    }

    void setColor(int color) {
        paint.setColor(color);
        invalidateSelf();
    }

    @Override
    public void draw(Canvas c) {
        float w = getBounds().width(), h = getBounds().height();
        float s = Math.min(w, h);
        c.save();
        c.translate(getBounds().left + (w - s) / 2f, getBounds().top + (h - s) / 2f);
        switch (kind) {
            case SEARCH: {
                paint.setStyle(Paint.Style.STROKE);
                paint.setStrokeWidth(s * 0.11f);
                paint.setStrokeCap(Paint.Cap.ROUND);
                c.drawCircle(s * 0.42f, s * 0.42f, s * 0.26f, paint);
                c.drawLine(s * 0.62f, s * 0.62f, s * 0.86f, s * 0.86f, paint);
                break;
            }
            case SETTINGS: {
                paint.setStyle(Paint.Style.FILL);
                float cx = s / 2f, cy = s / 2f;
                // roue dentée dessinée dans un calque pour pouvoir y percer le trou central
                Paint hole = new Paint(Paint.ANTI_ALIAS_FLAG);
                hole.setColor(0xFF000000);
                hole.setXfermode(new android.graphics.PorterDuffXfermode(android.graphics.PorterDuff.Mode.CLEAR));
                int layer = c.saveLayer(0, 0, s, s, null);
                c.drawCircle(cx, cy, s * 0.3f, paint);
                for (int i = 0; i < 8; i++) {
                    c.save();
                    c.rotate(i * 45f, cx, cy);
                    c.drawRoundRect(new RectF(cx - s * 0.075f, s * 0.08f, cx + s * 0.075f, s * 0.3f), s * 0.03f, s * 0.03f, paint);
                    c.restore();
                }
                c.drawCircle(cx, cy, s * 0.12f, hole);
                c.restoreToCount(layer);
                break;
            }
            case PLAY: {
                paint.setStyle(Paint.Style.FILL);
                Path p = new Path();
                p.moveTo(s * 0.28f, s * 0.18f);
                p.lineTo(s * 0.82f, s * 0.5f);
                p.lineTo(s * 0.28f, s * 0.82f);
                p.close();
                c.drawPath(p, paint);
                break;
            }
            case PAUSE: {
                paint.setStyle(Paint.Style.FILL);
                c.drawRoundRect(new RectF(s * 0.24f, s * 0.18f, s * 0.42f, s * 0.82f), s * 0.04f, s * 0.04f, paint);
                c.drawRoundRect(new RectF(s * 0.58f, s * 0.18f, s * 0.76f, s * 0.82f), s * 0.04f, s * 0.04f, paint);
                break;
            }
            default: {
                paint.setStyle(Paint.Style.STROKE);
                paint.setStrokeWidth(s * 0.12f);
                paint.setStrokeCap(Paint.Cap.ROUND);
                paint.setStrokeJoin(Paint.Join.ROUND);
                Path p = new Path();
                p.moveTo(s * 0.2f, s * 0.52f);
                p.lineTo(s * 0.42f, s * 0.74f);
                p.lineTo(s * 0.82f, s * 0.3f);
                c.drawPath(p, paint);
            }
        }
        c.restore();
    }

    @Override
    public void setAlpha(int alpha) {
        paint.setAlpha(alpha);
    }

    @Override
    public void setColorFilter(ColorFilter cf) {
        paint.setColorFilter(cf);
    }

    @Override
    public int getOpacity() {
        return PixelFormat.TRANSLUCENT;
    }
}
