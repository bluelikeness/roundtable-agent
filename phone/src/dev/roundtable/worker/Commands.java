package dev.roundtable.worker;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.app.KeyguardManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Path;
import android.graphics.Rect;
import android.hardware.HardwareBuffer;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.os.SystemClock;
import android.view.Display;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 게이트웨이 명령 수행(adapters/phone/protocol.py COMMANDS 표). 봇을 모르는 범용 명령만 있다.
 * 기록(WLog)에는 명령 이름·성공 여부만 남기고 화면 글자는 남기지 않는다.
 */
final class Commands {
    private static final int MAX_NODES = 6000;
    private final A11y svc;

    Commands(A11y svc) { this.svc = svc; }

    private static final long AWAKE_MS = 120_000;
    private android.os.PowerManager.WakeLock awake;

    /**
     * 화면을 쓰는 명령이 오면 2분 동안 화면이 꺼지지 않게 한다(질문 하나 = 보내기 → 답 대기 → 읽기 사이에
     * 화면이 꺼지고 잠기면 읽지 못한다). 접근성 탭은 사용자 조작으로 치지 않아 화면 꺼짐 타이머가 그대로 간다.
     * 이미 잠긴 화면을 풀지는 않는다.
     */
    @SuppressWarnings("deprecation")
    private synchronized void keepAwake() {
        PowerManager pm = (PowerManager) svc.getSystemService(Context.POWER_SERVICE);
        if (awake == null) {
            awake = pm.newWakeLock(PowerManager.SCREEN_DIM_WAKE_LOCK, "roundtable:busy");
            awake.setReferenceCounted(false);
        }
        awake.acquire(AWAKE_MS);
    }

    JSONObject run(String cmd, JSONObject a) throws Exception {
        if (!cmd.equals("state") && !cmd.equals("watch") && !cmd.equals("update_check") && !cmd.equals("logs") && !cmd.equals("app_info")
                && !cmd.equals("wake") && !cmd.equals("global")) keepAwake();
        switch (cmd) {
            case "state": return state();
            case "wake": return wake();
            case "watch": return watch(a);
            case "launch": return launch(a.getString("pkg"));
            case "tree": return tree();
            case "tap": return tap(a.getInt("x"), a.getInt("y"));
            case "click": return click(a.getJSONObject("selector"));
            case "click_at": return clickAt(a.getInt("x"), a.getInt("y"));
            case "set_text": return setText(a.getJSONObject("selector"), a.getString("text"));
            case "paste": return paste(a.getString("text"));
            case "global": return global(a.getString("action"));
            case "screenshot": return screenshot();
            case "update_check": return updateCheck();
            case "app_info": return appInfo(a.getString("pkg"));
            case "logs": { JSONObject o = new JSONObject(); o.put("text", WLog.text()); return o; }
            default: throw new IllegalArgumentException("모르는 명령 " + cmd);
        }
    }

    // --- 상태 -------------------------------------------------------------

