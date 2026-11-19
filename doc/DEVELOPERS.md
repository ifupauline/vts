# For developers

vts is Python 3.10 or later, with the standard library (`urllib`,
`json`, `sqlite3`, `xml.parsers.expat`, `curses`), Pillow for the
thumbnails and, on Windows, `windows-curses`. Nothing to build:

```sh
python -m vts --help        # run it from the source folder
pip install -e ".[test]"    # or install it there, with what the tests need
make test                   # the tests (pytest; pyte for the app)
make lint                   # ruff
make live-test              # against the real YouTube (needs the internet)
make fuzz                   # damaged and hostile answers (ROUNDS=2000)
make screenshots            # the README's pictures (needs pyte)
make install                # pipx install of this folder
```

## The tests

`tests/python/` (pytest):

- `test_units.py`: cleaning text for the terminal (escape sequences,
  invalid UTF-8, direction overrides), widths and cutting to a width,
  numbers and dates, the rules engine (paths, text, identifiers,
  results, a recorded search), the search filters compared with what
  the website sends, feeds (recorded and hostile ones: entities, escape
  characters, bad identifiers and links), the settings and the settings
  file (edited in place, explained), the updates' checks
- `test_library.py`: the library (favourites, Takeout CSV and OPML in
  and out, seen videos, history and resuming, playlists, recent
  searches, downloads, a library of the C version upgraded, one from a
  newer vts refused), mpv's and yt-dlp's command lines and output,
  thumbnails' geometry and drawing
- `test_commands.py`: the commands against `fake_server.py`, which plays
  YouTube from the recordings; runs on Linux and on Windows
- `test_suite.py`: the whole-program checks of `tests/test_cli.py`, on
  a Unix system: the commands on a terminal, playback and downloads with
  `fake_mpv.py` (which speaks mpv's IPC protocol and logs its command
  line, and plays yt-dlp, notify-send and systemctl too), new videos and
  their notifications, the timer, YouTube's refusals; the full screen
  app on a pseudo terminal, its screen read with pyte: Home, search with
  suggestions, a video page and its comments, Watch later, playing and
  stopping, qualities, subtitles, downloads and their progress, the
  history, removing, the help, the preview with its thumbnail (blocks
  and kitty images); `vts update`, the daily and weekly updates, `vts
  doctor`
- `test_app.py`: what the app does beyond those: the Settings screen,
  the recent searches, the playback keys, the watched marks, the
  Downloads screen, the buttons of notifications

The CI runs them on Linux and Windows, with Python 3.10 and 3.13.

## Code layout

