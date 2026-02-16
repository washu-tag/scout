from __future__ import annotations

from unittest.mock import patch, MagicMock
import socket

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verify_node import CheckStatus, ConnectivityChecker


class TestTCPReachable:
    def test_reachable_success(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.return_value = None

            result = checker.check_tcp(
                {
                    "host": "example.com",
                    "port": 443,
                    "expect": "reachable",
                    "description": "HTTPS",
                }
            )
        assert result.status == CheckStatus.PASS
        assert "reachable" in result.message

    def test_reachable_timeout(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.side_effect = socket.timeout("timed out")

            result = checker.check_tcp(
                {
                    "host": "example.com",
                    "port": 443,
                    "expect": "reachable",
                    "description": "HTTPS",
                }
            )
        assert result.status == CheckStatus.FAIL
        assert "timed out" in result.detail.lower()

    def test_reachable_connection_refused(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.side_effect = ConnectionRefusedError()

            result = checker.check_tcp(
                {
                    "host": "example.com",
                    "port": 443,
                    "expect": "reachable",
                }
            )
        assert result.status == CheckStatus.FAIL

    def test_reachable_os_error(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.side_effect = OSError("Network unreachable")

            result = checker.check_tcp(
                {
                    "host": "example.com",
                    "port": 443,
                    "expect": "reachable",
                }
            )
        assert result.status == CheckStatus.FAIL


class TestTCPUnreachable:
    def test_unreachable_timeout(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.side_effect = socket.timeout("timed out")

            result = checker.check_tcp(
                {
                    "host": "8.8.8.8",
                    "port": 443,
                    "expect": "unreachable",
                    "description": "Internet (air-gap)",
                }
            )
        assert result.status == CheckStatus.PASS
        assert "correctly unreachable" in result.message

    def test_unreachable_but_connects(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.return_value = None

            result = checker.check_tcp(
                {
                    "host": "8.8.8.8",
                    "port": 443,
                    "expect": "unreachable",
                    "description": "Internet (air-gap)",
                }
            )
        assert result.status == CheckStatus.FAIL
        assert "Air-gap" in result.detail

    def test_unreachable_connection_refused(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.side_effect = ConnectionRefusedError()

            result = checker.check_tcp(
                {
                    "host": "8.8.8.8",
                    "port": 443,
                    "expect": "unreachable",
                }
            )
        assert result.status == CheckStatus.PASS


class TestDNSResolvable:
    def test_resolvable_success(self):
        checker = ConnectivityChecker()
        with patch("verify_node.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 0))
            ]
            result = checker.check_dns(
                {
                    "hostname": "scout.washu.edu",
                    "expect": "resolvable",
                    "description": "Scout DNS",
                }
            )
        assert result.status == CheckStatus.PASS
        assert "1.2.3.4" in result.message

    def test_resolvable_fails(self):
        checker = ConnectivityChecker()
        with patch("verify_node.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.side_effect = socket.gaierror("Name resolution failed")
            result = checker.check_dns(
                {
                    "hostname": "scout.washu.edu",
                    "expect": "resolvable",
                    "description": "Scout DNS",
                }
            )
        assert result.status == CheckStatus.FAIL


class TestDNSUnresolvable:
    def test_unresolvable_success(self):
        checker = ConnectivityChecker()
        with patch("verify_node.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.side_effect = socket.gaierror("Name resolution failed")
            result = checker.check_dns(
                {
                    "hostname": "internal.example.com",
                    "expect": "unresolvable",
                }
            )
        assert result.status == CheckStatus.PASS

    def test_unresolvable_but_resolves(self):
        checker = ConnectivityChecker()
        with patch("verify_node.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 0))
            ]
            result = checker.check_dns(
                {
                    "hostname": "internal.example.com",
                    "expect": "unresolvable",
                }
            )
        assert result.status == CheckStatus.FAIL
        assert "1.2.3.4" in result.detail


class TestCustomTimeout:
    def test_timeout_passed_to_socket(self):
        checker = ConnectivityChecker(default_timeout=2.5)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.return_value = None

            checker.check_tcp(
                {
                    "host": "example.com",
                    "port": 80,
                    "expect": "reachable",
                }
            )
            mock_sock.settimeout.assert_called_with(2.5)

    def test_explicit_timeout_overrides_default(self):
        checker = ConnectivityChecker(default_timeout=5.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.return_value = None

            checker.check_tcp(
                {"host": "example.com", "port": 80, "expect": "reachable"},
                timeout=1.0,
            )
            mock_sock.settimeout.assert_called_with(1.0)


class TestDefaultDescription:
    def test_tcp_default_description(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch("verify_node.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value.__enter__ = MagicMock(return_value=mock_sock)
            mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.return_value = None

            result = checker.check_tcp(
                {
                    "host": "example.com",
                    "port": 443,
                    "expect": "reachable",
                }
            )
        assert "example.com:443" in result.message

    def test_dns_default_description(self):
        checker = ConnectivityChecker()
        with patch("verify_node.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 0))
            ]
            result = checker.check_dns(
                {
                    "hostname": "scout.washu.edu",
                    "expect": "resolvable",
                }
            )
        assert "scout.washu.edu" in result.message
