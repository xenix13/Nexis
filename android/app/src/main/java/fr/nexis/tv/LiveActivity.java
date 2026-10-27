package fr.nexis.tv;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.OptIn;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MimeTypes;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.AspectRatioFrameLayout;
import androidx.media3.ui.PlayerView;

import org.json.JSONObject;

/** TV en direct en plein écran : haut / bas (ou CH+ / CH-) changent de chaîne, OK affiche le programme. */
@OptIn(markerClass = UnstableApi.class)
public class LiveActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private ExoPlayer player;
    private PlayerView video;
    private LinearLayout info;
    private ImageView logo;
    private TextView number, name, now, next, error;
    private ProgressBar loader;
    private int index, wanted;
    private String sessionId;
    private int seq, retries;
    private boolean leaving;

    private final Runnable hideInfo = () -> info.setVisibility(View.GONE);
    private final Runnable tune = this::tune;

    static void open(Activity act, int index) {
        Intent i = new Intent(act, LiveActivity.class);
        i.putExtra("index", index);
        act.startActivity(i);
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Api.init(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Session.channels.length() == 0) {
            finish();
            return;
        }
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        setContentView(root);
        video = new PlayerView(this);
        video.setUseController(false);
        video.setResizeMode(AspectRatioFrameLayout.RESIZE_MODE_FIT);
        video.setShutterBackgroundColor(Color.BLACK);
        root.addView(video, new FrameLayout.LayoutParams(-1, -1));
        loader = new ProgressBar(this);
        root.addView(loader, new FrameLayout.LayoutParams(Ui.dp(this, 64), Ui.dp(this, 64), Gravity.CENTER));
        error = Ui.text(this, "", 18, Ui.TEXT);
        error.setGravity(Gravity.CENTER);
        error.setBackground(Ui.rounded(this, Color.argb(220, 20, 20, 24), 14));
        error.setPadding(Ui.dp(this, 28), Ui.dp(this, 20), Ui.dp(this, 28), Ui.dp(this, 20));
        error.setVisibility(View.GONE);
        root.addView(error, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER));

        info = Ui.row(this);
        info.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{Color.TRANSPARENT, Color.argb(220, 0, 0, 0)}));
        int p = Ui.dp(this, 48);
        info.setPadding(p, Ui.dp(this, 60), p, Ui.dp(this, 36));
        number = Ui.title(this, "", 34);
        number.setTextColor(Ui.ACCENT);
        info.addView(number, Ui.margins(this, 0, 0, 24, 0));
        FrameLayout logoBox = new FrameLayout(this);
        logoBox.setBackground(Ui.rounded(this, Color.argb(120, 255, 255, 255), 8));
        logo = new ImageView(this);
        logo.setScaleType(ImageView.ScaleType.FIT_CENTER);
        logo.setPadding(Ui.dp(this, 6), Ui.dp(this, 6), Ui.dp(this, 6), Ui.dp(this, 6));
        logoBox.addView(logo, new FrameLayout.LayoutParams(-1, -1));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(Ui.dp(this, 120), Ui.dp(this, 70));
        lp.rightMargin = Ui.dp(this, 24);
        info.addView(logoBox, lp);
        LinearLayout text = Ui.column(this);
        name = Ui.oneLine(Ui.title(this, "", 26));
        text.addView(name);
        now = Ui.oneLine(Ui.text(this, "", 18, Ui.TEXT));
        text.addView(now);
        next = Ui.oneLine(Ui.text(this, "", 15, Ui.MUTED));
        text.addView(next);
        info.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        root.addView(info, new FrameLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM));

        player = Players.create(this);
        video.setPlayer(player);
        player.addListener(new androidx.media3.common.Player.Listener() {
            @Override
            public void onPlaybackStateChanged(int s) {
                loader.setVisibility(s == androidx.media3.common.Player.STATE_BUFFERING ? View.VISIBLE : View.GONE);
                if (s == androidx.media3.common.Player.STATE_READY) retries = 0;
            }

            @Override
            public void onPlayerError(@NonNull PlaybackException e) {
                if (e.errorCode == PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW) {
                    player.seekToDefaultPosition();
                    player.prepare();
                    return;
                }
                if (retries++ < 2) {
                    handler.postDelayed(tune, 1500);
                    return;
                }
                showError(Tr.t("La chaîne ne répond pas (") + e.getErrorCodeName() + ").");
            }
        });
        index = Math.max(0, Math.min(Session.channels.length() - 1, getIntent().getIntExtra("index", 0)));
        wanted = index;
        showInfo();
        tune();
    }

    private JSONObject channel(int i) {
        return Session.channels.optJSONObject(i);
    }

    private void showInfo() {
        JSONObject c = channel(wanted);
        if (c == null) return;
        number.setText(c.isNull("number") ? "" : c.optString("number"));
        name.setText(c.optString("name"));
        Images.load(logo, c.isNull("logo") ? null : c.optString("logo", null), Ui.dp(this, 160));
        JSONObject n = c.optJSONObject("now"), x = c.optJSONObject("next");
        now.setText(n == null ? "" : LiveTab.hour(n.optLong("start")) + " – " + LiveTab.hour(n.optLong("stop")) + "   " + n.optString("title"));
        next.setText(x == null ? "" : Tr.t("Ensuite : ") + LiveTab.hour(x.optLong("start")) + "   " + x.optString("title"));
        info.setVisibility(View.VISIBLE);
        handler.removeCallbacks(hideInfo);
        handler.postDelayed(hideInfo, 5000);
    }

    private void showError(String message) {
        loader.setVisibility(View.GONE);
        error.setText(message + Tr.t("\nHaut / bas pour changer de chaîne, Retour pour quitter."));
        error.setVisibility(View.VISIBLE);
    }

    /** Démarre la chaîne choisie ; l'ancienne session s'arrête sur le serveur. */
    private void tune() {
        index = wanted;
        int mine = ++seq;
        error.setVisibility(View.GONE);
        loader.setVisibility(View.VISIBLE);
        stopSession();
        player.stop();
        JSONObject c = channel(index);
        if (c == null) return;
        Api.send("POST", "/api/live/" + c.optString("id") + "/play", null, r -> {
            if (mine != seq || leaving) {
                Api.fire("POST", "/api/live/s/" + r.optString("session") + "/stop", null);
                return;
            }
            sessionId = r.optString("session");
            player.setMediaItem(new MediaItem.Builder().setUri(Api.url(r.optString("url")))
                    .setMimeType(MimeTypes.APPLICATION_M3U8).build());
            player.prepare();
            player.setPlayWhenReady(true);
        }, e -> {
            if (mine == seq) showError(e.getMessage());
        });
    }

    private void stopSession() {
        if (sessionId != null) Api.fire("POST", "/api/live/s/" + sessionId + "/stop", null);
        sessionId = null;
    }

    private void zap(int delta) {
        int n = Session.channels.length();
        wanted = ((wanted + delta) % n + n) % n;
        retries = 0;
        showInfo();
        // on attend que la sélection s'arrête avant de lancer la chaîne (zapping rapide)
        handler.removeCallbacks(tune);
        handler.postDelayed(tune, 700);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int code = e.getKeyCode();
        boolean down = e.getAction() == KeyEvent.ACTION_DOWN;
        switch (code) {
            case KeyEvent.KEYCODE_BACK:
            case KeyEvent.KEYCODE_ESCAPE:
                if (e.getAction() == KeyEvent.ACTION_UP) {
                    if (info.getVisibility() == View.VISIBLE && wanted == index) info.setVisibility(View.GONE);
                    else leave();
                }
                return true;
            case KeyEvent.KEYCODE_DPAD_UP:
            case KeyEvent.KEYCODE_CHANNEL_UP:
            case KeyEvent.KEYCODE_PAGE_UP:
                if (down) zap(-1);
                return true;
            case KeyEvent.KEYCODE_DPAD_DOWN:
            case KeyEvent.KEYCODE_CHANNEL_DOWN:
            case KeyEvent.KEYCODE_PAGE_DOWN:
                if (down) zap(1);
                return true;
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_DPAD_LEFT:
            case KeyEvent.KEYCODE_DPAD_RIGHT:
                if (down && e.getRepeatCount() == 0) {
                    if (error.getVisibility() == View.VISIBLE) tune();
                    showInfo();
                }
                return true;
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
                if (down && e.getRepeatCount() == 0) player.setPlayWhenReady(!player.getPlayWhenReady());
                return true;
            default:
                return super.dispatchKeyEvent(e);
        }
    }

    private void leave() {
        if (leaving) return;
        leaving = true;
        stopSession();
        finish();
    }

    @Override
    protected void onStop() {
        super.onStop();
        if (!leaving) leave();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (player != null) {
            player.release();
            player = null;
        }
        super.onDestroy();
    }
}
