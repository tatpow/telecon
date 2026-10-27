# Сборка Телекона

Канонические команды сборки находятся в этом файле.
Пользовательский запуск описан в [README](README.md), правила разработки —
в [CONTRIBUTING](CONTRIBUTING.md) и [AGENTS](AGENTS.md).

Текущая версия — **v0.5.0**. Сборки создаются без файлов Hashsum.
Все релизные команды используют **нативный CPython 3.12**:
каждая ОС и архитектура собирается на соответствующем хосте.
Для команд `node --check` отдельно нужен [Node.js](https://nodejs.org/en/download).
Он используется для проверки JavaScript и не требуется пользователю готового приложения.

| Цель | Среда CI | Результат | Проверка записи |
|---|---|---|---|
| Linux x64 | Ubuntu 24.04 x64 | `.deb`, `.rpm`, `.tar.gz` | Не поддерживается |
| Windows 11 x64 | Windows Server 2025 x64 | `Setup.exe`, `.zip` | Локально на Windows 11 |
| macOS arm64 | macOS 15 Apple Silicon | `.dmg`, `.pkg`, `.zip` | Не поддерживается |
| macOS x64 | macOS 15 Intel | `.dmg`, `.pkg`, `.zip` | Не поддерживается |

Linux и macOS пока экспериментальные: календарь, настройки, аккаунт,
подключение и выход из встречи. Запись, защита от сна и предупреждения
о свёрнутой встрече остаются Windows-функциями.

## Linux

Инструкции ниже предназначены для **Ubuntu, Linux Mint и Fedora**.
CI компилирует на Ubuntu 24.04, затем устанавливает DEB и RPM в свежие
контейнеры Ubuntu 24.04 и Fedora 44, проверяя настоящий Qt/JS bridge и браузер.
Это проверка зависимостей и Xvfb, а не сертификация графических сред/Wayland.
Mint 22.x использует базу Ubuntu 24.04; отдельный Mint desktop CI не запускается.

### Ubuntu 24.04 LTS

Установите системные зависимости:

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev build-essential patchelf xvfb xauth libegl1 libopengl0 libgl1 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libxcb-render-util0 libxkbcommon-x11-0
```

Затем выполните общий раздел «Окружение и сборка Linux».

На более новой Ubuntu используйте отдельный CPython 3.12; не меняйте
системный Python ради сборки. У бинарника, собранного на новой glibc,
может не быть обратной совместимости со старыми дистрибутивами.

### Linux Mint 22.x

[Mint 22.x основан на Ubuntu 24.04](https://linuxmint.com/rel_wilma_mate.php),
поэтому используйте те же команды APT и общий раздел ниже.
Для Mint 21.x / LMDE совместимость этого архива отдельно не подтверждена.

### Fedora

Установите отдельный Python 3.12 и инструменты:

```bash
sudo dnf install -y python3.12 python3.12-devel gcc gcc-c++ make patchelf libglvnd-glx libglvnd-egl mesa-libGL mesa-libEGL libxcb xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil libxkbcommon-x11 nss alsa-lib libXcomposite libXdamage libXrandr libXtst libXScrnSaver cups-libs
```

Fedora предоставляет [несколько версий Python параллельно](https://developer.fedoraproject.org/tech/languages/python/multiple-pythons.html).
Не подменяйте системный `python3`. Для воспроизводимого результата собирайте
и проверяйте архив на целевой Fedora: Ubuntu-артефакт не считается
автоматически проверенным Fedora-релизом.

### Окружение и сборка Linux

Из корня исходников:

```bash
python3.12 -m venv build/builder-venv
source build/builder-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build-posix.txt
export PLAYWRIGHT_BROWSERS_PATH="$PWD/build/playwright-browsers"
python -m playwright install chromium
python -m unittest discover -s tests -v
node --check ui/app.js
node --check ui/motion.js
python app.py --portable-self-check build/source-smoke.json
python build_desktop.py --version v0.5.0 --no-hashsum
```

Команды smoke-check и сборки запускайте в графической сессии.
Для CI на Ubuntu сначала установите зависимости Chromium
`python -m playwright install --with-deps chromium`, затем используйте:

```bash
QT_API=pyside6 QTWEBENGINE_DISABLE_SANDBOX=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu xvfb-run -a python build_desktop.py --version v0.5.0 --no-hashsum
```

Отключение sandbox в этой команде относится только к изолированному CI/Xvfb.
Для обычного запуска приложения эти переменные не задаются.

Результат: `dist/Telecon-Linux-x64-v0.5.0.tar.gz`.
Распакуйте всю папку и запустите `./Telecon`.
Нужны системные библиотеки графики/звука, соответствующие зависимостям выше;
Python в готовом архиве отдельно не требуется. Qt/PySide6 и Chromium включены.
Права исполнения и символьные ссылки сохраняются архивом TAR.
Версия PySide6 зафиксирована на 6.9.3 для сборщика Nuitka 2.8.10, включая
согласованные Qt/shiboken wheels. Не заменяйте её свободным диапазоном без
проверки упакованного приложения. `qt.conf` и `qt6.conf` явно указывают
на включённый каталог `PySide6/qt-plugins`; отсутствие `libqxcb.so` прерывает сборку.

### Установщики Linux: DEB и RPM

После успешной нативной сборки на Ubuntu 24.04:

```bash
sudo apt-get install -y rpm desktop-file-utils
python build_posix_installers.py --release-dir dist/Telecon-Linux-x64-v0.5.0
python tests/check_linux_installers.py --version v0.5.0
```

Последняя команда требует Docker и выполняет установку/удаление только внутри
одноразовых официальных контейнеров, без изменения пакетов хоста. На нативной
Fedora нужны `rpm-build dpkg desktop-file-utils` и отдельно Docker для этого теста.
Результат: `Telecon-Linux-x64-v0.5.0.deb` и `.rpm` в `dist/`.
Приложение устанавливается в `/opt/telecon`, команда запуска — `/usr/bin/telecon`,
ярлык — в общем меню приложений. Для произвольной папки используйте TAR.GZ.
Пакеты явно объявляют Qt/Chromium-зависимости и glibc ≥ 2.39:
цели текущего CI — Ubuntu 24.04, Mint 22.x и Fedora 44 x64.
Пакетный менеджер удаляет файлы приложения и ярлык, сохраняя домашний каталог,
календарь, профиль и записи. Нет скриптов удаления пользовательских данных.
RPM не переписывает/не strip-ит уже проверенные бинарники и не экспортирует
приватные vendored SONAMEs как системные зависимости.

## Windows 11

Нужна Windows x64, CPython 3.12 x64 и
[Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/).
Для нативного звука используется process-loopback API Windows build 20348+;
основная пользовательская цель — Windows 11.
Для упаковки также нужен установленный Microsoft Visual C++ x64 Redistributable.

В PowerShell из корня проекта:

```powershell
py -3.12 -m venv build\builder-venv
.\build\builder-venv\Scripts\python.exe -m pip install -r requirements-build.lock.txt
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) 'build\playwright-browsers'
.\build\builder-venv\Scripts\python.exe -m playwright install chromium
.\build\builder-venv\Scripts\python.exe -m unittest discover -s tests -v
node --check ui/app.js
node --check ui/motion.js
.\build\builder-venv\Scripts\python.exe build_windows.py --version v0.5.0 --no-hashsum
```

Nuitka собирает standalone EXE; FFmpeg, Chromium, runtime DLL и документация
добавляются рядом. **Полный self-check упакованного EXE обязателен до ZIP**:
он проверяет настоящий захват/звук, сворачивание, последовательные записи,
MKV/MP4, 7/60 FPS, файлы, уведомления и подключение без записи.

Результат: `dist/Telecon-Windows-x64-v0.5.0.zip`.
Переносите всю папку, а не один EXE.
`SELF-CHECK.json` и `BUILD-INFO.json` описывают именно эту сборку.
Перед ZIP полный локальный сборщик дополнительно выполняет portable-проверку:
она запускает включённый в архив обычный Chromium в headless-режиме,
не требуя второй копии headless shell. Четыре результата находятся
в `BUILD-INFO.json` → `portable_self_check`.
Отчёт дополнительной проверки также включён как `PORTABLE-SELF-CHECK.json`;
`BUILD-INFO.json` сохраняет `tray_available` и `tray_checks`.
`--no-hashsum` убирает отдельные checksum-файлы и хеши исходников из манифеста.

### Установщик Windows

После успешной проверки приложения можно собрать установщик отдельно, без
повторной компиляции Nuitka:

```powershell
$compiler = & ./tools/install_inno_setup.ps1 -Destination build/tools/innosetup
python build_installer.py --release-dir dist/Telecon-Windows-x64-v0.5.0 --iscc $compiler
```

Используется Inno Setup **6.7.3** с официального immutable GitHub release.
Скрипт проверяет Authenticode издателя Pyrsys B.V. и разворачивает компилятор
в portable-режиме внутри выбранного build-каталога, без ярлыков и регистрации.
Готовый компилятор также можно передать через `--iscc`.

`build_installer.py` принимает только полную Windows-папку с успешными проверками
интерфейса/браузера и настоящего меню трея. Лицензия в ней должна побайтно совпадать
с `LICENSE` текущего checkout: она отображается в мастере и устанавливается рядом
с EXE. Сборка не скачивает изменяемый текст лицензии во время упаковки.

Результат: `dist/Telecon-Windows-x64-v0.5.0-Setup.exe`.
`--output-dir` задаёт отдельный каталог; существующий установщик не перезаписывается.
Мастер всегда показывает выбор папки, предлагает оба ярлыка (включены по умолчанию),
работает для текущего пользователя и регистрирует штатное удаление в Windows.
По умолчанию папка — `%LOCALAPPDATA%/Programs/Telecon`. Перед обновлением/удалением завершите
лекцию и выберите «Выйти» в трее: установщик не закрывает приложение принудительно.
Календарь, профиль и записи не входят в список удаляемых установщиком файлов.

Проверка установки в изолированную папку, ярлыков, лицензии, запуска и удаления:

```powershell
./tests/check_installer.ps1 -Setup dist/Telecon-Windows-x64-v0.5.0-Setup.exe -Payload dist/Telecon-Windows-x64-v0.5.0 -TestRoot build/installer-smoke
```

Тест отказывается работать при существующей регистрации Телекона, ярлыке рабочего
стола или группе меню «Пуск». Он создаёт и удаляет только собственную тестовую
установку; посторонний синтетический файл проверяется на сохранность.
Native builds создаёт ZIP и Setup.exe, проверяет установку/удаление и
загружает их в отдельные артефакты Portable и Installers.

### Windows в GitHub Actions

Hosted runner `windows-2025` — это **Windows Server 2025**, а не Windows 11.
CI использует явно ограниченную команду:

```powershell
python build_windows.py --version v0.5.0 --no-hashsum --validation portable
```

Она компилирует реальный Windows-пакет, проверяет календарь, запуск вложенного
Chromium и native WebView2/JS bridge. Она **не проверяет WGC/WASAPI, 60 FPS
и сворачивание записи** в интерактивной пользовательской сессии.
В манифесте будет `validation: portable`, `recording_checked: false`.
Это CI-артефакт, не подтверждение работы записи на Windows 11.
Обычная локальная команда сохраняет `--validation full` по умолчанию.

## macOS

Собирайте отдельно на **Apple Silicon (arm64)** и **Intel (x64)**.
Не называйте две отдельные сборки universal binary.
Нужны Xcode Command Line Tools и нативный CPython 3.12.
Например, с Homebrew:

```bash
xcode-select --install
brew install python@3.12
"$(brew --prefix python@3.12)/bin/python3.12" -m venv build/builder-venv
source build/builder-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build-posix.txt
export PLAYWRIGHT_BROWSERS_PATH="$PWD/build/playwright-browsers"
python -m playwright install chromium
python -m unittest discover -s tests -v
node --check ui/app.js
node --check ui/motion.js
python app.py --portable-self-check build/source-smoke.json
python build_desktop.py --version v0.5.0 --no-hashsum
```

Nuitka использует `--macos-create-app-bundle` и Cocoa/PyObjC.
Chromium находится внутри `Telecon.app/Contents/Resources/ms-playwright`.
Скрипт заново запечатывает внешнюю ad-hoc подпись после добавления браузера,
проверяет подпись и выполняет smoke-check готового бинарника с native UI.
Перед подписью HTML/CSS/JS и другие данные из `Contents/MacOS` переносятся
в `Contents/Resources`; относительные ссылки сохраняют пути, используемые
пакетами. Mach-O бинарники остаются на месте. Такой способ размещения данных
описан в [Apple TN2206](https://developer.apple.com/library/archive/technotes/tn2206/).
macOS загружает локальный интерфейс напрямую в WKWebView через `html`-режим
pywebview, без HTTP-сервера и исключений ATS. CSS и JavaScript из поставки
включаются в документ с одноразовым CSP nonce; произвольный inline-код запрещён.
Обычный запуск и smoke-check используют один загрузчик `ui_document.py`.
Windows и Linux сохраняют загрузку через локальный HTTP-сервер.

CI сначала запускает Cocoa-проверку из исходников, затем повторяет её в готовом
приложении. В обоих случаях проверяется настоящий Python/JS bridge и интерфейс.
При сбое сохраняются режим загрузки (`native_ui_mode`), стадии жизни окна и
стеки потоков (`*.threads.log`). `macos-app-info.json` содержит сведения о bundle.
Неуспешная portable-проверка также печатает JSON-отчёт прямо в лог команды:
причина доступна без скачивания diagnostics-артефакта.
HTTP-проба применяется только к платформам, использующим HTTP.

Результат:
`Telecon-macOS-arm64-v0.5.0.zip` либо
`Telecon-macOS-x64-v0.5.0.zip`.
Архив создаётся через `ditto`, сохраняя структуру bundle и ссылки.
Если любая проверка не прошла, ZIP не создаётся.

### Установщики macOS: DMG и PKG

После успешной нативной сборки, на том же Mac:

```bash
python build_posix_installers.py --release-dir dist/Telecon-macOS-arm64-v0.5.0
```

На Intel замените `arm64` на `x64`. Используются штатные `ditto`, `hdiutil`,
`pkgbuild`, `productbuild` из macOS/Xcode Command Line Tools, без кросс-компиляции.
DMG предлагает перетаскивание в Applications или другую папку. PKG показывает
точный LICENSE и устанавливает `.app` в `/Applications` выбранного тома.
Архитектурные требования PKG передаются SDK через product requirements plist.
Проектная документация и лицензии, включая сторонние, находятся внутри `.app`
в `Contents/Resources` **до** финального codesign, поэтому установка одного bundle
не теряет их. Права исполнения и относительные framework/resource ссылки сохраняются.

CI монтирует DMG только для чтения и запускает приложение прямо из образа;
затем устанавливает PKG в `/Applications`, повторяет codesign и native smoke,
и удаляет установленный bundle. Скрипт
`tests/check_macos_installers.py --version v0.5.0 --arch arm64 --allow-system-install`
разрешает системную установку/удаление только на одноразовом GitHub runner и
отказывается заменять существующий `Telecon.app`. Не запускайте его на личном Mac.
Пользователь удаляет приложение через Корзину после выхода из трея; календарь,
профиль и видео сохраняются. Служебная квитанция PKG может остаться в macOS.

Ad-hoc подпись **не является Developer ID и нотариализацией**.
Для публичной поставки с подтверждённым разработчиком необходимы собственные
Developer ID, entitlements и отдельный процесс подписания/нотариализации Apple.
Секреты подписи в репозитории не хранятся.
Информация о системном подтверждении запуска —
в [документации Apple](https://support.apple.com/en-ie/102445).

## CI и артефакты

В репозитории подготовлены два workflow:

- **Checks** — unit-тесты и проверка синтаксиса на push в ветки и на PR;
  на Linux также headless UI-регрессии:
  Ubuntu, Windows Server и macOS двух архитектур.
  Создание/отправка релизного тега не запускает повторную проверку того же коммита.
- **Native builds** — ручная нативная сборка установщиков и переносных архивов на трёх ОС.
  Каждый готовый бинарник проходит smoke-check перед упаковкой, а установщики —
  отдельные проверки установки, запуска и удаления до загрузки в Artifacts.
  Ни теги, ни GitHub Releases автоматически не создаются.

Для запуска: **GitHub → Actions → Native builds → Run workflow**,
выберите ветку с workflow и оставьте `v0.5.0`.
Ручной запуск доступен после того, как workflow окажется в default branch.
Для первичной проверки до слияния сборка также запускается при push в ветку
`ci/native-builds-*`. После исправлений запускайте workflow для нового коммита:
повтор старого job не использует новые изменения из ветки.

В успешном запуске откройте **Artifacts** и скачайте нужную архитектуру.
Этот раздел находится на общей странице запуска, а не в логе отдельного job.
Имена имеют вид `Installers-macOS-arm64-v0.5.0` и `Portable-macOS-arm64-v0.5.0`,
без числового ID запуска. GitHub скачивает артефакт как ZIP; внутри Installers
лежат готовые установщики, внутри Portable — ZIP для Windows/macOS или TAR.GZ
для Linux. Сам установщик или переносной архив создаётся в
`dist/` на runner и затем загружаются в Artifacts. В Releases они сами не попадают.
Артефакты хранятся **3 дня**; ограничения хранения отличаются от бесплатного
времени стандартных runner в публичном репозитории.
Каждый архив содержит версию, зависимости и результат проверки.
Для связи локального интерфейса с Python используется генератор API pywebview.
Он создаёт функции динамически, поэтому CSP разрешает `unsafe-eval` только
в дополнение к `script-src 'self'`. Inline-скрипты, inline-стили и загрузка
скриптов с других адресов остаются заблокированными. См. [API pywebview](https://pywebview.flowrl.com/api/).
При ошибке сохраняются отдельные diagnostics-артефакты: версии зависимостей,
отчёт компиляции, журнал smoke-check и список переносов ресурсов macOS.

Workflow использует `contents: read`, закреплённые SHA официальных actions,
не сохраняет checkout credentials и не использует секреты/реальные профили.
Код из PR не выполняется через `pull_request_target`.
Релиз публикуется отдельно после просмотра результатов.

[Стандартные runner GitHub](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) ·
[Nuitka standalone](https://nuitka.net/user-documentation/use-cases.html) ·
[Установка pywebview](https://pywebview.flowrl.com/guide/installation)

## Повторная сборка и ограничения

Трей использует уже имеющиеся GUI-библиотеки: WinForms NotifyIcon на Windows,
Cocoa NSStatusItem на macOS и Qt QSystemTrayIcon на Linux. Новые зависимости
не нужны. Иконки `ui/tray.png` и `ui/tray.ico` входят в поставку вместе с UI.
`--portable-self-check` сохраняет `tray_available` и `tray_checks`: создание
объектов, сворачивание или безопасный fallback, восстановление, закрытие в фоне,
возврат окна по повторному запуску и корректный выход. Пункты меню проверяются на
отсутствие дублей и выполняются через настоящие нативные обработчики.
Восстановленное окно должно быть развёрнуто, а не только иметь `Visible=true`
в свёрнутом состоянии. Инициализация трея обязательна;
отсутствие системного хоста на Linux/CI допустимо только при сохранении доступа к окну.
Настоящий видимый трей Linux проверяйте на рабочем столе с его поддержкой;
проверка под Xvfb не подтверждает показ значка в GNOME/KDE.

Скрипты отказываются перезаписывать готовый каталог или архив.
Для отдельной тестовой Windows-сборки той же версии можно указать
`--dist-dir dist/tray-menu-fix1`: исходная папка и запущенное приложение сохраняются.
При повторной сборке той же версии сохраните прежние файлы в другой папке,
затем повторите команду. В CI каждый запуск начинает с чистого checkout.

`COMMIT_MESSAGE.md` и `RELEASE_NOTES.md` — локальные черновики для копирования
в GitHub. Они исключены из Git и дистрибутивов; сборки не требуют этих файлов.
Реальные базы, профили, cookies и пользовательские записи не упаковываются.

Успешный unit-тест или рецепт не доказывает запуск на другой ОС.
Фактически выполненные проверки перечисляются в [CHECKS.md](CHECKS.md);
нативные результаты удалённых сборок подтверждаются конкретным запуском Actions.
