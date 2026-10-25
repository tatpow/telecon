# Сборка Телекона

Канонические команды сборки находятся в этом файле.
Пользовательский запуск описан в [README](README.md), правила разработки —
в [CONTRIBUTING](CONTRIBUTING.md) и [AGENTS](AGENTS.md).

Текущая версия — **v0.4.0b**. Сборки создаются без файлов Hashsum.
Все релизные команды используют **нативный CPython 3.12**:
каждая ОС и архитектура собирается на соответствующем хосте.
Для команд `node --check` отдельно нужен [Node.js](https://nodejs.org/en/download).
Он используется для проверки JavaScript и не требуется пользователю готового приложения.

| Цель | Среда CI | Результат | Проверка записи |
|---|---|---|---|
| Linux x64 | Ubuntu 24.04 x64 | Папка + `.tar.gz` | Не поддерживается |
| Windows 11 x64 | Windows Server 2025 x64 | Папка + `.zip` | Локально на Windows 11 |
| macOS arm64 | macOS 15 Apple Silicon | `Telecon.app` + `.zip` | Не поддерживается |
| macOS x64 | macOS 15 Intel | `Telecon.app` + `.zip` | Не поддерживается |

Linux и macOS пока экспериментальные: календарь, настройки, аккаунт,
подключение и выход из встречи. Запись, защита от сна и предупреждения
о свёрнутой встрече остаются Windows-функциями.

## Linux

Инструкции ниже предназначены для **Ubuntu, Linux Mint и Fedora**.
CI использует Ubuntu 24.04. Mint/Fedora не становятся проверенными
дистрибутивами только из-за наличия этих команд.

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
python build_desktop.py --version v0.4.0b --no-hashsum
```

Команды smoke-check и сборки запускайте в графической сессии.
Для CI на Ubuntu сначала установите зависимости Chromium
`python -m playwright install --with-deps chromium`, затем используйте:

```bash
QT_API=pyside6 QTWEBENGINE_DISABLE_SANDBOX=1 QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu xvfb-run -a python build_desktop.py --version v0.4.0b --no-hashsum
```

Отключение sandbox в этой команде относится только к изолированному CI/Xvfb.
Для обычного запуска приложения эти переменные не задаются.

Результат: `dist/Telecon-Linux-x64-v0.4.0b.tar.gz`.
Распакуйте всю папку и запустите `./Telecon`.
Нужны системные библиотеки графики/звука, соответствующие зависимостям выше;
Python в готовом архиве отдельно не требуется. Qt/PySide6 и Chromium включены.
Права исполнения и символьные ссылки сохраняются архивом TAR.
Версия PySide6 зафиксирована на 6.9.3 для сборщика Nuitka 2.8.10, включая
согласованные Qt/shiboken wheels. Не заменяйте её свободным диапазоном без
проверки упакованного приложения. `qt.conf` и `qt6.conf` явно указывают
на включённый каталог `PySide6/qt-plugins`; отсутствие `libqxcb.so` прерывает сборку.

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
.\build\builder-venv\Scripts\python.exe build_windows.py --version v0.4.0b --no-hashsum
```

Nuitka собирает standalone EXE; FFmpeg, Chromium, runtime DLL и документация
добавляются рядом. **Полный self-check упакованного EXE обязателен до ZIP**:
он проверяет настоящий захват/звук, сворачивание, последовательные записи,
MKV/MP4, 7/60 FPS, файлы, уведомления и подключение без записи.

Результат: `dist/Telecon-Windows-x64-v0.4.0b.zip`.
Переносите всю папку, а не один EXE.
`SELF-CHECK.json` и `BUILD-INFO.json` описывают именно эту сборку.
Перед ZIP полный локальный сборщик дополнительно выполняет portable-проверку:
она запускает включённый в архив обычный Chromium в headless-режиме,
не требуя второй копии headless shell. Четыре результата находятся
в `BUILD-INFO.json` → `portable_self_check`.
`--no-hashsum` убирает отдельные checksum-файлы и хеши исходников из манифеста.

### Windows в GitHub Actions

Hosted runner `windows-2025` — это **Windows Server 2025**, а не Windows 11.
CI использует явно ограниченную команду:

```powershell
python build_windows.py --version v0.4.0b --no-hashsum --validation portable
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
python build_desktop.py --version v0.4.0b --no-hashsum
```

Nuitka использует `--macos-create-app-bundle` и Cocoa/PyObjC.
Chromium находится внутри `Telecon.app/Contents/Resources/ms-playwright`.
Скрипт заново запечатывает внешнюю ad-hoc подпись после добавления браузера,
проверяет подпись и выполняет smoke-check готового бинарника с native UI.
Перед подписью HTML/CSS/JS и другие данные из `Contents/MacOS` переносятся
в `Contents/Resources`; относительные ссылки сохраняют пути, используемые
пакетами. Mach-O бинарники остаются на месте. Такой способ размещения данных
описан в [Apple TN2206](https://developer.apple.com/library/archive/technotes/tn2206/).

Результат:
`Telecon-macOS-arm64-v0.4.0b.zip` либо
`Telecon-macOS-x64-v0.4.0b.zip`.
Архив создаётся через `ditto`, сохраняя структуру bundle и ссылки.
Если любая проверка не прошла, ZIP не создаётся.

Ad-hoc подпись **не является Developer ID и нотариализацией**.
Для публичной поставки с подтверждённым разработчиком необходимы собственные
Developer ID, entitlements и отдельный процесс подписания/нотариализации Apple.
Секреты подписи в репозитории не хранятся.
Информация о системном подтверждении запуска —
в [документации Apple](https://support.apple.com/en-ie/102445).

## CI и артефакты

В репозитории подготовлены два workflow:

- **Checks** — unit-тесты и проверка синтаксиса на каждом push и PR;
  на Linux также headless UI-регрессии:
  Ubuntu, Windows Server и macOS двух архитектур.
- **Native builds** — ручная нативная сборка четырёх архивов на трёх ОС.
  Каждый готовый бинарник проходит свой smoke-check перед архивированием.
  Ни теги, ни GitHub Releases автоматически не создаются.

Для запуска: **GitHub → Actions → Native builds → Run workflow**,
выберите ветку с workflow и оставьте `v0.4.0b`.
Ручной запуск доступен после того, как workflow окажется в default branch.
Для первичной проверки до слияния сборка также запускается при push в ветку
`ci/native-builds-*`. После исправлений запускайте workflow для нового коммита:
повтор старого job не использует новые изменения из ветки.

В успешном запуске откройте **Artifacts** и скачайте нужную архитектуру.
Этот раздел находится на общей странице запуска, а не в логе отдельного job.
Имена имеют вид `Telecon-macOS-arm64-v0.4.0b`, без числового ID запуска.
GitHub скачивает артефакт как ZIP; внутри лежит архив приложения:
ZIP для Windows/macOS либо TAR.GZ для Linux. Рабочие архивы создаются в
`dist/` на runner и затем загружаются в Artifacts. В Releases они сами не попадают.
Артефакты хранятся **3 дня**; ограничения хранения отличаются от бесплатного
времени стандартных runner в публичном репозитории.
Каждый архив содержит версию, зависимости и результат проверки.
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

Скрипты отказываются перезаписывать готовый каталог или архив.
При повторной сборке той же версии сохраните прежние файлы в другой папке,
затем повторите команду. В CI каждый запуск начинает с чистого checkout.

`RELEASE_NOTES.md` входит в дистрибутив.
`COMMIT_MESSAGE.md` остаётся описанием коммита в исходниках.
Реальные базы, профили, cookies и пользовательские записи не упаковываются.

Успешный unit-тест или рецепт не доказывает запуск на другой ОС.
Фактически выполненные проверки перечисляются в [CHECKS.md](CHECKS.md);
нативные результаты удалённых сборок подтверждаются конкретным запуском Actions.
