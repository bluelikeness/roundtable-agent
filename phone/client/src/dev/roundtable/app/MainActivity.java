package dev.roundtable.app;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

/**
 * 원탁 앱: 원탁 웹 화면을 WebView 로 띄운다.
 * - 로그인 쿠키는 CookieManager 가 앱 안에 보관한다(웹과 같은 비밀번호·인증 코드 로그인).
 * - 📎 파일 붙이기는 안드로이드 파일 고르기 창으로, 파일 내려받기는 다운로드 관리자로 넘긴다.
 * - 원탁 주소 밖의 링크는 브라우저로 연다.
 */
public class MainActivity extends Activity {
    private static final int PICK = 1;

    private WebView web;
    private Uri home;
    private ValueCallback<Uri[]> pending;
    private View fullscreen;
    private WebChromeClient.CustomViewCallback fullscreenDone;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        home = Uri.parse(Defaults.URL);
        Window w = getWindow();
        w.setStatusBarColor(Color.parseColor("#1F2937"));

        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(true);
        s.setUserAgentString(s.getUserAgentString() + " RoundtableApp/" + Defaults.VERSION);
        CookieManager.getInstance().setAcceptCookie(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (sameSite(u)) return false;
                openOutside(u);
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> cb, FileChooserParams params) {
                if (pending != null) pending.onReceiveValue(null);
                pending = cb;
                Intent pick = params.createIntent();
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                }
                try {
                    startActivityForResult(Intent.createChooser(pick, "파일 고르기"), PICK);
                } catch (ActivityNotFoundException e) {
                    pending = null;
                    Toast.makeText(MainActivity.this, "파일을 고를 앱이 없습니다", Toast.LENGTH_SHORT).show();
                    return false;
                }
                return true;
            }

            // 영상 전체 화면
            @Override
            public void onShowCustomView(View view, CustomViewCallback done) {
                if (fullscreen != null) { done.onCustomViewHidden(); return; }
                fullscreen = view;
                fullscreenDone = done;
                ((ViewGroup) getWindow().getDecorView()).addView(view, new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
            }

            @Override
            public void onHideCustomView() {
                if (fullscreen == null) return;
                ((ViewGroup) getWindow().getDecorView()).removeView(fullscreen);
                fullscreen = null;
                if (fullscreenDone != null) fullscreenDone.onCustomViewHidden();
                fullscreenDone = null;
            }
        });
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> download(url, userAgent, disposition, mime));

        if (state != null) web.restoreState(state);
        else web.loadUrl(Defaults.URL);
    }

    private boolean sameSite(Uri u) {
        return home.getHost() != null && home.getHost().equalsIgnoreCase(u.getHost())
                && home.getScheme() != null && home.getScheme().equalsIgnoreCase(u.getScheme());
    }

    private void openOutside(Uri u) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "열 수 있는 앱이 없습니다", Toast.LENGTH_SHORT).show();
        }
    }

    /** 원탁 파일 보관함은 로그인 쿠키가 있어야 받을 수 있어, 다운로드 관리자에 쿠키를 함께 넘긴다. */
    private void download(String url, String userAgent, String disposition, String mime) {
        Uri u = Uri.parse(url);
        if (!sameSite(u)) { openOutside(u); return; }
        String name = URLUtil.guessFileName(url, disposition, mime);
        DownloadManager.Request r = new DownloadManager.Request(u)
                .setMimeType(mime)
                .addRequestHeader("User-Agent", userAgent)
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
        String cookie = CookieManager.getInstance().getCookie(url);
        if (cookie != null) r.addRequestHeader("Cookie", cookie);
        DownloadManager dm = getSystemService(DownloadManager.class);
        if (dm == null) return;
        dm.enqueue(r);
        Toast.makeText(this, name + " 내려받는 중", Toast.LENGTH_SHORT).show();
    }

    @Override
    protected void onActivityResult(int req, int result, Intent data) {
        if (req != PICK) { super.onActivityResult(req, result, data); return; }
        if (pending == null) return;
        pending.onReceiveValue(picked(result, data));
        pending = null;
    }

    /** 여러 개를 고르면 ClipData 로 오는데 parseResult 는 첫 개만 돌려준다. */
    private static Uri[] picked(int result, Intent data) {
        if (result != RESULT_OK || data == null) return null;
        if (data.getClipData() != null && data.getClipData().getItemCount() > 0) {
            int n = data.getClipData().getItemCount();
            Uri[] out = new Uri[n];
            for (int i = 0; i < n; i++) out[i] = data.getClipData().getItemAt(i).getUri();
            return out;
        }
        return WebChromeClient.FileChooserParams.parseResult(result, data);
    }

    @Override
    public void onBackPressed() {
        if (fullscreen != null) { web.getWebChromeClient().onHideCustomView(); return; }
        if (web.canGoBack()) { web.goBack(); return; }
        super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }
}
