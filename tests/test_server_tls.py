"""证书相关的纯逻辑：命名与 TCP 80 预检。"""
from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from heartapp import server_tls


class TestCertificateNaming(unittest.TestCase):
    def test_name_includes_address(self):
        self.assertEqual(server_tls.certificate_name({'public_ip': '8.8.8.8'}),
                         'heartapp-8.8.8.8')
        self.assertEqual(server_tls.certificate_name({'public_ip': '2001:db8::1'}),
                         'heartapp-2001-db8--1')


class TestPort80Preflight(unittest.TestCase):
    def test_service_port_80_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            server_tls._check_port_80({'public_ip': '8.8.8.8', 'port': '80'})
        self.assertIn('80', str(caught.exception))

    def test_busy_port_80_mentions_reverse_proxy(self):
        class BusySocket:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def setsockopt(self, *args):
                return None

            def bind(self, address):
                raise OSError('address already in use')

        with patch('socket.socket', return_value=BusySocket()):
            with self.assertRaises(ValueError) as caught:
                server_tls._check_port_80({'public_ip': '8.8.8.8', 'port': '443'})
        self.assertIn('反向代理', str(caught.exception))

    def test_free_port_80_passes(self):
        class FreeSocket:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def setsockopt(self, *args):
                return None

            def bind(self, address):
                return None

        with patch('socket.socket', return_value=FreeSocket()):
            server_tls._check_port_80({'public_ip': '8.8.8.8', 'port': '443'})


if __name__ == '__main__':
    unittest.main()
