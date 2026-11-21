package viddown.newpipe;

import org.schabi.newpipe.extractor.Image;
import org.schabi.newpipe.extractor.ListExtractor;
import org.schabi.newpipe.extractor.NewPipe;
import org.schabi.newpipe.extractor.Page;
import org.schabi.newpipe.extractor.StreamingService;
import org.schabi.newpipe.extractor.playlist.PlaylistInfo;
import org.schabi.newpipe.extractor.stream.StreamInfo;
import org.schabi.newpipe.extractor.stream.StreamInfoItem;

import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.PrintStream;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * VidDown's NewPipe helper.
 *
 *   java -jar newpipe-helper.jar info <url> [--proxy URL] [--limit N] [--noplaylist] [--cookies FILE]
 *
 * --noplaylist: for a video inside a playlist (watch?v=..&list=..) read just the video.
 * --cookies FILE: a Netscape cookies.txt; its cookies are sent with the requests to the sites they belong to.
 *
 * Prints ONE JSON object on stdout and exits 0, or prints {"error": ...} and
 * exits 2. Anything else the libraries print is sent to stderr so stdout stays
 * clean. core/newpipe_engine.py turns the JSON into yt-dlp-style info.
 *
 * The extractor's getters for the optional details (codec, fps, audio track
 * type ...) are read through reflection (see call()): a getter a newer or older
 * NewPipe release lacks then only leaves that one detail empty instead of
 * breaking the whole helper.
 */
public final class Main {

    private static final int DEFAULT_LIMIT = 200;

    public static void main(String[] args) {
        PrintStream stdout = new PrintStream(new FileOutputStream(FileDescriptor.out), false, StandardCharsets.UTF_8);
        System.setOut(System.err);                 // stray prints must not corrupt the JSON
        int code;
        Map<String, Object> result;
        try {
            result = run(args);
            code = 0;
        } catch (Throwable t) {
            result = new LinkedHashMap<>();
            result.put("error", t.getMessage() == null ? t.toString() : t.getMessage());
            result.put("kind", t.getClass().getSimpleName());
            code = 2;
        }
        StringBuilder sb = new StringBuilder();
        json(result, sb);
        stdout.println(sb);
        stdout.flush();
        System.exit(code);
    }

    // ------------------------------------------------------------ commands --
    private static Map<String, Object> run(String[] args) throws Exception {
        if (args.length < 2 || !"info".equals(args[0])) {
            throw new IllegalArgumentException("usage: info <url> [--proxy URL] [--limit N] [--noplaylist] [--cookies FILE]");
        }
        String url = args[1];
        String proxy = null;
        int limit = DEFAULT_LIMIT;
        boolean noPlaylist = false;
        String cookies = null;
        for (int i = 2; i < args.length; i++) {
            switch (args[i]) {
                case "--cookies": cookies = i + 1 < args.length ? args[++i] : null; break;
                case "--proxy": proxy = i + 1 < args.length ? args[++i] : null; break;
                case "--limit": limit = i + 1 < args.length ? Integer.parseInt(args[++i]) : limit; break;
                case "--noplaylist": noPlaylist = true; break;
                default: break;
            }
        }

        NewPipe.init(new HttpDownloader(proxy, cookies));
        StreamingService service = NewPipe.getServiceByUrl(url);
        // A direct call, not call(): this one decides playlist or video. Read through reflection
        // it silently came back null whenever reflection was restricted (a native build), and a
        // playlist link was then handled as a single video and failed.
        String linkType = String.valueOf(service.getLinkTypeByUrl(url));

        // --noplaylist only turns a "video inside a playlist" link (watch?v=..&list=..) into
        // that one video; a plain playlist link is always read as a playlist.
        boolean videoInsideList = noPlaylist && url.matches(".*[?&]v=[^&]+.*");
        if ("PLAYLIST".equals(linkType) && !videoInsideList) {
            return playlist(service, url, limit);
        }
        if ("CHANNEL".equals(linkType)) {
            throw new IllegalArgumentException(
                    "The NewPipe engine does not read channel pages yet. VidDown lists a channel "
                    + "with yt-dlp instead and downloads each video with NewPipe.");
        }
        return stream(url);
    }

