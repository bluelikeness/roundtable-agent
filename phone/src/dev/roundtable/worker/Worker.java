package dev.roundtable.worker;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;

import org.json.JSONObject;

import java.net.URI;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 게이트웨이 연결 관리(docs/proposals/phone-worker.md 4·5장). 접근성 서비스 안에서 돈다
 * (접근성 서비스는 켜져 있는 동안 시스템이 살려 두므로 별도 포그라운드 서비스가 필요 없다).
 *
 * 휴대폰이 바깥으로 연결하고 끊기면 지수 백오프(1→30초)로 다시 붙는다. 하트비트는 15초.
 */
final class Worker {
    private static final long PING_MS = 15_000;
    private static final long UPDATE_EVERY_MS = 6 * 60 * 60 * 1000L;
    /** 다른 휴대폰이 연결 중이라 거절(4423)됐을 때 다시 붙어 보기까지. 우선 폰으로 바뀌면 이만큼 안에 넘겨받는다 */
    private static final long REJECTED_WAIT_MS = 15_000;
    /** 연결할 때마다 하는 업데이트 확인의 최소 간격(거절이 반복돼도 latest.json 을 매번 묻지 않게) */
    private static final long CONNECT_UPDATE_MIN_MS = 10 * 60 * 1000L;
    private static long lastConnectUpdate = 0;

    interface StatusListener { void onStatus(String status); }

    private static volatile Worker instance;
    private static volatile StatusListener statusListener;
    private static volatile String status = "꺼짐(접근성 서비스가 꺼져 있음)";
    static final Set<String> watched = Collections.synchronizedSet(new HashSet<>());

    private final Commands commands;
    private final Context ctx;
    private final ExecutorService pool = Executors.newCachedThreadPool();
    private final Handler main = new Handler(Looper.getMainLooper());
    private volatile boolean running = true;
    private volatile WsClient ws;
    private Thread thread;

    private Worker(A11y svc) {
        this.ctx = svc.getApplicationContext();
        this.commands = new Commands(svc);
    }

    private static volatile String lastError = null;

    /** 앱이 죽으면 기록을 남긴다. 다음 연결의 hello 에 실어 서버 로그로 보낸다(화면 글자는 없다). */
    static void installCrashRecorder(Context c) {
        final Thread.UncaughtExceptionHandler prev = Thread.getDefaultUncaughtExceptionHandler();
        final java.io.File f = new java.io.File(c.getFilesDir(), "crash.txt");
        Thread.setDefaultUncaughtExceptionHandler((t, e) -> {
            try (java.io.PrintWriter w = new java.io.PrintWriter(new java.io.FileWriter(f))) {
                w.println(new java.util.Date() + " thread=" + t.getName());
                e.printStackTrace(w);
            } catch (Exception ignored) {}
            if (prev != null) prev.uncaughtException(t, e);
        });
    }

    private static String takeCrash(Context c) {
        java.io.File f = new java.io.File(c.getFilesDir(), "crash.txt");
        if (!f.isFile()) return null;
        try {
            byte[] b = java.nio.file.Files.readAllBytes(f.toPath());
            f.delete();
            String s = new String(b, java.nio.charset.StandardCharsets.UTF_8);
            return s.length() > 4000 ? s.substring(0, 4000) : s;
        } catch (Exception e) {
            return null;
        }
    }

    static synchronized void start(A11y svc) {
        installCrashRecorder(svc.getApplicationContext());
        stop();
        instance = new Worker(svc);
        instance.thread = new Thread(instance::loop, "worker-ws");
        instance.thread.start();
    }

    static synchronized void stop() {
        Worker w = instance;
        instance = null;
        if (w != null) {
            w.running = false;
            WsClient c = w.ws;
            if (c != null) c.close();
            w.pool.shutdownNow();
        }
        setStatus("꺼짐(접근성 서비스가 꺼져 있음)");
    }

