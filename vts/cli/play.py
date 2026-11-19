# SPDX-License-Identifier: Apache-2.0
"""play, download, subtitles"""

import sys

from vts import channel as ch
from vts import config, library, log, player, update, video
from vts.cli import EXIT_FAIL, EXIT_OK, Usage, out, parse
from vts.rules import Item
from vts.youtube import Client, YouTubeError


def parse_time(s):
    """ "1:02:03", "2:03" or "123" in seconds; None if it isn't a time"""
    parts = s.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    vals = []
    for p in parts:
        if not p or not (p.isascii() and p.isdigit()) or int(p) > 1000000:
            return None
        vals.append(int(p))
    total = 0
    for v in vals:
        total = total * 60 + v
    return total


def quality_arg(s):
    try:
        return config.check_quality(s)
    except config.BadValue:
        return None


def collect(cfg, refs):
    """
    the videos of @refs: video ids or links, and playlists (every video in
    them), fetched from YouTube only for playlists; None after an error
    """
    items = []
    yt = None
    for ref in refs:
        ident = video.ref(ref)
        if ident:
            items.append(Item("video", id=ident))
            continue
        pid = ch.playlist_ref(ref)
        if not pid:
            log.err(f"not a video or playlist: {ref}")
            return None
        if not yt:
            yt = Client(cfg)
        try:
            b = ch.playlist(yt, pid)
            while ch.more(yt, "playlist", b):
                pass
        except YouTubeError as e:
            log.err(str(e))
            return None
        items += [it for it in b.page.items if it.kind in ("video", "short")]
    return items


def sound_arg(cmd, v):
    """high, medium or low, else the usage"""
    if v not in config.SOUND_QUALITIES:
        print(f"vts {cmd}: --sound-quality: high, medium or low",
              file=sys.stderr)
        raise Usage()
    return v


PLAY_USAGE = """\
Usage: vts play [OPTION]... VIDEO|PLAYLIST...
Play videos with mpv, one after the other, from where you
stopped last time; where you stop goes into the history.

  -q, --quality Q    best, 2160, 1440, 1080, 720, 480, 360, 240, 144
  -a, --audio        the sound only
      --sound-quality S
                     the sound only, in S: high, medium
                     (about 128 kbit/s) or low (64)
  -s, --speed X      playback speed (0.25 to 4)
      --subs LANGS   subtitles: fr,en
  -t, --terminal     draw the video in the terminal
      --from TIME    start the first video at TIME (1:23)
  -n, --no-resume    start from the beginning
  -h, --help         show this help
"""


def cmd_play(cfg, argv):
    opts, args = parse("play", argv, "q:as:tnh",
                       ["quality=", "audio", "sound-quality=", "speed=",
                        "subs=", "terminal", "from=", "no-resume", "help"])
    o = player.PlayOptions(cfg)
    for k, v in opts:
        if k in ("-h", "--help"):
            sys.stdout.write(PLAY_USAGE)
            return EXIT_OK
        if k in ("-q", "--quality"):
            o.quality = quality_arg(v)
            if o.quality is None:
                print("vts play: --quality: best, 2160 ... 144",
                      file=sys.stderr)
                raise Usage()
        elif k in ("-a", "--audio"):
            o.quality = config.QUALITY_AUDIO
        elif k == "--sound-quality":
            o.sound = sound_arg("play", v)
            o.quality = config.QUALITY_AUDIO
        elif k in ("-s", "--speed"):
            try:
                o.speed = float(v)
            except ValueError:
                o.speed = 0
            if not 0.25 <= o.speed <= 4:
                print("vts play: --speed: 0.25 to 4", file=sys.stderr)
                raise Usage()
        elif k == "--subs":
            o.subs = v
        elif k in ("-t", "--terminal"):
            o.terminal = True
        elif k == "--from":
            t = parse_time(v)
            if t is None:
                print("vts play: --from: a time like 1:23", file=sys.stderr)
                raise Usage()
            o.start = t
        elif k in ("-n", "--no-resume"):
            o.resume = False
    if not args:
        sys.stderr.write(PLAY_USAGE)
        raise Usage()
    items = collect(cfg, args)
    if items is None:
        return EXIT_FAIL
    # too old a yt-dlp: said first, in case it plays nothing
    problem = update.can_play(cfg.player, cfg.downloader)
    if problem:
        log.warn(problem)
    try:
        lib = library.Library()
    except (library.LibError, OSError) as e:
        log.warn(f"no history: {e}")
        lib = None
    try:
        p = player.Player(cfg.player, lib, items, o)
    except player.PlayError as e:
        log.err(str(e))
        if lib:
            lib.close()
        return EXIT_FAIL
    # Ctrl-C closes mpv, and the position is kept
    try:
        while p.poll(0.5):
            pass
    except KeyboardInterrupt:
        pass
    status, problem = p.finish()
    if lib:
        lib.close()
    if problem:
        log.err(problem)
        status = status or 1
    return EXIT_OK if status == 0 else EXIT_FAIL


