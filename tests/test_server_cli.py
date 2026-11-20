"""统一服务端入口的子命令转发和失败处理。"""
import types
import unittest
from unittest.mock import Mock, patch

from heartapp import server_cli


class TestServerCLI(unittest.TestCase):
    def test_source_manager_remains_available(self):
        self.assertIn('tls_ip', server_cli.manager_module().DEFAULTS)

    def test_configuration_arguments_reach_manager(self):
        manager = types.SimpleNamespace(main=lambda args: args)
        with patch.object(server_cli, 'manager_module', return_value=manager):
            self.assertEqual(server_cli.main(['configure', '--tls-ip', '8.8.8.8']),
                             ['configure', '--tls-ip', '8.8.8.8'])

    def test_certificate_subcommand_preserves_help_argument(self):
        import sys
        helper = types.SimpleNamespace(main=lambda: sys.argv[1:])
        with patch.object(server_cli, 'helper_module', return_value=helper), patch.object(sys, 'argv', ['server']):
            self.assertEqual(server_cli.main(['certificate', 'setup', '--help']), ['setup', '--help'])

    def test_failed_health_check_is_reported(self):
        manager = types.SimpleNamespace(load_settings=lambda: dict(host='127.0.0.1', port='8765', tls_ip=''))
        with patch.object(server_cli, 'manager_module', return_value=manager), \
                patch.object(server_cli.socket, 'create_connection', side_effect=OSError('connection refused')):
            self.assertEqual(server_cli.main(['health']), 1)

    def test_firewall_failure_returns_error(self):
        helper = types.SimpleNamespace(open_port=Mock(side_effect=OSError('firewall offline')))
        manager = types.SimpleNamespace(load_settings=lambda: dict(port='8765'))
        with patch.object(server_cli.os, 'geteuid', return_value=0, create=True), \
                patch.object(server_cli, 'helper_module', return_value=helper), \
                patch.object(server_cli, 'manager_module', return_value=manager):
            self.assertEqual(server_cli.main(['firewall']), 1)
