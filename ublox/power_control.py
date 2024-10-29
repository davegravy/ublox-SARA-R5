import time
from mpio import GPIO
import mmap
import os
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

class PowerControl(ABC):
    """
    An abstract base class for controlling power states of modules.
    This class provides abstract methods to control the power state of a module using GPIO pins.
    It includes functionalities to power on, power off, perform a hard reset, and configure GPIO bias.
    Methods:
        __init__(): Initializes the GPIO pins and configures GPIO bias.
        get_power_state(): Returns the current power state of the module.
        await_power_state(target_state, timeout=30): Waits for the module to reach the target power state within the specified timeout.
        power_on_wake(): Powers on or wakes up the module.
        force_power_off(): Forces the module to power off.
        force_power_off_alt(): An alternative method to force the module to power off.
        hard_reset(): Performs a hard reset on the module.
        close(): Closes the GPIO instances.
        _config_gpio_bias(): Configures the GPIO bias settings.
    """

    @abstractmethod
    def __init__(self):
        pass

    @abstractmethod
    def get_power_state(self):
        pass

    @abstractmethod
    def await_power_state(self, target_state, timeout=30):
        pass

    @abstractmethod
    def power_on_wake(self):
        pass

    @abstractmethod
    def force_power_off(self):
        pass

    @abstractmethod
    def force_power_off_alt(self):
        pass

    @abstractmethod
    def hard_reset(self):
        pass

    @abstractmethod
    def close(self):
        pass

    @abstractmethod
    def _config_gpio_bias(self):
        pass

