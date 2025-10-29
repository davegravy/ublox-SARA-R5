from enum import Enum
from typing import Optional, Tuple, Dict, Union

class PSMPeriodicTau:
    """
    Encode/decode for the 8-bit Periodic TAU / GPRS Timer 3 coding (used by AT+CPSMS / +CEREG).
    Bits 8..6 = unit code (3 bits)
    Bits 5..1 = value (5 bits)

    Unit mapping (for Timer3 / T3412):
        0b000 -> multiples of 10 minutes
        0b001 -> multiples of 1 hour
        0b010 -> multiples of 10 hours
        0b011 -> multiples of 2 seconds
        0b100 -> multiples of 30 seconds
        0b101 -> multiples of 1 minute
        0b110 -> multiples of 320 hours
        0b111 -> timer deactivated (bits 5..1 ignored)
    """

    # unit_code -> multiplier in seconds
    _UNIT_MULTIPLIER = {
        0b000: 10 * 60,         # 10 minutes
        0b001: 60 * 60,         # 1 hour
        0b010: 10 * 3600,       # 10 hours
        0b011: 2,               # 2 seconds
        0b100: 30,              # 30 seconds
        0b101: 60,              # 1 minute
        0b110: 320 * 3600,      # 320 hours
        # 0b111 -> deactivated (special)
    }

    DEACTIVATED_STANDARD = "11111111"
    ZERO = "00000000"
    DISABLED = "DISABLED"

    # A convenience mapping label -> 8-bit string (populated below)
    CONVENIENCE: Dict[str, str] = {}

    @classmethod
    def encode(cls, seconds: Union[int, str]) -> str:
        """
        Encode a seconds value into an 8-character '0'/'1' bitstring.
        - If seconds is cls.DISABLED -> deactivated -> returns '11111111' (canonical).
        - If seconds == 0 -> returns '00000000'.
        - For other values: finds unit/value pair such that value * multiplier == seconds
          and 0 <= value <= 31. If no exact match, raises ValueError.
        """
        if seconds is cls.DISABLED:
            return cls.DEACTIVATED_STANDARD
        if seconds == 0:
            return cls.ZERO

        for unit_code, mult in cls._UNIT_MULTIPLIER.items():
            if seconds % mult != 0:
                continue
            value = seconds // mult
            if 0 <= value <= 31:
                return format(unit_code, "03b") + format(int(value), "05b")

        raise ValueError(f"No exact encoding available for {seconds} seconds")

    @classmethod
    def decode(cls, bitstr: str) -> Union[int, str]:
        """
        Decode an 8-bit bitstring into seconds.
        - If top 3 bits == '111' => deactivated -> return cls.DISABLED (bits 5..1 ignored).
        - If bitstr == '00000000' -> returns 0.
        - Otherwise returns multiplier * value (int seconds).
        Raises ValueError for invalid bitstr format or unknown unit.
        """
        if not isinstance(bitstr, str) or len(bitstr) != 8 or any(c not in "01" for c in bitstr):
            raise ValueError("bitstr must be an 8-character string of '0'/'1'")

        top3 = bitstr[0:3]
        if top3 == "111":
            # Per spec: bits 5..1 ignored when unit == 111 => deactivated
            return cls.DISABLED

        if bitstr == cls.ZERO:
            return 0

        unit_code = int(top3, 2)
        value = int(bitstr[3:], 2)
        mult = cls._UNIT_MULTIPLIER.get(unit_code)
        if mult is None:
            raise ValueError(f"Unknown unit code: {unit_code:03b}")

        return mult * value

    @classmethod
    def closest(cls, seconds: int) -> Tuple[str, int]:
        """
        Find the representable encoding whose decoded seconds is <= requested seconds
        and whose delta (requested - encoded) is minimal (i.e. best fit not exceeding target).
        Returns (bitstr, encoded_seconds).
        If seconds <= 0 returns ('00000000', 0).
        """
        if seconds <= 0:
            return cls.ZERO, 0

        best_bitstr = None
        best_encoded = None
        best_delta = None

        # iterate all unit/value combos
        for unit_code, mult in cls._UNIT_MULTIPLIER.items():
            for v in range(0, 32):  # 0..31
                encoded = mult * v
                if encoded == 0:
                    # that's the ZERO representation; handled above
                    continue
                if encoded <= seconds:
                    delta = seconds - encoded
                    if best_delta is None or delta < best_delta:
                        best_delta = delta
                        best_encoded = encoded
                        best_bitstr = format(unit_code, "03b") + format(v, "05b")
                        if delta == 0:
                            return best_bitstr, best_encoded

        if best_bitstr is None:
            # no representable value <= seconds (very small seconds that don't fit)
            raise ValueError("No representable timer value <= requested seconds")

        return best_bitstr, best_encoded

    @classmethod
    def human_label_for_seconds(cls, seconds: int) -> str:
        """Generate a human-friendly label like _1_hr_30_mins or _45_secs."""
        if seconds == 0:
            return "_0_secs"
        parts = []
        rem = seconds
        days = rem // 86400
        if days:
            parts.append(f"{days}_day" + ("s" if days != 1 else ""))
            rem %= 86400
        hrs = rem // 3600
        if hrs:
            parts.append(f"{hrs}_hr" + ("s" if hrs != 1 else ""))
            rem %= 3600
        mins = rem // 60
        if mins:
            parts.append(f"{mins}_min" + ("s" if mins != 1 else ""))
            rem %= 60
        if rem:
            parts.append(f"{rem}_secs")
        return "_" + "_".join(parts)

