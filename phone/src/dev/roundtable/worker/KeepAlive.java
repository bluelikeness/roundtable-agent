package dev.roundtable.worker;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/**
 * 포그라운드 서비스("Roundtable-Agent 워커 연결 중" 알림). 연결 자체는 접근성 서비스(Worker)가 들고 있고, 이 서비스는
 * 프로세스를 포그라운드로 올려 두는 역할만 한다. 백그라운드 앱은 삼성 등에서 연결된 지 몇 초 만에 네트워크가
 * 끊긴다(2026-11-11 실기기: SocketException "Software caused connection abort").
 */
public class KeepAlive extends Service {
    private static final String CHANNEL = "worker";
    private static final int ID = 1;
    static volatile boolean running = false;

    static void start(Context c) {
        try {
            Intent i = new Intent(c, KeepAlive.class);
            if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
            else c.startService(i);
        } catch (Exception e) {
            // 백그라운드에서 포그라운드 서비스를 못 띄우는 경우(안드로이드 12+ 제한). 앱 화면을 한 번 열면 거기서 띄운다
            WLog.add("연결 유지 서비스 시작 실패: " + e.getClass().getSimpleName());
        }
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Roundtable-Agent 워커 연결", NotificationManager.IMPORTANCE_LOW);
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        Notification n = b.setSmallIcon(android.R.drawable.ic_menu_share)
                .setContentTitle("Roundtable-Agent 워커")
                .setContentText("허브와 연결을 유지하는 중")
                .setOngoing(true)
                .setContentIntent(open)
                .build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(ID, n);
        }
        running = true;
        WLog.add("연결 유지 서비스 켜짐");
        return START_STICKY;
    }

    @Override public void onDestroy() {
        running = false;
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