    private static Map<String, Object> stream(String url) throws Exception {
        StreamInfo info = StreamInfo.getInfo(url);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("kind", "stream");
        out.put("id", info.getId());
        out.put("url", info.getUrl());
        out.put("original_url", info.getOriginalUrl());
        out.put("title", info.getName());
        out.put("uploader", info.getUploaderName());
        out.put("uploader_url", info.getUploaderUrl());
        out.put("duration", info.getDuration());
        out.put("view_count", info.getViewCount());
        out.put("like_count", call(info, "getLikeCount"));
        out.put("age_limit", call(info, "getAgeLimit"));
        out.put("category", call(info, "getCategory"));
        out.put("tags", call(info, "getTags"));
        Object desc = call(info, "getDescription");
        out.put("description", desc == null ? null : call(desc, "getContent"));
        out.put("stream_type", String.valueOf(info.getStreamType()));
        out.put("hls_url", call(info, "getHlsUrl"));
        out.put("dash_url", call(info, "getDashMpdUrl"));
        out.put("thumbnails", images(info.getThumbnails()));

        List<Object> videos = new ArrayList<>();
        addVideoStreams(videos, call(info, "getVideoStreams"), false);
        addVideoStreams(videos, call(info, "getVideoOnlyStreams"), true);
        out.put("video_streams", videos);

        List<Object> audios = new ArrayList<>();
        Object audioList = call(info, "getAudioStreams");
        if (audioList instanceof List) {
            for (Object a : (List<?>) audioList) {
                Map<String, Object> m = baseStream(a);
                m.put("average_bitrate", call(a, "getAverageBitrate"));
                m.put("bitrate", call(a, "getBitrate"));
                m.put("codec", call(a, "getCodec"));
                m.put("audio_locale", String.valueOf(call(a, "getAudioLocale")));
                m.put("track_id", call(a, "getAudioTrackId"));
                m.put("track_name", call(a, "getAudioTrackName"));
                m.put("track_type", String.valueOf(call(a, "getAudioTrackType")));
                addItagDetails(m, a);
                audios.add(m);
            }
        }
        out.put("audio_streams", audios);

        List<Object> subs = new ArrayList<>();
        Object subList = call(info, "getSubtitles");
        if (subList instanceof List) {
            for (Object s : (List<?>) subList) {
                Map<String, Object> m = baseStream(s);
                m.put("lang", call(s, "getLanguageTag"));
                m.put("name", call(s, "getDisplayLanguageName"));
                m.put("auto", call(s, "isAutoGenerated"));
                subs.add(m);
            }
        }
        out.put("subtitles", subs);
        return out;
    }

    private static void addVideoStreams(List<Object> into, Object list, boolean videoOnly) {
        if (!(list instanceof List)) {
            return;
        }
        for (Object v : (List<?>) list) {
            Map<String, Object> m = baseStream(v);
            m.put("video_only", videoOnly);
            m.put("height", call(v, "getHeight"));
            m.put("width", call(v, "getWidth"));
            m.put("fps", call(v, "getFps"));
            m.put("bitrate", call(v, "getBitrate"));
            m.put("codec", call(v, "getCodec"));
            m.put("resolution", call(v, "getResolution"));
            addItagDetails(m, v);
            into.add(m);
        }
    }

    /**
     * What YouTube's ItagItem knows about one stream: the exact length in bytes (only for adaptive
     * streams), the length in time, and the bitrate. Without these a quality list can only guess
     * sizes. Each getter is read separately, so one that is missing leaves just that value empty.
     */
    private static void addItagDetails(Map<String, Object> m, Object stream) {
        Object itag = call(stream, "getItagItem");
        if (itag == null) {
            return;
        }
        m.put("content_length", call(itag, "getContentLength"));
        m.put("approx_duration_ms", call(itag, "getApproxDurationMs"));
        m.put("itag_bitrate", call(itag, "getBitrate"));
        m.put("itag_average_bitrate", call(itag, "getAverageBitrate"));
    }