# Populate CONVENIENCE mapping programmatically (all representable values)
def _populate_convenience():
    names = {}
    seen_labels = set()
    # include ZERO
    names["_0_secs"] = PSMPeriodicTau.ZERO

    for unit_code, mult in PSMPeriodicTau._UNIT_MULTIPLIER.items():
        for v in range(0, 32):
            bitstr = format(unit_code, "03b") + format(v, "05b")
            secs = mult * v
            # Skip the ZERO duplicate (already added)
            if secs == 0:
                continue
            label = PSMPeriodicTau.human_label_for_seconds(secs)

            # avoid collisions: prefer more specific label when collisions occur
            if label in seen_labels:
                # build a more specific label (days_hrs_mins_secs)
                parts = []
                rem = secs
                d = rem // 86400
                if d:
                    parts.append(f"{d}_day" + ("s" if d != 1 else ""))
                rem %= 86400
                h = rem // 3600
                if h:
                    parts.append(f"{h}_hr" + ("s" if h != 1 else ""))
                rem %= 3600
                m = rem // 60
                if m:
                    parts.append(f"{m}_min" + ("s" if m != 1 else ""))
                rem %= 60
                if rem:
                    parts.append(f"{rem}_secs")
                label = "_" + "_".join(parts) if parts else f"_{secs}_secs"

            names[label] = bitstr
            seen_labels.add(label)

    # add deactivated canonical label
    names["_deactivated"] = PSMPeriodicTau.DEACTIVATED_STANDARD

    # sort keys for stable ordering and put into CONVENIENCE
    sorted_items = dict(sorted(names.items(), key=lambda kv: kv[0]))
    PSMPeriodicTau.CONVENIENCE.update(sorted_items)

