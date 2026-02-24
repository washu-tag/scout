from unittest.mock import patch, MagicMock
import socket

from verify_node import CheckStatus, ConnectivityChecker


class TestTCPReachable:
    def test_reachable_success(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        mock_sock = MagicMock()
        with patch(
            "verify_node.socket.create_connection", return_value=mock_sock
        ) as mock_create:
            result = checker.check_tcp(
                {
                    "host": "testhost.local",
                    "port": 443,
                    "expect": "reachable",
                    "description": "HTTPS",
                }
            )
            mock_create.assert_called_once_with(("testhost.local", 443), timeout=1.0)
        assert result.status == CheckStatus.PASS
        assert "reachable" in result.message

    def test_reachable_timeout(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch(
            "verify_node.socket.create_connection",
            side_effect=socket.timeout("timed out"),
        ):
            result = checker.check_tcp(
                {
                    "host": "testhost.local",
                    "port": 443,
                    "expect": "reachable",
                    "description": "HTTPS",
                }
            )
        assert result.status == CheckStatus.FAIL
        assert "timed out" in result.detail.lower()

    def test_reachable_connection_refused(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch(
            "verify_node.socket.create_connection",
            side_effect=ConnectionRefusedError(),
        ):
            result = checker.check_tcp(
                {
                    "host": "testhost.local",
                    "port": 443,
                    "expect": "reachable",
                }
            )
        assert result.status == CheckStatus.FAIL

    def test_reachable_os_error(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch(
            "verify_node.socket.create_connection",
            side_effect=OSError("Network unreachable"),
        ):
            result = checker.check_tcp(
                {
                    "host": "testhost.local",
                    "port": 443,
                    "expect": "reachable",
                }
            )
        assert result.status == CheckStatus.FAIL


class TestTCPUnreachable:
    def test_unreachable_timeout(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        with patch(
            "verify_node.socket.create_connection",
            side_effect=socket.timeout("timed out"),
        ):
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
        mock_sock = MagicMock()
        with patch("verify_node.socket.create_connection", return_value=mock_sock):
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
        with patch(
            "verify_node.socket.create_connection",
            side_effect=ConnectionRefusedError(),
        ):
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
                    "hostname": "dns.testhost.local",
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
                    "hostname": "dns.testhost.local",
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
                    "hostname": "internal.testhost.local",
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
                    "hostname": "internal.testhost.local",
                    "expect": "unresolvable",
                }
            )
        assert result.status == CheckStatus.FAIL
        assert "1.2.3.4" in result.detail


class TestCustomTimeout:
    def test_timeout_passed_to_create_connection(self):
        checker = ConnectivityChecker(default_timeout=2.5)
        mock_sock = MagicMock()
        with patch(
            "verify_node.socket.create_connection", return_value=mock_sock
        ) as mock_create:
            checker.check_tcp(
                {
                    "host": "testhost.local",
                    "port": 80,
                    "expect": "reachable",
                }
            )
            mock_create.assert_called_once_with(("testhost.local", 80), timeout=2.5)

    def test_explicit_timeout_overrides_default(self):
        checker = ConnectivityChecker(default_timeout=5.0)
        mock_sock = MagicMock()
        with patch(
            "verify_node.socket.create_connection", return_value=mock_sock
        ) as mock_create:
            checker.check_tcp(
                {"host": "testhost.local", "port": 80, "expect": "reachable"},
                timeout=1.0,
            )
            mock_create.assert_called_once_with(("testhost.local", 80), timeout=1.0)


class TestDefaultDescription:
    def test_tcp_default_description(self):
        checker = ConnectivityChecker(default_timeout=1.0)
        mock_sock = MagicMock()
        with patch("verify_node.socket.create_connection", return_value=mock_sock):
            result = checker.check_tcp(
                {
                    "host": "testhost.local",
                    "port": 443,
                    "expect": "reachable",
                }
            )
        assert "testhost.local:443" in result.message

    def test_dns_default_description(self):
        checker = ConnectivityChecker()
        with patch("verify_node.socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 0))
            ]
            result = checker.check_dns(
                {
                    "hostname": "dns.testhost.local",
                    "expect": "resolvable",
                }
            )
        assert "dns.testhost.local" in result.message
