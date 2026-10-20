package com.remotedesk.pro;

import android.content.Context;
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
import java.net.InetSocketAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util. LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Discovers RemoteDesk Pro hosts on the phone's Wi-Fi network using:
 * 1. RustDesk-style UDP Port 21116 Broadcast Probe
 * 2. Fast concurrent HTTP /api/host-info subnet sweep (fallback when router blocks UDP broadcast)
 */
public class LanDiscoveryScanner {

    public static final int DISCOVERY_UDP_PORT = 21116;
    public static final int DEFAULT_HTTP_PORT = 8000;

    public interface DiscoveryCallback {
        void onPeersDiscovered(JSONArray peers);
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

                // 1. Send UDP 21116 Broadcast Probe & listen for 700ms
                scanUdpBroadcast(foundPeers);

                // 2. Also sweep local /24 Wi-Fi subnet on port 8000 for instant HTTP discovery
                String subnetPrefix = getWifiSubnetPrefix(wm);
                if (subnetPrefix != null) {
                    sweepSubnetHttp(subnetPrefix, DEFAULT_HTTP_PORT, foundPeers);
                }
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
                            peer.put("pin", info.optString("pin", ""));
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
