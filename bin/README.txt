Put these static binaries (amd64) here when packaging:

  ffmpeg
  ffprobe
  aria2c

If they are here, find_binary() in core/downloader.py uses them instead of
searching the system, so users do not have to install them separately.

Each file needs the executable permission:
    chmod +x bin/ffmpeg bin/ffprobe bin/aria2c

Optional, for the NewPipe download engine:

  newpipe/viddown-newpipe[.exe]   the NATIVE helper - no Java needed. Preferred.
  newpipe/cacert.pem              its CA certificates (the helper finds this itself)
  newpipe/native-helper-info/     BUILD_INFO.json, THIRD_PARTY_NOTICES.md, licences (ship them:
                                  NewPipe Extractor is GPL-3.0-or-later)
  newpipe/native-helper.json      which release these files came from

  IMPORTED AUTOMATICALLY from https://github.com/saimumhabib/viddown-native-bundle:
  build_deb.sh and build_windows.ps1 run tools/fetch_newpipe_helper.py, which reads the
  rolling "index" release (components.json), downloads the newest newpipe-v<version> zip for
  the platform, checks its SHA-256 against the release's SHA256SUMS.txt and every file in it
  against the zip's own SHA256SUMS, and installs only the files listed above. If it is already
  in bin/newpipe the build keeps it. If the import fails the build goes on without the helper.

  By hand:  python tools/fetch_newpipe_helper.py [--platform linux-x86_64|windows-x86_64]
                                                 [--tag newpipe-v0.27.1] [--refresh]
  Build switches (environment): VIDDOWN_NEWPIPE_SKIP=1  VIDDOWN_NEWPIPE_TAG=newpipe-v0.27.1
                                VIDDOWN_NEWPIPE_REFRESH=1  VIDDOWN_NATIVE_REPO=owner/name
  build_deb.sh / build_windows.ps1 bundle the whole bin/newpipe folder when
  viddown-newpipe is in it.

Fallback, the older Java helper (see newpipe_helper/README.md):

  newpipe/newpipe-helper.jar    the helper program (built with "gradle jar")
  jre/                          a small Java runtime made with jlink (otherwise
                                the computer's own Java 17+ is used)

If both are there the native helper is used; the Java one only steps in when the
native one cannot start or gives no answer.
