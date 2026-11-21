# NewPipe helper

A small command-line program that asks the [NewPipe Extractor](https://github.com/TeamNewPipe/NewPipeExtractor)
library about a link and prints the answer as JSON. VidDown's NewPipe engine
(`core/newpipe_engine.py`) runs it; it is only used when
**Settings → Download engine → NewPipe** is selected.

NewPipe Extractor finds the streams (video, audio, subtitles, playlist entries).
The files are then downloaded, merged and converted by VidDown's normal pipeline.

## Build

Needs JDK 17 or newer and Gradle 8+:

```bash
cd newpipe_helper
gradle jar            # -> build/libs/newpipe-helper.jar
```

Update the extractor by changing the version in `build.gradle`
(releases: https://github.com/TeamNewPipe/NewPipeExtractor/releases). YouTube changes
often, so keep it current.

## Run it by hand

```bash
java -jar build/libs/newpipe-helper.jar info "https://www.youtube.com/watch?v=VIDEO_ID"
java -jar build/libs/newpipe-helper.jar info "https://www.youtube.com/playlist?list=ID" --limit 50
```

Options: `--proxy http://host:port` (HTTP proxies only), `--limit N` (playlist entries, default 200),
`--noplaylist` (read just the video of a `watch?v=..&list=..` link),
`--cookies FILE` (a Netscape `cookies.txt`; its cookies are sent with the requests to the sites they
belong to - VidDown writes a small private file with just the cookies of the link's site for one run
and deletes it afterwards).

The answer for a YouTube stream also carries `content_length` (exact bytes, adaptive streams only),
`approx_duration_ms` and `itag_bitrate`/`itag_average_bitrate`, which VidDown uses to show a real size per
quality. If you build the native helper in another repository, port these three changes there too:
the `--cookies` option (`CookieStore.java` + `HttpDownloader.java`), the `addItagDetails()` fields, and the
direct `service.getLinkTypeByUrl(url)` call (a reflection call there returns null in a native image, which
turns every playlist link into a "video" and makes it fail).

## Where VidDown looks for it

1. `VIDDOWN_NEWPIPE_JAR` (environment variable, for development)
2. `bin/newpipe/newpipe-helper.jar` inside the installed package
3. `newpipe_helper/build/libs/newpipe-helper.jar` (running from source after `gradle jar`)

Java: `VIDDOWN_JAVA`, then `bin/jre/bin/java` (a bundled runtime), then `JAVA_HOME`, then `PATH`.

`build_deb.sh` and `build_windows.ps1` add the jar (and `bin/jre/` if you made one) to the package
when the jar has been built. To ship a small private runtime:

```bash
jlink --add-modules java.base,java.net.http,java.logging,java.xml,jdk.unsupported,java.naming \
      --strip-debug --no-header-files --no-man-pages --output ../bin/jre
```

If the build complains about a missing module at run time, add it to `--add-modules`.

## License

NewPipe Extractor is licensed **GPL-3.0-or-later**. VidDown itself stays MIT, because the helper runs as a
separate program, but the jar you distribute must come with the GPL text and a way to get its source
(see `THIRD_PARTY_NOTICES.md`).
