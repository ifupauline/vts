# SPDX-License-Identifier: Apache-2.0
"""
The settings: ~/.config/vts/config (on Windows %APPDATA%\\vts\\config),
lines of "key = value" and "# comments".
"""

import os

from vts import platform, util

CONFIG_MAX = 64 * 1024

QUALITY_BEST = -1
QUALITY_AUDIO = 0
HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240, 144)
THUMBNAILS = ("no", "auto", "blocks", "sixel", "kitty", "iterm2")
SOUND_QUALITIES = ("high", "medium", "low")
START_SITES = ("youtube", "twitch", "soop", "last")
# the qualities live streams and past broadcasts come in (Streamlink's
# names without "p"; audio: the sound only)
TWITCH_QUALITIES = ("best", "1080", "720", "480", "360", "160", "audio")
SOOP_QUALITIES = ("best", "1080", "720", "540", "360", "audio")
SECTIONS = ("General", "YouTube", "Twitch", "SOOP")
PROGRAM_MAX = 255


class BadValue(ValueError):
    pass


def _letters(s):
    return s.isascii() and s.isalpha()


def check_language(v):
    """ "fr", "en-GB", "zh-Hant", written the way YouTube expects them"""
    main, dash, sub = v.partition("-")
    if (not 2 <= len(main) <= 3 or not _letters(main) or
            (dash and (not 2 <= len(sub) <= 4 or not _letters(sub)))):
        raise BadValue("expected a language code like fr, en-GB or pt-BR")
    out = main.lower()
    if dash:
        # regions upper case, scripts capitalised: zh-Hant
        out += "-" + (sub.upper() if len(sub) == 2 else sub.capitalize())
    return out


def check_region(v):
    if not v:
        return ""
    if len(v) != 2 or not _letters(v):
        raise BadValue("expected a two-letter country code like FR, "
                       "or nothing for automatic")
    return v.upper()


def check_quality(v):
    if v == "best":
        return QUALITY_BEST
    if v == "audio":
        return QUALITY_AUDIO
    h = util.parse_long(v, 1, 100000)
    if h in HEIGHTS:
        return h
    raise BadValue("expected best, audio, 2160, 1440, 1080, 720, 480, 360, "
                   "240 or 144")


def _one_of(choices):
    def check(v):
        if v in choices:
            return v
        raise BadValue("expected " + ", ".join(choices[:-1]) + " or " +
                       choices[-1])
    return check


def check_sound(v):
    if v in SOUND_QUALITIES:
        return v
    raise BadValue("expected high, medium or low")


def check_program(v):
    if not v or len(v.encode()) > PROGRAM_MAX:
        raise BadValue("expected the name or path of a program")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        raise BadValue("control characters are not allowed")
    return v


def check_subtitles(v):
    ok = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-,")
    if len(v) > 63 or not set(v) <= ok:
        raise BadValue("expected languages like fr,en, or nothing for none")
    return v


def check_bool(v):
    b = util.parse_bool(v)
    if b is None:
        raise BadValue("expected yes or no")
    return b


def check_thumbnails(v):
    if v in THUMBNAILS:
        return v
    b = util.parse_bool(v)
    if b is not None:
        return "auto" if b else "no"
    raise BadValue("expected auto, blocks, sixel, kitty, iterm2 or no")


def check_downloads(v):
    # a folder from the root or from home; empty: the usual one
    if (v and not v.startswith("/") and not v.startswith("~/") and v != "~"
            and not (platform.WINDOWS and (os.path.isabs(v) or
                                           v.startswith("~\\")))):
        raise BadValue("expected a folder like ~/Videos or /media/videos")
    if len(v.encode()) > 1023:
        raise BadValue("too long")
    return v


def quality_name(q):
    if q == QUALITY_BEST:
        return "best"
    if q == QUALITY_AUDIO:
        return "audio"
    return str(q)


def quality_label(q):
    """ "1080p", "best", "sound only" """
    if q == QUALITY_BEST:
        return "best"
    if q == QUALITY_AUDIO:
        return "sound only"
    return f"{q}p" if q != 2160 else "2160p (4K)"


class Setting:
    def __init__(self, key, check, values, default, about, choices=None,
                 section="YouTube"):
        self.key = key
        # where it is kept: twitch.quality in twitch_quality
        self.attr = key.replace(".", "_")
        self.section = section  # in SECTIONS
        self.check = check
        self.values = values  # what can be written, for people
        self.default = default  # what the default is, for people
        self.about = about  # what it does
        self.choices = choices  # the values to pick from, when a list