class PSMActiveTime:
    """
    Encoder/decoder for T3324 (Active Time) which uses the GPRS Timer 2 IE coding
    from 3GPP TS 24.008 (one octet: bits 8..6 = unit, bits 5..1 = 5-bit value).
    decode() returns:
      - None when top3 bits == '111' (timer deactivated; bits 5..1 ignored)
      - 0 for '00000000'
      - integer number of seconds otherwise

    Known unit mapping for GPRS Timer 2 (as used by T3324):
      0b000 -> multiples of 2 seconds
      0b001 -> multiples of 1 minute (60 seconds)
      0b010 -> multiples of 1 decihour (1/10 hour = 6 minutes = 360 seconds)
      0b111 -> deactivated (bits 5..1 ignored)

    Many vendor docs note: "Other values shall be interpreted as multiples of 1 minute"
    for older/this protocol version. This implementation decodes unknown non-111 units
    as 1 minute multiples to be robust.
    """

    # Known unit multipliers (seconds)
    _UNIT_MULTIPLIER = {
        0b000: 2,      # 2 seconds
        0b001: 60,     # 1 minute
        0b010: 360,    # 1 decihour (6 minutes)
        # other unit codes exist in other timer types; treat others as 60s fallback on decode
    }

    DEACTIVATED_CANONICAL = "11111111"
    ZERO = "00000000"
    DISABLED = "DISABLED"

    CONVENIENCE: Dict[str, str] = {}

    @classmethod
    def encode(cls, seconds: Union[int, str]) -> str:
        """
        Encode seconds -> 8-bit string.
        - seconds is cls.DISABLED -> return canonical deactivated "11111111"
        - seconds == 0 -> "00000000"
        - otherwise search for a (unit_code, value) such that multiplier * value == seconds
          and 0 <= value <= 31. Choose the smallest multiplier that fits (prefer smaller units).
        Raises ValueError if no exact representation exists.
        """
        if seconds is cls.DISABLED:
            return cls.DEACTIVATED_CANONICAL
        if seconds == 0:
            return cls.ZERO

        # Prefer smaller units first (so 2s multiples are chosen when possible)
        # Order: 2s, 60s, 360s
        for unit_code in (0b000, 0b001, 0b010):
            mult = cls._UNIT_MULTIPLIER[unit_code]
            if seconds % mult != 0:
                continue
            value = seconds // mult
            if 0 <= value <= 31:
                return format(unit_code, "03b") + format(int(value), "05b")

        # If not exact, we don't guess; raise so caller can use closest()
        raise ValueError(f"No exact GPRS-Timer2 encoding for {seconds} seconds")

    @classmethod
    def decode(cls, bitstr: str) -> Union[int, str]:
        """
        Decode an 8-character '0'/'1' string into seconds.
        - If top3 bits == '111' -> cls.DISABLED (deactivated)
        - If bitstr == '00000000' -> 0
        - If unit_code known -> multiplier * value
        - If unit_code unknown (but not 111) -> treat as 1 minute multiples (compat behaviour)
        Raises ValueError on malformed input.
        """
        if not isinstance(bitstr, str) or len(bitstr) != 8 or any(c not in "01" for c in bitstr):
            raise ValueError("bitstr must be an 8-character string of '0'/'1'")

        top3 = bitstr[:3]
        if top3 == "111":
            return cls.DISABLED
        if bitstr == cls.ZERO:
            return 0

        unit_code = int(top3, 2)
        value = int(bitstr[3:], 2)

        if unit_code in cls._UNIT_MULTIPLIER:
            mult = cls._UNIT_MULTIPLIER[unit_code]
            return mult * value

        # Fallback: treat unknown unit codes (non-111) as multiples of 1 minute (60s)
        # (many vendor docs say "other values shall be interpreted as multiples of 1 minute")
        return 60 * value

    @classmethod
    def closest(cls, seconds: int) -> Tuple[str, int]:
        """
        Return the representable bitstr whose decoded value is <= seconds and
        has the smallest delta (best fit not exceeding target).
        Returns (bitstr, encoded_seconds).
        If seconds <= 0 returns ZERO.
        """
        if seconds <= 0:
            return cls.ZERO, 0

        best = None
        best_delta = None
        best_encoded = None

        # enumerate plausible unit / value pairs (unit in known + also consider minute-fallback unit codes)
        # We'll iterate real unit codes we support (000,001,010) and also include 1-minute multiples (unit_code None)
        # to ensure we don't miss reasonable fits.
        # For completeness iterate values 0..31 for each representative unit code.
        candidate_units = list(cls._UNIT_MULTIPLIER.items())  # (unit_code, mult)
        # add a pseudo-unit for minute-fallback (use None to indicate 60s multiplier)
        candidate_units.append((None, 60))

        for unit_code, mult in candidate_units:
            for v in range(0, 32):
                if mult * v == 0:
                    continue
                encoded_sec = mult * v
                if encoded_sec <= seconds:
                    delta = seconds - encoded_sec
                    if best is None or delta < best_delta:
                        best_delta = delta
                        best_encoded = encoded_sec
                        if unit_code is None:
                            # create bitstr using 001 (1 minute) as canonical for minute-based fallback
                            bitstr = format(0b001, "03b") + format(v, "05b")
                        else:
                            bitstr = format(unit_code, "03b") + format(v, "05b")
                        best = bitstr
                        if delta == 0:
                            return best, best_encoded

        if best is None:
            raise ValueError("No representable timer value <= requested seconds")

        return best, best_encoded

    @classmethod
    def human_label_for_seconds(cls, seconds: int) -> str:
        """Human friendly label generation (e.g. _1_min_30_secs)"""
        if seconds == 0:
            return "_0_secs"
        parts = []
        rem = seconds
        days = rem // 86400
        if days:
            parts.append(f"{days}_day" + ("s" if days != 1 else ""))
            rem %= 86400
        hrs = rem // 3600
        if hrs:
            parts.append(f"{hrs}_hr" + ("s" if hrs != 1 else ""))
            rem %= 3600
        mins = rem // 60
        if mins:
            parts.append(f"{mins}_min" + ("s" if mins != 1 else ""))
            rem %= 60
        if rem:
            parts.append(f"{rem}_secs")
        return "_" + "_".join(parts)

