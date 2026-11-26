package dev.roundtable.worker;

import android.content.ClipData;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.SystemClock;
import android.provider.MediaStore;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.net.URLEncoder;
import java.util.ArrayList;

/**
 * 첨부 명령(adapters/phone/protocol.py share·media_since·media_upload).
 * 파일은 게이트웨이 중계(/phone/files)로 오간다. 기록(WLog)에는 개수·성공 여부만 남기고 파일 이름은 남기지 않는다.
 */
final class Media {
    private static final long SHARE_KEEP_MS = 60 * 60 * 1000L;

    private Media() {}

    /** wss://host/phone/ws → https://host/phone/files[/code] */
    static URL filesUrl(Context c, String code) throws Exception {
        URI ws = URI.create(Worker.url(c));
        String scheme = "wss".equalsIgnoreCase(ws.getScheme()) ? "https" : "http";
        String path = "/phone/files" + (code == null ? "" : "/" + code);
        return new URI(scheme, null, ws.getHost(), ws.getPort(), path, null, null).toURL();
    }

    private static HttpURLConnection open(Context c, String code) throws Exception {
        HttpURLConnection http = (HttpURLConnection) filesUrl(c, code).openConnection();
        http.setRequestProperty("Authorization", "Bearer " + Worker.token(c));
        http.setConnectTimeout(15_000);
        http.setReadTimeout(120_000);
        return http;
    }

    private static void copy(InputStream in, OutputStream out) throws Exception {
        byte[] buf = new byte[64 * 1024];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
    }

    // --- 공유하기로 넣기 ---------------------------------------------------

