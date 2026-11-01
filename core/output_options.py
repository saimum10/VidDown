"""
core/output_options.py
What the finished file looks like: the audio format, the video container, and
whether the thumbnail and metadata are embedded.

Pure logic on purpose (standard library only — no yt-dlp, no Tkinter) so it can
be unit-tested without either. core/downloader.py turns the result into yt-dlp
options; the download page and Settings only use the tables below.

Audio
-----
  mp3       re-encoded, bitrate chosen by the person (128/192/256/320 kbps)
  m4a       AAC in an .m4a file — the original stream is copied when the source
            is AAC, converted otherwise
  opus      Opus in an .opus file — copied (no quality loss) when the source is Opus
  original  the best stream exactly as the site serves it: never re-encoded,
            only moved into a plain audio file (.m4a for AAC, .opus for Opus)
  flac/wav  lossless containers (the source is lossy, so this only makes the
            file bigger — useful for editors that refuse lossy input)

Video containers
----------------
  mp4   default; plays almost everywhere
  mkv   holds every codec, several subtitle tracks, chapters
  webm  VP9/AV1 video + Opus audio only
  mov   H.264 video + AAC audio (Apple / video editors)
  avi   H.264 video + AAC audio (old players)
Streams are always copied into the container, never re-encoded, so a container
only offers the codecs it can really hold (see CONTAINERS).
"""

# ------------------------------------------------------------------ audio --
DEFAULT_AUDIO_FORMAT = "mp3"
DEFAULT_AUDIO_BITRATE = 192
MP3_BITRATES = (128, 192, 256, 320)

# key -> table row. "codec" is what yt-dlp's FFmpegExtractAudio is asked for
# ("best" = keep whatever the stream is). "selector" picks the source stream so
# that the cheap path (a straight copy) is taken whenever the site offers it.
AUDIO_FORMATS = {
    "mp3": {
        "label": "MP3", "codec": "mp3", "ext": "mp3",
        "selector": "bestaudio/best",
        "note": "Works on every device. Converted from the source, so it is slightly lossy.",
    },
    "m4a": {
        "label": "M4A", "codec": "m4a", "ext": "m4a",
        "selector": "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best",
        "note": "AAC. The original audio is copied as it is when the site serves AAC.",
    },
    "opus": {
        "label": "Opus", "codec": "opus", "ext": "opus",
        "selector": "bestaudio[acodec=opus]/bestaudio[ext=webm]/bestaudio/best",
        "note": "Best quality for its size. Original audio is copied when the site serves Opus.",
    },
    "original": {
        "label": "Original", "codec": "best", "ext": None,
        "selector": "bestaudio/best",
        "note": "The audio exactly as the site serves it — no re-encoding, no quality loss.",
    },
    "flac": {
        "label": "FLAC", "codec": "flac", "ext": "flac",
        "selector": "bestaudio/best",
        "note": "Lossless container. The source is lossy, so the file only gets bigger.",
    },
    "wav": {
        "label": "WAV", "codec": "wav", "ext": "wav",
        "selector": "bestaudio/best",
        "note": "Uncompressed. Very large; tags and cover art cannot be stored.",
    },
}
AUDIO_FORMAT_ORDER = ("mp3", "m4a", "opus", "original", "flac", "wav")

# Bytes per second of audio, for the size shown before downloading.
_FLAC_BPS = 900_000 / 8      # lossy source decoded to lossless: roughly 0.9 Mbit/s
_WAV_BPS = 192_000           # 16-bit stereo at 48 kHz


def normalize_audio_format(value):
    value = (value or "").strip().lower()
    return value if value in AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT


