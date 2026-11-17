package dev.roundtable.worker;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.os.Build;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 자동 업데이트. 게이트웨이의 /phone/apk/latest.json 을 기기 토큰으로 묻고, 새 버전이면 내려받아
 * PackageInstaller 로 설치한다. 처음 한 번은 안드로이드가 확인을 묻는다("이 출처 허용" → 설치).
 * 그 뒤로는 이 앱이 설치자가 되어 안드로이드 12+ 에서 확인 없이 설치된다(USER_ACTION_NOT_REQUIRED).
 */
final class Updater {
    private static final AtomicBoolean busy = new AtomicBoolean(false);

    private Updater() {}

    static long currentVersion(Context c) {
        try {
            PackageInfo pi = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= 28 ? pi.getLongVersionCode() : pi.versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    static String currentName(Context c) {
        try {
            return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

    /** wss://host/phone/ws → https://host/phone/apk/<path> */
    static URL apkUrl(Context c, String path) throws Exception {
        URI ws = URI.create(Worker.url(c));
        String scheme = "wss".equalsIgnoreCase(ws.getScheme()) ? "https" : "http";
        return new URI(scheme, null, ws.getHost(), ws.getPort(), "/phone/apk/" + path, null, null).toURL();
    }

    /** 뒤에서 확인한다. 새 버전이 있으면 내려받아 설치까지. */
    static void checkAsync(Context c) {
        Context ctx = c.getApplicationContext();
        if (!busy.compareAndSet(false, true)) return;
        new Thread(() -> {
            try {
                check(ctx);
            } catch (Exception e) {
                WLog.add("업데이트 확인 실패: " + e.getMessage());
            } finally {
                busy.set(false);
            }
        }, "worker-update").start();
    }

    private static void check(Context ctx) throws Exception {
        HttpURLConnection http = open(ctx, "latest.json");
        int code = http.getResponseCode();
        if (code == 404) return; // 배포본 없음
        if (code != 200) throw new IllegalStateException("latest.json HTTP " + code);
        JSONObject latest = new JSONObject(readAll(http.getInputStream()));
        long want = latest.getLong("versionCode");
        long have = currentVersion(ctx);
        if (want <= have) return;
        WLog.add("새 버전 " + latest.optString("versionName") + " (" + want + ") 내려받는 중");

        File apk = new File(ctx.getCacheDir(), "update.apk");
        http = open(ctx, "download");
        if (http.getResponseCode() != 200) throw new IllegalStateException("download HTTP " + http.getResponseCode());
        MessageDigest sha = MessageDigest.getInstance("SHA-256");
        try (InputStream in = http.getInputStream(); OutputStream out = new FileOutputStream(apk)) {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                sha.update(buf, 0, n);
            }
        }
        String got = hex(sha.digest());
        if (!got.equalsIgnoreCase(latest.optString("sha256"))) {
            apk.delete();
            throw new IllegalStateException("내려받은 APK 의 해시가 다름");
        }
        install(ctx, apk);
    }

    private static HttpURLConnection open(Context ctx, String path) throws Exception {
        HttpURLConnection http = (HttpURLConnection) apkUrl(ctx, path).openConnection();
        http.setRequestProperty("Authorization", "Bearer " + Worker.token(ctx));
        http.setConnectTimeout(15_000);
        http.setReadTimeout(60_000);
        return http;
    }

    private static void install(Context ctx, File apk) throws Exception {
        PackageInstaller pi = ctx.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        params.setAppPackageName(ctx.getPackageName());
        if (Build.VERSION.SDK_INT >= 31) {
            params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
        }
        int id = pi.createSession(params);
        try (PackageInstaller.Session s = pi.openSession(id)) {
            try (InputStream in = new FileInputStream(apk); OutputStream out = s.openWrite("worker.apk", 0, apk.length())) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                s.fsync(out);
            }
            Intent done = new Intent(ctx, InstallReceiver.class);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
            PendingIntent pending = PendingIntent.getBroadcast(ctx, id, done, flags);
            WLog.add("업데이트 설치 시작");
            s.commit(pending.getIntentSender());
        }
    }

    private static String readAll(InputStream in) throws Exception {
        try (InputStream i = in) {
            java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = i.read(buf)) > 0) b.write(buf, 0, n);
            return b.toString("UTF-8");
        }
    }

    private static String hex(byte[] d) {
        StringBuilder sb = new StringBuilder();
        for (byte x : d) sb.append(String.format(Locale.US, "%02x", x));
        return sb.toString();
    }
}
