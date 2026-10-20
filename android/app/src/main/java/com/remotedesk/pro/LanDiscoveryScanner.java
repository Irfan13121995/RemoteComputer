package com.remotedesk.pro;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Discovers and resolves RemoteDesk Pro hosts across both:
 * 1. Local Wi-Fi (UDP Port 21116 Broadcast + Concurrent /24 HTTP Subnet Sweep)
 * 2. Cross-Network / 4G / 5G Mobile Data (Global Cloud Rendezvous via ntfy.sh + HTTPS Tunnel)
 */
public class LanDiscoveryScanner {

    public static final int DISCOVERY_UDP_PORT = 21116;
    public static final int DEFAULT_HTTP_PORT = 8000;
    public static final String GLOBAL_RENDEZVOUS_BASE = "https://ntfy.sh";
    public static final String GLOBAL_TOPIC_PREFIX = "rdpro_v2_id_";

    public interface DiscoveryCallback {
        void onPeersDiscovered(JSONArray peers);
    }

    public static String normalizePartnerId(String raw) {
        if (raw == null) return "";
        return raw.replaceAll("[^0-9]", "");
    }

    public static boolean isNineDigitPartnerId(String input) {
        if (input == null) return false;
        String trimmed = input.trim();
        // If it contains dots or colons or slashes, it's an IP or URL, not a 9-digit Partner ID
        if (trimmed.contains(".") || trimmed.contains(":") || trimmed.contains("/")) {
            return false;
        }
        String digits = normalizePartnerId(trimmed);
        return digits.length() >= 6 && digits.length() <= 12;
    }

    public static void scanNetworkAsync(final Context context, final DiscoveryCallback callback) {
        new Thread(() -> {
            Map<String, JSONObject> foundPeers = Collections.synchronizedMap(new LinkedHashMap<>());
            WifiManager wm = (WifiManager) context.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            WifiManager.MulticastLock mLock = null;

            try {
                if (wm != null) {
                    mLock = wm.createMulticastLock("RemoteDeskProUdpLock");
                    mLock.setReferenceCounted(true);
                    mLock.acquire();
                }

                // 1. Send UDP 21116 Broadcast Probe & listen for 650ms
                scanUdpBroadcast(foundPeers);

                // 2. Sweep local /24 Wi-Fi subnet on port 8000 for instant HTTP discovery
                String subnetPrefix = getWifiSubnetPrefix(wm);
                if (subnetPrefix != null) {
                    sweepSubnetHttp(subnetPrefix, DEFAULT_HTTP_PORT, foundPeers);
                }

                // 3. Check saved hosts via Global Cloud Rendezvous so PCs appear even on 4G/5G Mobile Data
                checkSavedPeersGlobal(context, foundPeers);
            } catch (Exception ignored) {
            } finally {
                if (mLock != null && mLock.isHeld()) {
                    try {
                        mLock.release();
                    } catch (Exception ignored) {}
                }
            }

            JSONArray arr = new JSONArray();
            synchronized (foundPeers) {
                for (JSONObject obj : foundPeers.values()) {
                    arr.put(obj);
                }
            }
            if (callback != null) {
                callback.onPeersDiscovered(arr);
            }
        }).start();
    }

