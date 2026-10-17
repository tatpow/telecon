from __future__ import annotations

import os
import logging
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from storage import Lecture, TELEMOST_HOME_URL, user_data_dir, validate_telemost_url, validate_profile_url
from recording import FFmpegRecorder, browser_target, check_recorder, target_minimized


# Checked in the user-provided Telemost meeting on 2026-10-16. The outer
# Telemost shell already shows call controls before joining: only controls
# inside private-join iframe establish the actual meeting state.
CONTINUE_SELECTORS = (
    "[data-testid='orb-button']:has-text('Продолжить в браузере')",
    "button:has-text('Продолжить в браузере')",
)
MEETING_FRAME_SELECTOR = 'iframe[src*="telemost.yandex.ru/private-join/"]'
MUTE_MIC_SELECTORS = ("[data-testid='turn-off-mic-button']",)
MUTE_CAMERA_SELECTORS = ("[data-testid='turn-off-camera-button']",)
MIC_ALREADY_OFF_SELECTORS = ("[data-testid='turn-on-mic-button']",)
CAMERA_ALREADY_OFF_SELECTORS = ("[data-testid='turn-on-camera-button']",)
JOIN_SELECTORS = ("[data-testid='enter-conference-button']",)
JOINED_SELECTORS = ("[data-testid='end-call-alt-button']",)
MEDIA_ERROR_TITLE = "Включить видео не удалось"

StatusCallback = Callable[[str], None]
_PROFILE_LOCK = threading.Lock()


class LectureCancelled(RuntimeError):
    """The user stopped the lecture; resources have been cleaned up."""


@contextmanager
def _profile_access():
    if not _PROFILE_LOCK.acquire(blocking=False):
        raise RuntimeError("Профиль Телемоста уже используется. Закройте текущее окно подключения.")
    try:
        yield
    finally:
        _PROFILE_LOCK.release()


def configure_browser_bundle() -> None:
    """Use a Chromium bundle shipped next to a packaged executable, if present."""
    browsers = Path(sys.executable).resolve().parent / "ms-playwright"
    if browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)


def _start_playwright():
    configure_browser_bundle()
    return sync_playwright().start()


def _launch_browser_context(playwright, profile: Path):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=False,
        no_viewport=True,
        args=["--no-first-run", "--no-default-browser-check", "--start-maximized",
              "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
              "--disable-renderer-backgrounding"],
    )


def _configure_muted_preview(context, meeting_url: str) -> None:
    """Start this meeting's observed private-join screen with real devices off."""
    meeting_path = "/private-join/" + urlsplit(meeting_url).path.rstrip("/").rsplit("/", 1)[1]

    def muted_preview(route):
        request = route.request
        parsed = urlsplit(request.url)
        if (request.method != "GET" or not request.is_navigation_request()
                or parsed.hostname != "telemost.yandex.ru"
                or parsed.scheme != "https" or parsed.path.rstrip("/") != meeting_path):
            route.continue_()
            return
        query = parse_qsl(parsed.query, keep_blank_values=True)
        media = [(key, value) for key, value in query if key in ("mic", "camera")]
        if sorted(media) == [("camera", "off"), ("mic", "off")]:
            route.continue_()
            return
        query = [(key, value) for key, value in query if key not in ("mic", "camera")]
        query.extend((("mic", "off"), ("camera", "off")))
        target = urlunsplit(parsed._replace(query=urlencode(query)))
        # A redirect changes location.search as well as the network request;
        # route.continue_(url=...) would leave the UI reading its old flags.
        route.fulfill(status=307, headers={"location": target, "cache-control": "no-store"}, body="")

    context.route("https://telemost.yandex.ru/private-join/**", muted_preview)


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LectureCancelled("Лекция остановлена пользователем.")


def _wait(cancel_event: threading.Event | None, seconds: float) -> None:
    if cancel_event is not None:
        if cancel_event.wait(seconds):
            _check_cancel(cancel_event)
    else:
        time.sleep(seconds)


