package viddown.newpipe;

import org.schabi.newpipe.extractor.downloader.Downloader;
import org.schabi.newpipe.extractor.downloader.Request;
import org.schabi.newpipe.extractor.downloader.Response;
import org.schabi.newpipe.extractor.exceptions.ReCaptchaException;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.ProxySelector;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.zip.GZIPInputStream;

/**
 * The network layer NewPipe Extractor needs: it asks for a Downloader and
 * does all its page / API requests through it. Plain java.net.http, no extra
 * libraries.
 */
final class HttpDownloader extends Downloader {

    // java.net.http refuses to send these itself.
    private static final Set<String> RESTRICTED = Set.of(
            "connection", "content-length", "expect", "host", "upgrade");

    private final HttpClient client;
    private final CookieStore cookies;

    HttpDownloader(String proxy) throws IOException {
        this(proxy, null);
    }

    /** `cookieFile`: a Netscape cookies.txt whose cookies are sent to the sites they belong to. */
    HttpDownloader(String proxy, String cookieFile) throws IOException {
        this.cookies = CookieStore.load(cookieFile);
        HttpClient.Builder b = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NORMAL)
                .connectTimeout(Duration.ofSeconds(20));
        if (proxy != null && !proxy.isBlank()) {
            b.proxy(ProxySelector.of(parseProxy(proxy.trim())));
        }
        this.client = b.build();
    }

    /** "http://user:pw@host:port" -> host:port. java.net.http speaks HTTP proxies only. */
    private static InetSocketAddress parseProxy(String proxy) {
        String lower = proxy.toLowerCase(Locale.ROOT);
        if (lower.startsWith("socks")) {
            throw new IllegalArgumentException(
                    "The NewPipe engine supports HTTP proxies only, not SOCKS. "
                    + "Use an http:// proxy or switch the Download engine to yt-dlp.");
        }
        URI uri = URI.create(lower.contains("://") ? proxy : "http://" + proxy);
        if (uri.getHost() == null) {
            throw new IllegalArgumentException("Proxy address not understood: " + proxy);
        }
        int port = uri.getPort() > 0 ? uri.getPort() : 8080;
        return new InetSocketAddress(uri.getHost(), port);
    }

    @Override
    public Response execute(Request request) throws IOException, ReCaptchaException {
        HttpRequest.Builder rb = HttpRequest.newBuilder()
                .uri(URI.create(request.url()))
                .timeout(Duration.ofSeconds(30));

        URI target = URI.create(request.url());
        for (Map.Entry<String, List<String>> h : request.headers().entrySet()) {
            String name = h.getKey().toLowerCase(Locale.ROOT);
            if (RESTRICTED.contains(name)) {
                continue;
            }
            if (name.equals("cookie") && cookies.size() > 0) {
                continue;                       // merged with the stored cookies below
            }
            for (String v : h.getValue()) {
                rb.header(h.getKey(), v);
            }
        }
        if (cookies.size() > 0) {
            List<String> own = request.headers().entrySet().stream()
                    .filter(h -> h.getKey().equalsIgnoreCase("cookie"))
                    .flatMap(h -> h.getValue().stream())
                    .collect(java.util.stream.Collectors.toList());
            String merged = cookies.merge(target, String.join("; ", own));
            if (!merged.isEmpty()) {
                rb.header("Cookie", merged);
            }
        }

        byte[] data = request.dataToSend();
        HttpRequest.BodyPublisher body = (data == null || data.length == 0)
                ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofByteArray(data);
        rb.method(request.httpMethod(), body);

        HttpResponse<InputStream> resp;
        try {
            resp = client.send(rb.build(), HttpResponse.BodyHandlers.ofInputStream());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("Interrupted", e);
        }

        if (resp.statusCode() == 429) {
            throw new ReCaptchaException("reCaptcha Challenge requested", request.url());
        }

        String text;
        try (InputStream raw = resp.body()) {
            boolean gzip = resp.headers().firstValue("Content-Encoding")
                    .map(v -> v.toLowerCase(Locale.ROOT).contains("gzip")).orElse(false);
            try (InputStream in = gzip ? new GZIPInputStream(raw) : raw) {
                text = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            }
        }

        Map<String, List<String>> headers = new java.util.HashMap<>();
        resp.headers().map().forEach((k, v) -> headers.put(k, new ArrayList<>(v)));

        return new Response(resp.statusCode(), "", headers, text, resp.uri().toString());
    }
}
