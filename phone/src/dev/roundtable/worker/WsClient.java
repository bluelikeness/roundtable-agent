package dev.roundtable.worker;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.Locale;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * 최소 WebSocket 클라이언트(RFC 6455). 라이브러리 없이 빌드하려고 직접 구현한다.
 * 텍스트 프레임·조각난 프레임·ping/pong·close 만 다룬다. 보내기는 스레드 안전(synchronized).
 */
final class WsClient {
    interface Handler {
        void onText(String text);
    }

    private final SecureRandom rnd = new SecureRandom();
    private Socket socket;
    private DataInputStream in;
    private OutputStream out;
    private volatile boolean closed = false;
    /** 서버가 보낸 close 코드(없으면 0). 4423 = 다른 휴대폰이 연결 중 */
    volatile int closeCode = 0;

    /** 연결과 핸드셰이크. 실패하면 IOException. */
    void connect(URI uri, String bearerToken, int timeoutMs) throws IOException {
        boolean tls = "wss".equalsIgnoreCase(uri.getScheme());
        String host = uri.getHost();
        int port = uri.getPort() > 0 ? uri.getPort() : (tls ? 443 : 80);
        Socket s;
        if (tls) {
            Socket raw = new Socket();
            raw.connect(new InetSocketAddress(host, port), timeoutMs);
            // 호스트 이름으로 감싸야 SNI 가 들어간다(Cloudflare 가 SNI 로 사이트를 고른다)
            SSLSocket ssl = (SSLSocket) ((SSLSocketFactory) SSLSocketFactory.getDefault()).createSocket(raw, host, port, true);
            ssl.startHandshake();
            // 감싼 소켓은 호스트 이름을 검사하지 않는다. 직접 검사한다(중간자 방지)
            if (!HttpsURLConnection.getDefaultHostnameVerifier().verify(host, ssl.getSession())) {
                ssl.close();
                throw new IOException("서버 인증서의 호스트 이름이 맞지 않음: " + host);
            }
            s = ssl;
        } else {
            s = new Socket();
            s.connect(new InetSocketAddress(host, port), timeoutMs);
        }
        s.setTcpNoDelay(true);
        s.setSoTimeout(0);
        socket = s;
        in = new DataInputStream(new BufferedInputStream(s.getInputStream(), 64 * 1024));
        out = s.getOutputStream();

        byte[] keyBytes = new byte[16];
        rnd.nextBytes(keyBytes);
        String key = Base64.getEncoder().encodeToString(keyBytes);
        String path = (uri.getRawPath() == null || uri.getRawPath().isEmpty()) ? "/" : uri.getRawPath();
        if (uri.getRawQuery() != null) path += "?" + uri.getRawQuery();
        String hostHeader = host + ((uri.getPort() > 0) ? ":" + uri.getPort() : "");
        String req = "GET " + path + " HTTP/1.1\r\n"
                + "Host: " + hostHeader + "\r\n"
                + "Upgrade: websocket\r\n"
                + "Connection: Upgrade\r\n"
                + "Sec-WebSocket-Key: " + key + "\r\n"
                + "Sec-WebSocket-Version: 13\r\n"
                + "Authorization: Bearer " + bearerToken + "\r\n"
                + "User-Agent: roundtable-worker\r\n"
                + "\r\n";
        out.write(req.getBytes(StandardCharsets.UTF_8));
        out.flush();

        String status = readLine(in);
        if (status == null || !status.contains(" 101")) {
            throw new IOException("핸드셰이크 거절: " + status);
        }
        String line;
        while ((line = readLine(in)) != null && !line.isEmpty()) {
            // 헤더는 확인하지 않는다(Sec-WebSocket-Accept 검증 생략: TLS 로 서버를 확인한다)
        }
    }

