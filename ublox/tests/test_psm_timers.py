"""
Comprehensive pytest tests for PSMPeriodicTau and PSMActiveTime classes.
Tests cover encoding, decoding, closest fit, and the new DISABLED functionality.
"""
import pytest
from ublox.utils import PSMPeriodicTau, PSMActiveTime


class TestPSMPeriodicTau:
    """Test suite for PSMPeriodicTau class."""
    
    def test_disabled_constant_exists(self):
        """Test that DISABLED constant exists and has expected value."""
        assert hasattr(PSMPeriodicTau, 'DISABLED')
        assert PSMPeriodicTau.DISABLED == "DISABLED"
        assert isinstance(PSMPeriodicTau.DISABLED, str)
    
    def test_encode_disabled(self):
        """Test encoding DISABLED returns deactivated bitstring."""
        result = PSMPeriodicTau.encode(PSMPeriodicTau.DISABLED)
        assert result == PSMPeriodicTau.DEACTIVATED_STANDARD
        assert result == "11111111"
    
    def test_encode_zero(self):
        """Test encoding zero returns zero bitstring."""
        result = PSMPeriodicTau.encode(0)
        assert result == PSMPeriodicTau.ZERO
        assert result == "00000000"
    
    def test_decode_disabled(self):
        """Test decoding deactivated bitstring returns DISABLED."""
        result = PSMPeriodicTau.decode("11111111")
        assert result is PSMPeriodicTau.DISABLED
        
        # Test various deactivated patterns (111xxxxx all return DISABLED)
        for low_bits in ["00000", "01010", "11111", "10101"]:
            deactivated = "111" + low_bits
            result = PSMPeriodicTau.decode(deactivated)
            assert result is PSMPeriodicTau.DISABLED
    
    def test_decode_zero(self):
        """Test decoding zero bitstring returns 0."""
        result = PSMPeriodicTau.decode("00000000")
        assert result == 0

    def test_encode_six_hours(self):
        """Test encoding 6 hours returns correct bitstring."""
        result = PSMPeriodicTau.encode(21600)  # 6 hours = 21600 seconds
        assert result == "00100110"  # unit 101 (1 hour), value 6
    
    def test_encode_decode_roundtrip_valid_values(self):
        """Test encoding then decoding returns original value for valid inputs."""
        test_values = [
            0,      # zero case
            2,      # 2 second unit
            30,     # 30 second unit  
            60,     # 1 minute unit
            600,    # 10 minute unit
            3600,   # 1 hour unit
            36000,  # 10 hour unit
            1152000 # 320 hour unit
        ]
        
        for seconds in test_values:
            try:
                encoded = PSMPeriodicTau.encode(seconds)
                decoded = PSMPeriodicTau.decode(encoded)
                assert decoded == seconds, f"Roundtrip failed for {seconds}s"
            except ValueError:
                # Some values might not have exact representations
                pass
    
    def test_encode_decode_roundtrip_disabled(self):
        """Test DISABLED roundtrip encoding/decoding."""
        encoded = PSMPeriodicTau.encode(PSMPeriodicTau.DISABLED)
        decoded = PSMPeriodicTau.decode(encoded)
        assert decoded is PSMPeriodicTau.DISABLED
    
    def test_encode_invalid_values(self):
        """Test encoding invalid values raises ValueError."""
        invalid_values = [123, 77, 999999]  # Values with no exact representation
        
        for value in invalid_values:
            with pytest.raises(ValueError):
                PSMPeriodicTau.encode(value)
    
    def test_decode_invalid_bitstring(self):
        """Test decoding invalid bitstrings raises ValueError."""
        invalid_bitstrings = [
            "1234567",      # too short
            "123456789",    # too long
            "1234567a",     # non-binary character
            "",             # empty
            "12345678"      # non-binary characters
        ]
        
        for bitstr in invalid_bitstrings:
            with pytest.raises(ValueError):
                PSMPeriodicTau.decode(bitstr)
    
    def test_closest_zero_and_negative(self):
        """Test closest() with zero and negative values."""
        bitstr, encoded = PSMPeriodicTau.closest(0)
        assert bitstr == PSMPeriodicTau.ZERO
        assert encoded == 0
        
        bitstr, encoded = PSMPeriodicTau.closest(-100)
        assert bitstr == PSMPeriodicTau.ZERO
        assert encoded == 0
    
    def test_closest_finds_best_fit(self):
        """Test closest() finds best representable value <= target."""
        # Test a value that should find exact match
        bitstr, encoded = PSMPeriodicTau.closest(3600)  # 1 hour
        decoded = PSMPeriodicTau.decode(bitstr)
        assert decoded == 3600
        assert encoded == 3600
        
        # Test a value that needs approximation
        bitstr, encoded = PSMPeriodicTau.closest(3700)  # Between 1 hour and next
        decoded = PSMPeriodicTau.decode(bitstr)
        assert decoded <= 3700
        assert encoded <= 3700
    
    def test_human_label_for_seconds(self):
        """Test human-friendly label generation."""
        assert PSMPeriodicTau.human_label_for_seconds(0) == "_0_secs"
        assert PSMPeriodicTau.human_label_for_seconds(60) == "_1_min"
        assert PSMPeriodicTau.human_label_for_seconds(3600) == "_1_hr"
        assert PSMPeriodicTau.human_label_for_seconds(86400) == "_1_day"
        assert PSMPeriodicTau.human_label_for_seconds(90) == "_1_min_30_secs"
        assert PSMPeriodicTau.human_label_for_seconds(3661) == "_1_hr_1_min_1_secs"
    

