"""安装向导、旧配置兼容及管理菜单失败路径回归测试。"""
import argparse
from contextlib import nullcontext
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('linux_manager', ROOT / 'server/manage-linux.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class TestLinuxManager(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (('ENV_FILE', self.root / 'relay.env'), ('UNIT_FILE', self.root / 'relay.service')):
            patcher = patch.object(manager, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_special_password_roundtrip_and_permissions(self):
        settings = dict(manager.DEFAULTS, password='测试 空格 "quote" \\ $HOME $(echo test) # ; %')
        manager.save_settings(settings)
        self.assertEqual(manager.load_settings(), settings)
        if os.name != 'nt':
            self.assertEqual(manager.ENV_FILE.stat().st_mode & 0o777, 0o600)

    def test_legacy_config_preserves_port_user_and_literal_password(self):
        manager.ENV_FILE.write_text('HEARTAPP_RELAY_PASSWORD=old password $value\n', encoding='utf-8')
        manager.UNIT_FILE.write_text(
            'User=custom\nExecStart=/opt/python -m heartapp.relay --host 127.0.0.1 --port 9001 '
            '--max-clients 12 --room-size 3\n', encoding='utf-8')
        settings = manager.load_settings()
        self.assertEqual(settings['password'], 'old password $value')
        self.assertEqual(settings['port'], '9001')
        self.assertEqual(settings['user'], 'custom')
        manager.save_settings(settings)
        self.assertEqual(manager.load_settings(), settings)

    def test_rejects_short_repeated_and_common_passwords(self):
        for password in ("1234567", "aaaaaaaa", "password"):
            with self.subTest(password=password), self.assertRaises(ValueError):
                manager.validate(dict(manager.DEFAULTS, password=password))

    def test_cancel_install_does_not_write_config(self):
        args = argparse.Namespace(non_interactive=False)
        with patch.object(manager, 'terminal', return_value=nullcontext()), \
                patch.object(manager, '_masked_input', side_effect=['new-pass-1', 'new-pass-1']), \
                patch('builtins.input', side_effect=['', 'n']):
            self.assertEqual(manager.configure(args), 1)
        self.assertFalse(manager.ENV_FILE.exists())

    def test_first_install_checks_password_and_port_then_saves(self):
        args = argparse.Namespace(non_interactive=False)
        with patch.object(manager, 'terminal', return_value=nullcontext()), \
                patch.object(manager, '_masked_input', side_effect=['one-pass-1', 'two-pass-1', 'three-pass', 'three-pass']), \
                patch('builtins.input', side_effect=['invalid', '65536', '9002', 'y']):
            self.assertEqual(manager.configure(args), 0)
        self.assertEqual(manager.load_settings()['password'], 'three-pass')
        self.assertEqual(manager.load_settings()['port'], '9002')

    def test_upgrade_keeps_existing_values_on_enter(self):
        settings = dict(manager.DEFAULTS, port='9003', password='preserved')
        manager.save_settings(settings)
        with patch.object(manager, 'terminal', return_value=nullcontext()), \
                patch.object(manager, '_masked_input', return_value=''), \
                patch('builtins.input', side_effect=['', 'y']):
            self.assertEqual(manager.configure(argparse.Namespace(non_interactive=False)), 0)
        self.assertEqual(manager.load_settings(), settings)

    def test_noninteractive_first_install_requires_password(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                manager.configure(argparse.Namespace(non_interactive=True))
        self.assertFalse(manager.ENV_FILE.exists())

    def test_atomic_write_failure_preserves_config(self):
        settings = dict(manager.DEFAULTS, password='before-pw')
        manager.save_settings(settings)
        with patch.object(manager.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                manager.save_settings(dict(settings, password='after-pw'))
        self.assertEqual(manager.load_settings(), settings)
        self.assertEqual(list(self.root.iterdir()), [manager.ENV_FILE])

    def test_edit_password_can_cancel_or_restart(self):
        manager.save_settings(dict(manager.DEFAULTS, password='before-pw'))
        for confirmation, expected in (('n', 'before-pw'), ('y', 'after-pw')):
            with patch.object(manager, 'terminal', return_value=nullcontext()), \
                    patch.object(manager, '_masked_input', side_effect=['after-pw', 'after-pw']), \
                    patch('builtins.input', side_effect=['5', confirmation, '0']), \
                    patch.object(manager.subprocess, 'run') as run:
                self.assertEqual(manager.menu(), 0)
            self.assertEqual(manager.load_settings()['password'], expected)
            self.assertEqual(run.call_count, 1 if confirmation == 'y' else 0)

    def test_restart_failure_keeps_menu_available(self):
        manager.save_settings(dict(manager.DEFAULTS, password='test-pass'))
        with patch.object(manager, 'terminal', return_value=nullcontext()), \
                patch('builtins.input', side_effect=['6', '9004', 'y', '0']), \
                patch.object(manager.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'systemctl')):
            self.assertEqual(manager.menu(), 0)
        self.assertEqual(manager.load_settings()['port'], '9004')

    def test_no_terminal_fails_instead_of_reading_pipeline(self):
        with patch.object(manager.sys.stdin, 'isatty', return_value=False), \
                patch('builtins.open', side_effect=OSError('no terminal')):
            with self.assertRaisesRegex(ValueError, '--non-interactive'):
                with manager.terminal():
                    self.fail('must not reach prompt')
