"""服务端配置：校验、迁移、客户端地址。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from heartapp import server_config as cfg


def make(**overrides):
    config = dict(cfg.DEFAULTS, password='strong-pass-1')
    config.update(overrides)
    return config


class TestValidation(unittest.TestCase):
    def test_defaults_are_valid_with_a_password(self):
        cfg.validate(make())

    def test_rejects_short_repeated_and_common_passwords(self):
        for password in ('1234567', 'aaaaaaaa', 'password'):
            with self.subTest(password=password), self.assertRaises(ValueError):
                cfg.validate(make(password=password))

    def test_rejects_out_of_range_numbers(self):
        for key, value in (('port', '0'), ('port', '70000'),
                           ('max_clients', '0'), ('room_size', '129')):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                cfg.validate(make(**{key: value}))

    def test_rejects_invalid_host_and_user(self):
        for key, value in (('host', 'bad host'), ('user', '1bad')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                cfg.validate(make(**{key: value}))

    def test_tls_requires_public_ip_and_not_port_80(self):
        with self.assertRaises(ValueError):
            cfg.validate(make(public_ip='192.168.1.10'))
        with self.assertRaises(ValueError):
            cfg.validate(make(public_ip='8.8.8.8', port='80'))
        cfg.validate(make(public_ip='8.8.8.8', port='443'))


class TestHelpers(unittest.TestCase):
    def test_plaintext_needed_only_for_non_loopback_without_certificate(self):
        self.assertFalse(cfg.needs_plaintext(make()))
        self.assertTrue(cfg.needs_plaintext(make(host='0.0.0.0')))
        self.assertFalse(cfg.needs_plaintext(make(host='0.0.0.0', public_ip='8.8.8.8',
                                                  port='443')))

    def test_connect_address_matches_mode(self):
        self.assertEqual(cfg.connect_address(make(host='127.0.0.1')), 'ws://127.0.0.1:8765')
        self.assertEqual(cfg.connect_address(make(host='0.0.0.0')), 'ws://服务器IP:8765')
        self.assertEqual(cfg.connect_address(make(host='0.0.0.0', public_ip='8.8.8.8',
                                                  port='443')), 'wss://8.8.8.8')
        self.assertEqual(cfg.connect_address(make(host='0.0.0.0', public_ip='8.8.8.8',
                                                  port='8443')), 'wss://8.8.8.8:8443')

    def test_redacted_hides_password(self):
        shown = cfg.redacted(make())
        self.assertEqual(shown['password'], '********')


class TestStorage(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            config = make(public_ip='8.8.8.8', port='443')
            cfg.save(config, path)
            self.assertEqual(cfg.load(path), config)
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_migrates_legacy_env_and_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env_path = root / 'relay.env'
            unit_path = root / 'relay.service'
            env_path.write_text(
                cfg.LEGACY_ENV_HEADER + '\n'
                'HEARTAPP_RELAY_PASSWORD="legacy-pass-1"\n'
                'HEARTAPP_RELAY_TLS_IP="8.8.8.8"\n',
                encoding='utf-8',
            )
            unit_path.write_text(
                '[Service]\nUser=heartapp\n'
                'ExecStart=/opt/x/bin/python -m heartapp.relay '
                '--host 0.0.0.0 --port 443 --max-clients 30 --room-size 6\n',
                encoding='utf-8',
            )
            with patch.object(cfg, 'LEGACY_ENV_PATH', env_path), \
                    patch.object(cfg, 'LEGACY_UNIT_PATH', unit_path):
                migrated = cfg.load(Path(temporary) / 'missing.json')
        self.assertEqual(migrated['password'], 'legacy-pass-1')
        self.assertEqual(migrated['public_ip'], '8.8.8.8')
        self.assertEqual(migrated['host'], '0.0.0.0')
        self.assertEqual(migrated['port'], '443')
        self.assertEqual(migrated['max_clients'], '30')
        self.assertEqual(migrated['room_size'], '6')

    def test_missing_config_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(cfg, 'LEGACY_ENV_PATH', Path(temporary) / 'none.env'), \
                    patch.object(cfg, 'LEGACY_UNIT_PATH', Path(temporary) / 'none.service'):
                self.assertEqual(cfg.load(Path(temporary) / 'missing.json'), cfg.DEFAULTS)

    def test_invalid_json_structure_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            path.write_text('[]', encoding='utf-8')
            with self.assertRaises(ValueError):
                cfg.load(path)


if __name__ == '__main__':
    unittest.main()