def _report(callback: StatusCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _report_visibility(callback, hidden, target=None):
    if callback is not None:
        try:
            callback(hidden, target)
        except Exception:
            # An optional alert must never prevent saving or closing the recorder.
            logging.getLogger(__name__).exception("Meeting visibility callback failed")


def _first_visible(scope, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = scope.locator(selector).first
        if locator.is_visible():
            return locator
    return None


def _wait_visible(
    scope,
    selectors: tuple[str, ...],
    timeout_ms: int,
    cancel_event: threading.Event | None = None,
):
    deadline = time.monotonic() + timeout_ms / 1_000
    while True:
        _check_cancel(cancel_event)
        locator = _first_visible(scope, selectors)
        if locator is not None:
            return locator
        if time.monotonic() >= deadline:
            return None
        _wait(cancel_event, 0.1)


def _click_first(
    scope,
    selectors: tuple[str, ...],
    timeout_ms: int,
    required: bool,
    cancel_event: threading.Event | None = None,
) -> bool:
    button = _wait_visible(scope, selectors, timeout_ms, cancel_event)
    if button is not None:
        _check_cancel(cancel_event)
        button.click(timeout=2_000)
        return True
    if required:
        raise RuntimeError(
            "Не найдена кнопка подключения в Телемосте. Возможно, страница изменилась "
            "или требуется ручное подтверждение."
        )
    return False


def _dismiss_media_error(scope) -> bool:
    # Only acknowledge the device error observed in the user's screenshot.
    # Never dismiss arbitrary dialogs, login prompts, or organizer approvals.
    title = scope.get_by_text(MEDIA_ERROR_TITLE, exact=True).first
    if title.is_visible() is not True:
        return False
    button = scope.get_by_role("button", name="Понятно", exact=True).first
    if not button.is_visible():
        return False
    try:
        button.click(timeout=750)
        return True
    except PlaywrightTimeoutError:
        return False


def _try_media_off(scope, turn_off_selectors, already_off_selectors) -> bool:
    if _first_visible(scope, already_off_selectors) is not None:
        return True
    button = _first_visible(scope, turn_off_selectors)
    if button is not None:
        try:
            button.click(timeout=750)
        except PlaywrightTimeoutError:
            # Device initialization, a rerender or an error overlay can block
            # a click. Refresh locators and retry within the shared deadline.
            return False
    return _first_visible(scope, already_off_selectors) is not None


def _ensure_media_off(
    scope,
    turn_off_selectors: tuple[str, ...],
    already_off_selectors: tuple[str, ...],
    name: str,
    cancel_event: threading.Event | None = None,
    timeout_ms: int = 5_000,
) -> None:
    deadline = time.monotonic() + timeout_ms / 1_000
    while True:
        _check_cancel(cancel_event)
        _dismiss_media_error(scope)
        # A "turn on" button means the device is OFF. Never click it.
        if _try_media_off(scope, turn_off_selectors, already_off_selectors):
            return
        if time.monotonic() >= deadline:
            break
        _wait(cancel_event, .1)
    raise RuntimeError(
        f"Не удалось подтвердить, что {name} выключен. Подключение отменено."
    )


def _mute_devices_stably(scope, cancel_event: threading.Event | None = None, *, outer_scope=None) -> None:
    # Telemost initializes device state asynchronously. A transient "off"
    # button in a loading preview does not establish its settled state.
    deadline = time.monotonic() + 15
    stable_since = None
    while time.monotonic() < deadline:
        _check_cancel(cancel_event)
        _dismiss_media_error(scope)
        if outer_scope is not None:
            _dismiss_media_error(outer_scope)
        if (_first_visible(scope, MIC_ALREADY_OFF_SELECTORS) is None
                or _first_visible(scope, CAMERA_ALREADY_OFF_SELECTORS) is None):
            stable_since = None
        # Try both devices even when one has not initialized yet.
        mic_off = _try_media_off(scope, MUTE_MIC_SELECTORS, MIC_ALREADY_OFF_SELECTORS)
        camera_off = _try_media_off(scope, MUTE_CAMERA_SELECTORS, CAMERA_ALREADY_OFF_SELECTORS)
        both_off = mic_off and camera_off
        if both_off:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 1.5:
                return
        else:
            stable_since = None
        _wait(cancel_event, 0.15)
    raise RuntimeError("Не удалось подтвердить устойчивое выключение камеры и микрофона.")


def _close_browser(playwright, context) -> list[str]:
    errors = []
    if context is not None:
        try:
            context.close()
        except Exception as exc:
            errors.append(f"закрытие браузера: {exc}")
    if playwright is not None:
        try:
            playwright.stop()
        except Exception as exc:
            errors.append(f"остановка Playwright: {exc}")
    return errors


def _require_corporate_session(page) -> None:
    # This exact heading was observed in the outer Telemost shell for a guest.
    # Its absence is not proof of authentication; its presence rules out an
    # automatic guest join when the user requested their corporate account.
    guest_heading = page.get_by_role("heading", name="Войдите в аккаунт", exact=True).first
    if guest_heading.is_visible():
        raise RuntimeError(
            "Войдите в корпоративный аккаунт через Настройки → Войти в Яндекс. "
            "Телемост предлагает гостевой вход; автоматическое подключение отменено."
        )


def _wait_for_meeting_frame(page, cancel_event: threading.Event | None = None):
    deadline = time.monotonic() + 90
    continued = False
    while time.monotonic() < deadline:
        _check_cancel(cancel_event)
        if page.is_closed():
            raise RuntimeError("Окно Телемоста было закрыто.")
        if _first_visible(page, (MEETING_FRAME_SELECTOR,)) is not None:
            return page.frame_locator(MEETING_FRAME_SELECTOR)
        if not continued:
            button = _first_visible(page, CONTINUE_SELECTORS)
            if button is not None:
                _require_corporate_session(page)
                button.click(timeout=2_000)
                continued = True
        _wait(cancel_event, 0.1)
    raise RuntimeError(
        "Телемост не показал экран подключения. Проверьте корпоративный вход "
        "и доступность встречи через настройку профиля."
    )


def _join_telemost(
    lecture: Lecture,
    cancel_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
):
    meeting_url = validate_telemost_url(lecture.url)
    _check_cancel(cancel_event)
    profile = user_data_dir() / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    playwright = _start_playwright()
    context = None
    try:
        context = _launch_browser_context(playwright, profile)
        context.set_default_timeout(2_000)
        _configure_muted_preview(context, meeting_url)
        _check_cancel(cancel_event)
        # Real devices remain usable for manual enabling after the safe join.
        context.grant_permissions(["microphone", "camera"], origin="https://telemost.yandex.ru")
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(meeting_url, wait_until="domcontentloaded", timeout=15_000)
        page.bring_to_front()
        _report(status_callback, "Телемост: ожидаю экран подключения…")
        meeting = _wait_for_meeting_frame(page, cancel_event)
        controls = MUTE_MIC_SELECTORS + MIC_ALREADY_OFF_SELECTORS
        if _wait_visible(meeting, controls, 30_000, cancel_event) is None:
            raise RuntimeError("На экране Телемоста не найдены элементы управления устройствами.")
        for attempt in range(3):
            _report(status_callback, "Телемост: выключаю камеру и микрофон перед подключением…")
            _mute_devices_stably(meeting, cancel_event, outer_scope=page)
            _require_corporate_session(page)
            try:
                _click_first(meeting, JOIN_SELECTORS, 15_000, required=True, cancel_event=cancel_event)
                break
            except PlaywrightTimeoutError:
                if attempt == 2:
                    raise RuntimeError("Кнопка подключения Телемоста остаётся недоступной после повторных попыток.") from None
                _report(status_callback, "Телемост: повторно проверяю устройства и кнопку подключения…")
        if _wait_visible(meeting, JOINED_SELECTORS, 60_000, cancel_event) is None:
            raise RuntimeError(
                "Телемост не подтвердил вход во встречу: возможно, требуется допуск "
                "организатора или изменился интерфейс. Запись не запущена."
            )
        _mute_devices_stably(meeting, cancel_event, outer_scope=page)
        _report(status_callback, "Вход в Телемост подтверждён; запускаю запись FFmpeg…")
        return playwright, context, page, meeting
    except Exception as exc:
        cleanup_errors = _close_browser(playwright, context)
        if cleanup_errors:
            raise RuntimeError(f"{exc}; {'; '.join(cleanup_errors)}") from exc
        raise


def prepare_browser_profile(
    url: str = TELEMOST_HOME_URL,
    *,
    cancel_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
) -> None:
    """Open the private profile for manual corporate Yandex/SSO login only."""
    profile_url = validate_profile_url(url)
    _check_cancel(cancel_event)
    with _profile_access():
        profile = user_data_dir() / "browser-profile"
        profile.mkdir(parents=True, exist_ok=True)
        playwright = _start_playwright()
        context = None
        error = None
        try:
            context = _launch_browser_context(playwright, profile)
            closed = threading.Event()
            context.on("close", lambda *_: closed.set())
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(profile_url, wait_until="domcontentloaded", timeout=15_000)
            page.bring_to_front()
            _report(
                status_callback,
                "Войдите в корпоративный аккаунт Яндекса в открытом Chromium. "
                "Пароль и код вводите только на странице вуза/Яндекса. "
                "После входа закройте окно браузера; на настройку отведено 10 минут.",
            )
            deadline = time.monotonic() + 600
            while not closed.is_set():
                _check_cancel(cancel_event)
                if time.monotonic() >= deadline:
                    raise RuntimeError("Истекли 10 минут настройки профиля. Повторите вход, если он не завершён.")
                # Playwright dispatches browser events while pumping its API.
                if page.is_closed():
                    if not context.pages:
                        break
                    page = context.pages[0]
                try:
                    page.wait_for_timeout(250)
                except PlaywrightError:
                    if closed.is_set():
                        break
                    live_pages = [candidate for candidate in context.pages if not candidate.is_closed()]
                    if page.is_closed() and live_pages:
                        # Corporate SSO can replace its original tab with a
                        # popup while this wait is pumping browser events.
                        page = live_pages[0]
                        continue
                    raise
        except Exception as exc:
            # Closing Chromium can interrupt the final Playwright wait.
            if context is None or not closed.is_set():
                error = exc
        finally:
            cleanup_errors = _close_browser(playwright, context)
        if cleanup_errors:
            prefix = f"{error}; " if error is not None else ""
            raise RuntimeError(prefix + "; ".join(cleanup_errors)) from error
        if error is not None:
            raise error
        _report(status_callback, "Окно настройки профиля закрыто. Лекции будут открываться в том же профиле Chromium.")


def run_lecture(lecture: Lecture, settings: dict | None = None, *,
                cancel_event: threading.Event | None = None,
                status_callback: StatusCallback | None = None,
                visibility_callback: Callable | None = None) -> str:
    """Record only the configured meeting browser window and its process audio."""
    _check_cancel(cancel_event)
    validate_telemost_url(lecture.url)
    if lecture.ends_at <= datetime.now(tz=lecture.ends_at.tzinfo):
        raise RuntimeError("Время окончания лекции уже прошло.")
    with _profile_access():
        config = dict(settings or {})
        _report(status_callback, "Проверяю FFmpeg и захват звука процесса…")
        check_recorder(config)
        _check_cancel(cancel_event)
        playwright = context = recorder = None
        recording_started = False
        error = None
        output = None
        cleanup_errors = []
        try:
            playwright, context, page, meeting = _join_telemost(lecture, cancel_event, status_callback)
            _check_cancel(cancel_event)
            if lecture.ends_at <= datetime.now(tz=lecture.ends_at.tzinfo):
                raise RuntimeError("Время лекции истекло во время подключения.")
            target = browser_target(user_data_dir() / "browser-profile")
            recorder = FFmpegRecorder(target, config, lecture.id)
            recorder.start(cancel_event)
            recording_started = True
            _report(status_callback, f"Идёт запись FFmpeg: {lecture.title}")
            while datetime.now(tz=lecture.ends_at.tzinfo) < lecture.ends_at:
                _check_cancel(cancel_event)
                if page.is_closed() or _first_visible(meeting, JOINED_SELECTORS) is None:
                    raise RuntimeError("Соединение с Телемостом прервано; запись остановлена.")
                recorder.check()
                if visibility_callback is not None:
                    _report_visibility(visibility_callback, target_minimized(target), target)
                remaining = (lecture.ends_at - datetime.now(tz=lecture.ends_at.tzinfo)).total_seconds()
                _wait(cancel_event, min(.5, max(0, remaining)))
            _check_cancel(cancel_event)
        except Exception as exc:
            error = exc
        finally:
            _report_visibility(visibility_callback, False)
            if recorder is not None:
                try:
                    output = recorder.stop(validate=recording_started)
                    if recording_started:
                        _report(status_callback, f"Запись сохранена: {output}")
                except Exception as exc:
                    cleanup_errors.append(f"сохранение записи FFmpeg: {exc}")
            cleanup_errors.extend(_close_browser(playwright, context))
        if cleanup_errors:
            prefix = f"{error}; " if error is not None else ""
            raise RuntimeError(prefix + "; ".join(cleanup_errors)) from error
        if error is not None:
            raise error
        return output
