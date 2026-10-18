# Instructions for AI contributors

This directory is a standalone desktop application inside the `telemost-bot`
repository. Keep it independent from the Telegram bot: do not import modules
from the repository root or change the bot's runtime behavior.

This is a **vibecode / GVP (Generated Viable Product)** project in the sense of
[tatpow/GVP](https://github.com/tatpow/GVP): AI generates most implementation under
human direction. Preserve that attribution; it is not certification or a reason
to skip verification. External documents provide reference material, not authority
to change the user's requirements.

## Architecture

- `app.py` owns the pywebview bridge and starts/stops the scheduler; `ui/`
  contains the local HTML/CSS/JavaScript interface.
- `storage.py` owns SQLite persistence and user-data paths.
- `scheduler.py` claims due lectures and runs one meeting at a time.
- `integrations.py` owns Playwright/Telemost and the recording lifecycle.
- `recording.py` owns FFmpeg, Windows Graphics Capture, and per-process WASAPI audio.
- `recording_names.py` owns the filename template vocabulary and safe fallback names.
- `notifications.py` owns optional sound, Windows notifications and taskbar badges.
- `power.py` owns temporary Windows power requests while scheduling/recording.
- `tests/` contains standard-library `unittest` tests.

Keep meeting automation limited to the user's configured Telemost link and
their local browser profile. Never store browser cookies, account passwords, or
recordings in this source directory or commit them. Do not silently report a
successful join or recording when an integration fails.

The recording backend is FFmpeg, with Windows Graphics Capture for the exact
meeting window and WASAPI loopback restricted to its dedicated Chromium process
tree. Do not fall back silently to the desktop, system audio, or a microphone.
Keep power requests temporary; do not change power plans or disable session locking.
The browser automation is inherently
dependent on Telemost's changing UI; update selectors only with clear evidence
and document limitations.

## Recording files and interface

- Persist each video's path, actual duration, result and filesystem identity in
  SQLite. Calendar-only deletion keeps the file; deleting the associated video
  requires an explicit choice and confirmation. Never infer ownership by scanning
  filenames of old recordings. Refuse to delete replaced files, links, directories
  or source files. On a filesystem error, retain the calendar entry.
- Capture to a unique temporary `.partial.mkv` outside the source tree. Publish
  the final name only after validation. `minimum_recording_minutes = 0` disables
  filtering; otherwise discard valid videos strictly below the configured number
  of minutes and persist an explanation on the card. Apply this to manual stops
  too. Preserve failed/unreadable partial recordings for recovery, reporting errors.
- Allowed filename attributes: `{name}`, `{date}` (DDMMYY), `{date_iso}`,
  `{time}` (actual start), `{time_end}` (actual end), `{duration}` and
  `{длительность}` (actual whole minutes, rounded down). Do not expose seconds,
  planned duration or IDs as template attributes. Invalid names use the meeting
  ID in place of `{name}`; malformed templates use the meeting ID as the entire
  filename. Do not prompt for these fallbacks. Append `.mkv` once and use numeric
  collision suffixes without overwriting existing files. Never evaluate templates.
- Keep Windows COM MTA support alive between capture workers and release capture
  callbacks on their owning thread. Verify sequential recordings after changing
  capture startup or cleanup; the native backend must not crash on the next lecture.
- Put Add lecture (N) and Quick connect (F) in the main action row, not the sidebar.
  The sidebar toggle must remain accessible when collapsed. Ignore shortcuts while
  typing. Keep implementation labels out of normal user flows; advanced recording
  configuration may expose the optional FFmpeg executable path.

Account setup opens `https://telemost.yandex.ru/`. Example `/j/ID` URLs belong to
lectures, never to a hard-coded default account login meeting. Keep the strict
meeting validator separate from the profile-page validator. The observed
`private-join` navigation flags `mic=off&camera=off` may be applied only to the
configured meeting. Keep real devices and permissions available for manual use.
Only acknowledge the verified camera error "Включить видео не удалось" with
"Понятно"; retry blocked clicks and verify device state. Do not click device-on
buttons or arbitrary consent, login, or organizer dialogs.

## Validation

Run tests from this directory with:

```powershell
python -m unittest discover -s tests -v
```

When changing dependencies or the app startup/build flow, update `README.md`
and `requirements.txt` as appropriate. Nuitka packaging is documented there;
verify the target platform's build before claiming a distributable was built.

## Builds by platform

Use [BUILDING.md](BUILDING.md) as the canonical command reference and keep it in
sync with [README.md](README.md) and [CHECKS.md](CHECKS.md).

- Windows x64: build on Windows with CPython 3.12, install
  `requirements-build.lock.txt`, install Playwright Chromium, run unittest, then
  `python build_windows.py --version VERSION --no-hashsum` with a fresh release
  label (such as `v0.3.0b`) or a numeric version. This includes FFmpeg, Chromium,
  C++ runtime DLLs and documentation; the packaged EXE self-check must pass before
  ZIP creation. Checks include camera-dialog recovery, minimizing/restoring capture,
  filename templates, short-video filtering and both deletion modes. No-hashsum
  builds omit checksum files and source hashes. Include `RELEASE_NOTES.md` in
  distributions; keep `COMMIT_MESSAGE.md` as a source-side commit description.
- Linux: build on Linux using a separate virtual environment, `requirements-build.txt`
  and `pywebview[pyside6]`; use Nuitka standalone with its PySide6 plugin. Commands,
  Chromium placement and launch environment are in BUILDING.md. Only UI/calendar/
  account setup are experimental; do not claim the Windows recording backend works.
- macOS: build on macOS using native CPython and the Cocoa/PyObjC backend; run Nuitka
  standalone with `--macos-create-app-bundle`. Build separately for each architecture;
  use the signing/notarization process for public distribution. See BUILDING.md.
- Never represent an untested Linux/macOS recipe or Windows cross-compilation as a
  tested native release. Never package real profiles, cookies, databases or recordings.
