"""systemd 单元渲染与安装/卸载的纯逻辑部分。"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from heartapp import server_config as cfg
from heartapp import server_install as installer
from heartapp import server_system as system


def make(**overrides):
    config = dict(cfg.DEFAULTS, password='strong-pass-1')
    config.update(overrides)
    return config


class TestUnitRendering(unittest.TestCase):
    def test_plain_loopback_unit_has_no_low_port_capability(self):
        text = system.render_unit(make(), version='1.2.3')
        self.assertIn('User=heartapp', text)
        self.assertIn(f'WorkingDirectory={system.CURRENT.as_posix()}', text)
        self.assertIn((system.CURRENT / system.LAUNCHER_NAME).as_posix() + ' run', text)
        self.assertIn('--config /etc/heartapp-relay/config.json', text)
        self.assertIn('AmbientCapabilities=\n', text)
        self.assertIn('NoNewPrivileges=true', text)
        self.assertIn('ProtectSystem=strict', text)
        self.assertIn('RestrictAddressFamilies=AF_INET AF_INET6', text)
        self.assertNotIn('EnvironmentFile', text)
        self.assertIn('v1.2.3', text)

    def test_low_port_and_tls_get_only_bind_capability(self):
        for config in (make(port='443'),):
            with self.subTest(config=config):
                text = system.render_unit(config, version='1')
                self.assertIn('AmbientCapabilities=CAP_NET_BIND_SERVICE', text)
                self.assertIn('CapabilityBoundingSet=CAP_NET_BIND_SERVICE', text)

    def test_certificate_paths_are_passed_to_run(self):
        text = system.render_unit(make(), cert='/etc/heartapp-tls/current/fullchain.pem',
                                 key='/etc/heartapp-tls/current/privkey.pem', version='1')
        self.assertIn('--cert /etc/heartapp-tls/current/fullchain.pem', text)
        self.assertIn('--key /etc/heartapp-tls/current/privkey.pem', text)
        self.assertIn('CAP_NET_BIND_SERVICE', text)


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.root, ignore_errors=True))

    def test_dry_run_prints_plan_and_changes_nothing(self):
        package = self.root / 'package'
        (package / 'runtime').mkdir(parents=True)
        with patch.object(installer, '_require_root'), \
                patch.object(installer, '_require_systemd'), \
                patch('sys.stdout') as stdout:
            code = installer.install(package, make(), dry_run=True, version='9.9.9')
        self.assertEqual(code, 0)
        rendered = ''.join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn('dry-run', rendered)
        self.assertIn('9.9.9', rendered)
        self.assertFalse(system.CURRENT.exists())

    def test_missing_runtime_is_reported(self):
        package = self.root / 'package'
        package.mkdir()
        with self.assertRaises(installer.InstallError):
            installer._copy_release(package, '1.0.0')

    def test_uninstall_dry_run_lists_targets_only(self):
        existing = self.root / 'install'
        existing.mkdir()
        with patch.object(installer, '_require_root'), \
                patch.object(cfg, 'load', return_value=make()), \
                patch.object(system, 'APP_ROOT', existing), \
                patch.object(system, 'SYSTEM_BIN', self.root / 'bin'), \
                patch.object(system, 'UNIT_PATH', self.root / 'unit.service'), \
                patch.object(system, 'RENEW_SERVICE', self.root / 'renew.service'), \
                patch.object(system, 'RENEW_TIMER', self.root / 'renew.timer'), \
                patch.object(system, 'TLS_DIR', self.root / 'tls'), \
                patch.object(system, 'ACME_DIR', self.root / 'acme'), \
                patch.object(system, 'ACME_WORK', self.root / 'acme-work'), \
                patch.object(system, 'ACME_LOGS', self.root / 'acme-logs'), \
                patch.object(cfg, 'CONFIG_PATH', self.root / 'config.json'), \
                patch.object(cfg, 'LEGACY_ENV_PATH', self.root / 'legacy.env'), \
                patch('sys.stdout') as stdout:
            code = installer.uninstall(dry_run=True)
        self.assertEqual(code, 0)
        rendered = ''.join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn('dry-run', rendered)
        self.assertIn(str(existing), rendered)

    def test_snapshot_keeps_previous_unit_and_config(self):
        unit = self.root / 'unit.service'
        unit.write_text('old-unit', encoding='utf-8')
        config_path = self.root / 'config.json'
        config_path.write_text('{"password": "x"}', encoding='utf-8')
        with patch.object(system, 'UNIT_PATH', unit), \
                patch.object(cfg, 'CONFIG_PATH', config_path), \
                patch.object(system, 'CURRENT', self.root / 'current'), \
                patch.object(system, 'SYSTEM_BIN', self.root / 'bin'), \
                patch.object(system, 'SERVICE', 'heartapp-relay-test'):
            with installer._snapshot(make(), 'heartapp') as state:
                pass
        self.assertEqual(state['unit'], 'old-unit')
        self.assertIn(b'password', state['config'])


if __name__ == '__main__':
    unittest.main()
