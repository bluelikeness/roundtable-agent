package dev.roundtable.worker;

import android.os.Handler;
import android.os.Looper;

import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.Date;
import java.util.Locale;

/** 화면에 보이는 짧은 기록. 메시지·알림 본문은 넣지 않는다(명령 이름·길이·상태만). */
final class WLog {
    interface Listener { void onChanged(); }

    private static final ArrayDeque<String> lines = new ArrayDeque<>();
    private static final Handler main = new Handler(Looper.getMainLooper());
    private static volatile Listener listener;

    private WLog() {}

    static void setListener(Listener l) { listener = l; }

    static void add(String msg) {
        String line = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date()) + " " + msg;
        android.util.Log.i("RoundtableWorker", line);
        synchronized (lines) {
            lines.addLast(line);
            while (lines.size() > 300) lines.removeFirst();
        }
        main.post(() -> { Listener l = listener; if (l != null) l.onChanged(); });
    }

    static String text() {
        StringBuilder sb = new StringBuilder();
        synchronized (lines) {
            for (String l : lines) sb.append(l).append('\n');
        }
        return sb.toString();
    }
}
