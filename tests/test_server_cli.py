"""服务端统一入口的子命令。"""
import argparse
import unittest
from unittest.mock import patch

from heartapp import server_cli


class TestServerCLI(unittest.TestCase):
    def test_install_dry_run_never_touches_system(self):
        args = argparse.Namespace(
            password="strong-pass-1", host=None, port=None, max_clients=None,
            room_size=None, user=None, public_ip=None, package_root="/tmp/pkg",
            version="9.9.9", non_interactive=True, no_open_port=True, dry_run=True,
        )
        calls = []
        with patch.object(server_cli.cfg, "load", return_value=dict(server_cli.cfg.DEFAULTS)), \
                patch.object(server_cli.installer, "install",
                             side_effect=lambda *a, **k: calls.append(k) or 0):
            self.assertEqual(server_cli.install_action(args), 0)
        self.assertTrue(calls[0]["dry_run"])
        self.assertEqual(calls[0]["version"], "9.9.9")

    def test_password_can_come_from_environment(self):
        args = argparse.Namespace(
            password=None, host=None, port=None, max_clients=None, room_size=None,
            user=None, public_ip=None, package_root=None, version=None,
            non_interactive=True, no_open_port=True, dry_run=True,
        )
        captured = {}
        with patch.dict("os.environ", {"HEARTAPP_INSTALL_PASSWORD": "env-pass-1"}), \
                patch.object(server_cli.cfg, "load", return_value=dict(server_cli.cfg.DEFAULTS)), \
                patch.object(server_cli, "_find_package_root", return_value=server_cli.Path("/tmp")), \
                patch.object(server_cli.installer, "install",
                             side_effect=lambda root, config, **k: captured.update(config) or 0):
            server_cli.install_action(args)
        self.assertEqual(captured["password"], "env-pass-1")

    def test_install_reports_validation_errors_without_traceback(self):
        args = argparse.Namespace(
            password="123", host=None, port=None, max_clients=None, room_size=None,
            user=None, public_ip=None, package_root=None, version=None,
            non_interactive=True, no_open_port=True, dry_run=True,
        )
        with patch.object(server_cli.cfg, "load", return_value=dict(server_cli.cfg.DEFAULTS)):
            self.assertEqual(server_cli.install_action(args), 1)

    def test_uninstall_dry_run_forwards_flags(self):
        args = argparse.Namespace(keep_config=True, no_open_port=True,
                                  remove_user=False, dry_run=True)
        seen = {}
        with patch.object(server_cli.installer, "uninstall",
                          side_effect=lambda **k: seen.update(k) or 0):
            self.assertEqual(server_cli.uninstall_action(args), 0)
        self.assertTrue(seen["keep_config"])
        self.assertTrue(seen["dry_run"])
        self.assertTrue(seen["open_port"] is False)

    def test_config_show_does_not_print_password(self):
        config = dict(server_cli.cfg.DEFAULTS, password="secret-pass-1")
        with patch.object(server_cli.cfg, "load", return_value=config), \
                patch("sys.stdout") as stdout:
            self.assertEqual(server_cli.main(["config", "show"]), 0)
        rendered = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn("********", rendered)
        self.assertNotIn("secret-pass-1", rendered)

    def test_unknown_config_key_is_rejected(self):
        with patch.object(server_cli.cfg, "load", return_value=dict(server_cli.cfg.DEFAULTS)):
            self.assertEqual(server_cli.main(["config", "set", "nope", "1"]), 2)

    def test_status_returns_health_result(self):
        with patch.object(server_cli.installer, "status", return_value=1):
            self.assertEqual(server_cli.main(["status"]), 1)
