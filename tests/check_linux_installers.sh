#!/usr/bin/env bash
# Runs only inside a disposable Docker container; never against the host's /opt.
set -euo pipefail
kind="$1"
package="$2"
test -f /.dockerenv
case "$kind" in
  deb)
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "/packages/$package" xvfb xauth dbus-daemon
    ;;
  rpm)
    dnf install -y "/packages/$package" xorg-x11-server-Xvfb xorg-x11-xauth dbus-daemon
    ;;
  *) exit 2 ;;
esac
test -x /usr/bin/telecon
test -f /usr/share/applications/ru.tatpow.telecon.desktop
test -f /usr/share/icons/hicolor/64x64/apps/ru.tatpow.telecon.png
cmp /opt/telecon/LICENSE /usr/share/doc/telecon/copyright
# Root exists only inside this isolated container. Never ship these overrides.
export QTWEBENGINE_DISABLE_SANDBOX=1
export QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu
export PLAYWRIGHT_BROWSERS_PATH=/opt/telecon/ms-playwright
timeout 180s dbus-run-session -- xvfb-run -a /usr/bin/telecon --portable-self-check "/reports/installed-$kind.json"
mkdir -p "$HOME/.local/share/LectureAssistant" "$HOME/Videos"
printf 'synthetic calendar/profile marker\n' > "$HOME/.local/share/LectureAssistant/uninstall-sentinel.txt"
printf 'synthetic recording marker\n' > "$HOME/Videos/telecon-uninstall-sentinel.txt"
printf 'unregistered file\n' > /opt/telecon/uninstall-sentinel.txt
case "$kind" in
  deb) apt-get remove -y telecon ;;
  rpm) dnf remove -y telecon ;;
esac
test ! -e /usr/bin/telecon
test ! -e /opt/telecon/Telecon
test ! -e /usr/share/applications/ru.tatpow.telecon.desktop
test ! -e /usr/share/icons/hicolor/64x64/apps/ru.tatpow.telecon.png
test -s /opt/telecon/uninstall-sentinel.txt
test -s "$HOME/.local/share/LectureAssistant/uninstall-sentinel.txt"
test -s "$HOME/Videos/telecon-uninstall-sentinel.txt"
printf '{"ok":true,"installed_ui_checked":true,"uninstall_preserved_user_files":true}\n' > "/reports/uninstall-$kind.json"