    /** Fields every kind of stream has: address, container, how it is delivered. */
    private static Map<String, Object> baseStream(Object s) {
        Map<String, Object> m = new LinkedHashMap<>();
        Object content = call(s, "getContent");
        if (content == null) {
            content = call(s, "getUrl");
        }
        m.put("url", content);
        m.put("is_url", call(s, "isUrl"));
        m.put("delivery", String.valueOf(call(s, "getDeliveryMethod")));
        Object fmt = call(s, "getFormat");
        m.put("ext", fmt == null ? null : call(fmt, "getSuffix"));
        m.put("mime", fmt == null ? null : call(fmt, "getMimeType"));
        return m;
    }

    private static Map<String, Object> playlist(StreamingService service, String url, int limit) throws Exception {
        PlaylistInfo pl = PlaylistInfo.getInfo(url);
        List<StreamInfoItem> items = new ArrayList<>(pl.getRelatedItems());
        Page next = pl.getNextPage();
        while (items.size() < limit && next != null) {
            ListExtractor.InfoItemsPage<StreamInfoItem> more = PlaylistInfo.getMoreItems(service, url, next);
            if (more.getItems().isEmpty()) {
                break;
            }
            items.addAll(more.getItems());
            next = more.getNextPage();
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("kind", "playlist");
        out.put("id", pl.getId());
        out.put("url", pl.getUrl());
        out.put("title", pl.getName());
        out.put("uploader", pl.getUploaderName());
        out.put("stream_count", pl.getStreamCount());
        out.put("truncated", items.size() >= limit && next != null);
        out.put("thumbnails", images(call(pl, "getThumbnails")));

        List<Object> entries = new ArrayList<>();
        for (StreamInfoItem it : items) {
            if (entries.size() >= limit) {
                break;
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("url", it.getUrl());
            m.put("title", it.getName());
            m.put("duration", it.getDuration());
            m.put("uploader", it.getUploaderName());
            m.put("thumbnails", images(it.getThumbnails()));
            entries.add(m);
        }
        out.put("entries", entries);
        return out;
    }

    // ------------------------------------------------------------- helpers --
    private static List<Object> images(Object list) {
        List<Object> out = new ArrayList<>();
        if (list instanceof List) {
            for (Object o : (List<?>) list) {
                if (o instanceof Image) {
                    Image img = (Image) o;
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("url", img.getUrl());
                    m.put("width", img.getWidth());
                    m.put("height", img.getHeight());
                    out.add(m);
                }
            }
        }
        return out;
    }

    /** Calls a public no-argument or one-String-argument method; null when it is missing or fails. */
    private static Object call(Object target, String name, String... arg) {
        if (target == null) {
            return null;
        }
        try {
            Method m = arg.length == 0
                    ? target.getClass().getMethod(name)
                    : target.getClass().getMethod(name, String.class);
            return arg.length == 0 ? m.invoke(target) : m.invoke(target, (Object[]) arg);
        } catch (Throwable t) {
            return null;
        }
    }

    // -------------------------------------------------------------- JSON out --
    private static void json(Object o, StringBuilder sb) {
        if (o == null) {
            sb.append("null");
        } else if (o instanceof String) {
            quote((String) o, sb);
        } else if (o instanceof Boolean || o instanceof Integer || o instanceof Long
                || o instanceof Short || o instanceof Byte) {
            sb.append(o);
        } else if (o instanceof Number) {
            double d = ((Number) o).doubleValue();
            sb.append(Double.isNaN(d) || Double.isInfinite(d) ? "null" : o.toString());
        } else if (o instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) o).entrySet()) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                quote(String.valueOf(e.getKey()), sb);
                sb.append(':');
                json(e.getValue(), sb);
            }
            sb.append('}');
        } else if (o instanceof Collection) {
            sb.append('[');
            boolean first = true;
            for (Object e : (Collection<?>) o) {
                if (!first) {
                    sb.append(',');
                }
                first = false;
                json(e, sb);
            }
            sb.append(']');
        } else {
            quote(o.toString(), sb);
        }
    }

    private static void quote(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
    }

    private Main() { }
}