# in the order of `vts config`, with what they mean
SETTINGS = (
    Setting("language", check_language, "a language code: fr, en, en-GB, "
            "pt-BR, zh-Hant...", "from your system (LANG), else en",
            "The language of YouTube's texts: dates (\"3 days ago\"), "
            "numbers of views, and the order of results, as on the "
            "website in that language."),
    Setting("region", check_region, "a two-letter country code: FR, US, "
            "GB... or nothing", "from your system (LANG), else YouTube's "
            "guess", "The country whose results YouTube gives: what is "
            "popular there, and the videos available there."),
    Setting("quality", lambda v: check_quality(v), "best, 2160, 1440, "
            "1080, 720, 480, 360, 240, 144 or audio", "1080",
            "The highest quality videos play in (p plays in it, the menus "
            "of Q and d start on it). When a video has nothing that high, "
            "the best it has plays. best: the highest there is; audio: "
            "the sound only, without picture.",
            ["best", "2160", "1440", "1080", "720", "480", "360", "240",
             "144", "audio"]),
    Setting("sound_quality", check_sound, "high, medium or low", "high",
            "The quality of the sound when only the sound plays (a, or "
            "quality = audio) or is downloaded; Q and d ask. high: the best "
            "YouTube has; medium: about 128 kbit/s; low: about 64 kbit/s, "
            "half the data. MP3, M4A and Opus downloads are made at that "
            "bitrate too.", list(SOUND_QUALITIES)),
    Setting("player", check_program, "the name or path of a program",
            "mpv", "The program that plays videos. vts talks to it while it "
            "plays (position, pause, next), which only mpv can do.",
            section="General"),
    Setting("downloader", check_program, "the name or path of a program",
            "yt-dlp", "The program that finds the streams of a video (for "
            "mpv too) and downloads them. vts keeps it up to date when you "
            "installed it yourself.", section="General"),
    Setting("subtitles", check_subtitles, "languages, most wanted first: "
            "fr,en or de-DE, or nothing", "nothing: no subtitles",
            "The subtitles shown by default, when a video has them in one "
            "of these languages (written for it, or made by YouTube). u "
            "picks others for one video."),
    Setting("comments", _one_of(("top", "newest")), "top or newest",
            "top", "The order comments come in: YouTube's top ones first, or "
            "the newest first (o switches on the comments; vts comments "
            "--top or --newest).", ["top", "newest"]),
    Setting("thumbnails", check_thumbnails, "auto, blocks, sixel, kitty, "
            "iterm2 or no", "auto",
            "The pictures of videos in the app (when the terminal is 100 "
            "columns wide or more). auto: real pictures in the terminals "
            "that can show them (kitty, Ghostty, WezTerm, foot, Konsole, "
            "iTerm2), coloured blocks elsewhere. blocks: coloured blocks, "
            "for every terminal (and inside tmux). sixel, kitty, iterm2: "
            "that kind of picture, when auto guesses wrong. no: none.",
            list(THUMBNAILS[1:]) + ["no"], "General"),
    Setting("notifications", check_bool, "yes or no", "yes",
            "Desktop notifications of the new videos of your favourite "
            "channels, when `vts check` finds them (yourself or every 30 "
            "minutes with `vts check --install`). no: it only notes them, "
            "for Home.", ["yes", "no"]),
    Setting("notifier", check_program, "the name or path of a program",
            "notify-send" if not platform.WINDOWS else "(Windows' own)",
            "The program that shows notifications on Linux. On Windows, "
            "Windows' own notifications are used.", section="General"),
    Setting("downloads", check_downloads, "a folder: ~/Videos/YouTube, "
            "/media/disk/videos" + (", D:\\Videos" if platform.WINDOWS
                                    else "") + ", or nothing",
            "your Downloads folder", "Where d (and `vts download` without "
            "-d) puts what it downloads. It is made if it doesn't exist.",
            section="General"),
    Setting("updates", check_bool, "yes or no", "yes",
            "Once a day, fetch the latest description of YouTube's, "
            "Twitch's and SOOP's pages (so that vts keeps working when "
            "they change them) and install a newer vts after you quit; "
            "once a week, update Streamlink and have yt-dlp update itself. "
            "no: never (vts update still does it when you ask).",
            ["yes", "no"], "General"),
    Setting("start_site", _one_of(START_SITES),
            "youtube, twitch, soop or last", "youtube",
            "The site vts opens on (b switches between them). last: the "
            "one you were on when you quit.", list(START_SITES), "General"),
    Setting("twitch.quality", _one_of(TWITCH_QUALITIES),
            "best, 1080, 720, 480, 360, 160 or audio", "best",
            "The quality Twitch's live streams play in (p plays in it, Q "
            "starts on it). When a stream has nothing that high, the best "
            "it has plays. audio: the sound only.", list(TWITCH_QUALITIES),
            "Twitch"),
    Setting("twitch.low_latency", check_bool, "yes or no", "no",
            "Play Twitch's live streams a few seconds closer to live "
            "(Streamlink's low-latency mode, mpv's low-latency profile). "
            "The stream may pause more often on a slow connection.",
            ["yes", "no"], "Twitch"),
    Setting("soop.quality", _one_of(SOOP_QUALITIES),
            "best, 1080, 720, 540, 360 or audio", "best",
            "The quality SOOP's live streams play in (p plays in it, Q "
            "starts on it). audio: the sound only.", list(SOOP_QUALITIES),
            "SOOP"),
    Setting("soop.vod_quality", _one_of(SOOP_QUALITIES),
            "best, 1080, 720, 540, 360 or audio", "best",
            "The quality SOOP's past broadcasts play in.",
            list(SOOP_QUALITIES), "SOOP"),
)