| File                 | What                                                   |
| -------------------- | ------------------------------------------------------ |
| `vts/main.py`        | the program: options, settings, then the command (none: the app); `vts config` |
| `vts/tui/app.py`     | the app's frame (curses): the stack of screens, lists, drawing, keys and mouse, prompts with recent searches and suggestions, menus, the preview with its thumbnail, the player and downloads in the background, updates |
| `vts/tui/screens.py` | its screens: Home, Search and its filters, a video, comments and replies, a channel and its tabs, a playlist, the Library, your lists, the history, the downloads, the Favourites, the Settings and the checks |
| `vts/tui/draw.py`    | curses' drawing: text cut to a width, wrapped, colours |
| `vts/cli/browse.py`  | the commands that look: `search`, `suggest`, `info`, `comments`, `channel`, `playlist`, `feed` |
| `vts/cli/library.py` | `fav`, `history`, `list`, `later`                      |
| `vts/cli/play.py`    | `play`, `download`, `subtitles`                        |
| `vts/cli/upkeep.py`  | `check` and its timer (systemd, Windows' scheduled tasks), `update`, `doctor`, `notified` (behind the buttons of notifications) |
| `vts/youtube.py`     | the InnerTube client: web client version, requests, YouTube's refusals, suggestions |
| `vts/rules.py`       | reads `vts/youtube.json` and finds results in YouTube's answers |
| `vts/search.py`      | search, its filters (YouTube's `params`), next pages   |
| `vts/video.py`       | the video page (details, chapters, up next), comments and replies, video links |
| `vts/channel.py`     | channels (handles and links resolved, tabs, search inside) and playlists |
| `vts/feed.py`        | channel feeds (the official XML feed of the latest uploads) |
| `vts/library.py`     | the library in SQLite                                  |
| `vts/check.py`       | new videos of favourites: feeds read in parallel, compared with what was seen, notified |
| `vts/notify.py`      | notifications: notify-send (with buttons), Windows' own |
| `vts/player.py`      | mpv started and followed through its JSON IPC (a socket, a named pipe on Windows), yt-dlp for downloads and subtitles |
| `vts/thumbs.py`      | thumbnails: downloaded in the background, cached, drawn for the terminal (blocks, sixels, kitty, iTerm2) |
| `vts/update.py`      | updates of the rules and of yt-dlp, when due; whether playing can work |
| `vts/config.py`      | the settings, what each one means, the settings file   |
| `vts/http.py`        | HTTP: size limits, retries, parallel requests, no https to http |
| `vts/text.py`        | cleaning text from YouTube before display, widths      |
| `vts/platform.py`    | what differs between Linux and Windows: folders, programs, mpv's IPC |

## YouTube's answers

YouTube's web client talks JSON with `www.youtube.com/youtubei/v1/...`
(InnerTube). vts makes the same requests, with the client version read
from YouTube's home page (kept a day in the cache, `client`).

Where each piece of information sits in the answers is not in the code
but in `vts/youtube.json`: for each kind of result, the name of the block
YouTube wraps it in, and for each field, paths tried in order. When
YouTube moves something, the fix is usually a new path in that file.

The program also takes newer rules without a new version: once a day it
fetches `vts/youtube.json` from the `main` branch of the repository
(`VTS_RULES_URL` says where else; with `VTS_BASE_URL`, the test server's
`/rules.json`). It keeps them in the cache (`rules.json`) only if they
load, have everything the built-in rules have (same members, same types,
at any depth) and a larger `revision`. So after changing the rules, put
`revision` up (the date and a number, `2026111301`): every vts already
installed picks them up within a day. The repository must be public for
that; until then the fetch finds nothing (404) and the built-in rules
are used. A broken cached copy is ignored with a warning.

`make fuzz` runs `tests/fuzz.py`: each round damages one recorded
answer, feed or page (values of the wrong type, keys removed, deep
nesting, huge or hostile strings, escape sequences, cut short, bytes
changed), serves it and runs the command that reads it. vts must answer
with an error or a result; a Python traceback or a hang is kept in
`fuzz-crash-N/` with its command.

`make live-test` runs `tests/live_test.py`, the one test that talks to
the real YouTube: searches with filters and pages, a video page with
chapters, comments and replies, a channel's tabs and its search, a
playlist, feeds, suggestions and `vts doctor`. It checks shapes (how
many results, which fields), not content. It fails when YouTube changed
something vts reads, and exits with 3 when YouTube refuses the network
for a while, which says nothing either way. It runs every day too.

`tests/fixtures/youtube/` holds real answers recorded from YouTube, and
`index.json` tells `fake_server.py` which answer goes with which
request. Setting `VTS_BASE_URL=http://127.0.0.1:PORT` sends vts to that
server instead of YouTube (`VTS_IMAGE_URL` for the thumbnails).

The documentation: `README.md`, `doc/GUIDE.md` for users, `doc/vts.1`
(the manual page; check it with `groff -man -ww -z`) and `SECURITY.md`.
When a command, key or setting changes, these change in the same commit,
and so does `config.SETTINGS`, which explains the settings everywhere
(`vts config --help`, `--init`, the Settings screen).

Everything vts displays that came from outside goes through
`text.clean()` first: a title or a comment can carry escape sequences
that a terminal would obey.

## Style

PEP 8, lines of 79 columns, checked by `ruff check` with the rules of
`pyproject.toml`. Comments say why, in plain words. Errors that people
can do something about are exceptions with a message for them
(`YouTubeError`, `LibError`, `PlayError`, `BadRules`, `BadValue`),
caught where the message is shown.
