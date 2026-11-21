package viddown.newpipe;

import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Cookies for the requests the extractor makes, read from a Netscape cookies.txt
 * file (the file VidDown writes for the helper with --cookies FILE).
 *
 * The extractor sets a Cookie header of its own on some requests (YouTube's
 * consent cookie). {@link #merge} adds the matching cookies from the file to
 * whatever is already there; when both have a cookie of the same name the one
 * from the file wins, so a real signed-in SOCS cookie replaces the generic one.
 */
final class CookieStore {

    private static final class Entry {
        final String domain;        // without a leading dot, lower case
        final boolean subdomains;
        final String path;
        final boolean secure;
        final long expires;         // seconds since epoch, 0 = session cookie
        final String name;
        final String value;

        Entry(String domain, boolean subdomains, String path, boolean secure,
              long expires, String name, String value) {
            this.domain = domain;
            this.subdomains = subdomains;
            this.path = path;
            this.secure = secure;
            this.expires = expires;
            this.name = name;
            this.value = value;
        }
    }

    private final List<Entry> entries = new ArrayList<>();

    static CookieStore empty() {
        return new CookieStore();
    }

    /** Reads a Netscape cookies.txt. Lines that do not parse are skipped. */
    static CookieStore load(String file) throws IOException {
        CookieStore store = new CookieStore();
        if (file == null || file.isBlank()) {
            return store;
        }
        for (String line : Files.readAllLines(Path.of(file), StandardCharsets.UTF_8)) {
            store.parseLine(line);
        }
        return store;
    }

    int size() {
        return entries.size();
    }

    private void parseLine(String raw) {
        String line = raw;
        if (line.endsWith("\r")) {
            line = line.substring(0, line.length() - 1);
        }
        if (line.startsWith("#HttpOnly_")) {          // curl / yt-dlp mark HttpOnly cookies like this
            line = line.substring("#HttpOnly_".length());
        } else if (line.isBlank() || line.startsWith("#")) {
            return;
        }
        String[] f = line.split("\t", -1);
        if (f.length < 7 || f[5].isEmpty()) {
            return;
        }
        String domain = f[0].trim().toLowerCase(Locale.ROOT);
        boolean dotted = domain.startsWith(".");
        if (dotted) {
            domain = domain.substring(1);
        }
        if (domain.isEmpty()) {
            return;
        }
        long expires;
        try {
            expires = Long.parseLong(f[4].trim());
        } catch (NumberFormatException e) {
            expires = 0;
        }
        boolean subdomains = dotted || "TRUE".equalsIgnoreCase(f[1].trim());
        String path = f[2].isEmpty() ? "/" : f[2];
        entries.add(new Entry(domain, subdomains, path, "TRUE".equalsIgnoreCase(f[3].trim()),
                expires, f[5], f[6]));
    }

    /** "a=1; b=2" for the cookies that apply to this address, or "" when none do. */
    String headerFor(URI uri) {
        return headerFor(uri, System.currentTimeMillis() / 1000L);
    }

    String headerFor(URI uri, long nowSeconds) {
        Map<String, String> picked = pick(uri, nowSeconds);
        return join(picked);
    }

    /** `existing` is the request's current Cookie header (may be null/empty). Returns the merged header. */
    String merge(URI uri, String existing) {
        Map<String, String> picked = pick(uri, System.currentTimeMillis() / 1000L);
        if (picked.isEmpty()) {
            return existing == null ? "" : existing;
        }
        Map<String, String> merged = new LinkedHashMap<>();
        if (existing != null) {
            for (String part : existing.split(";")) {
                String p = part.trim();
                int eq = p.indexOf('=');
                if (eq > 0) {
                    merged.put(p.substring(0, eq), p.substring(eq + 1));
                }
            }
        }
        merged.putAll(picked);                        // the file's cookies win on a name clash
        return join(merged);
    }

    private Map<String, String> pick(URI uri, long nowSeconds) {
        Map<String, String> out = new LinkedHashMap<>();
        if (entries.isEmpty() || uri == null || uri.getHost() == null) {
            return out;
        }
        String host = uri.getHost().toLowerCase(Locale.ROOT);
        String path = uri.getRawPath() == null || uri.getRawPath().isEmpty() ? "/" : uri.getRawPath();
        boolean https = "https".equalsIgnoreCase(uri.getScheme());
        // longer paths first: the browser sends the most specific cookie of a name first
        List<Entry> hits = new ArrayList<>();
        for (Entry e : entries) {
            if (e.secure && !https) {
                continue;
            }
            if (e.expires > 0 && e.expires < nowSeconds) {
                continue;
            }
            boolean hostOk = host.equals(e.domain) || (e.subdomains && host.endsWith("." + e.domain));
            if (!hostOk || !pathMatches(path, e.path)) {
                continue;
            }
            hits.add(e);
        }
        hits.sort((a, b) -> Integer.compare(b.path.length(), a.path.length()));
        for (Entry e : hits) {
            out.putIfAbsent(e.name, e.value);
        }
        return out;
    }

    private static boolean pathMatches(String requestPath, String cookiePath) {
        if (requestPath.equals(cookiePath)) {
            return true;
        }
        if (!requestPath.startsWith(cookiePath)) {
            return false;
        }
        return cookiePath.endsWith("/") || requestPath.charAt(cookiePath.length()) == '/';
    }

    private static String join(Map<String, String> cookies) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> c : cookies.entrySet()) {
            if (sb.length() > 0) {
                sb.append("; ");
            }
            sb.append(c.getKey()).append('=').append(c.getValue());
        }
        return sb.toString();
    }

    private CookieStore() { }
}
