"""IP 证书部署原子性、身份校验和续期失败回归。"""
import importlib.util
import os
from pathlib import Path
import ssl
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'ip_tls', Path(__file__).resolve().parent.parent / 'server/ip-tls.py')
tls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tls)


class TestIPTLS(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name in ('APP', 'ACME', 'TLS', 'UNITS'):
            target = root / name
            target.mkdir()
            patcher = patch.object(tls, name, target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = dict(tls_ip='8.8.8.8', port='443', user='heartapp')
        self.source = tls.ACME / 'live' / tls.certificate_name(self.config)
        self.source.mkdir(parents=True)
        (self.source / 'fullchain.pem').write_bytes(b'certificate')
        (self.source / 'privkey.pem').write_bytes(b'private-key')
        for patcher in (
            patch.object(tls.ssl, 'SSLContext'),
            patch.object(tls.ssl._ssl, '_test_decode_cert', return_value={
                'subjectAltName': (('IP Address', '8.8.8.8'),),
                'notAfter': 'Jan  1 00:00:00 2035 GMT'}),
            patch.dict(sys.modules, {'grp': types.SimpleNamespace(
                getgrnam=lambda name: types.SimpleNamespace(
                    gr_gid=os.getgid() if hasattr(os, 'getgid') else 1000))}),
            patch.object(tls.os, 'chown', create=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_ip_mismatch_does_not_publish_certificate(self):
        with self.assertRaises(ssl.CertificateError):
            tls.deploy(dict(self.config, tls_ip='1.1.1.1'), restart=False)
        self.assertFalse((tls.TLS / 'current').exists())

    def test_expired_certificate_does_not_publish(self):
        with patch.object(tls.ssl._ssl, '_test_decode_cert', return_value={
                'subjectAltName': (('IP Address', '8.8.8.8'),),
                'notAfter': 'Jan  1 00:00:00 2020 GMT'}):
            with self.assertRaises(ValueError):
                tls.deploy(self.config, restart=False)
        self.assertFalse((tls.TLS / 'current').exists())

    @unittest.skipIf(os.name == 'nt', '证书指针使用 Linux 符号链接')
    def test_unchanged_certificate_does_not_restart_rooms(self):
        tls.deploy(self.config, restart=False)
        with patch.object(tls, 'run') as run:
            tls.deploy(self.config, restart=True)
        run.assert_not_called()
        self.assertEqual((tls.TLS / 'current/privkey.pem').stat().st_mode & 0o777, 0o640)

    @unittest.skipIf(os.name == 'nt', '证书指针使用 Linux 符号链接')
    def test_failed_restart_restores_previous_pair(self):
        tls.deploy(self.config, restart=False)
        previous = os.readlink(tls.TLS / 'current')
        (self.source / 'privkey.pem').write_bytes(b'new-key')
        with patch.object(tls, 'run', side_effect=[
                subprocess.CalledProcessError(1, 'systemctl'), None]):
            with self.assertRaises(subprocess.CalledProcessError):
                tls.deploy(self.config, restart=True)
        self.assertEqual(os.readlink(tls.TLS / 'current'), previous)
        self.assertEqual((tls.TLS / 'current/privkey.pem').read_bytes(), b'private-key')

    @unittest.skipIf(os.name == 'nt', '证书指针使用 Linux 符号链接')
    def test_changed_service_group_deploys_a_new_generation(self):
        tls.deploy(self.config, restart=False)
        previous = os.readlink(tls.TLS / 'current')
        group = types.SimpleNamespace(getgrnam=lambda name: types.SimpleNamespace(gr_gid=os.getgid() + 1))
        with patch.dict(sys.modules, {'grp': group}):
            tls.deploy(self.config, restart=False)
        self.assertNotEqual(os.readlink(tls.TLS / 'current'), previous)
        self.assertTrue((tls.TLS / previous / 'privkey.pem').exists())

    def test_failed_renewal_does_not_deploy(self):
        with patch.object(sys, 'argv', ['ip-tls.py', 'renew']), \
                patch.dict(sys.modules, {'fcntl': types.SimpleNamespace(
                    flock=lambda *args: None, LOCK_EX=1, LOCK_NB=2)}), \
                patch('builtins.open', unittest.mock.mock_open()), \
                patch.object(tls.os, 'geteuid', return_value=0, create=True), \
                patch.object(tls, 'settings', return_value=self.config), \
                patch.object(tls, 'run', side_effect=subprocess.TimeoutExpired('certbot', 240)), \
                patch.object(tls, 'deploy') as deploy:
            self.assertEqual(tls.main(), 1)
        deploy.assert_not_called()

    def test_setup_writes_renewal_timer_after_success(self):
        with patch.object(tls, 'run') as run, patch.object(tls, 'deploy') as deploy, \
                patch.object(tls.socket, 'socket'):
            tls.setup(self.config, firewall=False)
        issuance = next(call.args[0] for call in run.call_args_list if 'certonly' in call.args[0])
        self.assertIn('--ip-address', issuance)
        self.assertIn('--required-profile', issuance)
        self.assertIn('shortlived', issuance)
        self.assertNotIn('--staging', issuance)
        deploy.assert_called_once_with(self.config, restart=False)
        timer = (tls.UNITS / 'heartapp-ip-renew.timer').read_text(encoding='utf-8')
        self.assertIn('Persistent=true', timer)

    def test_failed_issuance_does_not_deploy_or_enable_timer(self):
        def fail_issuance(args, *unused):
            if 'certonly' in args:
                raise subprocess.CalledProcessError(1, args)
        with patch.object(tls, 'run', side_effect=fail_issuance), \
                patch.object(tls, 'deploy') as deploy, patch.object(tls.socket, 'socket'):
            with self.assertRaises(subprocess.CalledProcessError):
                tls.setup(self.config, firewall=False)
        deploy.assert_not_called()
        self.assertFalse((tls.UNITS / 'heartapp-ip-renew.timer').exists())

    def test_busy_validation_port_does_not_start_issuance(self):
        with patch.object(tls.socket, 'socket') as socket, patch.object(tls, 'run') as run:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError('busy')
            with self.assertRaisesRegex(ValueError, 'TCP 80'):
                tls.setup(self.config, firewall=False)
        run.assert_not_called()

    def test_bundled_runtime_uses_bundled_certbot_without_pip(self):
        launcher = tls.APP / 'current/heartapp-server'
        with patch.object(tls, 'bundled_launcher', return_value=launcher), \
                patch.object(tls, 'run') as run, patch.object(tls, 'deploy'), \
                patch.object(tls.socket, 'socket'):
            tls.setup(self.config, firewall=False)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any('pip' in str(arg) for command in commands for arg in command))
        self.assertTrue(any(command[:2] == [str(launcher), 'acme'] for command in commands))
        unit = (tls.UNITS / 'heartapp-ip-renew.service').read_text(encoding='utf-8')
        command = next(line.removeprefix('ExecStart=') for line in unit.splitlines()
                       if line.startswith('ExecStart='))
        self.assertEqual(shlex.split(command), [str(launcher), 'certificate', 'renew'])


if __name__ == '__main__':
    unittest.main()