    /**
     * Resolve a user-entered Partner ID (e.g. "349 443 493"), tunnel hostname ("xyz.lhr.life"),
     * or LAN IP ("192.168.1.50:8000") into a verified reachable HTTP/HTTPS base URL.
     * Works seamlessly on both same Wi-Fi and 4G/5G Mobile Data / external networks.
     */
    public static String resolveBestHostUrl(Context context, String hostOrId) {
        if (hostOrId == null) return null;
        String input = hostOrId.trim();
        if (input.isEmpty()) return null;

        // Case 1: User entered a 9-digit Partner ID (e.g. "349 443 493")
        if (isNineDigitPartnerId(input)) {
            String normId = normalizePartnerId(input);
            // Check Global Cloud Rendezvous (returns both lan_url and public_url)
            JSONObject cloudRecord = lookupFromGlobalCloud(normId);
            if (cloudRecord != null) {
                String lanUrl = cloudRecord.optString("lan_url", "");
                String publicUrl = cloudRecord.optString("public_url", "");
                if (!lanUrl.isEmpty() && isUrlReachable(lanUrl, 500)) {
                    return cleanBaseUrl(lanUrl);
                }
                if (!publicUrl.isEmpty()) {
                    return cleanBaseUrl(publicUrl);
                }
                if (!lanUrl.isEmpty()) {
                    return cleanBaseUrl(lanUrl);
                }
            }
            // Also check saved hosts if any matches this machine_id
            String savedFallback = findSavedUrlForPartnerId(context, normId);
            if (savedFallback != null) {
                return savedFallback;
            }
            return null;
        }

        // Case 2: User entered a URL, tunnel domain (.lhr.life / .trycloudflare.com), or IP:Port
        String candidateUrl = formatDirectHostUrl(input);
        if (isUrlReachable(candidateUrl, 900)) {
            return candidateUrl;
        }

        // Case 3: If the direct LAN IP was not reachable (e.g. user switched from Wi-Fi to 4G/5G Mobile Data),
        // check if we know the 9-digit Partner ID for this saved host and resolve its Global Tunnel URL!
        String associatedId = findPartnerIdForSavedHost(context, input);
        if (associatedId != null && !associatedId.isEmpty()) {
            JSONObject cloudRecord = lookupFromGlobalCloud(associatedId);
            if (cloudRecord != null) {
                String pubUrl = cloudRecord.optString("public_url", "");
                if (!pubUrl.isEmpty()) {
                    return cleanBaseUrl(pubUrl);
                }
            }
        }

        // Return candidateUrl as final fallback
        return candidateUrl;
    }