    /** 설정을 바꾼 뒤: 지금 연결을 끊어 새 설정으로 다시 붙게 한다. */
    static void reconnect() {
        Worker w = instance;
        if (w != null && w.ws != null) w.ws.close();
    }

    static void setStatusListener(StatusListener l) {
        statusListener = l;
        if (l != null) l.onStatus(status);
    }

    static String status() { return status; }

    private static void setStatus(String s) {
        status = s;
        StatusListener l = statusListener;
        if (l != null) new Handler(Looper.getMainLooper()).post(() -> l.onStatus(s));
    }

    // --- 설정 -------------------------------------------------------------

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("worker", Context.MODE_PRIVATE);
    }

    /**
     * 사용자가 화면에서 바꾼 값이 있으면 그것, 없으면 APK 에 든 기본값. 새 APK(업데이트·재설치)의 기본값이
     * 이전과 다르면(서버 주소가 바뀐 배포) 화면에서 바꾼 값을 지우고 새 기본값을 따른다.
     */
    private static SharedPreferences settled(Context c) {
        SharedPreferences p = prefs(c);
        String built = Defaults.URL + "|" + Defaults.TOKEN.hashCode();
        if (!built.equals(p.getString("built", ""))) {
            p.edit().remove("url").remove("token").putString("built", built).apply();
        }
        return p;
    }

    static String url(Context c) { return settled(c).getString("url", Defaults.URL); }

    static String token(Context c) { return settled(c).getString("token", Defaults.TOKEN); }

    // --- 연결 -------------------------------------------------------------

    private void loop() {
        long backoff = 1000;
        while (running) {
            String url = url(ctx), token = token(ctx);
            if (url.isEmpty() || token.isEmpty()) {
                setStatus("설정 필요: 서버 주소와 기기 토큰");
                sleep(5000);
                continue;
            }
            WsClient c = new WsClient();
            ws = c;
            Thread pinger = null;
            long openedAt = 0;
            try {
                setStatus("연결 중… " + URI.create(url).getHost());
                c.connect(URI.create(url), token, 15_000);
                JSONObject hello = new JSONObject();
                hello.put("type", "hello");
                hello.put("device", Build.MANUFACTURER + " " + Build.MODEL);
                // 같은 모델 두 대를 가르는 기기 고유 값(앱 서명·기기·사용자마다 다르고, 재설치해도 같다)
                String devId = Settings.Secure.getString(ctx.getContentResolver(), Settings.Secure.ANDROID_ID);
                if (devId != null && !devId.isEmpty()) hello.put("device_id", devId);
                hello.put("version", Updater.currentName(ctx) + "(" + Updater.currentVersion(ctx) + ")");
                hello.put("sdk", Build.VERSION.SDK_INT);
                hello.put("android", Build.VERSION.RELEASE);
                if (lastError != null) hello.put("last_error", lastError);
                lastError = null;
                String crash = takeCrash(ctx);
                if (crash != null) hello.put("crash", crash);
                c.sendText(hello.toString());
                setStatus("연결됨 · " + URI.create(url).getHost() + " · v" + Updater.currentName(ctx));
                WLog.add("게이트웨이 연결됨");
                openedAt = System.currentTimeMillis();
                if (System.currentTimeMillis() - lastConnectUpdate > CONNECT_UPDATE_MIN_MS) {
                    lastConnectUpdate = System.currentTimeMillis();
                    Updater.checkAsync(ctx);
                }
                pinger = new Thread(() -> {
                    long lastUpdate = System.currentTimeMillis();
                    while (running && ws == c) {
                        sleep(PING_MS);
                        try { c.sendText("{\"type\":\"ping\"}"); } catch (Exception e) {
                            if (lastError == null) lastError = "하트비트 실패 " + e.getClass().getSimpleName() + ": " + e.getMessage();
                            c.close();
                            return;
                        }
                        if (System.currentTimeMillis() - lastUpdate > UPDATE_EVERY_MS) {
                            lastUpdate = System.currentTimeMillis();
                            Updater.checkAsync(ctx);
                        }
                    }
                }, "worker-ping");
                pinger.start();
                c.readLoop(this::onMessage);
            } catch (Exception e) {
                if (running && c.closeCode == 4423) {
                    // 다른 휴대폰이 쓰는 중. 끊김으로 남기지 않고 천천히 다시 물어본다
                    setStatus("대기 중: 다른 휴대폰이 연결돼 있음 · " + (REJECTED_WAIT_MS / 1000) + "초마다 확인");
                } else if (running) {
                    String why = e.getClass().getSimpleName() + ": " + e.getMessage();
                    lastError = (lastError != null && why.contains("Socket closed")) ? lastError + " → " + why : why;
                    setStatus("끊김: " + e.getMessage() + " · " + (backoff / 1000) + "초 뒤 재연결");
                    WLog.add("연결 끊김: " + e.getClass().getSimpleName() + " " + e.getMessage());
                }
            } finally {
                c.close();
                if (pinger != null) pinger.interrupt();
                ws = null;
            }
            if (!running) break;
            if (c.closeCode == 4423) {
                backoff = 1000;
                sleep(REJECTED_WAIT_MS);
                continue;
            }
            // 한동안 붙어 있다 끊긴 경우는 곧바로 다시(재연결이 반복되는 경우만 점점 늦춘다)
            if (openedAt > 0 && System.currentTimeMillis() - openedAt > 10_000) backoff = 1000;
            sleep(backoff);
            backoff = Math.min(backoff * 2, 30_000);
        }
    }

    private void onMessage(String text) {
        try {
            JSONObject msg = new JSONObject(text);
            if (!"req".equals(msg.optString("type"))) return;
            final int id = msg.getInt("id");
            final String cmd = msg.getString("cmd");
            final JSONObject args = msg.optJSONObject("args") != null ? msg.optJSONObject("args") : new JSONObject();
            pool.submit(() -> {
                JSONObject res = new JSONObject();
                try {
                    res.put("type", "res");
                    res.put("id", id);
                    try {
                        res.put("result", commands.run(cmd, args));
                        res.put("ok", true);
                    } catch (Exception e) {
                        res.put("ok", false);
                        res.put("error", e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
                        WLog.add("명령 실패 " + cmd + ": " + e.getMessage());
                    }
                    send(res.toString());
                } catch (Exception ignored) {}
            });
        } catch (Exception e) {
            WLog.add("잘못된 메시지: " + e.getMessage());
        }
    }

    private void send(String text) {
        WsClient c = ws;
        if (c == null) return;
        try {
            c.sendText(text);
        } catch (Exception e) {
            if (lastError == null) lastError = "보내기 실패 " + e.getClass().getSimpleName() + ": " + e.getMessage();
            WLog.add("보내기 실패: " + e.getClass().getSimpleName() + " " + e.getMessage());
            c.close();
        }
    }

    /** 알림 리스너가 부른다. 연결돼 있을 때만 보낸다(놓친 알림은 어댑터가 화면을 직접 읽어 메운다). */
    static void event(String name, JSONObject data) {
        Worker w = instance;
        if (w == null) return;
        try {
            JSONObject e = new JSONObject();
            e.put("type", "event");
            e.put("event", name);
            e.put("data", data);
            final String text = e.toString();
            // 알림 리스너는 메인 스레드에서 부른다. 메인 스레드에서 소켓에 쓰면 NetworkOnMainThreadException 으로
            // 연결이 끊긴다(2026-11-11 실기기: 앱을 열 때마다 알림이 바뀌며 끊김). 뒤 스레드로 넘긴다
            w.pool.submit(() -> w.send(text));
        } catch (Exception ignored) {}
    }

    private static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException ignored) {}
    }
}
