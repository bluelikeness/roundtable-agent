package dev.roundtable.worker;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 공유하기로 봇 앱에 넘기는 파일(캐시 폴더 share/)을 content:// 로 내준다. 읽기 전용.
 * 주소: content://dev.roundtable.worker.share/<code>/<이름>. 받는 앱에는 공유 인텐트가 읽기 권한만 잠깐 준다.
 */
public class ShareProvider extends ContentProvider {
    static final String AUTHORITY = "dev.roundtable.worker.share";
    static final String DIR = "share";

    private static final ConcurrentHashMap<String, String[]> META = new ConcurrentHashMap<>(); // code → {name, mime}

    static Uri register(String code, String name, String mime) {
        META.put(code, new String[]{name, mime});
        return new Uri.Builder().scheme("content").authority(AUTHORITY).appendPath(code).appendPath(name).build();
    }

    private File file(Uri uri) throws FileNotFoundException {
        if (uri.getPathSegments().isEmpty()) throw new FileNotFoundException();
        String code = uri.getPathSegments().get(0);
        if (!code.matches("[A-Za-z0-9_-]{8,64}")) throw new FileNotFoundException();
        File f = new File(new File(getContext().getCacheDir(), DIR), code);
        if (!f.isFile()) throw new FileNotFoundException();
        return f;
    }

    @Override
    public boolean onCreate() { return true; }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new SecurityException("읽기 전용");
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public String getType(Uri uri) {
        String[] m = uri.getPathSegments().isEmpty() ? null : META.get(uri.getPathSegments().get(0));
        return m == null ? "application/octet-stream" : m[1];
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        File f;
        try {
            f = file(uri);
        } catch (FileNotFoundException e) {
            return null;
        }
        String[] m = META.get(uri.getPathSegments().get(0));
        String name = m != null ? m[0] : uri.getLastPathSegment();
        String[] cols = projection != null ? projection : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = name;
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
        }
        c.addRow(row);
        return c;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }

    @Override
    public int delete(Uri uri, String selection, String[] args) { return 0; }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] args) { return 0; }
}