    /** 파일을 받아 공유하기로 pkg 앱에 넘긴다. 앱이 앞에 뜰 때까지(10초) 기다린다. */
    static JSONObject share(A11y svc, String pkg, JSONArray files, java.util.function.Supplier<String> foreground) throws Exception {
        if (files == null || files.length() == 0) throw new IllegalArgumentException("files 없음");
        File dir = new File(svc.getCacheDir(), ShareProvider.DIR);
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IllegalStateException("임시 폴더를 만들지 못함");
        long now = System.currentTimeMillis();
        File[] old = dir.listFiles();
        if (old != null) for (File f : old) if (now - f.lastModified() > SHARE_KEEP_MS) f.delete();

        ArrayList<Uri> uris = new ArrayList<>();
        String type = null;
        for (int i = 0; i < files.length(); i++) {
            JSONObject f = files.getJSONObject(i);
            String code = f.getString("code");
            if (!code.matches("[A-Za-z0-9_-]{8,64}")) throw new IllegalArgumentException("잘못된 code");
            String mime = f.optString("mime", "application/octet-stream");
            String name = f.optString("name", "file");
            HttpURLConnection http = open(svc, code);
            int status = http.getResponseCode();
            if (status != 200) throw new IllegalStateException("파일 받기 HTTP " + status);
            File out = new File(dir, code);
            try (InputStream in = http.getInputStream(); OutputStream o = new FileOutputStream(out)) {
                copy(in, o);
            } finally {
                http.disconnect();
            }
            uris.add(ShareProvider.register(code, name, mime));
            type = type == null ? mime : commonType(type, mime);
        }

        Intent send;
        if (uris.size() == 1) {
            send = new Intent(Intent.ACTION_SEND);
            send.putExtra(Intent.EXTRA_STREAM, uris.get(0));
        } else {
            send = new Intent(Intent.ACTION_SEND_MULTIPLE);
            send.putParcelableArrayListExtra(Intent.EXTRA_STREAM, uris);
        }
        send.setType(type);
        ClipData clip = ClipData.newRawUri("", uris.get(0));
        for (int i = 1; i < uris.size(); i++) clip.addItem(new ClipData.Item(uris.get(i)));
        send.setClipData(clip);
        send.setPackage(pkg);
        send.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION);
        if (svc.getPackageManager().queryIntentActivities(send, 0).isEmpty()) {
            throw new IllegalStateException("이 앱은 공유하기로 이런 파일을 받지 않음: " + pkg);
        }
        svc.startActivity(send);
        long end = SystemClock.elapsedRealtime() + 10_000;
        String fg = null;
        while (SystemClock.elapsedRealtime() < end) {
            fg = foreground.get();
            if (pkg.equals(fg)) break;
            Thread.sleep(200);
        }
        WLog.add("공유하기: 파일 " + uris.size() + "개 → " + (pkg.equals(fg) ? "앱 열림" : "앱 확인 안 됨"));
        JSONObject o = new JSONObject();
        o.put("shared", uris.size());
        o.put("foreground", fg == null ? JSONObject.NULL : fg);
        return o;
    }

    /** image/png + image/jpeg → image/*, image + video → *\/* */
    static String commonType(String a, String b) {
        if (a.equals(b)) return a;
        String ta = a.contains("/") ? a.substring(0, a.indexOf('/')) : a;
        String tb = b.contains("/") ? b.substring(0, b.indexOf('/')) : b;
        return ta.equals(tb) ? ta + "/*" : "*/*";
    }

    // --- 저장된 결과 찾기·올리기 ---------------------------------------------

    static boolean canRead(Context c) {
        if (Build.VERSION.SDK_INT >= 33) {
            return c.checkSelfPermission("android.permission.READ_MEDIA_IMAGES") == PackageManager.PERMISSION_GRANTED
                    && c.checkSelfPermission("android.permission.READ_MEDIA_VIDEO") == PackageManager.PERMISSION_GRANTED;
        }
        return c.checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED;
    }

    private static Uri collection() {
        return MediaStore.Files.getContentUri("external");
    }

    /** since_ms 뒤 저장된 사진·영상. pkg 가 있으면 그 앱이 저장한 것만(OWNER_PACKAGE_NAME, 안드로이드 10+). */
    static JSONObject since(Context c, long sinceMs, String pkg, JSONArray mimes) throws Exception {
        JSONObject o = new JSONObject();
        JSONArray items = new JSONArray();
        o.put("items", items);
        o.put("access", canRead(c));
        if (!canRead(c)) return o;
        ArrayList<String> want = new ArrayList<>();
        if (mimes != null) for (int i = 0; i < mimes.length(); i++) want.add(mimes.getString(i));
        boolean owner = Build.VERSION.SDK_INT >= 29;
        String[] cols = owner
                ? new String[]{MediaStore.MediaColumns._ID, MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.MediaColumns.MIME_TYPE,
                        MediaStore.MediaColumns.SIZE, MediaStore.MediaColumns.DATE_ADDED, MediaStore.MediaColumns.OWNER_PACKAGE_NAME}
                : new String[]{MediaStore.MediaColumns._ID, MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.MediaColumns.MIME_TYPE,
                        MediaStore.MediaColumns.SIZE, MediaStore.MediaColumns.DATE_ADDED};
        String sel = MediaStore.MediaColumns.DATE_ADDED + " >= ? AND (" + MediaStore.Files.FileColumns.MEDIA_TYPE + " = "
                + MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE + " OR " + MediaStore.Files.FileColumns.MEDIA_TYPE + " = "
                + MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO + ")";
        String[] args = {String.valueOf(Math.max(0, sinceMs / 1000))};
        try (Cursor cur = c.getContentResolver().query(collection(), cols, sel, args, MediaStore.MediaColumns.DATE_ADDED + " DESC")) {
            if (cur == null) return o;
            while (cur.moveToNext() && items.length() < 20) {
                String mime = cur.getString(2) == null ? "" : cur.getString(2);
                String who = owner ? cur.getString(5) : null;
                if (pkg != null && !pkg.isEmpty() && owner && !pkg.equals(who)) continue;
                if (!want.isEmpty() && want.stream().noneMatch(mime::startsWith)) continue;
                JSONObject it = new JSONObject();
                it.put("id", cur.getLong(0));
                it.put("name", cur.getString(1) == null ? "file" : cur.getString(1));
                it.put("mime", mime);
                it.put("size", cur.getLong(3));
                it.put("date_ms", cur.getLong(4) * 1000);
                it.put("owner", who == null ? JSONObject.NULL : who);
                items.put(it);
            }
        }
        return o;
    }

    /** media_since 의 항목 하나를 게이트웨이에 올린다. */
    static JSONObject upload(Context c, long id) throws Exception {
        if (!canRead(c)) throw new IllegalStateException("저장소 읽기 권한 없음");
        ContentResolver cr = c.getContentResolver();
        Uri uri = ContentUris.withAppendedId(collection(), id);
        String name = "file";
        String mime = "application/octet-stream";
        try (Cursor cur = cr.query(uri, new String[]{MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.MediaColumns.MIME_TYPE},
                null, null, null)) {
            if (cur == null || !cur.moveToFirst()) throw new IllegalStateException("파일 없음");
            if (cur.getString(0) != null) name = cur.getString(0);
            if (cur.getString(1) != null) mime = cur.getString(1);
        }
        // 사진·영상은 종류별 모음 주소로 여는 편이 권한 문제가 적다
        if (mime.startsWith("image/")) uri = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id);
        else if (mime.startsWith("video/")) uri = ContentUris.withAppendedId(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, id);

        HttpURLConnection http = open(c, null);
        http.setRequestMethod("POST");
        http.setDoOutput(true);
        http.setChunkedStreamingMode(64 * 1024);
        http.setRequestProperty("Content-Type", mime);
        http.setRequestProperty("X-File-Name", URLEncoder.encode(name, "UTF-8").replace("+", "%20"));
        try (InputStream in = cr.openInputStream(uri); OutputStream out = http.getOutputStream()) {
            if (in == null) throw new IllegalStateException("파일을 열지 못함");
            copy(in, out);
        }
        int status = http.getResponseCode();
        if (status != 200) throw new IllegalStateException("올리기 HTTP " + status);
        JSONObject res;
        try (InputStream in = http.getInputStream()) {
            java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
            copy(in, b);
            res = new JSONObject(b.toString("UTF-8"));
        } finally {
            http.disconnect();
        }
        WLog.add("저장 파일 올림: " + res.optLong("size") + " bytes");
        return res;
    }
}
