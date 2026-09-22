import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ai_probe.tray import TrayController
from ai_probe.ui.probe_app import ProbeApp


class MinimizeTests(unittest.TestCase):
    def test_toolbar_falls_back_to_taskbar_when_tray_unavailable(self):
        app = SimpleNamespace(root=Mock(), status=Mock(), tray=Mock())
        app.tray.minimize.return_value = False
        ProbeApp._minimize_to_tray(app)
        app.root.iconify.assert_called_once_with()
        app.root.withdraw.assert_not_called()
        app.root.deiconify.assert_not_called()
        app.root.focus_force.assert_not_called()

    def test_toolbar_keeps_successful_tray_minimization(self):
        app = SimpleNamespace(root=Mock(), status=Mock(), tray=Mock())
        app.tray.minimize.return_value = True
        ProbeApp._minimize_to_tray(app)
        app.root.iconify.assert_not_called()
        app.root.deiconify.assert_not_called()

    def test_native_minimization_remains_iconic_without_tray(self):
        root = Mock()
        root.state.return_value = "iconic"
        tray = TrayController(root, on_restore=Mock())
        tray.start = Mock(return_value=False)
        tray.handle_unmap()
        root.withdraw.assert_not_called()
        root.deiconify.assert_not_called()
        self.assertFalse(tray._hidden)
