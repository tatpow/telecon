import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import integrations
from storage import Lecture


def make_lecture(url="https://telemost.yandex.ru/j/42574758760781"):
    starts_at = datetime.now()
    return Lecture(7, "Лекция", url, starts_at, starts_at + timedelta(minutes=1))


class FakeLocator:
    def __init__(self, scope, selector):
        self.scope = scope
        self.selector = selector
        self.first = self

    def is_visible(self):
        return self.scope.visible.get(self.selector, False)

    def click(self, **_kwargs):
        if self.scope.visible.get("error-title") and self.selector != "ack":
            raise PlaywrightTimeoutError("Device error dialog intercepts pointer events")
        self.scope.clicked.append(self.selector)
        if self.selector == "ack":
            self.scope.visible["error-title"] = False
            self.scope.visible["ack"] = False
        if self.selector in integrations.MUTE_MIC_SELECTORS:
            self.scope.set_media(mic=False)
        elif self.selector in integrations.MUTE_CAMERA_SELECTORS:
            self.scope.set_media(camera=False)
        elif self.selector in integrations.JOIN_SELECTORS:
            self.scope.visible[integrations.JOINED_SELECTORS[0]] = True


class FakeScope:
    def __init__(self):
        self.visible = {}
        self.clicked = []

    def locator(self, selector):
        return FakeLocator(self, selector)

    def get_by_text(self, text, exact=False):
        return FakeLocator(self, "error-title" if text == integrations.MEDIA_ERROR_TITLE else "unknown-text")

    def get_by_role(self, role, name=None, exact=False):
        return FakeLocator(self, "ack" if role == "button" and name == "Понятно" else "unknown-role")

    def set_media(self, mic=None, camera=None):
        if mic is not None:
            self.visible[integrations.MUTE_MIC_SELECTORS[0]] = mic
            self.visible[integrations.MIC_ALREADY_OFF_SELECTORS[0]] = not mic
        if camera is not None:
            self.visible[integrations.MUTE_CAMERA_SELECTORS[0]] = camera
            self.visible[integrations.CAMERA_ALREADY_OFF_SELECTORS[0]] = not camera


