"""Native macOS menu-bar item; all callbacks stay on Cocoa's main loop."""
import AppKit
import Foundation


class TeleconTrayActions(Foundation.NSObject):
    def open_(self, sender):
        self.restore_callback()

    def quit_(self, sender):
        self.exit_callback()

    def applicationShouldTerminate_(self, application):
        self.exit_callback()
        return AppKit.NSTerminateCancel

    def applicationSupportsSecureRestorableState_(self, application):
        return True

    def applicationShouldHandleReopen_hasVisibleWindows_(self, application, visible):
        self.restore_callback()
        return False


class CocoaTray:
    def __init__(self, window, restore, exit):
        from tray import icon_path
        self.window = window
        self.actions = TeleconTrayActions.alloc().init()
        self.actions.restore_callback = restore
        self.actions.exit_callback = exit
        self.image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon_path()))
        if self.image is None:
            raise RuntimeError("Не найдена иконка Телекона.")
        self.image.setSize_(Foundation.NSMakeSize(18, 18))
        self.status = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSSquareStatusItemLength)
        self.status.button().setImage_(self.image)
        self.status.button().setToolTip_("Телекон")
        self.menu = AppKit.NSMenu.alloc().init()
        for title, selector in (("Открыть Телекон", "open:"), ("Выйти", "quit:")):
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
            item.setTarget_(self.actions)
            self.menu.addItem_(item)
        self.status.setMenu_(self.menu)
        self.status.setVisible_(False)
        self.application = AppKit.NSApplication.sharedApplication()
        self.previous_delegate = self.application.delegate()
        self.application.setDelegate_(self.actions)
        # pywebview's WebKit key handler otherwise stops NSApp immediately on
        # Command-Q. Route it through the same graceful exit as our tray menu.
        def key_event(event):
            if (event.modifierFlags() & AppKit.NSEventModifierFlagCommand
                    and (event.charactersIgnoringModifiers() or "").lower() == "q"):
                exit()
                return None
            return event
        self.key_handler = key_event
        self.key_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown, key_event)
        self.timer = None

    def start(self, tick):
        self.timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            .2, True, lambda _: tick())

    def available(self):
        return self.status is not None

    def minimized(self):
        return self.window.native.isMiniaturized()

    def window_visible(self):
        return self.window.native.isVisible() and not self.window.native.isMiniaturized()

    def menu_labels(self):
        return [item.title() for item in self.menu.itemArray()]

    def restore_window(self):
        self.window.restore()
        self.window.show()

    def invoke_menu(self, index):
        self.menu.performActionForItemAtIndex_(index)

    def set_visible(self, enabled):
        self.status.setVisible_(bool(enabled))

    def stop(self):
        if self.timer:
            self.timer.invalidate()
        AppKit.NSEvent.removeMonitor_(self.key_monitor)
        self.application.setDelegate_(self.previous_delegate)
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self.status)
        self.status = None