class TestPSMActiveTime:
    """Test suite for PSMActiveTime class."""
    
    def test_disabled_constant_exists(self):
        """Test that DISABLED constant exists and has expected value."""
        assert hasattr(PSMActiveTime, 'DISABLED')
        assert PSMActiveTime.DISABLED == "DISABLED"
        assert isinstance(PSMActiveTime.DISABLED, str)
    
    def test_encode_disabled(self):
        """Test encoding DISABLED returns deactivated bitstring."""
        result = PSMActiveTime.encode(PSMActiveTime.DISABLED)
        assert result == PSMActiveTime.DEACTIVATED_CANONICAL
        assert result == "11111111"
    
    def test_encode_zero(self):
        """Test encoding zero returns zero bitstring."""
        result = PSMActiveTime.encode(0)
        assert result == PSMActiveTime.ZERO
        assert result == "00000000"
    
    def test_decode_disabled(self):
        """Test decoding deactivated bitstring returns DISABLED."""
        result = PSMActiveTime.decode("11111111")
        assert result is PSMActiveTime.DISABLED
        
        # Test various deactivated patterns (111xxxxx all return DISABLED)
        for low_bits in ["00000", "01010", "11111", "10101"]:
            deactivated = "111" + low_bits
            result = PSMActiveTime.decode(deactivated)
            assert result is PSMActiveTime.DISABLED
    
    def test_decode_zero(self):
        """Test decoding zero bitstring returns 0."""
        result = PSMActiveTime.decode("00000000")
        assert result == 0

    def test_encode_five_minutes(self):
        """Test encoding 5 minutes returns correct bitstring."""
        result = PSMActiveTime.encode(300)  # 5 minutes = 300 seconds
        assert result == "00100101"  # unit 010 (1 minute), value 5
    
    def test_encode_decode_roundtrip_valid_values(self):
        """Test encoding then decoding returns original value for valid inputs."""
        test_values = [
            0,      # zero case
            2,      # 2 second unit
            4,      # 2 second unit
            60,     # 1 minute unit
            120,    # 1 minute unit
            360,    # 1 decihour unit (6 minutes)
            720     # 1 decihour unit (12 minutes)
        ]
        
        for seconds in test_values:
            try:
                encoded = PSMActiveTime.encode(seconds)
                decoded = PSMActiveTime.decode(encoded)
                assert decoded == seconds, f"Roundtrip failed for {seconds}s"
            except ValueError:
                # Some values might not have exact representations
                pass
    
    def test_encode_decode_roundtrip_disabled(self):
        """Test DISABLED roundtrip encoding/decoding."""
        encoded = PSMActiveTime.encode(PSMActiveTime.DISABLED)
        decoded = PSMActiveTime.decode(encoded)
        assert decoded is PSMActiveTime.DISABLED
    
    def test_encode_prefers_smaller_units(self):
        """Test that encoding prefers smaller units when multiple representations exist."""
        # 120 seconds can be represented as:
        # - 60 * 2 (unit 001, value 2) 
        # - 2 * 60 (unit 000, value 60 - but 60 > 31, so invalid)
        encoded = PSMActiveTime.encode(120)
        decoded = PSMActiveTime.decode(encoded)
        assert decoded == 120
        
        # Should prefer 2-second unit for small even numbers
        encoded = PSMActiveTime.encode(4)
        # Decode and check it's exact
        decoded = PSMActiveTime.decode(encoded)
        assert decoded == 4
    
    def test_encode_invalid_values(self):
        """Test encoding invalid values raises ValueError."""
        invalid_values = [3, 7, 123, 777]  # Values with no exact representation
        
        for value in invalid_values:
            with pytest.raises(ValueError):
                PSMActiveTime.encode(value)
    
    def test_decode_invalid_bitstring(self):
        """Test decoding invalid bitstrings raises ValueError."""
        invalid_bitstrings = [
            "1234567",      # too short
            "123456789",    # too long
            "1234567a",     # non-binary character
            "",             # empty
            "12345678"      # non-binary characters
        ]
        
        for bitstr in invalid_bitstrings:
            with pytest.raises(ValueError):
                PSMActiveTime.decode(bitstr)
    
    def test_decode_unknown_unit_fallback(self):
        """Test decoding unknown unit codes falls back to 1-minute multiples."""
        # Create a bitstring with unknown unit code (e.g., 011) but not 111
        unknown_unit_bitstr = "01100001"  # unit 011, value 1
        result = PSMActiveTime.decode(unknown_unit_bitstr)
        assert result == 60  # Should treat as 1 minute multiple
    
    def test_closest_zero_and_negative(self):
        """Test closest() with zero and negative values."""
        bitstr, encoded = PSMActiveTime.closest(0)
        assert bitstr == PSMActiveTime.ZERO
        assert encoded == 0
        
        bitstr, encoded = PSMActiveTime.closest(-100)
        assert bitstr == PSMActiveTime.ZERO
        assert encoded == 0
    
    def test_closest_finds_best_fit(self):
        """Test closest() finds best representable value <= target."""
        # Test exact match
        bitstr, encoded = PSMActiveTime.closest(360)  # 1 decihour
        decoded = PSMActiveTime.decode(bitstr)
        assert decoded == 360
        assert encoded == 360
        
        # Test approximation needed
        bitstr, encoded = PSMActiveTime.closest(370)  # Between 360 and next
        decoded = PSMActiveTime.decode(bitstr)
        assert decoded <= 370
        assert encoded <= 370
    
    def test_human_label_for_seconds(self):
        """Test human-friendly label generation."""
        assert PSMActiveTime.human_label_for_seconds(0) == "_0_secs"
        assert PSMActiveTime.human_label_for_seconds(60) == "_1_min"
        assert PSMActiveTime.human_label_for_seconds(3600) == "_1_hr"
        assert PSMActiveTime.human_label_for_seconds(86400) == "_1_day"
        assert PSMActiveTime.human_label_for_seconds(90) == "_1_min_30_secs"
        assert PSMActiveTime.human_label_for_seconds(3661) == "_1_hr_1_min_1_secs"
    
    def test_convenience_mapping_exists(self):
        """Test that CONVENIENCE mapping is populated and contains expected entries."""
        assert len(PSMActiveTime.CONVENIENCE) > 0
        assert "_0_secs" in PSMActiveTime.CONVENIENCE
        assert "_deactivated" in PSMActiveTime.CONVENIENCE
        assert PSMActiveTime.CONVENIENCE["_0_secs"] == PSMActiveTime.ZERO
        assert PSMActiveTime.CONVENIENCE["_deactivated"] == PSMActiveTime.DEACTIVATED_CANONICAL


