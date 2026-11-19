# Security

## Reporting a problem

Please report security problems privately, through GitHub's "Report a
vulnerability" (Security tab of the repository), rather than in a public
issue. Say what an attacker can do and how to reproduce it; a fix is
worked on as soon as the report is understood.

## What vts protects against

vts shows text written by strangers (titles, descriptions, comments,
channel names) and hands identifiers to other programs. The design
assumes everything coming from the network is hostile:

- **Terminal escape sequences.** Every string from YouTube or from a
  feed goes through `text.clean()` before it reaches the terminal:
  control characters (C0 and C1, ESC included) and bidirectional
  overrides are removed, invalid UTF-8 becomes U+FFFD. A title can't
  move the cursor, change the window title, write to the clipboard or
  send keys back. Thumbnails are drawn by vts itself from decoded
  pixels (or by chafa from the cached picture), never by passing data
  from the network through.
- **Arguments to other programs.** mpv, yt-dlp, notify-send, systemctl
  and schtasks are started directly (a list of arguments, never through
  a shell).
  Video, channel and playlist identifiers are checked against their
  exact format before use, and the addresses given to mpv and yt-dlp are
  built by vts from them, so an answer can't slip in an option or
  another site. Text in desktop notifications has its markup escaped.
- **Network.** HTTPS only to YouTube, i.ytimg.com and GitHub (an `https`
  request is never redirected to `http`); answers are size-limited, and
  timeouts and retries are bounded. Thumbnail addresses are made from
  video identifiers, not taken from answers.
- **Parsing.** JSON and XML (expat, with document types refused, so no
  entity expansion) are read with size limits, and results are not
  looked for deeper than a limit; identifiers and links in them are
  validated before they are kept. JPEG thumbnails are decoded by Pillow
  with their size limited.
- **Files.** The library and what vts keeps in its cache are created
  readable by you only (0600, folders 0700, on Linux), and written
  atomically.
  Imported files (Takeout CSV, OPML) are treated like network data.

## Updates

The rules that describe YouTube's answers are fetched once a day over
HTTPS from this repository. They are data, never code: a file that
doesn't load, lacks something the program reads or isn't newer is not
used, and the rules built into the program remain the fallback. Turning
updates off (`updates = no`, `--offline`) keeps vts on its built-in
rules. yt-dlp is only told to update itself (`yt-dlp --update`, its own
update mechanism) when it is a file you can replace, not one installed
by the system.

## What vts doesn't protect against

- mpv and yt-dlp process the video streams; their own security matters
  as much as vts'. Keep them up to date.
- Someone who can write to your files (the settings, the library, the
  cache) can change what vts does, like for any program.
- YouTube sees your IP address and what you watch, as it would in a
  browser without an account. vts sends no cookie but YouTube's consent
  answer, and no telemetry.