DOWNLOAD_USAGE = """\
Usage: vts download [OPTION]... VIDEO|PLAYLIST...
Download videos with yt-dlp.

  -q, --quality Q  best, 2160, 1440, 1080, 720, ...
  -a, --audio      the sound only, as YouTube has it
      --audio-format F
                   the sound only, as mp3, m4a, opus,
                   flac or wav (needs ffmpeg)
      --sound-quality S
                   the sound only, in S: high, medium
                   (about 128 kbit/s) or low (64)
  -d, --dir DIR    where to put them (by default the
                   downloads setting, or here)
  -h, --help       show this help
"""


def cmd_download(cfg, argv):
    try:
        import getopt
        opts, args = getopt.gnu_getopt(argv, "q:ad:h",
                                       ["quality=", "audio", "audio-format=",
                                        "sound-quality=", "dir=", "help"])
    except getopt.GetoptError as e:
        print(f"vts download: {e.msg}", file=sys.stderr)
        print("Try 'vts download --help'.", file=sys.stderr)
        raise Usage() from None
    quality = cfg.quality
    audio_format = None
    sound = cfg.sound_quality
    folder = None
    for k, v in opts:
        if k in ("-h", "--help"):
            sys.stdout.write(DOWNLOAD_USAGE)
            return EXIT_OK
        if k in ("-q", "--quality"):
            quality = quality_arg(v)
            if quality is None:
                print("vts download: --quality: best, 2160 ... 144",
                      file=sys.stderr)
                raise Usage()
        elif k in ("-a", "--audio"):
            quality = config.QUALITY_AUDIO
        elif k == "--audio-format":
            if v not in player.AUDIO_FORMATS:
                print("vts download: --audio-format: mp3, m4a, opus, flac or "
                      "wav", file=sys.stderr)
                raise Usage()
            quality = config.QUALITY_AUDIO
            audio_format = v
        elif k == "--sound-quality":
            sound = sound_arg("download", v)
            quality = config.QUALITY_AUDIO
        elif k in ("-d", "--dir"):
            folder = v
    if not args:
        print("vts download: which videos?", file=sys.stderr)
        raise Usage()
    # without -d: the setting's folder, if there is one
    if folder is None:
        folder = "."
        if cfg.downloads:
            try:
                folder = cfg.downloads_dir()
            except OSError:
                folder = None
            if not folder:
                log.err(f"downloads = {cfg.downloads}: the folder can't be "
                        "made")
                return EXIT_FAIL
    items = collect(cfg, args)
    if items is None:
        return EXIT_FAIL
    try:
        status = player.download(cfg.downloader, folder, items, quality,
                                 audio_format, sound)
    except FileNotFoundError:
        log.err(f"{cfg.downloader} is not installed")
        return EXIT_FAIL
    except OSError as e:
        log.err(f"download: {e.strerror}")
        return EXIT_FAIL
    return EXIT_OK if status == 0 else EXIT_FAIL


def cmd_subtitles(cfg, argv):
    if argv in (["-h"], ["--help"]):
        out("Usage: vts subtitles VIDEO\n"
            "The subtitles VIDEO has: their code (for vts play --subs) and "
            "name.")
        return EXIT_OK
    if len(argv) != 1:
        print("Usage: vts subtitles VIDEO", file=sys.stderr)
        raise Usage()
    ident = video.ref(argv[0])
    if not ident:
        log.err(f"not a video: {argv[0]}")
        return EXIT_FAIL
    try:
        subs = player.subtitles(cfg.downloader, ident, cfg.language)
    except player.PlayError as e:
        log.err(str(e))
        return EXIT_FAIL
    for s in subs:
        out(f"{s.code}\t{s.name}")
    if not subs:
        out("no subtitles")
    return EXIT_OK
