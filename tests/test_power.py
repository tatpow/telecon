import unittest
from unittest.mock import Mock, patch
import power

class PowerTests(unittest.TestCase):
    def kernel(self):
        kernel=Mock()
        kernel.PowerCreateRequest.return_value=100
        kernel.PowerSetRequest.return_value=1
        return kernel
    def test_request_is_temporary_idempotent_and_released(self):
        kernel=self.kernel()
        with patch.object(power.ctypes,'WinDLL',return_value=kernel):
            guard=power.SleepInhibitor()
            guard.set_active(True);guard.set_active(True)
            self.assertTrue(guard.active)
            self.assertEqual(kernel.PowerSetRequest.call_count,2)
            guard.close();guard.close()
            self.assertFalse(guard.active)
            self.assertEqual(kernel.PowerClearRequest.call_count,2)
            kernel.CloseHandle.assert_called_once_with(100)
    def test_partially_failed_request_releases_handle(self):
        kernel=self.kernel();kernel.PowerSetRequest.side_effect=[1,0]
        with patch.object(power.ctypes,'WinDLL',return_value=kernel):
            guard=power.SleepInhibitor()
            with self.assertRaises(OSError):guard.set_active(True)
            self.assertFalse(guard.active)
            kernel.CloseHandle.assert_called_once_with(100)
    def test_no_handle_is_held_when_create_fails(self):
        kernel=self.kernel();kernel.PowerCreateRequest.return_value=None
        with patch.object(power.ctypes,'WinDLL',return_value=kernel):
            guard=power.SleepInhibitor()
            with self.assertRaises(OSError):guard.set_active(True)
            kernel.PowerSetRequest.assert_not_called()

if __name__=='__main__':unittest.main()