# Populate CONVENIENCE map programmatically
def _populate_convenience():
    names = {}
    names["_0_secs"] = PSMActiveTime.ZERO

    # known units: 000 (2s), 001 (60s), 010 (360s)
    for unit_code, mult in PSMActiveTime._UNIT_MULTIPLIER.items():
        for v in range(0, 32):
            bitstr = format(unit_code, "03b") + format(v, "05b")
            secs = mult * v
            if secs == 0:
                continue
            label = PSMActiveTime.human_label_for_seconds(secs)
            # avoid collisions by making label more specific if needed
            if label in names:
                # build a descriptive label with composite parts
                rem = secs
                parts = []
                d = rem // 86400
                if d:
                    parts.append(f"{d}_day" + ("s" if d != 1 else ""))
                    rem %= 86400
                h = rem // 3600
                if h:
                    parts.append(f"{h}_hr" + ("s" if h != 1 else ""))
                    rem %= 3600
                m = rem // 60
                if m:
                    parts.append(f"{m}_min" + ("s" if m != 1 else ""))
                    rem %= 60
                if rem:
                    parts.append(f"{rem}_secs")
                label = "_" + "_".join(parts) if parts else f"_{secs}_secs"

            names[label] = bitstr

    names["_deactivated"] = PSMActiveTime.DEACTIVATED_CANONICAL

    # stable ordering
    PSMActiveTime.CONVENIENCE.update(dict(sorted(names.items(), key=lambda kv: kv[0])))

_populate_convenience()

# Quick demonstration when run as script
if __name__ == "__main__":
    samples = [
        (PSMActiveTime.DISABLED, "deactivated"),
        (0, "zero"),
        (2, "2s"),
        (4, "4s"),
        (60, "1m"),
        (240, "4m"),
        (360, "6m (1 decihour)"),
        (123, "nonexact"),
    ]
    for secs, name in samples:
        try:
            b = PSMActiveTime.encode(secs)
            dec = PSMActiveTime.decode(b)
            print(f"{name}: encode({secs}) -> {b} -> decode -> {dec}")
        except Exception as e:
            print(f"{name}: encode({secs}) -> ERROR: {e}")

    # decode deactivated examples (any '111xxxxx' -> None)
    for low in ("00000", "01010", "11111"):
        s = "111" + low
        print(f"decode {s} -> {PSMActiveTime.decode(s)}")

    # show some convenience entries
    for k, v in list(PSMActiveTime.CONVENIENCE.items())[:12]:
        print(k, v)

class EDRXMode(Enum):
    """
    Represents the eDRX mode.

    AT Command: AT+CEDRXS=<eDRXMode>,<eDRXAccessTechnology>,<eDRXCycle>
    """
    DISABLED = 0
    ENABLED = 1
    ENABLED_WITH_URC = 2
    DISABLED_AND_RESET = 3

class EDRXAccessTechnology(Enum):
    """
    Represents the eDRX access technology.

    AT Command: AT+CEDRXS=<eDRXMode>,<eDRXAccessTechnology>,<eDRXCycle>
    """
    EUTRAN_WB_S1 = 4
    EUTRAN_NB_S1 = 5

class EDRXCycle(Enum):
    """
    Represents the eDRX cycle.

    AT Command: AT+CEDRXS=<eDRXMode>,<eDRXAccessTechnology>,<eDRXCycle>
    """
    T_5_12 = '0000'
    T_10_24 = '0001'
    T_20_48 = '0010'
    T_40_96 = '0011'
    T_81_92 = '0100'
    T_163_84 = '0101'
    T_327_68 = '0110'
    T_655_36 = '0111'
    T_1310_72 = '1000'
    T_2621_44 = '1001'
    T_5242_88 = '1010'
    T_10485_76 = '1011'
    T_20971_52 = '1100'
    T_41943_04 = '1101'
    T_83886_08 = '1110'
    T_167772_16 = '1111'