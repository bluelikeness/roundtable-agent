package dev.roundtable.worker;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;

/**
 * 화면 깨우기(명령 wake). 투명한 화면을 잠금화면 위에 띄우며 화면을 켜고, 보안 잠금이 없으면(잠금 없음·밀어서 해제)
 * 잠금화면을 넘긴다. PIN·패턴·지문 같은 보안 잠금은 넘기지 않는다(이 화면을 띄우지도 않는다 — Commands.wake).
 * 접근성 서비스가 띄우므로 안드로이드 10+ 의 백그라운드 화면 실행 제한에 걸리지 않는다.
 */
public class WakeActivity extends Activity {
    static void start(Context c) {
        Intent i = new Intent(c, WakeActivity.class);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_ANIMATION
                | Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS | Intent.FLAG_ACTIVITY_NO_HISTORY);
        c.startActivity(i);
    }

    @SuppressWarnings("deprecation")
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD);
        KeyguardManager km = (KeyguardManager) getSystemService(KEYGUARD_SERVICE);
        if (km.isKeyguardLocked() && !km.isDeviceSecure()) {
            km.requestDismissKeyguard(this, new KeyguardManager.KeyguardDismissCallback() {
                @Override public void onDismissSucceeded() { WLog.add("깨우기: 잠금화면 넘김"); finish(); }
                @Override public void onDismissError() { WLog.add("깨우기: 잠금화면 넘기기 실패"); finish(); }
                @Override public void onDismissCancelled() { finish(); }
            });
        }
        // 콜백이 오지 않아도 오래 남지 않는다(이 화면이 앞에 있으면 봇 앱 조종을 가린다)
        new Handler(Looper.getMainLooper()).postDelayed(this::finish, 1500);
    }

    @Override protected void onPause() {
        super.onPause();
        overridePendingTransition(0, 0);
    }
}
