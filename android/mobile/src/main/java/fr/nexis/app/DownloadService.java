package fr.nexis.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * Téléchargements en arrière-plan. Le transfert lui-même est mené par la page (comme dans le navigateur) ; sans ce
 * service, Android gèle l'application dès qu'on la quitte et le téléchargement s'arrête. Tant qu'un téléchargement est
 * en cours, l'application est donc déclarée « au travail » : notification « Noxa télécharge… » avec la progression,
 * processeur et Wi-Fi gardés éveillés, y compris écran éteint. Le service s'arrête dès que la file est vide.
 */
public class DownloadService extends Service {
    static final String CHANNEL = "downloads";
    static final int NOTIF = 4201;
    private static final long MAX_AWAKE = 6 * 3600 * 1000L; // garde-fou : jamais plus de 6 h d'affilée

    private PowerManager.WakeLock cpu;
    private WifiManager.WifiLock wifi;

    static void channel(Context ctx) {
        NotificationChannel c = new NotificationChannel(CHANNEL, ctx.getString(R.string.dl_channel), NotificationManager.IMPORTANCE_LOW);
        c.setShowBadge(false);
        ctx.getSystemService(NotificationManager.class).createNotificationChannel(c);
    }

    static Notification build(Context ctx, int count, String title, String text, int percent) {
        channel(ctx);
        Intent open = new Intent(ctx, MainActivity.class).putExtra("open", "#/downloads")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(ctx, 4201, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        String head = count > 1 ? ctx.getString(R.string.dl_many, count) : ctx.getString(R.string.dl_one);
        Notification.Builder b = new Notification.Builder(ctx, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setColor(0xFFFFA43A)
                .setContentTitle(head)
                .setContentText(title == null || title.isEmpty() ? text : title + " · " + text)
                .setContentIntent(pi)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setProgress(100, Math.max(0, Math.min(100, percent)), percent <= 0);
        if (Build.VERSION.SDK_INT >= 31) b.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        return b.build();
    }

    /** Mise à jour de la notification (le service tourne déjà : pas besoin de le relancer). */
    static void update(Context ctx, int count, String title, String text, int percent) {
        ctx.getSystemService(NotificationManager.class).notify(NOTIF, build(ctx, count, title, text, percent));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification n = build(this, intent == null ? 1 : intent.getIntExtra("count", 1),
                intent == null ? "" : intent.getStringExtra("title"), intent == null ? "" : intent.getStringExtra("text"),
                intent == null ? 0 : intent.getIntExtra("percent", 0));
        if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIF, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        else startForeground(NOTIF, n);
        if (cpu == null) {
            cpu = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "nexis:downloads");
            cpu.setReferenceCounted(false);
            cpu.acquire(MAX_AWAKE);
        }
        if (wifi == null) {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null) {
                @SuppressWarnings("deprecation")
                WifiManager.WifiLock w = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "nexis:downloads");
                wifi = w;
                wifi.setReferenceCounted(false);
                wifi.acquire();
            }
        }
        return START_NOT_STICKY; // relancé par la page, pas par Android (sans la page, rien à télécharger)
    }

    @Override
    public void onDestroy() {
        if (cpu != null && cpu.isHeld()) cpu.release();
        if (wifi != null && wifi.isHeld()) wifi.release();
        cpu = null;
        wifi = null;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}
