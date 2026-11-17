package dev.roundtable.worker;

import android.app.Notification;
import android.os.Bundle;
import android.os.Parcelable;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONObject;

/**
 * 게이트웨이가 watch 로 지정한 앱(Vooy·피클·뮤즈)의 알림만 이벤트로 올린다.
 * 알림 제목·본문은 보내지 않는다(길이만). 어댑터는 알림을 "답이 왔다"는 신호로만 쓰고 내용은 화면에서 읽는다.
 */
public class NotifListener extends NotificationListenerService {
    @Override public void onListenerConnected() {
        WLog.add("알림 접근 연결됨");
    }

    @Override public void onNotificationPosted(StatusBarNotification sbn) {
        send("notif.posted", sbn);
    }

    @Override public void onNotificationRemoved(StatusBarNotification sbn) {
        send("notif.removed", sbn);
    }

    private void send(String event, StatusBarNotification sbn) {
        String pkg = sbn.getPackageName();
        if (!Worker.watched.contains(pkg)) return;
        try {
            Notification n = sbn.getNotification();
            Bundle e = n.extras;
            JSONObject d = new JSONObject();
            d.put("pkg", pkg);
            d.put("key", sbn.getKey());
            d.put("id", sbn.getId());
            d.put("channel", n.getChannelId());
            String tpl = e.getString(Notification.EXTRA_TEMPLATE);
            d.put("template", tpl == null ? JSONObject.NULL : tpl.substring(tpl.lastIndexOf('$') + 1));
            CharSequence title = e.getCharSequence(Notification.EXTRA_TITLE);
            CharSequence text = e.getCharSequence(Notification.EXTRA_TEXT);
            d.put("title_len", title == null ? 0 : title.length());
            d.put("text_len", text == null ? 0 : text.length());
            Parcelable[] msgs = e.getParcelableArray(Notification.EXTRA_MESSAGES);
            d.put("messages", msgs == null ? 0 : msgs.length);
            d.put("when_ms", n.when);
            d.put("summary", (n.flags & Notification.FLAG_GROUP_SUMMARY) != 0);
            d.put("ongoing", (n.flags & Notification.FLAG_ONGOING_EVENT) != 0);
            Worker.event(event, d);
            WLog.add((event.endsWith("posted") ? "알림 " : "알림 사라짐 ") + pkg + " ch=" + n.getChannelId());
        } catch (Exception ignored) {}
    }
}
