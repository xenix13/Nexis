# Development

[← Back to the README](../README.md) · Previous: [Troubleshooting](troubleshooting.md)

Noxa is deliberately simple to hack on: the server is **Python 3 standard library only**, the interface is **plain JavaScript modules with no build step**, and the tests generate their own media with ffmpeg.

**Contents:** [Run from source](#run-from-source) · [Tests](#tests) · [Code layout](#code-layout) · [Translations](#translations) · [Apps](#apps) · [Releases](#releases-and-branches) · [Screenshots](#documentation-screenshots)

## Run from source

Requirements: Python 3.9+, ffmpeg / ffprobe. Optional: Pillow (only used by some tests), Playwright (browser tests).

```bash
git clone https://github.com/xenix13/Noxa.git && cd Noxa
python3 -m seance --data ./data --port 8420
```

Open `http://localhost:8420`; the setup code is printed in the console. Edit a file in `static/` and reload the page; restart the server after changing Python code. `--debug` (or `SEANCE_DEBUG=1`) logs the ffmpeg commands.

## Tests

```bash
python3 -m unittest discover -s tests -t .          # the whole suite (~200 tests, a few minutes)
python3 -m unittest tests.test_cold -v              # one file
python3 tests/ui_smoke.py ./captures video.webm     # browser walkthrough (Playwright) with screenshots
python3 tests/ui_features.py ./captures video.webm  # chapters, tasks, live TV, watch together, settings
python3 tests/hls_real.py path/to/hls.min.js        # real hls.js playback (VP9/Opus): resume, seeking, PGS
```

The suite builds a small library with ffmpeg (H.264, HEVC, 10-bit, multi-audio, subtitles, chapters) and fakes the external services (TMDB, Seerr, OpenSubtitles, Trakt, S3, WebDAV, SFTP, Radarr / Sonarr…), so it runs offline. CI (`.github/workflows/tests.yml`) runs it on Ubuntu 24.04 with the same ffmpeg as the Docker image, checks the shell scripts and the JavaScript syntax, and builds the `.deb`.

## Code layout

```
seance/                 the server (Python package; "seance" is the internal name)
  __main__.py           command line, start-up
  api.py                assembles the server; routes/ holds the HTTP routes by topic
  web.py                HTTP server, static files, security headers
  db.py                 SQLite and migrations; auth.py: accounts, sessions; twofa.py: TOTP
  scanner.py, names.py  library scan and file name recognition
  metadata.py, images.py  TMDB and .nfo; images
  transcoder.py, media.py playback decision, ffmpeg and HLS
  subcache.py, subtitles.py, opensubs.py, whisper.py  subtitles
  chapters.py, introdetect.py, markers.py  chapter images, intro detection, skip markers
  tasks.py              scheduled tasks; watcher.py: new content detection
  federation.py         linked servers (TLS, tunnel, replication)
  cold.py, coldfree.py  cold storage and freeing up space
  backup.py, remote_storage.py  backups and external storages
  livetv.py, dlna.py, photos.py, books.py, syncplay.py  live TV, DLNA, photos, audiobooks, watch together
  downloads.py, push.py, notify.py, stats.py, health.py  offline downloads, notifications, stats, alerts
  themes.py, suggest.py, discover.py, tracking.py  theme store, suggestions, Discover/Seerr, Trakt/Letterboxd
  update.py, apps.py    self-update, app catalogue
  mountd.py             the root helper that mounts SMB shares read-only
  i18n.py, lang_en.py   server messages and their English translation
static/                 the web interface (ES modules, no bundler)
  js/app.js             router; js/views/ one file per page; js/player.js the player
  js/i18n/en.js         English translation of the interface
  themes/               the theme store (one JSON per theme)
  vendor/               hls.js, JASSUB, libpgs, qrcode (pinned, SHA-256 checked)
android/app             Android TV app (Java, ExoPlayer)
android/mobile          Android phone / tablet app (WebView shell)
apps/desktop            Windows / macOS / Linux app (Electron shell)
deploy/                 install script, Proxmox script, .deb builder, systemd units
tests/                  unit and integration tests, browser walkthroughs
tools/screenshots/      demo library and documentation screenshots
```

## Translations

French text in the code is the **translation key**:

- Interface: `tr('Paramètres')` (`static/js/i18n.js`), translations in `static/js/i18n/en.js`.
- Server messages: `tr("Introuvable")` (`seance/i18n.py`), translations in `seance/lang_en.py`, where `{}` stands for a variable part.
- Android TV app: `Tr.t("…")` (`Tr.java`).
- Plurals: `plural(n, '{n} film', '{n} films')`. The same word with two meanings takes a context: `tr('bouton|Lecture')`.

`tests/test_i18n.py` checks that every interface string and every server error message has its translation.

**Adding a language:** create `static/js/i18n/<code>.js` and `seance/lang_<code>.py`, then add the code to `LANGUAGES` in `i18n.js` and `i18n.py`. Pull requests for new languages are very welcome.

## Apps

- **Desktop** (`apps/desktop`, Electron): `npm ci && npm start`; `npm run dist` builds the installers for the current platform.
- **Android phone** (`android/mobile`) and **Android TV** (`android/app`): `gradle -p android assembleRelease` (JDK 17, Gradle 8.7). Icons are regenerated with `python3 android/make_icons.py`.
- The apps have their own version numbers (`apps/desktop/package.json`, `android/mobile/build.gradle`); the server reads them from the `noxa-apps.json` file attached to each release.

## Releases and branches

- `test`: where new work lands first; the *Test* update channel and the `:test` Docker image follow it.
- `main`: stable releases. Every push to `main` with a new version in `seance/__init__.py` makes CI publish the GitHub release `v<version>` with the `.deb`, the Docker images (`latest` and `<version>`), the Android TV APK and the desktop / Android apps. Hand-written notes are taken from `docs/releases/v<version>.md` when that file exists.
- `docker-compose.yml` on `main` must use `ghcr.io/xenix13/noxa:latest` (CI checks it).
- Versions follow [semantic versioning](https://semver.org).

## Former name (Nexis)

The project was called **Nexis** until 4.0. A few invisible identifiers keep that name on purpose, because changing them would break existing installs or links between servers:

- Android application ids `fr.nexis.app` and `fr.nexis.tv` (Android only updates an app over one with the same id), and the bridges `window.NexisAndroid` / `window.nexisNative`;
- HTTP headers `X-Nexis-Server` and `X-Nexis-Device`, the `NEXIS1-` linked-server invitations, the `nexis-` prefix of backup files, browser storage keys (`nexis.*`) and the desktop app's data folder;
- the internal name `seance` (Python package, system user, service, folders).

The Docker image is published as `ghcr.io/xenix13/noxa` and, for existing installs, `ghcr.io/xenix13/nexis`; the `.deb` package `noxa` replaces `nexis`; `nexis` is kept as an alias of the `noxa` command.

## Documentation screenshots

The images in `docs/images` come from a generated demo library with fictional titles:

```bash
python3 tools/screenshots/demo_library.py /srv/media        # posters, .nfo files and videos (~4 GB)
python3 tools/screenshots/take.py /srv/media docs/images     # starts a throwaway server and takes the screenshots
```

Run `take.py` as a regular user (as root every folder looks writable and the Libraries page warns about it). Set `CHROMIUM=/path/to/chrome` if Playwright's own browser is not installed.

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md).