class AT91PowerControl(PowerControl):
    """
    An AT-SAMA5D27-specific implementation for controlling the SARA-R510S-01B LTE module.
    This class provides methods to control the power state of the LTE module using GPIO pins.
    It includes functionalities to power on, power off, perform a hard reset, and configure GPIO bias.
    Attributes:
        gpio_v_int (GPIO): GPIO instance for the V_INT pin.
        gpio_reset_n (GPIO): GPIO instance for the RESET_N pin.
        gpio_pwr_on (GPIO): GPIO instance for the PWR_ON pin.
    Methods:
        __init__(): Initializes the GPIO pins and configures GPIO bias.
        get_power_state(): Returns the current power state of the LTE module.
        await_power_state(target_state, timeout=30): Waits for the LTE module to reach the target power state within the specified timeout.
        power_on_wake(): Powers on or wakes up the LTE module.
        force_power_off(): Forces the LTE module to power off.
        force_power_off_alt(): An alternative method to force the LTE module to power off.
        hard_reset(): Performs a hard reset on the LTE module.
        close(): Closes the GPIO instances.
        _config_gpio_bias(): Configures the GPIO bias settings.
    """

    def __init__(self):
        # 128 gpio in gpiochip0
        # 0 ~ 31 PA0 -> PA31
        # 32 ~ 63 PB0 -> PB31
        # 64 ~ 95 PC0 -> PC31
        # 96 ~ 127 PD0 -> PD31

        #NOTE setting mode on outputs may cause flicker of the power state
        logger.info("Initializing PowerControl")

        self._config_gpio_bias()
        self.gpio_v_int = GPIO(87, GPIO.IN)
        vint_initial = self.gpio_v_int.get()
        self.gpio_reset_n = GPIO(85, GPIO.OUT,initial=vint_initial) #TODO invert for next hw rev
        self.gpio_pwr_on = GPIO(89, GPIO.OUT,initial=False)

    def get_power_state(self):
        """
        Retrieve the current power state.
        Returns:
            bool: The current state of the power, as indicated by the GPIO pin.
        """

        return self.gpio_v_int.get()

    def await_power_state(self, target_state, timeout=30):
        logger.debug(f"Awaiting power state. target_state: {target_state} timeout: {timeout}")
        if self.get_power_state() == target_state:
            return True
        #burn one to clear past edges
        self.gpio_v_int.poll(edge=GPIO.RISING if target_state else GPIO.FALLING, timeout=0.01)
        result = self.gpio_v_int.poll(edge=GPIO.RISING if target_state else GPIO.FALLING, timeout=timeout)
        success = GPIO.RISING if target_state else GPIO.FALLING
        if result == success:
            current_state = self.get_power_state()
            logger.debug(f"current state: {current_state}, target_state: {target_state}")
            return current_state == target_state

    def power_on_wake(self):
        """
        Powers on the device if it is not already powered on.
        Returns:
            bool: True if the device is successfully powered on, False otherwise.
        """
        power_state = self.get_power_state()

        if power_state:
            logger.info("Power ON/Wake requested, already on")
            return power_state
        logger.info("Power ON/Wake requested, powering on")
        self.gpio_pwr_on.set(True)
        self.gpio_reset_n.set(True) #TODO remove when reset fixed
        time.sleep(2.5)
        self.gpio_pwr_on.set(False)
        time.sleep(0.25)
        success = self.get_power_state() == True
        return success

    def force_power_off(self):
        """
        Forces the power off sequence for the device.
        Returns:
            bool: True if the device was successfully powered off, False otherwise.
        """

        power_state = self.get_power_state()

        if not power_state:
            logger.info("Force power OFF requested, already off")
            return True

        logger.info("Force power OFF requested, powering off")
        logger.debug("setting pwr_on to 0")
        self.gpio_pwr_on.set(True)
        time.sleep(22.5)
        logger.debug("setting reset_n to 0")
        self.gpio_reset_n.set(False) #TODO invert for next hw rev
        time.sleep(1)
        logger.debug("setting pwr_on to 1")
        self.gpio_pwr_on.set(False)
        time.sleep(2)
        logger.debug("setting reset_n to 1")
        self.gpio_reset_n.set(True)
        time.sleep(0.25)
        success = self.get_power_state() == False
        return success

    def force_power_off_alt(self):
        """
        An alternative method to force the power off sequence for the device.
        Returns:
            bool: True if the device was successfully powered off, False otherwise.
        """

        power_state = self.get_power_state()

        if not power_state:
            logger.info("Force power OFF requested, already off")
            return True

        logger.info("Force power OFF requested, powering off (ALT method)")
        logger.debug("setting pwr_on to 0")
        self.gpio_pwr_on.set(True)
        time.sleep(4.25)
        logger.debug("setting pwr_on to 1")
        self.gpio_pwr_on.set(False)
        success = self.get_power_state() == False
        return success

    def hard_reset(self):
        """
        Perform a hard reset of the device.
        Returns:
            bool: True if the reset was successful and the power state is on, False otherwise.
        """

        if not self.get_power_state():
            logger.warning("Hard reset requested but power is off")
            return False

        logger.info("Hard reset requested, resetting")
        self.gpio_reset_n.set(False) #TODO invert for next hw rev
        time.sleep(0.2)
        self.gpio_reset_n.set(True)
        success = self.get_power_state() == True
        return success

    def close(self):
        """
        Closes the GPIO connections for the power control.
        This method ensures that all GPIO connections used for power control 
        (gpio_v_int, gpio_reset_n, gpio_pwr_on) are properly closed to free up 
        resources and avoid potential issues with unclosed connections.
        """
        logger.info("Closing PowerControl")
        self.gpio_v_int.close()
        self.gpio_reset_n.close()
        self.gpio_pwr_on.close()
        logger.info("PowerControl closed")

    def _config_gpio_bias(self):
        """
        Configures the GPIO bias settings for PC23 to have a pulldown and no pullup.
        Raises:
            OSError: If there is an error opening or mapping the memory.
            ValueError: If the verification of written values fails.
        """
        port_c_mask_offset = 0x80
        port_c_config_offset = 0x84
        pc23_mask = 0x800000
        #PC23 CONFIG for pulldown, no pullup
        pc23_config = 0x400

        # Open /dev/mem
        mem_file = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)

        # Map the memory
        mem = mmap.mmap(mem_file, mmap.PAGESIZE, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ, offset=0xfc038000)

        # Write the values to the memory map
        mem[port_c_mask_offset:port_c_mask_offset + 4] = (pc23_mask).to_bytes(4, byteorder='little')
        mem[port_c_config_offset:port_c_config_offset + 4] = (pc23_config).to_bytes(4, byteorder='little')

        # Read back the values to verify
        read_mask = int.from_bytes(mem[port_c_mask_offset:port_c_mask_offset + 4], byteorder='little')
        read_config = int.from_bytes(mem[port_c_config_offset:port_c_config_offset + 4], byteorder='little')

        if read_mask != pc23_mask:
            raise ValueError(f"Verification failed for mask: expected {pc23_mask}, got {read_mask}")

        if read_config != pc23_config:
            raise ValueError(f"Verification failed for config: expected {pc23_config}, got {read_config}")


        # Close the memory map and file
        mem.close()
        os.close(mem_file)