_BY_KEY = {s.key: s for s in SETTINGS}


def setting(key):
    return _BY_KEY.get(key)


class Config:
    def __init__(self):
        self.language = "en"
        self.region = ""
        self.quality = 1080
        self.sound_quality = "high"
        self.player = "mpv"
        self.downloader = "yt-dlp"
        self.subtitles = ""
        self.thumbnails = "auto"
        self.comments = "top"
        self.notifications = True
        self.notifier = "notify-send"
        self.downloads = ""
        self.updates = True
        self.start_site = "youtube"
        self.twitch_quality = "best"
        self.twitch_low_latency = False
        self.soop_quality = "best"
        self.soop_vod_quality = "best"
        # from the command line
        self.config_file = None
        self.verbose = False
        self.offline = False
        self._defaults_from_locale()

    def _defaults_from_locale(self):
        """ "fr_FR.UTF-8@euro": language fr, region FR"""
        loc = None
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            loc = os.environ.get(var) or None
            if loc:
                break
        if not loc and platform.WINDOWS:
            loc = _windows_locale()
        if not loc:
            return
        n = len(loc)
        for i, c in enumerate(loc):
            if c in "_.@-":
                n = i
                break
        lang = loc[:n]
        if not 2 <= n <= 3 or not lang.islower():
            return
        try:
            self.language = check_language(lang)
        except BadValue:
            return
        rest = loc[n + 1:] if n < len(loc) and loc[n] in "_-" else ""
        region = rest
        for i, c in enumerate(rest):
            if c in ".@":
                region = rest[:i]
                break
        if len(region) == 2:
            try:
                self.region = check_region(region)
            except BadValue:
                pass

    def set(self, key, value):
        """set @key; KeyError when unknown, BadValue with why"""
        s = _BY_KEY.get(key)
        if not s:
            raise KeyError(key)
        setattr(self, s.attr, s.check(value))

    def get(self, key):
        """the value of @key as written in the file"""
        v = getattr(self, _BY_KEY[key].attr if key in _BY_KEY else key)
        if key == "quality":
            return quality_name(v)
        if isinstance(v, bool):
            return "yes" if v else "no"
        return v

    def load(self, path):
        """
        read @path; returns the messages (warnings) and raises BadSettings
        with all of them when a value is wrong; OSError when unreadable
        """
        data = platform.read_file(path, CONFIG_MAX, binary=True)
        if b"\0" in data:
            raise BadSettings([f"{path}: not a settings file"])
        msgs = []
        bad = False
        for n, raw in enumerate(data.decode("utf-8", "replace").split("\n"),
                                1):
            line = raw.rstrip("\r").strip(" \t")
            if not line or line.startswith("#"):
                continue
            key, eq, value = line.partition("=")
            if not eq:
                msgs.append(f"{path}:{n}: expected key = value")
                bad = True
                continue
            key = key.strip(" \t")
            value = value.strip(" \t")
            # "a value" may be quoted, to keep its spaces visible
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            try:
                self.set(key, value)
            except KeyError:
                msgs.append(f"{path}:{n}: unknown setting '{key}': ignored")
            except BadValue as e:
                msgs.append(f"{path}:{n}: bad value for '{key}': {e}")
                bad = True
        if bad:
            raise BadSettings(msgs)
        return msgs

    def write(self):
        """the settings as a settings file"""
        out = []
        for s in SETTINGS:
            v = self.get(s.key)
            out.append(f"{s.key} =" + (f" {v}" if v != "" else "") + "\n")
        return "".join(out)

    def downloads_dir(self):
        """the folder for downloads, made if missing; None without a home"""
        d = self.downloads
        if not d:
            return platform.downloads_dir()
        if d == "~" or d.startswith("~/") or d.startswith("~\\"):
            home = platform.home()
            if not home:
                return None
            d = home + d[1:]
        # "~/Videos/" and "~/Videos" alike
        while len(d) > 1 and d[-1] in "/\\" and not d.endswith(":\\"):
            d = d[:-1]
        platform.mkdirs(d)
        return d


