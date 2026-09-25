import pytest

from app.tools.led_normalizer import LEDState, normalize_led
from app.tools.net_validator import (
    InvalidNetworkInput,
    classify_ip,
    parse_subnet,
    validate_gateway,
)


class TestParseSubnet:
    def test_valid_private_subnet(self):
        info = parse_subnet("192.168.1.0/24")
        assert info.network_address == "192.168.1.0"
        assert info.prefix_length == 24
        assert info.num_addresses == 256
        assert info.is_private is True

    def test_valid_public_subnet(self):
        info = parse_subnet("8.8.8.0/24")
        assert info.is_private is False

    def test_host_bits_set_is_normalized(self):
        info = parse_subnet("10.0.0.5/24")
        assert info.network_address == "10.0.0.0"

    def test_invalid_cidr_raises(self):
        with pytest.raises(InvalidNetworkInput):
            parse_subnet("not-a-subnet")


class TestClassifyIp:
    @pytest.mark.parametrize(
        "ip",
        ["10.0.0.1", "172.16.5.4", "192.168.1.1"],
    )
    def test_rfc1918_ranges_are_private(self, ip):
        assert classify_ip(ip) is True

    def test_public_ip_is_not_private(self):
        assert classify_ip("8.8.8.8") is False

    def test_invalid_ip_raises(self):
        with pytest.raises(InvalidNetworkInput):
            classify_ip("999.999.999.999")


class TestValidateGateway:
    def test_gateway_in_subnet(self):
        result = validate_gateway("192.168.1.1", "192.168.1.0/24")
        assert result.is_valid_ip is True
        assert result.in_subnet is True
        assert result.is_private is True

    def test_gateway_outside_subnet(self):
        result = validate_gateway("10.0.0.1", "192.168.1.0/24")
        assert result.in_subnet is False

    def test_malformed_gateway_is_flagged_not_raised(self):
        result = validate_gateway("not-an-ip", "192.168.1.0/24")
        assert result.is_valid_ip is False
        assert result.in_subnet is False

    def test_invalid_subnet_raises(self):
        with pytest.raises(InvalidNetworkInput):
            validate_gateway("192.168.1.1", "not-a-subnet")


class TestNormalizeLed:
    def test_blinking_amber_is_booting(self):
        result = normalize_led("blinking amber 2Hz")
        assert result.color == "amber"
        assert result.pattern == "blinking"
        assert result.frequency_hz == 2.0
        assert result.state == LEDState.BOOTING

    def test_solid_green_is_online(self):
        result = normalize_led("solid green")
        assert result.state == LEDState.ONLINE
        assert result.frequency_hz is None

    def test_pulsing_red_is_fault(self):
        result = normalize_led("pulsing red")
        assert result.state == LEDState.FAULT

    def test_yellow_is_aliased_to_amber(self):
        result = normalize_led("flashing yellow")
        assert result.color == "amber"
        assert result.pattern == "blinking"
        assert result.state == LEDState.BOOTING

    def test_off_is_unknown(self):
        result = normalize_led("LED off")
        assert result.state == LEDState.UNKNOWN

    def test_unrecognized_text_is_unknown(self):
        result = normalize_led("mysterious purple glow")
        assert result.color is None
        assert result.state == LEDState.UNKNOWN

    def test_is_case_insensitive(self):
        result = normalize_led("SOLID GREEN")
        assert result.state == LEDState.ONLINE
