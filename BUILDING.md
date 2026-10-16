# Сборка Телекона на разных платформах

Телекон — **vibecode / GVP (Generated Viable Product)** в смысле
[концепции GVP](https://github.com/tatpow/GVP). Способ создания не заменяет
проверку готового приложения.

| Целевая ОС | Где собирать | Статус |
| --- | --- | --- |
| Windows x64, build 20348+ | Windows x64, CPython 3.12 | Полный backend записи; EXE проверяется перед ZIP |
| Linux x64 | Linux x64, CPython 3.12, GCC, Qt/PySide6 | Экспериментальные UI/календарь/аккаунт; записи и защиты от сна нет |
| macOS arm64 или x86_64 | macOS с Python той же архитектуры и Xcode CLI tools | Экспериментальные UI/календарь/аккаунт; записи и защиты от сна нет |

Linux/macOS-команды ниже не проверялись на этой Windows. Это инструкции для
нативной сборки и последующей проверки на соответствующей ОС, а не готовые релизы.
Не обещайте переносимость DLL или Windows-захвата на другие платформы.

## Windows x64 — проверяемая сборка

В PowerShell из корня проекта:

```powershell
py -3.12 -m venv build\builder-venv
.\build\builder-venv\Scripts\python.exe -m pip install -r requirements-build.lock.txt
.\build\builder-venv\Scripts\python.exe -m playwright install chromium
.\build\builder-venv\Scripts\python.exe -m unittest discover -s tests -v
.\build\builder-venv\Scripts\python.exe build_windows.py --version 2026.10.16.3
```

Нужны установленный Microsoft Visual C++ x64 Redistributable и Edge WebView2.
Nuitka скачивает совместимый MinGW при необходимости; Visual Studio для этого
пути не требуется. Используйте новый номер версии для новых исходников.
Windows lock-файл содержит Windows wheels и не применяется на Linux/macOS.

Скрипт включает `ui/`, FFmpeg, Chromium, библиотеки захвата, app-local C++ runtime,
лицензии и Markdown-документацию. Плагин pywebview в Nuitka 2.8 исключает новый
`webview.platforms.win32`; скрипт отключает этот фильтр и включает Windows-модуль
явно, исключая платформенные модули других ОС.

Готовый EXE сначала проверяется на временных данных: закрытие ошибки камеры,
выключение устройств в тестовом интерфейсе, запуск Chromium, настоящая запись
синтетического видео/звука через FFmpeg, native-окно и Python bridge, освобождение
power request. Корпоративная сессия и настоящие лекции не используются.

Результаты — `dist/Telecon-Windows-x64-<версия>/Telecon.exe`, ZIP всей папки и
`.zip.sha256`. В релизе находятся `SELF-CHECK.json`, `BUILD-INFO.json` и
`START-HERE.txt`. Распаковывайте всю папку, не копируйте один EXE.

```powershell
.\dist\Telecon-Windows-x64-2026.10.16.3\Telecon.exe --self-check "$env:TEMP\telecon-self-check.json"
```

`--skip-compile` предназначен только для повторной упаковки неизменённого
скомпилированного приложения; не используйте его после правки исходников или
для изменения версии уже собранного EXE.

## Linux — экспериментальная сборка интерфейса

Установите CPython 3.12 с venv, GCC/G++, patchelf и системные библиотеки Qt/
Chromium для вашего дистрибутива. Например, на Debian/Ubuntu инструменты сборки
обычно устанавливаются пакетами `build-essential`, `python3-dev`, `python3-venv`,
`patchelf`. Используйте виртуальное окружение, не системный pip.

```bash
python3.12 -m venv build/linux-venv
source build/linux-venv/bin/activate
python -m pip install -r requirements-build.txt 'pywebview[pyside6]'
python -m playwright install --with-deps chromium
python -m nuitka --standalone --enable-plugin=pyside6 \
  --include-data-dir=ui=ui --include-package=imageio_ffmpeg \
  --playwright-include-browser=none \
  --nofollow-import-to=proctap,windows_capture \
  --output-dir=build/linux --output-filename=Telecon app.py
```

Чтобы Chromium не зависел от кеша сборщика, скопируйте установленную ревизию
рядом с бинарным файлом, в поддерживаемую приложением папку `ms-playwright`:

```bash
python - <<'PY'
from pathlib import Path
from shutil import copytree
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    revision = Path(p.chromium.executable_path).parent.parent
copytree(revision, Path('build/linux/app.dist/ms-playwright') / revision.name)
PY
PYWEBVIEW_GUI=qt ./build/linux/app.dist/Telecon
```

Переносите всю `app.dist`. На целевой машине проверьте QtWebEngine, календарь,
сохранение тестовой лекции и вход в аккаунт через главную Телемоста. Не запускайте
Windows `--self-check`: он намеренно проверяет Windows API записи и не является
кроссплатформенным тестом. Не включайте расписание/запись в этой экспериментальной
сборке: соответствующие native-backend и предотвращение сна не портированы.
Зависимости Chromium устанавливаются отдельно на целевой системе; упаковка
библиотек Qt не делает Linux-бинарник совместимым со всеми дистрибутивами.

## macOS — экспериментальная сборка интерфейса

Установите Xcode Command Line Tools (`xcode-select --install`) и обычный
CPython 3.12 той архитектуры, для которой собираете приложение. Для Apple Silicon
и Intel нужны отдельные сборки; эти команды не создают universal binary.

```bash
python3.12 -m venv build/macos-venv
source build/macos-venv/bin/activate
python -m pip install -r requirements-build.txt
python -m playwright install chromium
python -m nuitka --standalone --macos-create-app-bundle \
  --include-data-dir=ui=ui --include-package=imageio_ffmpeg \
  --playwright-include-browser=none \
  --nofollow-import-to=proctap,windows_capture \
  --output-dir=build/macos --output-filename=Telecon app.py
```

pywebview использует Cocoa/WebKit и PyObjC. Проверьте созданный `.app` в
`build/macos`: имя каталога приложения определяется Nuitka. После определения
точного пути к бинарнику `Contents/MacOS/Telecon` скопируйте установленную
ревизию Chromium рядом с ним, в `Contents/MacOS/ms-playwright/<ревизия>`;
принцип Python-копирования тот же, что в Linux-рецепте. Переносите весь `.app`.

Все изменения содержимого bundle, включая Chromium и Markdown-документацию,
выполняются до финальной подписи. Для публичного распространения нужны корректная
подпись Developer ID и notarization Apple, включая вложенные executable/framework;
сам по себе запуск Nuitka не подтверждает их выполнение. Проверьте запуск
на целевой macOS, календарь и корпоративный вход. Windows-запись и защита от сна
в этой сборке отсутствуют; Windows `--self-check` к ней неприменим.

## Проверка и границы релиза

Для любой платформы сначала проверяйте standalone-папку или `.app` на целевой ОС.
После изменения исходников нужна новая компиляция, а не копирование новых `.py`
поверх скомпилированного приложения. Тестовые и реальные базы, cookies, профили
браузеров и записи не должны попадать в папку сборки или архив.

Полный unittest-набор включает Windows-интеграции и запускается в поддерживаемом
Windows-окружении. Для предварительной проверки общего кода на Linux/macOS можно
отдельно выполнить `python -m unittest discover -s tests -p test_storage_scheduler.py -v`;
это не заменяет нативную проверку интерфейса и не подтверждает backend записи.
Результаты фактических проверок перечислены в [CHECKS.md](CHECKS.md).

Документация инструментов: [установка pywebview](https://pywebview.flowrl.com/guide/installation.html),
[standalone-сборка Nuitka](https://nuitka.net/user-documentation/use-cases.html).