def normalize_audio_bitrate(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_AUDIO_BITRATE
    return value if value in MP3_BITRATES else DEFAULT_AUDIO_BITRATE


def audio_label(fmt, bitrate=None):
    """'Audio Only (MP3 192 kbps)', 'Audio Only (Opus)' …"""
    fmt = normalize_audio_format(fmt)
    name = AUDIO_FORMATS[fmt]["label"]
    if fmt == "mp3":
        name += f" {normalize_audio_bitrate(bitrate)} kbps"
    return f"Audio Only ({name})"


def audio_size_bytes(fmt, bitrate, duration, source_bytes=None):
    """Expected size of the finished audio file, or None when unknown.
    mp3 / flac / wav follow the duration; m4a / opus / original are copies of
    the source stream when possible, so they follow its size."""
    fmt = normalize_audio_format(fmt)
    if fmt == "mp3":
        return int(duration * normalize_audio_bitrate(bitrate) * 1000 / 8) if duration else None
    if fmt == "flac":
        return int(duration * _FLAC_BPS) if duration else None
    if fmt == "wav":
        return int(duration * _WAV_BPS) if duration else None
    return int(source_bytes) if source_bytes else None


def audio_ytdlp_postprocessor(fmt, bitrate=None):
    """The FFmpegExtractAudio entry for yt-dlp's `postprocessors` list."""
    fmt = normalize_audio_format(fmt)
    pp = {"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_FORMATS[fmt]["codec"]}
    if fmt == "mp3":
        pp["preferredquality"] = str(normalize_audio_bitrate(bitrate))
    return pp


# ------------------------------------------------------------------ video --
DEFAULT_CONTAINER = "mp4"
CONTAINER_ORDER = ("mp4", "mkv", "webm", "mov", "avi")

# key -> label, the video codec families it can hold (None = any), the audio
# codecs to prefer so nothing needs re-encoding (None = no preference).
CONTAINERS = {
    "mp4":  {"label": "MP4",  "codecs": None, "audio": None,
             "note": "Plays on almost every device."},
    "mkv":  {"label": "MKV",  "codecs": None, "audio": None,
             "note": "Holds any codec, several subtitle tracks and chapters. Some TVs and phones will not open it."},
    "webm": {"label": "WebM", "codecs": ("VP9", "AV1"), "audio": r"^(opus|vorbis)",
             "note": "VP9 or AV1 video with Opus audio. Made for browsers; other players vary."},
    "mov":  {"label": "MOV",  "codecs": ("H.264",), "audio": r"^mp4a",
             "note": "H.264 with AAC audio. For Apple devices and video editors."},
    "avi":  {"label": "AVI",  "codecs": ("H.264",), "audio": r"^mp4a",
             "note": "H.264 with AAC audio, for old players. Subtitles cannot be embedded."},
}

# yt-dlp can only put subtitles into these (it skips the rest with a warning).
_SUBTITLE_CONTAINERS = ("mp4", "mkv", "webm", "mov")


def normalize_container(value):
    value = (value or "").strip().lower()
    return value if value in CONTAINERS else DEFAULT_CONTAINER


def container_allows_codec(container, family):
    """Can `container` hold video of codec family `family` (a name from
    downloader.CODEC_FAMILIES)? Unknown family: only the unrestricted ones."""
    allowed = CONTAINERS[normalize_container(container)]["codecs"]
    return allowed is None or family in allowed


def container_choices(codecs_present, chosen_codec="Auto"):
    """[(key, label, enabled)] for the container picker.

    codecs_present: the codec families the selected quality offers.
    chosen_codec:   "Auto" or the family the person picked — a container that
                    cannot hold that family is disabled."""
    present = set(codecs_present or ())
    out = []
    for key in CONTAINER_ORDER:
        info = CONTAINERS[key]
        allowed = info["codecs"]
        if allowed is None:
            ok = True
        elif chosen_codec and chosen_codec != "Auto":
            ok = chosen_codec in allowed
        else:
            ok = bool(present & set(allowed))
        out.append((key, info["label"], ok))
    return out


def container_supports_subtitles(container):
    return normalize_container(container) in _SUBTITLE_CONTAINERS


def subtitle_format_for(container):
    """WebM only takes WebVTT; the others take SRT (mov/mp4 turn it into mov_text)."""
    return "vtt" if normalize_container(container) == "webm" else "srt"


# ---------------------------------------------------------------- embedding --
# Cover art can be written into these files. (WebM, WAV and AVI have no place
# for a picture.) Metadata (title, artist, date, description, chapters) can go
# into every format, so there is no table for it.
THUMBNAIL_EXTS = ("mp3", "m4a", "mp4", "m4v", "mov", "mkv", "mka", "opus", "ogg", "flac")


def thumbnail_supported(ext):
    return (ext or "").lower() in THUMBNAIL_EXTS


def thumbnail_supported_for(quality):
    """Can the file this quality produces carry a cover picture?"""
    quality = quality or {}
    if quality.get("audio_only"):
        fmt = normalize_audio_format(quality.get("audio_format"))
        ext = AUDIO_FORMATS[fmt]["ext"]
        return True if ext is None else thumbnail_supported(ext)   # "original" is m4a or opus: both fine
    container = quality.get("container")
    return thumbnail_supported(normalize_container(container) if container else DEFAULT_CONTAINER)


def embed_note(quality):
    """Why the cover cannot be embedded for this quality ('' when it can)."""
    if thumbnail_supported_for(quality):
        return ""
    if (quality or {}).get("audio_only"):
        return "WAV files cannot hold a cover picture."
    container = normalize_container((quality or {}).get("container")).upper()
    return f"{container} files cannot hold a cover picture."


# ------------------------------------------------------------ the full plan --
def plan_postprocessing(quality, options):
    """Everything yt-dlp has to be told so the file comes out as chosen.

    Returns a dict:
      merge_output_format  container for merged video+audio (None: not video)
      postprocessors       yt-dlp postprocessor dicts, in the order they must run
      writethumbnail       download the thumbnail so it can be embedded
      embed_thumbnail      add the cover-embedding step (done by core/cover_embed.py,
                           which also copes with Opus/FLAC and never fails a download)
      subtitles            subtitle languages that will really be embedded
      subtitle_format      'srt' / 'vtt'
      notes                short human-readable remarks (shown to the person)
    """
    quality = quality or {}
    options = options or {}
    audio_only = bool(quality.get("audio_only"))
    notes = []
    pps = []

    want_thumb = bool(options.get("embed_thumbnail"))
    want_meta = bool(options.get("embed_metadata"))
    plan = {
        "merge_output_format": None, "postprocessors": pps, "writethumbnail": False,
        "embed_thumbnail": False, "subtitles": [], "subtitle_format": "srt", "notes": notes,
    }

    if audio_only:
        fmt = normalize_audio_format(quality.get("audio_format"))
        pps.append(audio_ytdlp_postprocessor(fmt, quality.get("audio_bitrate")))
    else:
        container = normalize_container(quality.get("container")) if quality.get("container") else DEFAULT_CONTAINER
        plan["merge_output_format"] = container
        if container != DEFAULT_CONTAINER:
            # A single-file download (no merge) keeps its own container unless told otherwise.
            pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": container})

        langs = list(options.get("subtitles") or [])
        if langs and container_supports_subtitles(container):
            sub_fmt = subtitle_format_for(container)
            plan["subtitles"], plan["subtitle_format"] = langs, sub_fmt
            pps.append({"key": "FFmpegSubtitlesConvertor", "format": sub_fmt})
            pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
        elif langs:
            notes.append(f"{container.upper()} cannot hold subtitles — they were not added.")

    if want_meta:
        pps.append({"key": "FFmpegMetadata", "add_chapters": True,
                    "add_metadata": True, "add_infojson": False})

    if want_thumb:
        if thumbnail_supported_for(quality):
            plan["writethumbnail"] = True
            plan["embed_thumbnail"] = True
        else:
            notes.append(embed_note(quality) + " The cover was not embedded.")
    return plan
