package com.remotedesk.pro;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.view.KeyEvent;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.view.inputmethod.InputMethodManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;

/**
 * MainActivity for RemoteDesk Pro Android Client (v2.0.0).
 * Provides:
 * - Offline Native Android Connection & Wi-Fi Discovery Hub (UDP 21116 + Subnet Scan)
 * - Hardware-accelerated Remote Desktop Session WebView
 * - Native Haptic Feedback, Soft Keyboard Controller, Saved Hosts, and Deep-Link QR Pairing
 */
public class MainActivity extends Activity {

    private static final String PREFS_NAME = "RemoteDeskProPrefs";
    private static final String KEY_SAVED_HOSTS = "saved_hosts_json";
    private static final String KEY_LAST_HOST = "last_host";
    private static final String HUB_ASSET_URL = "file:///android_asset/mobile_hub.html";

    private WebView webView;
    private boolean inRemoteSession = false;

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        webView = new WebView(this);
        webView.setBackgroundColor(0xFF0A0E17);
        webView.setLayerType(View.LAYER_TYPE_HARDWARE, null);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        webView.addJavascriptInterface(new AndroidBridge(), "AndroidBridge");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return false;
            }
        });
        webView.setWebChromeClient(new WebChromeClient());

        setContentView(webView);

        // Handle deep link (remotedesk://connect?host=192.168.1.50:8000&pin=123456)
        Intent intent = getIntent();
        if (intent != null && Intent.ACTION_VIEW.equals(intent.getAction())) {
            Uri uri = intent.getData();
            if (uri != null) {
                String host = uri.getQueryParameter("host");
                String pin = uri.getQueryParameter("pin");
                if (host != null && !host.isEmpty()) {
                    connectToHostInternal(host, pin != null ? pin : "", "remote-control");
                    return;
                }
            }
        }

        loadMobileHub();
    }

    private void loadMobileHub() {
        inRemoteSession = false;
        runOnUiThread(() -> webView.loadUrl(HUB_ASSET_URL));
    }

    private void connectToHostInternal(String hostAddr, String pin, String mode) {
        inRemoteSession = true;
        String cleanHost = hostAddr.trim();
        if (!cleanHost.startsWith("http://") && !cleanHost.startsWith("https://")) {
            cleanHost = "http://" + cleanHost;
        }
        if (cleanHost.endsWith("/")) {
            cleanHost = cleanHost.substring(0, cleanHost.length() - 1);
        }

        // Persist last used host
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
        prefs.edit().putString(KEY_LAST_HOST, hostAddr.trim()).apply();

        final String targetUrl = cleanHost
            + "/?autoconnect=1&mobile=1&show_desktop=1&pin=" + Uri.encode(pin != null ? pin.trim() : "")
            + "&mode=" + Uri.encode(mode != null ? mode : "remote-control");

        runOnUiThread(() -> webView.loadUrl(targetUrl));
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (inRemoteSession) {
                loadMobileHub();
                return true;
            } else if (webView.canGoBack()) {
                webView.goBack();
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    /**
     * JavaScript interface exposed as `window.AndroidBridge` to both the embedded Mobile Hub
     * and the remote desktop web client.
     */
    public class AndroidBridge {

        @JavascriptInterface
        public boolean isAndroidApp() {
            return true;
        }

        @JavascriptInterface
        public String getAppVersion() {
            return "2.0.0";
        }

        @JavascriptInterface
        public void vibrate(int milliseconds) {
            try {
                Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
                if (v != null && v.hasVibrator()) {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        v.vibrate(VibrationEffect.createOneShot(Math.max(10, milliseconds), VibrationEffect.DEFAULT_AMPLITUDE));
                    } else {
                        v.vibrate(Math.max(10, milliseconds));
                    }
                }
            } catch (Exception ignored) {}
        }

        @JavascriptInterface
        public void showSoftKeyboard() {
            runOnUiThread(() -> {
                try {
                    webView.requestFocus();
                    InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
                    if (imm != null) {
                        imm.showSoftInput(webView, InputMethodManager.SHOW_IMPLICIT);
                    }
                } catch (Exception ignored) {}
            });
        }

        @JavascriptInterface
        public void connectToHost(String host, String pin, String mode) {
            vibrate(25);
            connectToHostInternal(host, pin, mode);
        }

        @JavascriptInterface
        public void returnToMobileHub() {
            vibrate(20);
            loadMobileHub();
        }

        @JavascriptInterface
        public String getLastHost() {
            SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
            return prefs.getString(KEY_LAST_HOST, "");
        }

        @JavascriptInterface
        public String getSavedHosts() {
            SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
            return prefs.getString(KEY_SAVED_HOSTS, "[]");
        }

        @JavascriptInterface
        public void saveHostsJson(String jsonArrayStr) {
            SharedPreferences prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
            prefs.edit().putString(KEY_SAVED_HOSTS, jsonArrayStr).apply();
        }

        @JavascriptInterface
        public void startLanScan() {
            LanDiscoveryScanner.scanNetworkAsync(MainActivity.this, (JSONArray peers) -> {
                final String jsonStr = peers.toString();
                runOnUiThread(() -> {
                    webView.evaluateJavascript(
                        "if(window.onAndroidPeersDiscovered){window.onAndroidPeersDiscovered(" + jsonStr + ");}",
                        null
                    );
                });
            });
        }
    }
}