    public static JSONObject lookupFromGlobalCloud(String partnerId) {
        String normId = normalizePartnerId(partnerId);
        if (normId.length() < 6) return null;
        String topic = GLOBAL_TOPIC_PREFIX + normId;
        HttpURLConnection conn = null;
        try {
            URL url = new URL(GLOBAL_RENDEZVOUS_BASE + "/" + topic + "/json?poll=1&since=12h");
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(2500);
            conn.setReadTimeout(2500);
            conn.setRequestMethod("GET");
            conn.setRequestProperty("Accept", "application/json");
            if (conn.getResponseCode() == 200) {
                BufferedReader reader = new BufferedReader(
                    new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8)
                );
                JSONObject latestRecord = null;
                String line;
                while ((line = reader.readLine()) != null) {
                    line = line.trim();
                    if (line.isEmpty()) continue;
                    try {
                        JSONObject event = new JSONObject(line);
                        if ("message".equals(event.optString("event"))) {
                            String msgStr = event.optString("message", "");
                            if (!msgStr.isEmpty()) {
                                JSONObject payload = new JSONObject(msgStr);
                                if (normId.equals(payload.optString("normalized_id"))) {
                                    latestRecord = payload;
                                }
                            }
                        }
                    } catch (Exception ignored) {}
                }
                reader.close();
                return latestRecord;
            }
        } catch (Exception ignored) {
        } finally {
            if (conn != null) conn.disconnect();
        }
        return null;
    }

    private static void checkSavedPeersGlobal(Context context, Map<String, JSONObject> foundPeers) {
        try {
            SharedPreferences prefs = context.getSharedPreferences("RemoteDeskProPrefs", Context.MODE_PRIVATE);
            String savedStr = prefs.getString("saved_hosts_json", "[]");
            JSONArray savedArr = new JSONArray(savedStr);
            for (int i = 0; i < savedArr.length(); i++) {
                JSONObject item = savedArr.getJSONObject(i);
                String mid = item.optString("machine_id", "");
                if (mid.isEmpty() && isNineDigitPartnerId(item.optString("host", ""))) {
                    mid = item.optString("host", "");
                }
                String normId = normalizePartnerId(mid);
                if (normId.length() >= 6) {
                    // Check if already found on local Wi-Fi
                    boolean alreadyOnLan = false;
                    synchronized (foundPeers) {
                        for (JSONObject existing : foundPeers.values()) {
                            if (normId.equals(normalizePartnerId(existing.optString("machine_id", "")))) {
                                alreadyOnLan = true;
                                break;
                            }
                        }
                    }
                    if (!alreadyOnLan) {
                        JSONObject cloud = lookupFromGlobalCloud(normId);
                        if (cloud != null) {
                            String pubUrl = cloud.optString("public_url", "");
                            String lanUrl = cloud.optString("lan_url", "");
                            String bestAddr = !pubUrl.isEmpty() ? pubUrl : lanUrl;
                            if (!bestAddr.isEmpty()) {
                                JSONObject peer = new JSONObject();
                                peer.put("machine_id", cloud.optString("machine_id", mid));
                                peer.put("hostname", cloud.optString("hostname", item.optString("name", "Remote-PC")));
                                peer.put("os", cloud.optString("os", "Windows") + " • Global Tunnel");
                                peer.put("host_addr", cloud.optString("machine_id", mid));
                                peer.put("public_url", pubUrl);
                                peer.put("pin", item.optString("pin", ""));
                                foundPeers.put("cloud_" + normId, peer);
                            }
                        }
                    }
                }
            }
        } catch (Exception ignored) {}
    }

    private static String findPartnerIdForSavedHost(Context context, String hostInput) {
        try {
            SharedPreferences prefs = context.getSharedPreferences("RemoteDeskProPrefs", Context.MODE_PRIVATE);
            JSONArray arr = new JSONArray(prefs.getString("saved_hosts_json", "[]"));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.getJSONObject(i);
                if (hostInput.equalsIgnoreCase(item.optString("host", ""))) {
                    String mid = item.optString("machine_id", "");
                    if (!mid.isEmpty()) return mid;
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    private static String findSavedUrlForPartnerId(Context context, String normId) {
        try {
            SharedPreferences prefs = context.getSharedPreferences("RemoteDeskProPrefs", Context.MODE_PRIVATE);
            JSONArray arr = new JSONArray(prefs.getString("saved_hosts_json", "[]"));
            for (int i = 0; i < arr.length(); i++) {
                JSONObject item = arr.getJSONObject(i);
                String mid = normalizePartnerId(item.optString("machine_id", ""));
                if (normId.equals(mid)) {
                    String pub = item.optString("public_url", "");
                    if (!pub.isEmpty()) return cleanBaseUrl(pub);
                    String host = item.optString("host", "");
                    if (!host.isEmpty() && !isNineDigitPartnerId(host)) {
                        return formatDirectHostUrl(host);
                    }
                }
            }
        } catch (Exception ignored) {}
        return null;
    }

    public static String formatDirectHostUrl(String rawHost) {
        String h = rawHost.trim();
        if (h.startsWith("http://") || h.startsWith("https://")) {
            return cleanBaseUrl(h);
        }
        if (h.endsWith(".lhr.life") || h.endsWith(".trycloudflare.com") || h.endsWith(".ngrok-free.app")) {
            return cleanBaseUrl("https://" + h);
        }
        // If bare IPv4 without port, default to :8000
        if (h.matches("^\\d{1,3}(\\.\\d{1,3}){3}$")) {
            return "http://" + h + ":" + DEFAULT_HTTP_PORT;
        }
        return cleanBaseUrl("http://" + h);
    }

    private static String cleanBaseUrl(String url) {
        String u = url.trim();
        while (u.endsWith("/")) {
            u = u.substring(0, u.length() - 1);
        }
        return u;
    }

    private static boolean isUrlReachable(String baseUrl, int timeoutMs) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(cleanBaseUrl(baseUrl) + "/api/host-info");
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs);
            conn.setRequestMethod("GET");
            int code = conn.getResponseCode();
            return code >= 200 && code < 400;
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static void scanUdpBroadcast(Map<String, JSONObject> foundPeers) {
        DatagramSocket socket = null;
        try {
            socket = new DatagramSocket(null);
            socket.setReuseAddress(true);
            socket.setBroadcast(true);
            socket.setSoTimeout(350);

            JSONObject probe = new JSONObject();
            probe.put("magic", "REMOTEDESK_PRO_V2");
            probe.put("type", "probe");
            probe.put("machine_id", "ANDROID-CLIENT");
            probe.put("hostname", "Android-Phone");
            probe.put("os", "Android");
            probe.put("port", DEFAULT_HTTP_PORT);

            byte[] payload = probe.toString().getBytes(StandardCharsets.UTF_8);
            DatagramPacket packet = new DatagramPacket(
                payload,
                payload.length,
                InetAddress.getByName("255.255.255.255"),
                DISCOVERY_UDP_PORT
            );
            socket.send(packet);

            long deadline = System.currentTimeMillis() + 650;
            byte[] buf = new byte[4096];
            while (System.currentTimeMillis() < deadline) {
                DatagramPacket recv = new DatagramPacket(buf, buf.length);
                socket.receive(recv);
                String raw = new String(recv.getData(), 0, recv.getLength(), StandardCharsets.UTF_8);
                JSONObject data = new JSONObject(raw);
                if ("REMOTEDESK_PRO_V2".equals(data.optString("magic"))) {
                    String mid = data.optString("machine_id", "");
                    if (!mid.isEmpty() && !"ANDROID-CLIENT".equals(mid)) {
                        String ip = recv.getAddress().getHostAddress();
                        int port = data.optInt("port", DEFAULT_HTTP_PORT);
                        JSONObject peer = new JSONObject();
                        peer.put("machine_id", mid);
                        peer.put("hostname", data.optString("hostname", "Remote-PC"));
                        peer.put("os", data.optString("os", "Windows"));
                        peer.put("ip", ip);
                        peer.put("port", port);
                        peer.put("host_addr", ip + ":" + port);
                        foundPeers.put(ip + ":" + port, peer);
                    }
                }
            }
        } catch (Exception ignored) {
        } finally {
            if (socket != null) {
                socket.close();
            }
        }
    }

    private static String getWifiSubnetPrefix(WifiManager wm) {
        try {
            if (wm == null) return null;
            WifiInfo info = wm.getConnectionInfo();
            if (info == null) return null;
            int ipInt = info.getIpAddress();
            if (ipInt == 0) return null;
            int b1 = ipInt & 0xff;
            int b2 = (ipInt >> 8) & 0xff;
            int b3 = (ipInt >> 16) & 0xff;
            return b1 + "." + b2 + "." + b3 + ".";
        } catch (Exception e) {
            return null;
        }
    }

    private static void sweepSubnetHttp(String prefix, int port, Map<String, JSONObject> foundPeers) {
        ExecutorService pool = Executors.newFixedThreadPool(32);
        for (int i = 1; i <= 254; i++) {
            final String candidateIp = prefix + i;
            pool.submit(() -> {
                HttpURLConnection conn = null;
                try {
                    URL url = new URL("http://" + candidateIp + ":" + port + "/api/host-info");
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setConnectTimeout(220);
                    conn.setReadTimeout(350);
                    conn.setRequestMethod("GET");
                    if (conn.getResponseCode() == 200) {
                        BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
                        StringBuilder sb = new StringBuilder();
                        String line;
                        while ((line = reader.readLine()) != null) {
                            sb.append(line);
                        }
                        reader.close();
                        JSONObject info = new JSONObject(sb.toString());
                        String mid = info.optString("machine_id", "");
                        if (!mid.isEmpty()) {
                            JSONObject peer = new JSONObject();
                            peer.put("machine_id", mid);
                            peer.put("hostname", info.optString("hostname", "Remote-PC"));
                            peer.put("os", info.optString("os", "Windows"));
                            peer.put("public_url", info.optString("public_url", ""));
                            peer.put("ip", candidateIp);
                            peer.put("port", port);
                            peer.put("host_addr", candidateIp + ":" + port);
                            foundPeers.put(candidateIp + ":" + port, peer);
                        }
                    }
                } catch (Exception ignored) {
                } finally {
                    if (conn != null) conn.disconnect();
                }
            });
        }
        pool.shutdown();
        try {
            pool.awaitTermination(1200, TimeUnit.MILLISECONDS);
        } catch (InterruptedException ignored) {}
    }
}
