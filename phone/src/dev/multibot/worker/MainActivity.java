package dev.multibot.worker;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/** 설정·상태 화면. 연결은 접근성 서비스가 켜져 있는 동안 뒤에서 유지된다(이 화면을 닫아도 된다). */
public class MainActivity extends Activity {
    private TextView status, perms, log;
    private EditText url, token;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        int pad = dp(14);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("멀티봇 워커");
        title.setTextSize(22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        col.addView(title);

        status = new TextView(this);
        status.setTextSize(16);
        status.setPadding(0, dp(6), 0, dp(6));
        col.addView(status);
        perms = new TextView(this);
        col.addView(perms);

        col.addView(section("권한 (처음 한 번)"));
        col.addView(button("앱 정보 열기 (⋮ → 제한된 설정 허용)", v -> startActivity(
                new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName())))));
        col.addView(button("알림 접근 설정 열기", v -> startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))));
        col.addView(button("접근성 설정 열기 → 멀티봇 워커 켜기", v -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))));
        col.addView(button("배터리 최적화 예외 (연결 끊김 방지)", v -> {
            try {
                startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
            } catch (Exception e) {
                startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
            }
        }));

        col.addView(section("서버"));
        url = new EditText(this);
        url.setSingleLine(true);
        url.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        url.setText(Worker.url(this));
        col.addView(url);
        token = new EditText(this);
        token.setSingleLine(true);
        token.setHint("기기 토큰");
        token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        token.setText(Worker.token(this));
        col.addView(token);
        col.addView(button("저장하고 다시 연결", v -> {
            Worker.prefs(this).edit()
                    .putString("url", url.getText().toString().trim())
                    .putString("token", token.getText().toString().trim())
                    .apply();
            Worker.reconnect();
            Toast.makeText(this, "저장했습니다", Toast.LENGTH_SHORT).show();
        }));

        col.addView(section("기록 (메시지 내용은 남기지 않음)"));
        log = new TextView(this);
        log.setTypeface(Typeface.MONOSPACE);
        log.setTextSize(11);
        log.setTextIsSelectable(true);
        col.addView(log);

        ScrollView sv = new ScrollView(this);
        sv.addView(col);
        setContentView(sv);
    }

    @Override protected void onResume() {
        super.onResume();
        // 앱 화면이 열려 있을 때는 포그라운드라 연결 유지 서비스를 확실히 띄울 수 있다
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
        KeepAlive.start(this);
        Worker.setStatusListener(s -> status.setText(s));
        WLog.setListener(() -> log.setText(WLog.text()));
        log.setText(WLog.text());
        String enabled = Settings.Secure.getString(getContentResolver(), "enabled_notification_listeners");
        boolean notif = enabled != null && enabled.contains(new ComponentName(this, NotifListener.class).flattenToString());
        String a11y = Settings.Secure.getString(getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        boolean acc = a11y != null && a11y.contains(new ComponentName(this, A11y.class).flattenToString());
        android.os.PowerManager pm = (android.os.PowerManager) getSystemService(POWER_SERVICE);
        boolean battery = pm.isIgnoringBatteryOptimizations(getPackageName());
        perms.setText("알림 접근: " + (notif ? "허용됨" : "필요") + "   접근성: " + (acc ? "켜짐" : "필요")
                + "\n배터리 최적화 예외: " + (battery ? "예" : "아니오(연결이 끊길 수 있음)"));
    }

    @Override protected void onPause() {
        super.onPause();
        Worker.setStatusListener(null);
        WLog.setListener(null);
    }

    private TextView section(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, dp(16), 0, dp(4));
        return t;
    }

    private Button button(String s, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(s);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        return b;
    }

    private int dp(int v) { return (int) (v * getResources().getDisplayMetrics().density); }
}
