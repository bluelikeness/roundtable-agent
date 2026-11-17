package dev.roundtable.worker;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;

/** 자동 업데이트 설치 결과. 사용자 확인이 필요하면(처음 한 번) 확인 화면을 띄운다. */
public class InstallReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context ctx, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
            if (confirm != null) {
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                WLog.add("업데이트: 설치 확인이 필요합니다(처음 한 번)");
                try { ctx.startActivity(confirm); } catch (Exception e) { WLog.add("확인 화면을 띄우지 못함: " + e.getMessage()); }
            }
        } else if (status == PackageInstaller.STATUS_SUCCESS) {
            WLog.add("업데이트 설치 완료");
        } else {
            WLog.add("업데이트 설치 실패(" + status + "): " + intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE));
        }
    }
}
