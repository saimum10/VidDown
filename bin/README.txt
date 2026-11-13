Put these static binaries (amd64) here when packaging:

  ffmpeg
  ffprobe
  aria2c

If they are here, find_binary() in core/downloader.py uses them instead of
searching the system, so users do not have to install them separately.

Each file needs the executable permission:
    chmod +x bin/ffmpeg bin/ffprobe bin/aria2c