    private JSONObject state() throws Exception {
        PowerManager pm = (PowerManager) svc.getSystemService(Context.POWER_SERVICE);
        KeyguardManager km = (KeyguardManager) svc.getSystemService(Context.KEYGUARD_SERVICE);
        BatteryManager bm = (BatteryManager) svc.getSystemService(Context.BATTERY_SERVICE);
        JSONObject o = new JSONObject();
        o.put("screen_on", pm.isInteractive());
        o.put("locked", km.isKeyguardLocked());
        // 보안 잠금(PIN·패턴·지문)이 있는지. 없으면 서버가 wake 로 화면을 켜고 잠금화면을 넘길 수 있다
        o.put("secure", km.isDeviceSecure());
        o.put("foreground", foreground());
        o.put("battery", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY));
        o.put("version", Updater.currentName(svc) + "(" + Updater.currentVersion(svc) + ")");
        // 화면 크기·밀도·키보드 위쪽 끝. 전송 버튼을 트리에 내놓지 않는 앱의 좌표 대비책에 쓴다
        o.put("width", svc.getResources().getDisplayMetrics().widthPixels);
        o.put("height", svc.getResources().getDisplayMetrics().heightPixels);
        o.put("density", svc.getResources().getDisplayMetrics().density);
        Integer ime = null;
        for (AccessibilityWindowInfo w : svc.getWindows()) {
            if (w.getType() == AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
                Rect r = new Rect();
                w.getBoundsInScreen(r);
                ime = r.top;
            }
        }
        o.put("ime_top", ime == null ? JSONObject.NULL : ime);
        // 알림 접근이 꺼져 있으면 답 알림이 오지 않는다(어댑터는 60초 뒤 화면을 직접 읽는다)
        android.app.NotificationManager nm = (android.app.NotificationManager) svc.getSystemService(Context.NOTIFICATION_SERVICE);
        o.put("notif_access", Build.VERSION.SDK_INT >= 27 && nm.isNotificationListenerAccessGranted(
                new android.content.ComponentName(svc, NotifListener.class)));
        o.put("battery_exempt", pm.isIgnoringBatteryOptimizations(svc.getPackageName()));
        o.put("keepalive", KeepAlive.running);
        // 서버가 기기 능력에 맞춰 동작을 고른다(예: 스크린샷은 30 이상)
        o.put("sdk", Build.VERSION.SDK_INT);
        o.put("android", Build.VERSION.RELEASE);
        return o;
    }

    /** 설치된 앱 버전. 서버가 셀렉터를 "패키지@버전" 단위로 기억하는 데 쓴다(앱이 업데이트되면 다시 찾는다). */
    private JSONObject appInfo(String pkg) throws Exception {
        android.content.pm.PackageInfo pi = svc.getPackageManager().getPackageInfo(pkg, 0);
        JSONObject o = new JSONObject();
        o.put("version_name", pi.versionName == null ? "" : pi.versionName);
        o.put("version_code", Build.VERSION.SDK_INT >= 28 ? pi.getLongVersionCode() : pi.versionCode);
        return o;
    }

    private String foreground() {
        AccessibilityNodeInfo r = svc.getRootInActiveWindow();
        if (r != null && r.getPackageName() != null) return r.getPackageName().toString();
        for (AccessibilityWindowInfo w : svc.getWindows()) {
            if (w.getType() == AccessibilityWindowInfo.TYPE_APPLICATION && w.isActive()) {
                AccessibilityNodeInfo root = w.getRoot();
                if (root != null && root.getPackageName() != null) return root.getPackageName().toString();
            }
        }
        return null;
    }

    private JSONObject watch(JSONObject a) throws Exception {
        JSONArray p = a.optJSONArray("pkgs");
        synchronized (Worker.watched) {
            Worker.watched.clear();
            if (p != null) for (int i = 0; i < p.length(); i++) Worker.watched.add(p.getString(i));
        }
        WLog.add("알림 감시: " + Worker.watched.size() + "개 앱");
        return new JSONObject();
    }

    private JSONObject updateCheck() throws Exception {
        Updater.checkAsync(svc);
        JSONObject o = new JSONObject();
        o.put("current", Updater.currentVersion(svc));
        return o;
    }

    // --- 화면 -------------------------------------------------------------

    private JSONObject launch(String pkg) throws Exception {
        Intent i = svc.getPackageManager().getLaunchIntentForPackage(pkg);
        if (i == null) throw new IllegalStateException("설치되지 않은 앱: " + pkg);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        svc.startActivity(i);
        long end = SystemClock.elapsedRealtime() + 10_000;
        String fg = null;
        while (SystemClock.elapsedRealtime() < end) {
            fg = foreground();
            if (pkg.equals(fg)) break;
            Thread.sleep(200);
        }
        JSONObject o = new JSONObject();
        o.put("foreground", fg);
        return o;
    }

    /** 앱 창들을 uiautomator dump 와 같은 XML 로. 시스템 UI(알림창)·키보드 창은 넣지 않는다. */
    private JSONObject tree() throws Exception {
        List<AccessibilityNodeInfo> roots = new ArrayList<>();
        for (AccessibilityWindowInfo w : svc.getWindows()) {
            if (w.getType() == AccessibilityWindowInfo.TYPE_APPLICATION) {
                AccessibilityNodeInfo r = w.getRoot();
                if (r != null) roots.add(r);
            }
        }
        if (roots.isEmpty()) {
            AccessibilityNodeInfo r = svc.getRootInActiveWindow();
            if (r != null) roots.add(r);
        }
        StringBuilder sb = new StringBuilder(64 * 1024);
        sb.append("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">");
        int[] count = {0};
        for (AccessibilityNodeInfo r : roots) appendNode(sb, r, count, 0);
        sb.append("</hierarchy>");
        JSONObject o = new JSONObject();
        o.put("xml", sb.toString());
        return o;
    }

    private void appendNode(StringBuilder sb, AccessibilityNodeInfo n, int[] count, int depth) {
        if (n == null || count[0] >= MAX_NODES || depth > 80) return;
        count[0]++;
        // 안드로이드 10 이하는 노드 캐시가 늦게 갱신된다(2026-11-12 노트9: set_text·붙여넣기·전송 뒤 몇 초 동안
        // 입력창 글자와 버튼이 옛 상태로 보임 → 서버가 실패로 오판). 읽을 때마다 새로 받는다.
        // 입력창은 어느 버전이든 새로 받는다(글자만 늦게 바뀌는 경우)
        if (Build.VERSION.SDK_INT < 30 || n.isEditable()) {
            try { n.refresh(); } catch (Exception ignored) { }
        }
        Rect b = new Rect();
        n.getBoundsInScreen(b);
        sb.append("<node");
        attr(sb, "class", n.getClassName());
        attr(sb, "package", n.getPackageName());
        attr(sb, "resource-id", n.getViewIdResourceName());
        attr(sb, "content-desc", n.getContentDescription());
        attr(sb, "text", n.getText());
        attr(sb, "clickable", String.valueOf(n.isClickable()));
        attr(sb, "editable", String.valueOf(n.isEditable()));
        attr(sb, "focused", String.valueOf(n.isFocused()));
        attr(sb, "bounds", "[" + b.left + "," + b.top + "][" + b.right + "," + b.bottom + "]");
        sb.append('>');
        for (int i = 0; i < n.getChildCount(); i++) appendNode(sb, n.getChild(i), count, depth + 1);
        sb.append("</node>");
    }

    private static void attr(StringBuilder sb, String k, CharSequence v) {
        sb.append(' ').append(k).append("=\"");
        if (v != null) {
            for (int i = 0; i < v.length(); i++) {
                char c = v.charAt(i);
                switch (c) {
                    case '&': sb.append("&amp;"); break;
                    case '<': sb.append("&lt;"); break;
                    case '>': sb.append("&gt;"); break;
                    case '"': sb.append("&quot;"); break;
                    case '\n': sb.append("&#10;"); break;
                    case '\r': sb.append("&#13;"); break;
                    case '\t': sb.append("&#9;"); break;
                    default:
                        // XML 1.0 에서 쓸 수 없는 제어 문자·짝 없는 서로게이트는 버린다
                        if (c < 0x20 || c == 0xFFFE || c == 0xFFFF) break;
                        if (Character.isHighSurrogate(c)) {
                            if (i + 1 < v.length() && Character.isLowSurrogate(v.charAt(i + 1))) {
                                sb.append(c).append(v.charAt(++i));
                            }
                            break;
                        }
                        if (Character.isLowSurrogate(c)) break;
                        sb.append(c);
                }
            }
        }
        sb.append('"');
    }

    private JSONObject tap(int x, int y) throws Exception {
        Path p = new Path();
        p.moveTo(x, y);
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(p, 0, 60)).build();
        CountDownLatch done = new CountDownLatch(1);
        AtomicBoolean ok = new AtomicBoolean(false);
        boolean sent = svc.dispatchGesture(g, new AccessibilityService.GestureResultCallback() {
            @Override public void onCompleted(GestureDescription d) { ok.set(true); done.countDown(); }
            @Override public void onCancelled(GestureDescription d) { done.countDown(); }
        }, null);
        if (!sent || !done.await(3, TimeUnit.SECONDS) || !ok.get()) throw new IllegalStateException("탭 실패");
        return new JSONObject();
    }

    /** uiautomator2 셀렉터 키(description·descriptionMatches·text·textMatches·resourceId·className). */
    private static boolean matches(AccessibilityNodeInfo n, JSONObject sel) {
        String desc = n.getContentDescription() == null ? "" : n.getContentDescription().toString();
        String text = n.getText() == null ? "" : n.getText().toString();
        String rid = n.getViewIdResourceName() == null ? "" : n.getViewIdResourceName();
        String cls = n.getClassName() == null ? "" : n.getClassName().toString();
        if (sel.has("description") && !desc.equals(sel.optString("description"))) return false;
        if (sel.has("descriptionMatches") && !desc.matches(sel.optString("descriptionMatches"))) return false;
        if (sel.has("text") && !text.equals(sel.optString("text"))) return false;
        if (sel.has("textMatches") && !text.matches(sel.optString("textMatches"))) return false;
        if (sel.has("resourceId") && !rid.equals(sel.optString("resourceId"))) return false;
        if (sel.has("className") && !cls.equals(sel.optString("className"))) return false;
        return true;
    }

    private List<AccessibilityNodeInfo> appNodes() {
        List<AccessibilityNodeInfo> out = new ArrayList<>();
        ArrayDeque<AccessibilityNodeInfo> q = new ArrayDeque<>();
        // 루트는 한 번만 받는다: 창이 바뀌는 중(깨운 직후 등)에는 두 번째 호출이 null 일 수 있다(ArrayDeque 는 null 을 못 받음)
        for (AccessibilityWindowInfo w : svc.getWindows()) {
            if (w.getType() != AccessibilityWindowInfo.TYPE_APPLICATION) continue;
            AccessibilityNodeInfo root = w.getRoot();
            if (root != null) q.add(root);
        }
        if (q.isEmpty()) {
            AccessibilityNodeInfo root = svc.getRootInActiveWindow();
            if (root != null) q.add(root);
        }
        while (!q.isEmpty() && out.size() < MAX_NODES) {
            AccessibilityNodeInfo n = q.poll();
            if (Build.VERSION.SDK_INT < 30) {
                try { n.refresh(); } catch (Exception ignored) { }  // tree() 와 같은 이유(노드 캐시가 늦게 갱신)
            }
            out.add(n);
            for (int i = 0; i < n.getChildCount(); i++) {
                AccessibilityNodeInfo c = n.getChild(i);
                if (c != null) q.add(c);
            }
        }
        return out;
    }

    /** 셀렉터로 찾은 노드에 접근성 클릭(ACTION_CLICK). React Native 버튼은 좌표 탭보다 확실하다. 안 되면 좌표 탭. */
    private JSONObject click(JSONObject sel) throws Exception {
        AccessibilityNodeInfo target = null;
        for (AccessibilityNodeInfo n : appNodes()) {
            if (matches(n, sel)) { target = n; break; }
        }
        if (target == null) throw new IllegalStateException("누를 노드를 찾지 못함");
        JSONObject o = new JSONObject();
        if (target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            o.put("method", "action");
            return o;
        }
        Rect r = new Rect();
        target.getBoundsInScreen(r);
        tap(r.centerX(), r.centerY());
        o.put("method", "gesture");
        return o;
    }

    /** 그 위치를 덮는 가장 작은 '누를 수 있는' 노드에 접근성 클릭. 이름 없는 버튼(Flutter 아이콘)용. 없으면 좌표 탭. */
    private JSONObject clickAt(int x, int y) throws Exception {
        AccessibilityNodeInfo best = null;
        long bestArea = Long.MAX_VALUE;
        Rect r = new Rect();
        for (AccessibilityNodeInfo n : appNodes()) {
            if (!n.isClickable()) continue;
            n.getBoundsInScreen(r);
            if (!r.contains(x, y)) continue;
            long area = (long) r.width() * r.height();
            if (area < bestArea) { best = n; bestArea = area; }
        }
        JSONObject o = new JSONObject();
        if (best != null && best.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            o.put("method", "action");
            return o;
        }
        tap(x, y);
        o.put("method", "gesture");
        return o;
    }

    private JSONObject setText(JSONObject sel, String text) throws Exception {
        AccessibilityNodeInfo target = null;
        for (AccessibilityNodeInfo n : appNodes()) {
            if (!matches(n, sel)) continue;
            if (n.isEditable()) { target = n; break; }
            if (target == null) target = n;
        }
        if (target == null) throw new IllegalStateException("입력창을 찾지 못함");
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        if (target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return new JSONObject();
        // 포커스를 받아야 글을 받는 입력창
        target.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        Thread.sleep(800);
        AccessibilityNodeInfo f = svc.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (f != null && f.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return new JSONObject();
        throw new IllegalStateException("입력창에 글을 넣지 못함");
    }

    private JSONObject paste(String text) throws Exception {
        ClipboardManager cm = (ClipboardManager) svc.getSystemService(Context.CLIPBOARD_SERVICE);
        AtomicReference<Exception> err = new AtomicReference<>();
        CountDownLatch set = new CountDownLatch(1);
        // 클립보드는 메인 스레드에서 다룬다
        svc.runOnMain(() -> {
            try { cm.setPrimaryClip(ClipData.newPlainText("roundtable", text)); } catch (Exception e) { err.set(e); }
            set.countDown();
        });
        set.await(2, TimeUnit.SECONDS);
        try {
            if (err.get() != null) throw err.get();
            AccessibilityNodeInfo f = svc.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (f == null || !f.performAction(AccessibilityNodeInfo.ACTION_PASTE)) {
                throw new IllegalStateException("붙여넣기 실패(포커스된 입력창 없음)");
            }
            return new JSONObject();
        } finally {
            // 마지막 입력이 클립보드에 남지 않게
            svc.runOnMain(() -> {
                try {
                    if (Build.VERSION.SDK_INT >= 28) cm.clearPrimaryClip();
                    else cm.setPrimaryClip(ClipData.newPlainText("", ""));
                } catch (Exception ignored) {}
            });
        }
    }

    /**
     * 화면을 켜고(보안 잠금이 없으면) 잠금화면을 넘긴다. 보안 잠금이 있으면 아무것도 하지 않는다(풀 수도 없고, 주머니
     * 속에서 화면만 켜 봐야 소용없다). 결과는 state 와 같은 screen_on·locked·secure.
     */
    @SuppressWarnings("deprecation")
    private JSONObject wake() throws Exception {
        PowerManager pm = (PowerManager) svc.getSystemService(Context.POWER_SERVICE);
        KeyguardManager km = (KeyguardManager) svc.getSystemService(Context.KEYGUARD_SERVICE);
        boolean secure = km.isDeviceSecure();
        boolean woke = false;
        if (!secure && (!pm.isInteractive() || km.isKeyguardLocked())) {
            boolean wasOn = pm.isInteractive();
            woke = true;
            // 화면 켜기: ACQUIRE_CAUSES_WAKEUP 잠금(옛 방식, 대부분의 기기) + 잠금화면 위 투명 화면(새 방식)
            PowerManager.WakeLock w = pm.newWakeLock(
                    PowerManager.SCREEN_BRIGHT_WAKE_LOCK | PowerManager.ACQUIRE_CAUSES_WAKEUP, "roundtable:wake");
            w.acquire(3000);
            svc.runOnMain(() -> {
                try { WakeActivity.start(svc); } catch (Exception e) { WLog.add("깨우기 화면 실패: " + e.getClass().getSimpleName()); }
            });
            long end = System.currentTimeMillis() + 3000;
            while (System.currentTimeMillis() < end && (!pm.isInteractive() || km.isKeyguardLocked())) Thread.sleep(150);
            if (pm.isInteractive() && km.isKeyguardLocked()) {
                // 넘기기 요청이 안 먹는 잠금화면: 위로 밀기(보안 잠금이 없을 때만 여기 온다)
                swipeUp();
                end = System.currentTimeMillis() + 1500;
                while (System.currentTimeMillis() < end && km.isKeyguardLocked()) Thread.sleep(150);
            }
            WLog.add("깨우기: " + (wasOn ? "켜져 있음" : "화면 켬") + (km.isKeyguardLocked() ? ", 잠금화면 남음" : ""));
        }
        if (pm.isInteractive() && !km.isKeyguardLocked()) keepAwake();
        JSONObject o = new JSONObject();
        o.put("screen_on", pm.isInteractive());
        o.put("locked", km.isKeyguardLocked());
        o.put("secure", secure);
        o.put("woke", woke);  // 꺼져 있거나 잠금화면이던 것을 이번에 켰다(서버가 다 쓴 뒤 다시 끌지 정한다)
        return o;
    }

    private void swipeUp() throws Exception {
        int w = svc.getResources().getDisplayMetrics().widthPixels;
        int h = svc.getResources().getDisplayMetrics().heightPixels;
        Path p = new Path();
        p.moveTo(w / 2f, h * 0.85f);
        p.lineTo(w / 2f, h * 0.25f);
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(p, 0, 300)).build();
        CountDownLatch done = new CountDownLatch(1);
        svc.dispatchGesture(g, new AccessibilityService.GestureResultCallback() {
            @Override public void onCompleted(GestureDescription d) { done.countDown(); }
            @Override public void onCancelled(GestureDescription d) { done.countDown(); }
        }, null);
        done.await(2, TimeUnit.SECONDS);
    }

    private JSONObject global(String action) {
        int a;
        switch (action) {
            case "home": a = AccessibilityService.GLOBAL_ACTION_HOME; break;
            case "back": a = AccessibilityService.GLOBAL_ACTION_BACK; break;
            case "lock_screen":
                // 다 쓴 뒤 화면 끄기(서버 설정). 잠금 설정이 없으면 화면만 꺼진다
                if (Build.VERSION.SDK_INT < 28) throw new IllegalStateException("안드로이드 9 이상에서만 화면 끄기 가능");
                a = AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN;
                break;
            default: throw new IllegalArgumentException("모르는 동작 " + action);
        }
        if (!svc.performGlobalAction(a)) throw new IllegalStateException("동작 실패 " + action);
        return new JSONObject();
    }

    private JSONObject screenshot() throws Exception {
        if (Build.VERSION.SDK_INT < 30) throw new IllegalStateException("안드로이드 11 이상에서만 스크린샷 가능");
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<byte[]> png = new AtomicReference<>();
        AtomicReference<String> err = new AtomicReference<>();
        svc.takeScreenshot(Display.DEFAULT_DISPLAY, svc.getMainExecutor(), new AccessibilityService.TakeScreenshotCallback() {
            @Override public void onSuccess(AccessibilityService.ScreenshotResult r) {
                try (HardwareBuffer hb = r.getHardwareBuffer()) {
                    Bitmap hw = Bitmap.wrapHardwareBuffer(hb, r.getColorSpace());
                    Bitmap bmp = hw.copy(Bitmap.Config.ARGB_8888, false);
                    ByteArrayOutputStream out = new ByteArrayOutputStream();
                    bmp.compress(Bitmap.CompressFormat.PNG, 100, out);
                    png.set(out.toByteArray());
                } catch (Exception e) {
                    err.set(e.getMessage());
                }
                done.countDown();
            }

            @Override public void onFailure(int code) {
                err.set("스크린샷 실패 코드 " + code);
                done.countDown();
            }
        });
        if (!done.await(8, TimeUnit.SECONDS)) throw new IllegalStateException("스크린샷 시간 초과");
        if (png.get() == null) throw new IllegalStateException(err.get());
        JSONObject o = new JSONObject();
        o.put("png_b64", Base64.getEncoder().encodeToString(png.get()));
        return o;
    }
}