class BadSettings(Exception):
    def __init__(self, msgs):
        super().__init__("\n".join(msgs))
        self.msgs = msgs


def _windows_locale():
    try:
        import locale
        loc = locale.getlocale()[0] or ""
    except (ValueError, AttributeError):
        return None
    # "French_France" on some versions: not a code, nothing to take
    return loc if "_" in loc and loc[:2].islower() else None


def default_path(cfg):
    """the settings file used: -c FILE, or the usual one (None if no home)"""
    if cfg.config_file:
        return cfg.config_file
    d = platform.config_dir()
    return os.path.join(d, "config") if d else None


def edit_file(path, key, value):
    """
    set @key to @value in the settings file @path, keeping the rest of it
    (comments too): the line of the setting is changed, or one is added;
    with @value None, it goes and the default applies
    """
    try:
        lines = platform.read_file(path, CONFIG_MAX).split("\n")
    except FileNotFoundError:
        lines = []
    new = None if value is None else (f"{key} = {value}" if value != ""
                                      else f"{key} =")
    done = value is None  # None: the line goes, the default applies
    out = []
    for raw in lines:
        k, eq, _ = raw.strip(" \t").partition("=")
        if eq and not raw.lstrip().startswith("#") and k.strip() == key:
            if not done:
                out.append(new)
                done = True
            continue
        out.append(raw)
    lines = out
    if not done:
        while lines and lines[-1] == "":
            lines.pop()
        lines.append(new)
    text = "\n".join(lines).rstrip("\n") + "\n"
    d = os.path.dirname(path)
    if d:
        platform.mkdirs(d)
    platform.write_atomic(path, text)


def init_text(cfg):
    """a settings file with every setting explained, the current values"""
    import textwrap
    out = ["# vts settings: `key = value`, one per line; # starts a comment.",
           "# `vts config --help` explains them, `vts config` shows them.",
           ""]
    for s in SETTINGS:
        for line in textwrap.wrap(s.about, 74):
            out.append("# " + line)
        out.append(f"# values: {s.values}")
        out.append(f"# default: {s.default}")
        v = cfg.get(s.key)
        out.append(f"{s.key} = {v}" if v != "" else f"{s.key} =")
        out.append("")
    return "\n".join(out)


USAGE = """\
Usage: vts [OPTION]... [COMMAND [ARGUMENT]...]
Video Terminal Shell, a terminal client for YouTube.
Without a command, the full screen app starts (? shows its keys).

Options:
  -c, --config FILE  read the settings from FILE instead of
                     {config}
  -v, --verbose      say more about what happens
      --offline      no updates of the rules or yt-dlp
  -h, --help         show this help and exit
  -V, --version      show the version and exit

Commands:
  play VIDEO...      play videos or playlists with mpv
                     (vts play --help)
  download VIDEO...  download them with yt-dlp
  search WORDS...    search, with the website's filters
                     (vts search --help)
  suggest WORDS...   what the search box suggests
  info VIDEO         the video page: details, description,
                     chapters, what comes next
  comments VIDEO     its comments (vts comments --help)
  channel CHANNEL    a channel's videos, Shorts, live streams,
                     playlists, or search it (vts channel --help)
  playlist PLAYLIST  the videos of a playlist
  feed CHANNEL       a channel's latest uploads, from its feed
  fav ...            your favourite channels (vts fav --help)
  history ...        what you watched, where you stopped
  later ...          Watch later
  check              new videos of your favourites, with desktop
                     notifications (vts check --help)
  list ...           your playlists (vts list --help)
  config             show the settings in use; what each one does
                     and its values (vts config --help)
  subtitles VIDEO    its subtitles, for play --subs
  update             update the rules and yt-dlp now
  doctor             check that everything works
"""


def usage():
    where = ("%APPDATA%\\vts\\config" if platform.WINDOWS
             else "~/.config/vts/config")
    return USAGE.format(config=where)