class IntegrationTests(unittest.TestCase):
    def test_browser_has_real_devices_and_persistent_maximized_profile(self):
        playwright = Mock()
        profile = Path("browser-profile")
        integrations._launch_browser_context(playwright, profile)
        options = playwright.chromium.launch_persistent_context.call_args.kwargs
        self.assertEqual(options["user_data_dir"], str(profile))
        self.assertFalse(options["headless"])
        self.assertTrue(options["no_viewport"])
        self.assertIn("--start-maximized", options["args"])
        self.assertFalse(any("fake" in arg or "disable-media" in arg for arg in options["args"]))

    def test_bundled_chromium_directory_is_preferred_for_packaged_app(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            executable = Path(temporary_dir) / "app.exe"
            browsers = Path(temporary_dir) / "ms-playwright"
            browsers.mkdir()
            with (
                patch.object(integrations.sys, "executable", str(executable)),
                patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "old-cache"}),
            ):
                integrations.configure_browser_bundle()
                self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], str(browsers.resolve()))

    def test_already_muted_devices_are_observed_and_never_clicked_on(self):
        scope = FakeScope()
        scope.set_media(mic=False, camera=False)
        integrations._ensure_media_off(
            scope, integrations.MUTE_MIC_SELECTORS, integrations.MIC_ALREADY_OFF_SELECTORS, "микрофон"
        )
        integrations._ensure_media_off(
            scope, integrations.MUTE_CAMERA_SELECTORS, integrations.CAMERA_ALREADY_OFF_SELECTORS, "камера"
        )
        self.assertEqual(scope.clicked, [])

    def test_enabled_devices_are_muted_and_the_new_state_is_verified(self):
        scope = FakeScope()
        scope.set_media(mic=True, camera=True)
        integrations._ensure_media_off(
            scope, integrations.MUTE_MIC_SELECTORS, integrations.MIC_ALREADY_OFF_SELECTORS, "микрофон"
        )
        integrations._ensure_media_off(
            scope, integrations.MUTE_CAMERA_SELECTORS, integrations.CAMERA_ALREADY_OFF_SELECTORS, "камера"
        )
        self.assertEqual(scope.clicked, [*integrations.MUTE_MIC_SELECTORS, *integrations.MUTE_CAMERA_SELECTORS])

    def test_unknown_device_state_cancels_join(self):
        scope = FakeScope()
        with patch.object(integrations, "_wait_visible", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "не удалось подтвердить|Не удалось подтвердить"):
                integrations._ensure_media_off(
                    scope, integrations.MUTE_MIC_SELECTORS, integrations.MIC_ALREADY_OFF_SELECTORS, "микрофон",
                    timeout_ms=0,
                )
        self.assertEqual(scope.clicked, [])

    def test_device_can_appear_already_muted_after_preview_initializes(self):
        scope = FakeScope()

        def initialized(*_args):
            scope.set_media(mic=False)

        with patch.object(integrations, "_wait", side_effect=initialized):
            integrations._ensure_media_off(
                scope, integrations.MUTE_MIC_SELECTORS, integrations.MIC_ALREADY_OFF_SELECTORS, "микрофон"
            )
        self.assertEqual(scope.clicked, [])

    def test_camera_error_is_acknowledged_before_muting_and_does_not_enable_camera(self):
        scope = FakeScope()
        scope.set_media(mic=False, camera=True)
        scope.visible.update({"error-title": True, "ack": True})
        integrations._ensure_media_off(scope, integrations.MUTE_CAMERA_SELECTORS,
                                       integrations.CAMERA_ALREADY_OFF_SELECTORS, "камера")
        self.assertEqual(scope.clicked, ["ack", integrations.MUTE_CAMERA_SELECTORS[0]])
        self.assertTrue(scope.visible[integrations.CAMERA_ALREADY_OFF_SELECTORS[0]])

    def test_transient_blocked_camera_click_is_retried(self):
        scope = FakeScope()
        scope.set_media(camera=True)
        original = FakeLocator.click
        attempts = []
        def click(locator, **kwargs):
            if locator.selector in integrations.MUTE_CAMERA_SELECTORS and not attempts:
                attempts.append(True)
                raise PlaywrightTimeoutError("Preview rerendered")
            return original(locator, **kwargs)
        with patch.object(FakeLocator, "click", click), patch.object(integrations, "_wait"):
            integrations._ensure_media_off(scope, integrations.MUTE_CAMERA_SELECTORS,
                                           integrations.CAMERA_ALREADY_OFF_SELECTORS, "камера")
        self.assertEqual(scope.clicked, [integrations.MUTE_CAMERA_SELECTORS[0]])

    def test_unknown_microphone_does_not_prevent_attempt_to_turn_camera_off(self):
        scope = FakeScope()
        scope.set_media(camera=True)
        clock = [0.0]
        def advance(_event, seconds):
            clock[0] += seconds
        with patch.object(integrations.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(integrations, "_wait", side_effect=advance):
            with self.assertRaisesRegex(RuntimeError, "устойчивое выключение"):
                integrations._mute_devices_stably(scope)
        self.assertEqual(scope.clicked, [integrations.MUTE_CAMERA_SELECTORS[0]])

    def test_initial_preview_redirect_only_changes_devices_for_configured_meeting(self):
        context = Mock()
        integrations._configure_muted_preview(context, make_lecture().url)
        handler = context.route.call_args.args[1]
        route = Mock()
        route.request.method = "GET"
        route.request.is_navigation_request.return_value = True
        route.request.url = "https://telemost.yandex.ru/private-join/42574758760781?selectedUid=123&mic=on&camera=on&lang=ru"
        handler(route)
        from urllib.parse import urlsplit, parse_qs
        target = route.fulfill.call_args.kwargs["headers"]["location"]
        self.assertEqual(parse_qs(urlsplit(target).query),
                         {"selectedUid": ["123"], "mic": ["off"], "camera": ["off"], "lang": ["ru"]})
        route.fulfill.assert_called_once()
        route.continue_.assert_not_called()
        for url in (target, "https://telemost.yandex.ru/private-join/other?mic=on&camera=on"):
            route.reset_mock()
            route.request.url = url
            handler(route)
            route.continue_.assert_called_once()
            route.fulfill.assert_not_called()

    def test_asynchronous_device_initialization_is_muted_before_stability_is_accepted(self):
        scope = FakeScope()
        scope.set_media(mic=False, camera=False)
        clock = [0.0]
        initialized = [False]

        def advance(_cancel_event, seconds):
            clock[0] += seconds
            if clock[0] >= 0.3 and not initialized[0]:
                initialized[0] = True
                scope.set_media(mic=True, camera=True)

        with (
            patch.object(integrations.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(integrations, "_wait", side_effect=advance),
        ):
            integrations._mute_devices_stably(scope)
        self.assertGreaterEqual(clock[0], 1.8)
        self.assertEqual(scope.clicked, [*integrations.MUTE_MIC_SELECTORS, *integrations.MUTE_CAMERA_SELECTORS])

    def test_join_uses_verified_iframe_and_confirms_media_again_after_join(self):
        lecture = make_lecture()
        meeting = FakeScope()
        meeting.set_media(mic=False, camera=False)
        meeting.visible[integrations.JOIN_SELECTORS[0]] = True
        outer = FakeScope()
        outer.visible[integrations.CONTINUE_SELECTORS[0]] = True
        outer.visible[integrations.MEETING_FRAME_SELECTOR] = True
        page = Mock()
        page.is_closed.return_value = False
        page.get_by_role.return_value.first.is_visible.return_value = False
        page.locator.side_effect = outer.locator
        page.frame_locator.return_value = meeting
        context = Mock(pages=[page])
        playwright = Mock()
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(integrations, "_start_playwright", return_value=playwright),
                patch.object(integrations, "_launch_browser_context", return_value=context),
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)),
                patch.object(integrations, "_mute_devices_stably") as mute,
            ):
                result = integrations._join_telemost(lecture)
        self.assertEqual(result, (playwright, context, page, meeting))
        page.frame_locator.assert_called_once_with(integrations.MEETING_FRAME_SELECTOR)
        self.assertEqual(mute.call_count, 2)
        self.assertEqual(meeting.clicked, [integrations.JOIN_SELECTORS[0]])
        context.grant_permissions.assert_called_once_with(
            ["microphone", "camera"], origin="https://telemost.yandex.ru"
        )
        context.close.assert_not_called()

    def test_join_retries_after_camera_error_covers_connect_button(self):
        meeting = FakeScope()
        meeting.set_media(mic=False, camera=False)
        meeting.visible[integrations.JOIN_SELECTORS[0]] = True
        page = Mock()
        page.is_closed.return_value = False
        page.get_by_role.return_value.first.is_visible.return_value = False
        context = Mock(pages=[page])
        original = FakeLocator.click
        attempts = []
        def click(locator, **kwargs):
            if locator.selector in integrations.JOIN_SELECTORS and not attempts:
                attempts.append(True)
                meeting.visible.update({"error-title": True, "ack": True})
                meeting.set_media(camera=True)
                raise PlaywrightTimeoutError("Dialog appeared immediately before join")
            return original(locator, **kwargs)
        clock = [0.0]
        def advance(_event, seconds):
            clock[0] += seconds
        with tempfile.TemporaryDirectory() as temporary_dir, \
                patch.object(integrations, "_start_playwright", return_value=Mock()), \
                patch.object(integrations, "_launch_browser_context", return_value=context), \
                patch.object(integrations, "_wait_for_meeting_frame", return_value=meeting), \
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)), \
                patch.object(integrations.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(integrations, "_wait", side_effect=advance), \
                patch.object(FakeLocator, "click", click):
            integrations._join_telemost(make_lecture())
        self.assertEqual(meeting.clicked, ["ack", integrations.MUTE_CAMERA_SELECTORS[0],
                                          integrations.JOIN_SELECTORS[0]])
        context.close.assert_not_called()

    def test_outer_shell_call_controls_are_never_evidence_of_join(self):
        meeting = FakeScope()
        meeting.set_media(mic=False, camera=False)
        meeting.visible[integrations.JOIN_SELECTORS[0]] = True
        outer = FakeScope()
        outer.visible[integrations.MEETING_FRAME_SELECTOR] = True
        outer.visible[integrations.JOINED_SELECTORS[0]] = True
        page = Mock()
        page.is_closed.return_value = False
        page.get_by_role.return_value.first.is_visible.return_value = False
        page.locator.side_effect = outer.locator
        page.frame_locator.return_value = meeting
        context = Mock(pages=[page])
        playwright = Mock()
        real_wait = integrations._wait_visible

        def wait(scope, selectors, timeout_ms, cancel_event=None):
            if selectors == integrations.JOINED_SELECTORS:
                return None
            return real_wait(scope, selectors, 0, cancel_event)

        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(integrations, "_start_playwright", return_value=playwright),
                patch.object(integrations, "_launch_browser_context", return_value=context),
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)),
                patch.object(integrations, "_mute_devices_stably"),
                patch.object(integrations, "_wait_visible", side_effect=wait),
                self.assertRaisesRegex(RuntimeError, "не подтвердил вход"),
            ):
                integrations._join_telemost(make_lecture())
        context.close.assert_called_once()
        playwright.stop.assert_called_once()

    def test_continue_button_is_clicked_even_when_it_loads_after_several_seconds(self):
        outer = FakeScope()
        page = Mock()
        page.is_closed.return_value = False
        page.get_by_role.return_value.first.is_visible.return_value = False
        page.locator.side_effect = outer.locator
        meeting = FakeScope()
        page.frame_locator.return_value = meeting
        clock = [0.0]

        def advance(_event, seconds):
            clock[0] += seconds
            if clock[0] >= 4:
                outer.visible[integrations.CONTINUE_SELECTORS[0]] = True
            if outer.clicked:
                outer.visible[integrations.MEETING_FRAME_SELECTOR] = True

        with (
            patch.object(integrations.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(integrations, "_wait", side_effect=advance),
        ):
            self.assertIs(integrations._wait_for_meeting_frame(page), meeting)
        self.assertGreaterEqual(clock[0], 4)
        self.assertEqual(outer.clicked, [integrations.CONTINUE_SELECTORS[0]])

    def test_expired_corporate_session_is_rejected_before_continue(self):
        outer = FakeScope()
        outer.visible[integrations.CONTINUE_SELECTORS[0]] = True
        page = Mock()
        page.is_closed.return_value = False
        page.locator.side_effect = outer.locator
        page.get_by_role.return_value.first.is_visible.return_value = True

        with self.assertRaisesRegex(RuntimeError, "Войдите в корпоративный аккаунт"):
            integrations._wait_for_meeting_frame(page)
        self.assertEqual(outer.clicked, [])
        page.frame_locator.assert_not_called()
        page.get_by_role.assert_called_once_with("heading", name="Войдите в аккаунт", exact=True)

    def test_guest_session_in_ready_iframe_is_rejected_before_join(self):
        meeting = FakeScope()
        meeting.set_media(mic=False, camera=False)
        meeting.visible[integrations.JOIN_SELECTORS[0]] = True
        outer = FakeScope()
        outer.visible[integrations.MEETING_FRAME_SELECTOR] = True
        page = Mock()
        page.is_closed.return_value = False
        page.get_by_role.return_value.first.is_visible.return_value = True
        page.locator.side_effect = outer.locator
        page.frame_locator.return_value = meeting
        context = Mock(pages=[page])
        playwright = Mock()
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(integrations, "_start_playwright", return_value=playwright),
                patch.object(integrations, "_launch_browser_context", return_value=context),
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)),
                patch.object(integrations, "_mute_devices_stably"),
                self.assertRaisesRegex(RuntimeError, "Войдите в корпоративный аккаунт"),
            ):
                integrations._join_telemost(make_lecture())
        self.assertEqual(meeting.clicked, [])
        context.close.assert_called_once()
        playwright.stop.assert_called_once()

    def test_navigation_failure_closes_browser_and_does_not_claim_join(self):
        page = Mock()
        page.goto.side_effect = PlaywrightError("navigation failed")
        context = Mock(pages=[page])
        playwright = Mock()
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(integrations, "_start_playwright", return_value=playwright),
                patch.object(integrations, "_launch_browser_context", return_value=context),
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)),
                self.assertRaisesRegex(PlaywrightError, "navigation failed"),
            ):
                integrations._join_telemost(make_lecture())
        context.close.assert_called_once()
        playwright.stop.assert_called_once()

    def test_only_configured_telemost_meeting_links_are_automated(self):
        for url in (
            "http://telemost.yandex.ru/j/123", "https://example.org/j/123",
            "https://telemost.yandex.ru.example.org/j/123", "https://telemost.yandex.ru/",
            "https://user:secret@telemost.yandex.ru/j/123", "https://telemost.yandex.ru/j/",
        ):
            with self.subTest(url=url), patch.object(integrations, "_start_playwright") as start:
                with self.assertRaises(ValueError):
                    integrations._join_telemost(make_lecture(url))
                start.assert_not_called()

    def test_profile_setup_opens_only_requested_url_and_never_joins_or_records(self):
        page = Mock()
        page.is_closed.return_value = True
        context = Mock(pages=[])
        context.new_page.return_value = page
        playwright = Mock()
        statuses = []
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(integrations, "_start_playwright", return_value=playwright),
                patch.object(integrations, "_launch_browser_context", return_value=context),
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)),
                patch.object(integrations, "FFmpegRecorder") as recorder,
            ):
                integrations.prepare_browser_profile(make_lecture().url, status_callback=statuses.append)
        page.goto.assert_called_once_with(make_lecture().url, wait_until="domcontentloaded", timeout=15_000)
        page.locator.assert_not_called()
        context.grant_permissions.assert_not_called()
        recorder.assert_not_called()
        context.close.assert_called_once()
        playwright.stop.assert_called_once()
        self.assertTrue(any("корпоративный аккаунт" in status for status in statuses))

    def test_profile_setup_defaults_to_homepage_without_opening_example_meeting(self):
        page = Mock()
        page.is_closed.return_value = True
        context = Mock(pages=[])
        context.new_page.return_value = page
        with tempfile.TemporaryDirectory() as temporary_dir, \
                patch.object(integrations, "_start_playwright", return_value=Mock()), \
                patch.object(integrations, "_launch_browser_context", return_value=context), \
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)):
            integrations.prepare_browser_profile()
        page.goto.assert_called_once_with("https://telemost.yandex.ru/", wait_until="domcontentloaded", timeout=15_000)
        context.grant_permissions.assert_not_called()

    def test_profile_setup_cancellation_cleans_up_and_releases_profile(self):
        event = threading.Event()
        page = Mock()
        page.is_closed.return_value = False
        page.wait_for_timeout.side_effect = lambda _ms: event.set()
        context = Mock(pages=[page])
        playwright = Mock()
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(integrations, "_start_playwright", return_value=playwright),
                patch.object(integrations, "_launch_browser_context", return_value=context),
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)),
                self.assertRaises(integrations.LectureCancelled),
            ):
                integrations.prepare_browser_profile(make_lecture().url, cancel_event=event)
        context.close.assert_called_once()
        playwright.stop.assert_called_once()
        self.assertFalse(integrations._PROFILE_LOCK.locked())

    def test_corporate_sso_popup_survives_original_tab_closing_during_wait(self):
        original = Mock()
        original.is_closed.return_value = False
        popup = Mock()
        popup.is_closed.return_value = False
        context = Mock(pages=[original, popup])
        playwright = Mock()

        def close_original(_ms):
            original.is_closed.return_value = True
            context.pages = [popup]
            raise PlaywrightError("Target page has been closed")

        def finish_login(_ms):
            context.pages = []
            context.on.call_args.args[1]()
            raise PlaywrightError("Target browser has been closed")

        original.wait_for_timeout.side_effect = close_original
        popup.wait_for_timeout.side_effect = finish_login
        with tempfile.TemporaryDirectory() as temporary_dir:
            with (
                patch.object(integrations, "_start_playwright", return_value=playwright),
                patch.object(integrations, "_launch_browser_context", return_value=context),
                patch.object(integrations, "user_data_dir", return_value=Path(temporary_dir)),
            ):
                integrations.prepare_browser_profile(make_lecture().url)
        original.wait_for_timeout.assert_called_once_with(250)
        popup.wait_for_timeout.assert_called_once_with(250)
        context.close.assert_called_once()
        playwright.stop.assert_called_once()
        self.assertFalse(integrations._PROFILE_LOCK.locked())

    def test_profile_cannot_be_used_by_two_jobs_at_once(self):
        with integrations._profile_access(), patch.object(integrations, "_start_playwright") as start:
            with self.assertRaisesRegex(RuntimeError, "уже используется"):
                integrations.prepare_browser_profile(make_lecture().url)
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
