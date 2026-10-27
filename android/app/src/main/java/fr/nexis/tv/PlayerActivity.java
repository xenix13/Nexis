package fr.nexis.tv;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.OptIn;
import androidx.media3.common.C;
import androidx.media3.common.Format;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MimeTypes;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.TrackSelectionOverride;
import androidx.media3.common.TrackSelectionParameters;
import androidx.media3.common.Tracks;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.common.util.Util;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.AspectRatioFrameLayout;
import androidx.media3.ui.PlayerView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Lecteur natif (ExoPlayer). Le serveur décide pour chaque fichier : lecture directe (MKV, HEVC, 10 bits, son
 * multicanal transmis à l'ampli), remux HLS, ou conversion si l'appareil ne sait pas décoder.
 * Télécommande : OK lecture/pause, gauche/droite pour avancer ou reculer, haut/bas pour les commandes.
 */
@OptIn(markerClass = UnstableApi.class)
public class PlayerActivity extends Activity {
    private static final String SIDECAR = "nexis-subtitles";
    private static final long REPORT_EVERY = 10000;
    // lecture directe qui ne démarre pas : on passe par un flux préparé par le serveur
    private static final long DIRECT_PATIENCE = 20000;
    // plus rien à l'écran qu'une roue : on dit ce qui se passe et on propose de réessayer
    private static final long STALL_AFTER = 60000;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private ExoPlayer player;
    private PlayerView video;
    private FrameLayout root;
    private LinearLayout panel, floating, errorBox;
    private TextView title, subtitle, timeCur, timeEnd, errorText, status;
    private TimeBar bar;
    private ImageView playButton;
    private Icons playIcon;
    private Button tracksButton, nextButton, skipButton, upNextButton, retryButton;
    private ProgressBar loader;

    private String itemId;
    private JSONObject item, session;
    private List<JSONObject> audios = new ArrayList<>(), subs = new ArrayList<>();
    private JSONObject audioSel, subSel;
    private boolean burning, forceHls, tracksApplied, introSkipped, upNextDismissed, ended, leaving;
    private boolean everReady, stallShown;
    private int startSeq, retries, countdown;
    private long lastReport, lastPosition;
    private long waitSince = SystemClock.elapsedRealtime(), preparedAt;

    private final Runnable hidePanel = this::hidePanel;
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            update();
            watch();
            handler.postDelayed(this, 500);
        }
    };
    private final Runnable countdownTick = new Runnable() {
        @Override
        public void run() {
            countdown--;
            if (countdown <= 0) {
                playNext();
                return;
            }
            upNextButton.setText("Épisode suivant dans " + countdown + " s");
            handler.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Api.init(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        buildUi();
        immersive();
        player = Players.create(this);
        video.setPlayer(player);
        player.addListener(new androidx.media3.common.Player.Listener() {
            @Override
            public void onPlaybackStateChanged(int s) {
                loader.setVisibility(s == androidx.media3.common.Player.STATE_BUFFERING ? View.VISIBLE : View.GONE);
                if (s == androidx.media3.common.Player.STATE_READY) {
                    retries = 0;
                    everReady = true;
                    // le message « ne démarre pas » ne doit pas rester si la lecture a fini par démarrer
                    if (stallShown && errorBox.getVisibility() == View.VISIBLE) {
                        errorBox.setVisibility(View.GONE);
                        root.requestFocus();
                    }
                }
                if (s == androidx.media3.common.Player.STATE_ENDED) onEnded();
            }

            @Override
            public void onIsPlayingChanged(boolean playing) {
                paintPlay();
                if (!ended) report(playing ? "playing" : "paused", true, false);
                if (!playing && panel.getVisibility() == View.VISIBLE) handler.removeCallbacks(hidePanel);
                else if (playing && panel.getVisibility() == View.VISIBLE) scheduleHide();
            }

            @Override
            public void onTracksChanged(@NonNull Tracks tracks) {
                if (!tracksApplied && !tracks.isEmpty()) {
                    tracksApplied = true;
                    applyTracks(tracks);
                }
            }

            @Override
            public void onPlayerError(@NonNull PlaybackException e) {
                playbackError(e);
            }
        });
        itemId = getIntent().getStringExtra("id");
        loadItem(getIntent().getLongExtra("offset", 0));
        handler.post(tick);
    }

    // ------------------------------------------------------------------ interface
    private void buildUi() {
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        root.setFocusable(true);
        setContentView(root);

        video = new PlayerView(this);
        video.setUseController(false);
        video.setResizeMode(AspectRatioFrameLayout.RESIZE_MODE_FIT);
        video.setShutterBackgroundColor(Color.BLACK);
        video.setKeepContentOnPlayerReset(true);
        video.setFocusable(false);
        Players.styleSubtitles(video.getSubtitleView());
        root.addView(video, new FrameLayout.LayoutParams(-1, -1));

        loader = new ProgressBar(this);
        root.addView(loader, new FrameLayout.LayoutParams(Ui.dp(this, 64), Ui.dp(this, 64), Gravity.CENTER));

        // ce que fait le serveur pendant l'attente (lecture directe, remux, conversion) et depuis combien de temps
        status = Ui.text(this, "", 18, Ui.MUTED);
        status.setGravity(Gravity.CENTER);
        status.setVisibility(View.GONE);
        FrameLayout.LayoutParams stp = new FrameLayout.LayoutParams(Ui.dp(this, 640),
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER);
        stp.topMargin = Ui.dp(this, 96);
        root.addView(status, stp);

        // commandes (bas de l'écran)
        panel = Ui.column(this);
        panel.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{Color.TRANSPARENT, Color.argb(200, 0, 0, 0), Color.argb(235, 0, 0, 0)}));
        panel.setPadding(dp(56), dp(70), dp(56), dp(30));
        title = Ui.oneLine(Ui.title(this, "", 26));
        panel.addView(title);
        subtitle = Ui.oneLine(Ui.text(this, "", 17, Ui.MUTED));
        panel.addView(subtitle);
        bar = new TimeBar(this);
        bar.listener = new TimeBar.Listener() {
            @Override
            public void preview(long positionMs) {
                timeCur.setText(Ui.clock(positionMs));
                scheduleHide();
            }

            @Override
            public void seek(long positionMs) {
                player.seekTo(positionMs);
                upNextDismissed = false;
                scheduleHide();
            }
        };
        bar.setOnClickListener(v -> togglePlay());
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(-1, dp(30));
        bp.topMargin = dp(14);
        panel.addView(bar, bp);
        LinearLayout times = Ui.row(this);
        timeCur = Ui.text(this, "0:00", 15, Ui.TEXT);
        timeEnd = Ui.text(this, "", 15, Ui.MUTED);
        times.addView(timeCur, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        times.addView(timeEnd);
        panel.addView(times);
        LinearLayout buttons = Ui.row(this);
        buttons.setPadding(0, dp(14), 0, 0);
        playButton = new ImageView(this);
        playIcon = new Icons(Icons.PAUSE, Color.BLACK);
        playButton.setImageDrawable(playIcon);
        playButton.setFocusable(true);
        playButton.setPadding(dp(12), dp(12), dp(12), dp(12));
        playButton.setBackground(Ui.focusable(this, Ui.ACCENT, Color.WHITE, 26));
        playButton.setOnClickListener(v -> togglePlay());
        buttons.addView(playButton, new LinearLayout.LayoutParams(dp(52), dp(52)));
        tracksButton = Ui.button(this, "Audio et sous-titres", false);
        tracksButton.setOnClickListener(v -> tracksMenu());
        buttons.addView(tracksButton, Ui.margins(this, 14, 0, 0, 0));
        nextButton = Ui.button(this, "Épisode suivant", false);
        nextButton.setOnClickListener(v -> playNext());
        buttons.addView(nextButton, Ui.margins(this, 12, 0, 0, 0));
        panel.addView(buttons);
        panel.setVisibility(View.GONE);
        root.addView(panel, new FrameLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM));

        // « Passer le générique », « Épisode suivant » : en bas à droite, hors des commandes
        floating = Ui.row(this);
        skipButton = Ui.button(this, "Passer le générique", true);
        skipButton.setOnClickListener(v -> skipIntro());
        skipButton.setVisibility(View.GONE);
        floating.addView(skipButton);
        upNextButton = Ui.button(this, "Épisode suivant", true);
        upNextButton.setOnClickListener(v -> playNext());
        upNextButton.setVisibility(View.GONE);
        floating.addView(upNextButton, Ui.margins(this, 12, 0, 0, 0));
        FrameLayout.LayoutParams fp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM | Gravity.END);
        fp.setMargins(0, 0, dp(56), dp(56));
        root.addView(floating, fp);

        // erreur
        errorBox = Ui.column(this);
        errorBox.setGravity(Gravity.CENTER_HORIZONTAL);
        errorBox.setBackground(Ui.rounded(this, Color.argb(235, 20, 20, 24), 16));
        errorBox.setPadding(dp(36), dp(28), dp(36), dp(28));
        errorText = Ui.text(this, "", 18, Ui.TEXT);
        errorText.setGravity(Gravity.CENTER);
        errorBox.addView(errorText, new LinearLayout.LayoutParams(dp(560), ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout eb = Ui.row(this);
        eb.setPadding(0, dp(20), 0, 0);
        retryButton = Ui.button(this, "Réessayer", true);
        retryButton.setOnClickListener(v -> retry());
        eb.addView(retryButton, Ui.margins(this, 0, 0, 12, 0));
        Button back = Ui.button(this, "Retour", false);
        back.setOnClickListener(v -> leave());
        eb.addView(back);
        errorBox.addView(eb);
        errorBox.setVisibility(View.GONE);
        root.addView(errorBox, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER));
        root.requestFocus();
    }

    private int dp(float v) {
        return Ui.dp(this, v);
    }

    private void paintPlay() {
        boolean playing = player != null && player.getPlayWhenReady();
        playIcon = new Icons(playing ? Icons.PAUSE : Icons.PLAY, Color.BLACK);
        playButton.setImageDrawable(playIcon);
    }

    private void showPanel(View focus) {
        if (panel.getVisibility() != View.VISIBLE) {
            panel.setVisibility(View.VISIBLE);
            panel.setAlpha(0f);
            panel.animate().alpha(1f).setDuration(150).start();
            panel.post(() -> floating.setTranslationY(-panel.getHeight() + dp(40)));
        }
        if (focus != null) focus.requestFocus();
        scheduleHide();
    }

    private void hidePanel() {
        if (panel.getVisibility() != View.VISIBLE) return;
        panel.setVisibility(View.GONE);
        floating.setTranslationY(0);
        if (skipButton.getVisibility() == View.VISIBLE) skipButton.requestFocus();
        else if (upNextButton.getVisibility() == View.VISIBLE) upNextButton.requestFocus();
        else root.requestFocus();
    }

    private void scheduleHide() {
        handler.removeCallbacks(hidePanel);
        if (player != null && player.getPlayWhenReady()) handler.postDelayed(hidePanel, 5000);
    }

    @SuppressWarnings("deprecation")
    private void immersive() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }

    // ------------------------------------------------------------------ chargement et flux
    private void loadItem(long offset) {
        loader.setVisibility(View.VISIBLE);
        errorBox.setVisibility(View.GONE);
        everReady = false;
        stallShown = false;
        preparedAt = 0;
        waitSince = SystemClock.elapsedRealtime();
        Api.obj("/api/items/" + itemId, d -> {
            if (leaving) return;
            item = d;
            audios = Player.list(d.optJSONArray("audio"));
            subs = Player.list(d.optJSONArray("subtitles"));
            JSONObject tp = d.optJSONObject("track_prefs");
            audioSel = Player.pickAudio(audios, tp);
            subSel = Player.pickSubtitle(subs, audioSel, tp);
            if ("episode".equals(d.optString("type"))) {
                title.setText(d.optString("show"));
                subtitle.setText("Saison " + d.optInt("season") + " · Épisode " + d.optInt("episode") + " — " + d.optString("title"));
            } else {
                title.setText(d.optString("title"));
                int y = d.optInt("year", 0);
                subtitle.setText(y > 0 ? String.valueOf(y) : "");
            }
            tracksButton.setVisibility(audios.size() > 1 || !subs.isEmpty() ? View.VISIBLE : View.GONE);
            startStream(offset, false);
        }, e -> showError(e.getMessage()));
    }

    /** Demande un flux au serveur (et remplace le précédent) à la position donnée. */
    private void startStream(long offset, boolean burn) {
        int seq = ++startSeq;
        loader.setVisibility(View.VISIBLE);
        errorBox.setVisibility(View.GONE);
        everReady = false;
        stallShown = false;
        preparedAt = 0;
        waitSince = SystemClock.elapsedRealtime();
        lastPosition = offset;
        JSONObject body = Api.json("id", itemId, "offset", offset, "caps", Players.caps(this),
                "audio", audioSel == null ? null : audioSel.optInt("id"),
                "subtitle", burn && subSel != null ? subSel.optInt("id") : 0,
                "replace", session == null ? null : session.optString("stream"),
                "no_direct", forceHls);
        Api.send("POST", "/api/play", body, s -> {
            if (seq != startSeq || leaving) {
                Api.fire("POST", "/api/stop", Api.json("stream", s.optString("stream")));
                return;
            }
            session = s;
            burning = burn;
            ended = false;
            boolean imageSub = subSel != null && !subSel.optBoolean("overlay");
            if (imageSub && !burn && !"direct".equals(s.optString("mode"))) {
                // sous-titres en image (PGS…) hors lecture directe : le serveur les incruste dans l'image
                startStream(s.optLong("offset", offset), true);
                return;
            }
            nextButton.setVisibility(s.optJSONObject("next") != null ? View.VISIBLE : View.GONE);
            prepare(s.optLong("offset", offset));
        }, e -> {
            if (seq == startSeq) showError(e.getMessage());
        });
    }

    private boolean direct() {
        return session != null && "direct".equals(session.optString("mode"));
    }

    /** Charge le flux dans ExoPlayer, avec les sous-titres texte du serveur (WebVTT) si besoin. */
    private void prepare(long startMs) {
        tracksApplied = false;
        preparedAt = SystemClock.elapsedRealtime();
        MediaItem.Builder mb = new MediaItem.Builder().setUri(Api.url(session.optString("url")));
        if (!direct()) mb.setMimeType(MimeTypes.APPLICATION_M3U8);
        if (subSel != null && subSel.optBoolean("overlay")) {
            Uri uri = Uri.parse(Api.url("/api/items/" + itemId + "/subtitles/" + subSel.optInt("id") + ".vtt"));
            mb.setSubtitleConfigurations(Collections.singletonList(new MediaItem.SubtitleConfiguration.Builder(uri)
                    .setMimeType(MimeTypes.TEXT_VTT)
                    .setLanguage(subSel.optString("lang"))
                    .setLabel(SIDECAR)
                    .setId(SIDECAR)
                    .setSelectionFlags(C.SELECTION_FLAG_DEFAULT)
                    .build()));
        }
        player.setMediaItem(mb.build(), startMs);
        player.prepare();
        player.setPlayWhenReady(true);
    }

    private static List<Tracks.Group> groups(Tracks tracks, int type) {
        List<Tracks.Group> out = new ArrayList<>();
        for (Tracks.Group g : tracks.getGroups()) if (g.getType() == type && g.length > 0) out.add(g);
        return out;
    }

    /** Pistes choisies : audio (lecture directe : le fichier les contient toutes) et sous-titres. */
    private void applyTracks(Tracks tracks) {
        TrackSelectionParameters.Builder b = player.getTrackSelectionParameters().buildUpon();
        if (direct() && audioSel != null) {
            int ordinal = audios.indexOf(audioSel);
            List<Tracks.Group> a = groups(tracks, C.TRACK_TYPE_AUDIO);
            if (ordinal >= 0 && ordinal < a.size()) {
                b.setOverrideForType(new TrackSelectionOverride(a.get(ordinal).getMediaTrackGroup(), 0));
            }
        }
        Tracks.Group text = null;
        for (Tracks.Group g : groups(tracks, C.TRACK_TYPE_TEXT)) {
            Format f = g.getTrackFormat(0);
            if (SIDECAR.equals(f.label) || (f.id != null && f.id.contains(SIDECAR))) text = g;
        }
        if (text == null && subSel != null && !subSel.optBoolean("overlay") && direct() && !burning) {
            text = embeddedImageSub(tracks);
            if (text == null) {
                // piste introuvable dans le fichier : on repasse par le serveur, qui l'incruste
                handler.post(() -> startStream(player.getCurrentPosition(), true));
                return;
            }
        }
        if (text != null) {
            b.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false);
            b.setOverrideForType(new TrackSelectionOverride(text.getMediaTrackGroup(), 0));
        } else {
            b.clearOverridesOfType(C.TRACK_TYPE_TEXT);
            b.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true);
        }
        player.setTrackSelectionParameters(b.build());
    }

    private static boolean isImageSub(Format f) {
        String[] mimes = {MimeTypes.APPLICATION_PGS, MimeTypes.APPLICATION_VOBSUB, MimeTypes.APPLICATION_DVBSUBS};
        for (String m : mimes) {
            if (m.equals(f.sampleMimeType) || (f.codecs != null && f.codecs.contains(m))) return true;
        }
        return false;
    }

    /** Sous-titres en image du fichier (lecture directe) : même langue, même rang que dans la liste du serveur. */
    private Tracks.Group embeddedImageSub(Tracks tracks) {
        String lang = subSel.optString("lang");
        String norm = lang.isEmpty() ? null : Util.normalizeLanguageCode(lang);
        int rank = 0;
        for (JSONObject s : subs) {
            if (s == subSel) break;
            if (!s.optBoolean("overlay") && !s.optBoolean("external") && lang.equals(s.optString("lang"))) rank++;
        }
        List<Tracks.Group> found = new ArrayList<>();
        for (Tracks.Group g : groups(tracks, C.TRACK_TYPE_TEXT)) {
            Format f = g.getTrackFormat(0);
            if (!isImageSub(f)) continue;
            if (norm == null || norm.equals(f.language == null ? null : Util.normalizeLanguageCode(f.language))) found.add(g);
        }
        if (found.isEmpty()) return null;
        return found.get(Math.min(rank, found.size() - 1));
    }

    private long duration() {
        long d = session == null ? 0 : session.optLong("duration", 0);
        if (d <= 0 && player != null && player.getDuration() != C.TIME_UNSET) d = player.getDuration();
        return Math.max(0, d);
    }

    // ------------------------------------------------------------------ suivi de lecture
    private void update() {
        if (player == null || session == null) return;
        long pos = player.getCurrentPosition(), dur = duration();
        if (player.getPlaybackState() == androidx.media3.common.Player.STATE_READY) lastPosition = pos;
        if (!bar.seeking()) {
            bar.update(pos, player.getBufferedPosition(), dur);
            timeCur.setText(Ui.clock(pos));
        }
        timeEnd.setText(dur > 0 ? Ui.clock(dur) : "");
        if (player.isPlaying()) report("playing", false, false);
        JSONObject markers = session.optJSONObject("markers");
        JSONObject intro = markers == null ? null : markers.optJSONObject("intro");
        boolean inIntro = intro != null && pos >= intro.optLong("start") && pos < intro.optLong("end") - 1500;
        if (inIntro && Session.flag("auto_skip_intro", false) && !introSkipped) {
            skipIntro();
        } else if (inIntro && skipButton.getVisibility() != View.VISIBLE) {
            skipButton.setVisibility(View.VISIBLE);
            if (panel.getVisibility() != View.VISIBLE) skipButton.requestFocus();
        } else if (!inIntro && skipButton.getVisibility() == View.VISIBLE) {
            boolean had = skipButton.isFocused();
            skipButton.setVisibility(View.GONE);
            if (had) root.requestFocus();
        }
        JSONObject next = session.optJSONObject("next");
        if (next != null && !ended && countdown <= 0) {
            JSONObject credits = markers == null ? null : markers.optJSONObject("credits");
            long creditsAt = credits != null ? credits.optLong("start") : dur > 300000 ? dur - 30000 : Long.MAX_VALUE;
            boolean show = pos >= creditsAt && !upNextDismissed;
            if (show && upNextButton.getVisibility() != View.VISIBLE) {
                upNextButton.setText("Épisode suivant");
                upNextButton.setVisibility(View.VISIBLE);
                if (panel.getVisibility() != View.VISIBLE && skipButton.getVisibility() != View.VISIBLE) upNextButton.requestFocus();
            } else if (!show && upNextButton.getVisibility() == View.VISIBLE) {
                boolean had = upNextButton.isFocused();
                upNextButton.setVisibility(View.GONE);
                if (had) root.requestFocus();
            }
        }
    }

    // ------------------------------------------------------------------ attente du premier affichage
    /** Pourquoi le serveur ne lit pas le fichier tel quel (« vidéo HEVC illisible par cet appareil »…). */
    private String reasons() {
        JSONArray r = session == null ? null : session.optJSONArray("reasons");
        if (r == null || r.length() == 0) return "";
        List<String> out = new ArrayList<>();
        for (int i = 0; i < r.length(); i++) out.add(r.optString(i));
        return android.text.TextUtils.join(", ", out);
    }

    private String modeLabel() {
        if (session == null) return "connexion au serveur";
        if (direct()) return "lecture directe du fichier";
        if ("copy".equals(session.optString("mode"))) return "flux préparé par le serveur (remux)";
        return "conversion de la vidéo par le serveur";
    }

    /**
     * Tant que rien ne s'affiche : indique ce qui est en cours, bascule une lecture directe qui n'aboutit pas sur
     * un flux préparé par le serveur, et finit par afficher un message avec « Réessayer » au lieu d'une roue sans fin.
     */
    private void watch() {
        boolean waiting = !everReady && !leaving && !ended && errorBox.getVisibility() != View.VISIBLE;
        if (!waiting) {
            status.setVisibility(View.GONE);
            return;
        }
        long now = SystemClock.elapsedRealtime();
        long seconds = (now - waitSince) / 1000;
        String why = reasons();
        String label = modeLabel();
        StringBuilder text = new StringBuilder(label.substring(0, 1).toUpperCase(java.util.Locale.FRANCE))
                .append(label.substring(1)).append("…");
        if (!why.isEmpty()) text.append('\n').append(why);
        if (seconds >= 5) text.append('\n').append(seconds).append(" s");
        status.setText(text);
        status.setVisibility(View.VISIBLE);
        if (session != null && direct() && !forceHls && preparedAt > 0 && now - preparedAt > DIRECT_PATIENCE) {
            forceHls = true;
            Ui.toast(this, "Lecture directe trop lente : passage par le serveur");
            startStream(lastPosition, burning);
            return;
        }
        if (!stallShown && now - waitSince > STALL_AFTER) {
            stallShown = true;
            String detail = why.isEmpty() ? "" : " : " + why;
            showError("La lecture ne démarre pas (" + modeLabel() + detail + ").\n"
                    + "Le serveur ou le partage réseau est peut-être lent. Réessayez, ou attendez encore un peu.");
        }
    }

    /** Cause technique d'une erreur ExoPlayer (ex. « Response code: 404 »), pour savoir quoi corriger. */
    private static String cause(Throwable e) {
        Throwable c = e == null ? null : e.getCause();
        if (c == null) return "";
        String m = c.getMessage();
        return "\n" + c.getClass().getSimpleName() + (m == null || m.isEmpty() ? "" : " : " + m);
    }

    private void report(String state, boolean force, boolean finished) {
        if (session == null || item == null || player == null) return;
        long now = SystemClock.elapsedRealtime();
        if (!force && now - lastReport < REPORT_EVERY) return;
        lastReport = now;
        long pos = player.getCurrentPosition();
        if (pos < 1000 && !"stopped".equals(state) && !finished) return;
        JSONObject b = Api.json("id", itemId, "position", pos, "duration", duration(),
                "stream", session.optString("stream"), "state", state);
        if (finished) {
            try {
                b.put("ended", true);
            } catch (JSONException ignored) {
                // clé littérale
            }
        }
        Api.fire("POST", "/api/progress", b);
    }

    private void togglePlay() {
        if (player == null) return;
        if (player.getPlaybackState() == androidx.media3.common.Player.STATE_ENDED) {
            player.seekTo(0);
            player.play();
        } else if (player.getPlayWhenReady()) {
            player.pause();
        } else {
            player.play();
        }
        paintPlay();
    }

    private void skipIntro() {
        JSONObject markers = session == null ? null : session.optJSONObject("markers");
        JSONObject intro = markers == null ? null : markers.optJSONObject("intro");
        if (intro == null) return;
        introSkipped = true;
        player.seekTo(intro.optLong("end"));
        boolean had = skipButton.isFocused();
        skipButton.setVisibility(View.GONE);
        if (had) root.requestFocus();
    }

    private void onEnded() {
        if (ended) return;
        ended = true;
        report("stopped", true, true);
        JSONObject next = session == null ? null : session.optJSONObject("next");
        if (next != null && Session.flag("autoplay_next", true)) {
            countdown = Math.max(1, Session.prefs.optInt("next_delay", 5));
            upNextButton.setText("Épisode suivant dans " + countdown + " s");
            upNextButton.setVisibility(View.VISIBLE);
            upNextButton.requestFocus();
            handler.postDelayed(countdownTick, 1000);
        } else {
            leave();
        }
    }

    private void playNext() {
        JSONObject next = session == null ? null : session.optJSONObject("next");
        if (next == null) return;
        handler.removeCallbacks(countdownTick);
        countdown = 0;
        if (!ended) report("playing", true, false);
        itemId = next.optString("id");
        introSkipped = false;
        upNextDismissed = false;
        tracksApplied = false;
        forceHls = false;
        retries = 0;
        upNextButton.setVisibility(View.GONE);
        skipButton.setVisibility(View.GONE);
        hidePanel();
        player.stop();
        loadItem(0);  // le flux en cours est remplacé (« replace ») par celui de l'épisode suivant
    }

    // ------------------------------------------------------------------ pistes audio et sous-titres
    private void tracksMenu() {
        handler.removeCallbacks(hidePanel);
        String audioLabel = audioSel == null ? "—" : audioSel.optString("title");
        String subLabel = subSel == null ? "Aucun" : subSel.optString("title");
        String[] rows = {"Audio : " + audioLabel, "Sous-titres : " + subLabel};
        Ui.dialog(this).setTitle("Audio et sous-titres").setItems(rows, (d, which) -> {
            if (which == 0) audioMenu();
            else subtitleMenu();
        }).setOnDismissListener(d -> scheduleHide()).show();
    }

    private void audioMenu() {
        if (audios.isEmpty()) return;
        String[] labels = new String[audios.size()];
        int sel = 0;
        for (int i = 0; i < audios.size(); i++) {
            labels[i] = audios.get(i).optString("title");
            if (audios.get(i) == audioSel) sel = i;
        }
        Ui.dialog(this).setTitle("Audio").setSingleChoiceItems(labels, sel, (d, which) -> {
            d.dismiss();
            JSONObject a = audios.get(which);
            if (a == audioSel) return;
            audioSel = a;
            saveTrackPrefs();
            if (direct()) applyTracks(player.getCurrentTracks());
            else startStream(player.getCurrentPosition(), burning);
        }).show();
    }

    private void subtitleMenu() {
        List<JSONObject> menu = Player.menuSubs(subs, audioSel);
        String[] labels = new String[menu.size() + 1];
        labels[0] = "Aucun";
        int sel = 0;
        for (int i = 0; i < menu.size(); i++) {
            labels[i + 1] = menu.get(i).optString("title");
            if (menu.get(i) == subSel) sel = i + 1;
        }
        Ui.dialog(this).setTitle("Sous-titres").setSingleChoiceItems(labels, sel, (d, which) -> {
            d.dismiss();
            JSONObject s = which == 0 ? null : menu.get(which - 1);
            if (s == subSel) return;
            subSel = s;
            saveTrackPrefs();
            long pos = player.getCurrentPosition();
            boolean image = s != null && !s.optBoolean("overlay");
            if (burning) startStream(pos, false);  // l'ancienne piste est incrustée : nouveau flux
            else if (image && !direct()) startStream(pos, true);
            else if (image) applyTracks(player.getCurrentTracks());
            else prepare(pos);  // sous-titres texte : même flux, nouveau fichier WebVTT
        }).show();
    }

    /** Choix retenu pour ce film ou cette série (comme dans l'interface web). */
    private void saveTrackPrefs() {
        JSONObject b = Api.json("audio_lang", audioSel == null ? null : audioSel.optString("lang"),
                "sub_lang", subSel == null ? "off" : subSel.optString("lang"),
                "sub_forced", subSel != null && subSel.optBoolean("forced"));
        Api.fire("PUT", "/api/items/" + itemId + "/tracks", b);
    }

    // ------------------------------------------------------------------ erreurs
    private void playbackError(PlaybackException e) {
        long pos = Math.max(lastPosition, player.getCurrentPosition());
        if (direct() && !forceHls) {
            // fichier refusé par les décodeurs de l'appareil : on passe par un flux préparé par le serveur
            forceHls = true;
            startStream(pos, burning);
            return;
        }
        if (retries < 2) {
            retries++;
            Ui.toast(this, "Flux interrompu, reconnexion…");
            startStream(pos, burning);
            return;
        }
        showError("La lecture s'est interrompue (" + e.getErrorCodeName() + ")." + cause(e));
    }

    private void showError(String message) {
        loader.setVisibility(View.GONE);
        hidePanel();
        errorText.setText(message);
        errorBox.setVisibility(View.VISIBLE);
        retryButton.requestFocus();
    }

    private void retry() {
        errorBox.setVisibility(View.GONE);
        retries = 0;
        if (item == null) loadItem(lastPosition);
        else startStream(lastPosition, burning);
    }

    /** Fin de la lecture : position enregistrée, flux arrêté sur le serveur. */
    private void leave() {
        if (leaving) return;
        leaving = true;
        if (!ended) report("stopped", true, false);
        if (session != null) Api.fire("POST", "/api/stop", Api.json("stream", session.optString("stream")));
        finish();
    }

    // ------------------------------------------------------------------ télécommande
    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int code = e.getKeyCode();
        boolean down = e.getAction() == KeyEvent.ACTION_DOWN;
        boolean first = down && e.getRepeatCount() == 0;
        if (code == KeyEvent.KEYCODE_BACK || code == KeyEvent.KEYCODE_ESCAPE) {
            if (e.getAction() != KeyEvent.ACTION_UP) return true;
            if (errorBox.getVisibility() == View.VISIBLE) leave();
            else if (countdown > 0) {
                handler.removeCallbacks(countdownTick);
                countdown = 0;
                leave();
            } else if (panel.getVisibility() == View.VISIBLE) hidePanel();
            else if (upNextButton.getVisibility() == View.VISIBLE && upNextButton.isFocused()) {
                upNextDismissed = true;
                upNextButton.setVisibility(View.GONE);
                root.requestFocus();
            } else leave();
            return true;
        }
        if (errorBox.getVisibility() == View.VISIBLE) return super.dispatchKeyEvent(e);
        switch (code) {
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
            case KeyEvent.KEYCODE_HEADSETHOOK:
                if (first) togglePlay();
                return true;
            case KeyEvent.KEYCODE_MEDIA_PLAY:
                if (first) player.play();
                return true;
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                if (first) player.pause();
                return true;
            case KeyEvent.KEYCODE_MEDIA_STOP:
                if (first) leave();
                return true;
            case KeyEvent.KEYCODE_MEDIA_NEXT:
                if (first) playNext();
                return true;
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD:
            case KeyEvent.KEYCODE_MEDIA_REWIND:
                if (down) {
                    long step = TimeBar.step(e.getRepeatCount()) * (code == KeyEvent.KEYCODE_MEDIA_REWIND ? -1 : 1);
                    player.seekTo(Math.max(0, Math.min(Math.max(0, duration() - 2000), player.getCurrentPosition() + step)));
                    showPanel(null);
                }
                return true;
            default:
                break;
        }
        if (panel.getVisibility() != View.VISIBLE) {
            View f = getCurrentFocus();
            boolean onFloating = f == skipButton || f == upNextButton;
            if (code == KeyEvent.KEYCODE_DPAD_CENTER || code == KeyEvent.KEYCODE_ENTER || code == KeyEvent.KEYCODE_NUMPAD_ENTER) {
                if (onFloating) return super.dispatchKeyEvent(e);
                if (first) {
                    togglePlay();
                    showPanel(bar);
                }
                return true;
            }
            if (code == KeyEvent.KEYCODE_DPAD_LEFT || code == KeyEvent.KEYCODE_DPAD_RIGHT) {
                if (onFloating && first && code == KeyEvent.KEYCODE_DPAD_LEFT && f == upNextButton
                        && skipButton.getVisibility() == View.VISIBLE) {
                    skipButton.requestFocus();
                    return true;
                }
                if (down) {
                    showPanel(bar);
                    return bar.dispatchKeyEvent(e);
                }
                return true;
            }
            if (code == KeyEvent.KEYCODE_DPAD_UP || code == KeyEvent.KEYCODE_DPAD_DOWN) {
                if (first) showPanel(playButton);
                return true;
            }
        } else {
            scheduleHide();
        }
        return super.dispatchKeyEvent(e);
    }

    @Override
    protected void onResume() {
        super.onResume();
        immersive();
    }

    @Override
    protected void onStop() {
        super.onStop();
        // bouton Accueil de la télécommande, mise en veille : la lecture s'arrête proprement
        if (!leaving) {
            if (player != null) player.pause();
            leave();
        }
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