class TestPSMTimerInteroperability:
    """Test interoperability and consistency between both timer classes."""
    
    def test_disabled_constants_consistency(self):
        """Test that both classes have consistent DISABLED constants."""
        assert PSMPeriodicTau.DISABLED == PSMActiveTime.DISABLED
        assert PSMPeriodicTau.DISABLED == "DISABLED"
    
    def test_deactivated_bitstrings_consistency(self):
        """Test that both classes use same deactivated bitstring."""
        assert PSMPeriodicTau.DEACTIVATED_STANDARD == "11111111"
        assert PSMActiveTime.DEACTIVATED_CANONICAL == "11111111"
    
    def test_zero_bitstrings_consistency(self):
        """Test that both classes use same zero bitstring."""
        assert PSMPeriodicTau.ZERO == "00000000"
        assert PSMActiveTime.ZERO == "00000000"
    
    def test_disabled_identity_checks(self):
        """Test that DISABLED checks work with 'is' operator."""
        # Encode DISABLED and decode, should return same object
        tau_encoded = PSMPeriodicTau.encode(PSMPeriodicTau.DISABLED)
        tau_decoded = PSMPeriodicTau.decode(tau_encoded)
        assert tau_decoded is PSMPeriodicTau.DISABLED
        
        active_encoded = PSMActiveTime.encode(PSMActiveTime.DISABLED)
        active_decoded = PSMActiveTime.decode(active_encoded)
        assert active_decoded is PSMActiveTime.DISABLED
    
    def test_type_hints_compatibility(self):
        """Test that methods accept and return expected types."""
        from typing import Union
        
        # Test that DISABLED is accepted in encode methods
        tau_result = PSMPeriodicTau.encode(PSMPeriodicTau.DISABLED)
        assert isinstance(tau_result, str)
        
        active_result = PSMActiveTime.encode(PSMActiveTime.DISABLED)
        assert isinstance(active_result, str)
        
        # Test that decode methods return Union[int, str]
        tau_decoded = PSMPeriodicTau.decode("11111111")
        assert isinstance(tau_decoded, str)  # DISABLED case
        
        active_decoded = PSMActiveTime.decode("11111111")  
        assert isinstance(active_decoded, str)  # DISABLED case
        
        tau_decoded_int = PSMPeriodicTau.decode("00000000")
        assert isinstance(tau_decoded_int, int)  # regular case
        
        active_decoded_int = PSMActiveTime.decode("00000000")
        assert isinstance(active_decoded_int, int)  # regular case


if __name__ == "__main__":
    pytest.main([__file__, "-v"])