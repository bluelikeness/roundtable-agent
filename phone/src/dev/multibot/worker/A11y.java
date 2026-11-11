package dev.multibot.worker;

import android.accessibilityservice.AccessibilityService;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.view.accessibility.AccessibilityEvent;

/** 접근성 서비스. 켜져 있는 동안 게이트웨이 연결(Worker)을 유지하고 명령을 수행한다. */
public class A11y extends AccessibilityService {
    private final Handler main = new Handler(Looper.getMainLooper());

    @Override protected void onServiceConnected() {
        WLog.add("접근성 서비스 켜짐 → 게이트웨이 연결 시작");
        KeepAlive.start(this);
        Worker.start(this);
    }

    @Override public boolean onUnbind(Intent intent) {
        WLog.add("접근성 서비스 꺼짐");
        Worker.stop();
        return super.onUnbind(intent);
    }

    @Override public void onDestroy() {
        Worker.stop();
        super.onDestroy();
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) {}

    @Override public void onInterrupt() {}

    void runOnMain(Runnable r) { main.post(r); }
}