    private static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') b.write(c);
            if (b.size() > 8192) throw new IOException("헤더가 너무 김");
        }
        if (c == -1 && b.size() == 0) return null;
        return b.toString("UTF-8");
    }

    void sendText(String text) throws IOException {
        sendFrame(0x1, text.getBytes(StandardCharsets.UTF_8));
    }

    private synchronized void sendFrame(int opcode, byte[] payload) throws IOException {
        if (closed) throw new IOException("닫힌 연결");
        ByteArrayOutputStream f = new ByteArrayOutputStream(payload.length + 14);
        f.write(0x80 | opcode);
        int n = payload.length;
        if (n < 126) {
            f.write(0x80 | n);
        } else if (n < 65536) {
            f.write(0x80 | 126);
            f.write((n >>> 8) & 0xff);
            f.write(n & 0xff);
        } else {
            f.write(0x80 | 127);
            long ln = n;
            for (int i = 7; i >= 0; i--) f.write((int) ((ln >>> (8 * i)) & 0xff));
        }
        byte[] mask = new byte[4];
        rnd.nextBytes(mask);
        f.write(mask, 0, 4);
        byte[] masked = new byte[n];
        for (int i = 0; i < n; i++) masked[i] = (byte) (payload[i] ^ mask[i & 3]);
        f.write(masked, 0, n);
        out.write(f.toByteArray());
        out.flush();
    }

    /** 끊길 때까지 읽는다. 텍스트 메시지마다 handler 를 부른다. */
    void readLoop(Handler handler) throws IOException {
        ByteArrayOutputStream frag = null;
        while (!closed) {
            int b0 = in.readUnsignedByte();
            int b1 = in.readUnsignedByte();
            boolean fin = (b0 & 0x80) != 0;
            int op = b0 & 0x0f;
            boolean masked = (b1 & 0x80) != 0;
            long len = b1 & 0x7f;
            if (len == 126) len = in.readUnsignedShort();
            else if (len == 127) len = in.readLong();
            if (len > 64L * 1024 * 1024) throw new IOException("프레임이 너무 큼");
            byte[] mask = null;
            if (masked) {
                mask = new byte[4];
                in.readFully(mask);
            }
            byte[] data = new byte[(int) len];
            in.readFully(data);
            if (mask != null) for (int i = 0; i < data.length; i++) data[i] ^= mask[i & 3];

            switch (op) {
                case 0x0: // 이어지는 조각
                    if (frag == null) throw new IOException("잘못된 이어짐 프레임");
                    frag.write(data, 0, data.length);
                    if (fin) {
                        handler.onText(frag.toString("UTF-8"));
                        frag = null;
                    }
                    break;
                case 0x1:
                    if (fin) {
                        handler.onText(new String(data, StandardCharsets.UTF_8));
                    } else {
                        frag = new ByteArrayOutputStream();
                        frag.write(data, 0, data.length);
                    }
                    break;
                case 0x8:
                    if (data.length >= 2) closeCode = ((data[0] & 0xff) << 8) | (data[1] & 0xff);
                    try { sendFrame(0x8, new byte[0]); } catch (IOException ignored) {}
                    close();
                    throw new IOException(String.format(Locale.US, "서버가 닫음%s",
                            data.length >= 2 ? " (" + (((data[0] & 0xff) << 8) | (data[1] & 0xff)) + ")" : ""));
                case 0x9:
                    sendFrame(0xA, data);
                    break;
                default:
                    break; // pong·바이너리는 무시
            }
        }
    }

    /** 닫기. TLS 소켓은 닫을 때 네트워크를 쓰므로(close_notify) 메인 스레드에서 부르면 뒤 스레드로 넘긴다. */
    void close() {
        if (!closed) WLog.add("소켓 닫기: " + Thread.currentThread().getName());
        closed = true;
        final Socket s = socket;
        if (s == null) return;
        Runnable r = () -> {
            try { s.close(); } catch (IOException ignored) {}
        };
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) {
            new Thread(r, "ws-close").start();
        } else {
            r.run();
        }
    }
}
