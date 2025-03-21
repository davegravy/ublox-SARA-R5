"""
This module provides a representation of the Ublox SARA R5XX module and their functionalities.

Currently only SARA R510S is supported.

It includes classes and enums for managing the module's communication, 
power control, network registration,

Example usage:
    import modules

    # Create an instance of the SaraR5Module class
    module = modules.SaraR5Module(serial_port='/dev/ttyUSB0')

    # Initialize the serial communication with the module
    module.serial_init()

    # Perform various operations with the module

    # Close the serial connection
    module.serial_close()
"""

from enum import Enum
from dataclasses import dataclass, field
from functools import partial
from collections import namedtuple
from typing import Callable
from contextlib import nullcontext


import os
import threading
import queue
import datetime
import logging
import time
import serial
import validators
import errno
import select
import io
import json


from ublox.http import HTTPClient
from ublox.mqtt import MQTTClient
from ublox.security_profile import SecurityProfile
from ublox.utils import PSMActiveTime, PSMPeriodicTau, EDRXMode, EDRXCycle,EDRXAccessTechnology
from ublox.power_control import PowerControl
#from ublox.socket import UDPSocket

Stats = namedtuple('Stats', 'type name value')

class CMEError(Exception):
    """CME ERROR on Module"""

class ATError(Exception):
    """AT Command Error"""

class ATTimeoutError(ATError):
    """Making an AT Action took to long"""

class ConnectionTimeoutError(ATTimeoutError):
    """Module did not connect within the specified time"""

class ModuleNotRespondingError(Exception):
    """Module did not respond"""

class URDFFileFormatError(ValueError):
    """Custom exception raised for invalid URDFILE format."""
    pass

class MobileNetworkOperator(Enum):
    """
    Represents the mobile network operator.

    AT Command: AT+UMNOPROF=<MobileNetworkOperator>
    """
    UNDEFINED_REGULATORY = 0
    SIM_ICCID_IMSI_SELECT = 1
    AT_AND_T = 2
    VERIZON = 3
    TELSTRA = 4
    T_MOBILE_US = 5
    CHINA_TELECOM = 6
    SPRINT = 8
    VODAFONE = 19
    NTT_DOCOMO = 20
    TELUS = 21
    SOFTBANK = 28
    DEUTSCHE_TELEKOM = 31
    US_CELLULAR = 32
    VIVO = 33
    LG_U_PLUS = 38
    SKT = 39
    KDDI = 41
    ROGERS = 43
    CLARO_BRASIL = 44
    TIM_BRASIL = 45
    ORANGE_FRANCE = 46
    BELL = 47
    GLOBAL = 90
    STANDARD_EUROPE = 100
    STANDARD_EUROPE_NO_EPCO = 101
    STANDARD_JP_GLOBAL = 102
    AT_AND_T_2_4_12 = 198
    GENERIC_VOICE_CAPABLE_AT_AND_T = 199
    GCF_PTCRB = 201
    FIRSTNET = 206

class AT_Command_Handler():

    def __init__(self, response_queue, output_fn, logger=None):
        self.logger = logger or logging.getLogger(__name__)

        self.response_queue = response_queue
        self.output_fn = output_fn
    
    def send_cmd(self, command:str, input_data:bytes=None, expected_reply=True, expected_multiline_reply=False, file_out=False, timeout=10):
        
        """
        Sends a command to the module and waits for a response.

        Args:
            command (str): The command to send to the module.
            input_data (bytes, optional): Additional data to send after receiving a ">" prompt.
                Defaults to None.
            expected_reply (bool or str, optional): The expected reply from the module.
                - If True, expects a reply with a prefix matching the command.
                - If False, no reply is expected.
                - If a string, expects a reply with the specified prefix.
                Defaults to True.
            expected_multiline_reply (bool, optional): Specifies whether a multiline reply is expected.
                Only applicable if expected_reply is True or a string. Defaults to False.

            timeout (int, optional): The maximum time to wait for a response, in seconds.
                Defaults to 10.

        Returns:
            list or None: The response from the module, split into a list if 
                it's a single-line reply. Returns None if no response is expected.

        Raises:
            TypeError: If expected_reply is not of type bool or str.
            ValueError: If multiline_reply is True and expected_reply is False.
            ATTimeoutError: If a response is not received within the specified timeout.
            ATError: If the module returns an "ERROR" response.
            CMEError: If the module returns a "+CME ERROR" response.

        """

        self.command_str = command
        self.input_data = input_data
        self.expected_reply = expected_reply
        self.expected_multiline_reply = expected_multiline_reply
        self.file_out = file_out
        self._validate()
        self._prepare_expected_reply()

        self.command_send_time = self.output_fn(self._command_bytes(terminated=True))
        
        #timestamp_write_str = self.command_send_time.strftime("%Y-%m-%d_%H-%M-%S")
        #self.logger.debug('Sent:%s          %s: %s', chr(10), timestamp_write_str, self._command_bytes(terminated=True))

        self.got_linefeed = False
        self.got_reply = True if not self.expected_reply_bytes else False
        self.got_ok = False
        self.result, self.multiline_result = None, []
        self.debug_log = []
        self.timeout_time = time.time() + timeout
        file_context = open(self.file_out, 'wb') if self.file_out else nullcontext() # Choose context manager conditionally


        try:
            if self.input_data is not None:
                self.logger.debug(f"send_cmd with input data, timeout is in {self.timeout_time - time.time()} seconds")
            with file_context as output_file:
                while not time.time() > self.timeout_time:
                    if self.got_ok and self.got_reply and self.input_data is None:
                        break
                    response, timestamp_read = self._get_response()
                    self._process_response(response, timestamp_read, output_file)
                    if self.input_data and response and response.startswith(b">"):                    
                        write_timeout = self.timeout_time - time.time()
                        self.output_fn(self.input_data,timeout=write_timeout)
                        self.input_data = None
                else:   
                    self.logger.error(f"Timeout waiting for response to '{self.command_str}'. State: got_ok={self.got_ok}, got_reply={self.got_reply}, input_data={self.input_data}")
                    raise ATTimeoutError("Timeout waiting for response")
        except Exception as e:
            raise e
        finally:
            self._log_debug_info()
        
        if self.expected_multiline_reply and not self.file_out:
            return self.multiline_result  
        else:
            return self.result

    def _peek_queue(q:queue.Queue):
        # Acquire the internal mutex so no other thread can modify the queue
        with q.mutex:
            # Create a shallow copy of the internal deque/list
            return list(q.queue)
    
    def _validate(self):
        if not isinstance(self.expected_reply, (bool, str)):
            raise TypeError("expected_reply is not of type bool or str")
        if self.expected_multiline_reply and not self.expected_reply:
            raise ValueError("multiline_reply cannot be True if expected_reply is False")
        if self.file_out is not None and not isinstance(self.file_out, str):
            raise TypeError("file_out must be either None or a string")
        if self.file_out is not None and not self.expected_multiline_reply:
            raise ValueError("file_out can only be used with expected_multiline_reply=True")

    def _command_bytes(self, terminated=True):
        command_bytes_unterminated = self.command_str.encode().rstrip(b"\r\n")
        result =  command_bytes_unterminated + b"\r\n" if terminated else command_bytes_unterminated
        return result

    def _prepare_expected_reply(self):
        if self.expected_reply is True:
            expected_reply_bytes = self.command_str.lstrip("AT").split("=")[0].split("?")[0].encode() + b":"
        elif self.expected_reply is False:
            expected_reply_bytes = None
        elif isinstance(self.expected_reply, str):
            expected_reply_bytes = self.expected_reply.encode()
        self.expected_reply_bytes=expected_reply_bytes
    
    def _get_response(self):
        time_remaining = self.timeout_time - time.time()
        try:
            response, timestamp_read = self.response_queue.get(timeout=time_remaining)
            self.debug_log.append((timestamp_read, response))
            return response, timestamp_read
        except queue.Empty:
            return None, None
        
    def _process_response(self, response, timestamp_read, output_file:io.BufferedWriter):
        
        linefeed = b"\r\n"

        #TODO: handle scenario where OK received before linefeed (bad state) 
        if timestamp_read is not None and timestamp_read + datetime.timedelta(seconds=0.02) < self.command_send_time:
            self.logger.debug(f"Timestamp read {timestamp_read} is before command send time {self.command_send_time}")
            self.logger.debug(f"Command in progress: {self.command_str}, violating response: {response}")
            #raise ValueError("Timestamp read is before command send time")
            
        if response is None:
            return
        if response == linefeed:
            if self.got_ok:
                self.logger.warning('got linefeed after OK')
            if self.got_linefeed:
                self.logger.warning('got consecutive linefeeds')
            self.got_linefeed = True
        elif response.startswith(b"OK"): #TODO: make this more specific, ie if response == b"OK\r\n"
            if not self.got_linefeed:
                self.logger.warning('got OK before linefeed')
            self.got_ok = True
            self.got_linefeed = False
            if self.expected_reply_bytes and not self.got_reply:
                raise ATError("got OK before expected reply")
        elif response.startswith(b"ERROR"): #TODO: make this more specific
            self.got_linefeed = False
            raise ATError
        elif response.startswith(b"+CME ERROR:"):
            code = response.lstrip(b"+CME ERROR:").rstrip(b"\r\n").decode()
            self.got_linefeed = False
            #TODO: convert code to error message
            raise CMEError(code)
        elif self.expected_reply_bytes and response.startswith(self.expected_reply_bytes):
            if not self.got_linefeed:
                self.logger.warning('got reply before linefeed')
            self.got_reply = True
            self.got_linefeed = False
            self.result = response[len(self.expected_reply_bytes):].rstrip(b"\r\n").decode().strip().split(",")
            if self.file_out:
                output_file.write(response)
            else: 
                self.multiline_result.append(response)
        elif self.expected_multiline_reply and self.expected_reply_bytes and self.got_reply:
            if self.got_linefeed:
                self._write_multiline_output(linefeed, output_file)
                self.got_linefeed = False
            self._write_multiline_output(response, output_file)
        elif response.startswith(self._command_bytes(terminated=False)):
            pass #why? echo?
        elif self.input_data and response.startswith(b">"):
            if not self.got_linefeed:
                self.logger.warning('got ">" before linefeed')
            self.got_linefeed = False
            # raw data input prompt, handled elsewhere
            pass
        else:
            self.logger.warning('got unexpected %s', response)

    def _write_multiline_output(self, data, output_file:io.BufferedWriter):
        if self.file_out:
            output_file.write(data)
        else:
            self.multiline_result.append(data)

    def _log_debug_info(self):
        # debug_str = [f'{timestamp.strftime("%Y-%m-%d_%H-%M-%S-%f")}: {response}' for timestamp, response in self.debug_log]
        # debug_output = '\n          '.join(debug_str)
        # self.logger.debug('Received:%s          %s', chr(10), debug_output)
        pass

@dataclass
class SaraR5ModuleState:

    """
    Represents the state of the SaraR5Module.

    example usage:
    module_state = SaraR5ModuleState()
    module_state.psd = {**self.module_state.psd, "ip": ip, "is_active": True}

    """
    imei: str = None
    iccid: str = None
    model_name: str = None
    psd: dict = field(default_factory=dict)
    psm: 'SaraR5Module.PSMState' = None
    signalling_cx_status: bool = False
    registration_status: 'SaraR5Module.EPSNetRegistrationStatus' = None
    radio_status: dict = field(default_factory=dict)
    radio_stats: dict = field(default_factory=dict)
    location: dict = field(default_factory=dict)
    logger: logging.Logger = field(default=logging.getLogger(__name__))

    parameter_names = {
        'imei': 'IMEI',
        'iccid': 'ICCID',
        'model_name': 'Model Name',
        'psd': 'Packet Switched Data',
        'psm': 'Power Saving Mode',
        'signalling_cx_status': 'Signalling Connection Status',
        'registration_status': 'Registration Status',
        'radio_status': 'Radio Status',
        'radio_stats': 'Radio Statistics',
        'location': 'GPS Location Data'
    }

    def state_change(self, parameter_name, old_value, new_value):

        if isinstance(old_value, Enum):
            old_value = old_value.name
        if isinstance(new_value, Enum):
            new_value = new_value.name

        human_friendly_name = self.parameter_names.get(parameter_name, parameter_name)
        self.logger.info(f"Module state: {human_friendly_name} changed from {old_value} to {new_value}")

    def __setattr__(self, name, value):
        if name in self.__dict__:
            old_value = self.__dict__[name]
            if old_value != value:
                self.state_change(name, old_value, value)
        super().__setattr__(name, value)

@dataclass
class SaraR5SerialConfig:
    """
    Represents the serial configuration for the SaraR5Module.

    Args:
        serial_port (str): The serial port to communicate with the module.
        baudrate (int, optional): The baudrate for the serial communication. Defaults to 115200.
        rtscts (bool, optional): Enable RTS/CTS flow control. Defaults to False.
    """
    serial_port: str
    baudrate: int = 115200
    rtscts: bool = True
    echo: bool = False

@dataclass
class SaraR5ModuleConfig:
    mno_profile: MobileNetworkOperator
    apn: str
    roaming: bool = False
    power_saving_mode: bool = False
    edrx_mode: EDRXMode = EDRXMode.DISABLED
    tau: PSMPeriodicTau = None
    active_time: PSMActiveTime = None
    registration_status_reporting: 'SaraR5Module.EPSNetRegistrationReportConfig' = None
    

class SaraR5Module:
    """
    Represents a u-blox SARA-R5 module.

    Args:
        power_toggle (Callable[[], None], optional): A function to toggle the power of the module. 
            Provide a function which toggles the GPIO to which PWR_ON pin of the module is connected
            using the required timing described in the datasheet. Defaults to None.
    """
    class HEXMode(Enum):
        """
        Represents the HEX mode for AT commands.

        AT Command: AT+UDCONF=1,<HEXMode>
        """
        DISABLED = 0
        ENABLED = 1

    class ErrorFormat(Enum):
        """
        Represents the error format for AT commands.

        AT Command: AT+CMEE=<ErrorFormat>
        """
        DISABLED = 0
        NUMERIC = 1
        VERBOSE = 2

    class ModuleFunctionality(Enum):
        """
        Represents the module functionality.

        AT Command: AT+CFUN=<ModuleFunctionality>
        """
        MINIMUM_FUNCTIONALITY = 0 #no TxRx
        FULL_FUNCTIONALITY = 1
        AIRPLANE_MODE = 4
        DISABLE_RF_AND_SIM = 7
        DISABLE_RF_AND_SIM_2 = 8
        FAST_SAFE_POWEROFF = 10
        SILENT_RESET = 16
        RESTORE_PROTOCOL_STACK = 126

    class ModulePowerMode(Enum):
        """
        Represents the module power mode.

        AT Command: AT+CFUN?
        """
        ON = 1
        MINIMUM_FUNCTIONALITY = 0
        AIRPLANE_MODE = 4
        MIN_FUNC_DISABLE_SIM = 19

    class STK_Mode(Enum):
        """
        Represents the SIM Toolkit mode.

        AT Command: AT+CFUN?
        """
        STK_DEDICATED_MODE = 6
        STK_DISABLED_MODE_1 = 0
        STK_DISABLED_MODE_2 = 7
        STK_DISABLED_MODE_3 = 8
        STK_RAW_MODE = 9

    class RadioAccessTechnology(Enum):
        """
        Represents the radio access technology.

        AT Command: AT+URAT=<RadioAccessTechnology>
        """
        LTE_CAT_M1 = 7
        NB_IOT = 8

    class CurrentRadioAccessTechnology(Enum):
        """
        Represents the current radio access technology.

        AT Command: AT+URAT?
        """
        _2G = 2
        _3G = 3
        _4G = 4
        UNKNOWN = 5
        LTE_CAT_M1 = 6
        NB_IOT = 7

    class CurrentRadioServiceState(Enum):
        """
        Represents the current radio service state.

        AT Command: AT+UCREG?
        """
        NOT_KNOWN = 0
        RADIO_OFF = 1
        SEARCHING = 2
        NO_SERVICE = 3
        REGISTERED = 4

    class LTERadioResourceControlState(Enum):
        """
        Represents the LTE radio resource control state.

        AT Command: AT+ULOCCELL?
        """
        NULL = 0
        IDLE = 1
        ATTEMPT_TO_CONNECT = 2
        CONNECTED = 3
        LEAVING_CONNECTED_STATE = 4
        ATTEMPT_LEAVING_E_UTRA = 5
        ATTEMPT_ENTERING_E_UTRA = 6
        NOT_KNOWN = 255

    class SignalCxReportConfig(Enum):
        """
        Represents the signalling connection status report configuration.

        AT Command: AT+CSCON=<SignallingCxStatusReportConfig>
        """
        DISABLED = 0
        ENABLED_MODE_ONLY = 1
        ENABLED_MODE_AND_STATE = 2
        ENABLED_MODE_AND_STATE_AND_ACCESS = 3

    class EPSNetRegistrationReportConfig(Enum):
        """
        Represents the EPS network registration report configuration.

        AT Command: AT+CEREG=<EPSNetworkRegistrationReportConfig>
        """
        DISABLED = 0
        ENABLED = 1
        ENABLED_WITH_LOCATION = 2
        ENABLED_WITH_LOCATION_AND_EMM_CAUSE = 3
        ENABLED_WITH_LOCATION_AND_PSM = 4
        ENABLED_WITH_LOCATION_AND_EMM_CAUSE_AND_PSM = 5

    class EPSNetRegistrationStatus(Enum):
        """
        Represents the EPS network registration status.

        AT Command: AT+CEREG?
        """
        NOT_REGISTERED = 0
        REGISTERED_HOME_NET = 1
        NOT_REGGISTERED_AND_SEARCHING = 2
        REGISTRATION_DENIED = 3
        UNKNOWN = 4
        REGISTERED_AND_ROAMING = 5
        EMERGENCY_BEARER_ONLY = 8

    class PSDProtocolType(Enum):
        """
        Represents the PSD protocol type.

        AT Command: AT+UPSD=<profile_id>,0,<PSDProtocolType>
        """
        IPV4 = 0
        IPV6 = 1
        IPV4V6_WITH_IPV4_PREFERRED = 2
        IPV4V6_WITH_IPV6_PREFERRED = 3

    class PSDAction(Enum):
        """
        Represents the PSD action.

        AT Command: AT+UPSDA=<profile_id>,<PSDAction>
        """
        RESET = 0
        STORE = 1
        LOAD = 2
        ACTIVATE = 3
        DEACTIVATE = 4

    class PSDParameters(Enum):
        """
        Represents the PSD parameters.

        AT Command: AT+UPSND=<profile_id>,<PSDParameters>
        """
        IP_ADDRESS = 0
        DNS1 = 1
        DNS2 = 2
        QOS_PRECEDENCE = 3
        QOS_DELAY = 4
        QOS_RELIABILITY = 5
        QOS_PEAK_RATE = 6
        QOS_MEAN_RATE = 7
        ACTIVATION_STATUS = 8
        QOS_DELIVERY_ORDER = 9
        QOS_ERRONEOUS_SDU_DELIVERY = 10
        QOS_EXTENDED_GUARANTEED_DOWNLINK_BIT_RATE = 11
        QOS_EXTENDED_MAXIMUM_DOWNLINK_BIT_RATE = 12
        QOS_GUARANTEED_DOWNLINK_BIT_RATE = 13
        QOS_GUARANTEED_UPLINK_BIT_RATE = 14
        QOS_MAXIMUM_DOWNLINK_BIT_RATE = 15
        QOS_MAXIMUM_UPLINK_BIT_RATE = 16
        QOS_MAXIMUM_SDU_SIZE = 17
        QOS_RESIDUAL_BIT_ERROR_RATE = 18
        QOS_SDU_ERROR_RATIO = 19
        QOS_SIGNALLING_INDICATOR = 20
        QOS_SOURCE_STATISTICS_DESCRIPTOR = 21
        QOS_TRAFFIC_CLASS = 22
        QOS_TRAFFIC_PRIORITY = 23
        QOS_TRANSFER_DELAY = 24

    class PDPType(Enum):
        """
        Represents the PDP type.

        AT Command: AT+UPSD=<profile_id>,0,<PDPType>
        """
        IPV4 = 'IP'
        NONIP = 'NONIP'
        IPV4V6 = 'IPV4V6'
        IPV6 = 'IPV6'

    class PowerSavingUARTMode(Enum):
        """
        Represents the power saving UART mode.

        AT Command: AT+UPSV=<PowerSavingUARTMode>
        """
        DISABLED = 0
        ENABLED = 1
        RTS_CONTROLLED = 2
        DTS_CONTROLLED = 3
        ENABLED_2 = 4 # same as ENABLED?

    class PSMMode(Enum):
        """
        Represents the PSM mode.

        AT Command: AT+CPSMS=<PSMMode>,,,<RequestedPeriodicTau>,<RequestedActiveTime>
        """
        DISABLED = 0
        ENABLED = 1
        DISABLED_AND_RESET = 2

    class PSMState(Enum):
        """
        Represents the PSM state.

        URC: +UUPSMR: <state>[,<param1>]
        """
        PSM_INACTIVE = 0
        ENTERING_PSM = 1
        PSM_BLOCKED = 2
        PARTIAL_PSM_CLIENT_BLOCKING = 3

    def __init__(self, 
                 serial_config:SaraR5SerialConfig,
                 module_config:SaraR5ModuleConfig, 
                 power_control: type = PowerControl, 
                 model:str = "R520",
                 logger=None, 
                 tx_rx_logger=None):
        

        self.logger = logger or logging.getLogger(__name__)
        if logger is None:
            self.logger.setLevel(logging.DEBUG)
            self.logger.addHandler(logging.StreamHandler())

        self.tx_rx_logger = tx_rx_logger or logging.getLogger(__name__ + '.tx_rx')
        if tx_rx_logger is None:
            self.tx_rx_logger.setLevel(logging.DEBUG)
            self.tx_rx_logger.addHandler(logging.StreamHandler())

        self.serial_config = serial_config
        self.module_config = module_config
        self.module_state = SaraR5ModuleState(logger=self.logger)

        self._serial = serial.Serial(self.serial_config.serial_port, baudrate=self.serial_config.baudrate,
                                     rtscts=self.serial_config.rtscts,bytesize=8,parity='N',
                                     stopbits=1,timeout=0.1)
        self._serial_flush_event = threading.Event()
        self.power_control:PowerControl = power_control(logger=self.logger)
        self.model = model

        self.serial_read_queue = queue.Queue()
        self.at_cmd_handler = AT_Command_Handler(self.serial_read_queue, self._write_serial_and_log, logger=self.logger)
        

        self.terminate = False
        self.large_binary_xfer = False #to communicate to the read_thread to expect a lot of binary data over UART


        self.read_uart_thread = threading.Thread(target=self._read_from_uart)
        self.read_uart_thread.daemon = True




        self.sockets = {}
        self.http_profiles = {}
        self.security_profiles = {}
        self.mqtt_client = MQTTClient(self)



        self.urc_mappings = {
            "+CEREG":  self.handle_cereg,
            "+UUPSDD": self.handle_uupsdd,
            "+UUPSDA": self.handle_uupsda,
            "+UUHTTPCR": partial(HTTPClient.handle_uuhttpcr, self),
            "+CSCON": self.handle_cscon,
            "+UUPSMR": self.handle_uupsmr,
            "+UUMQTTC": self.mqtt_client.handle_uumqttc,
            "+UULOC": self.handle_uuloc
            #"+CGPADDR": self.handle_cgpaddr,
        }

        # receive_log_name = 'receive_log.csv'
        # send_log_name = 'send_log.csv'
        # if os.path.exists(receive_log_name):
        #     os.remove(receive_log_name)
        # if os.path.exists(send_log_name):
        #     os.remove(send_log_name)
        # self.receive_log = open(receive_log_name, 'a',encoding='utf-8')
        # self.send_log = open(send_log_name, 'a', encoding='utf-8')

        # self.read_vin_thread.start()
        self.read_uart_thread.start()

    def serial_init(self, clean=False, retry_threshold=5):
        """
        Initializes the serial communication with the module.

        Args:
            retry_threshold (int, optional): The maximum number of retries. Defaults to 5.
            clean (bool, optional): If True, a full module power down is first performed followed by soft reset. Defaults to False.
            
        Raises:
            Exception: If the module does not respond.
        """
        self.logger.info('Initializing module (clean=%s)', clean)

        responding = None
        power_cycles_count = 0
        hard_reset_count = 0

        if clean:
            self.logger.info("Powering OFF the module")
            self.logger.debug(f"Module model: {self.model} ")
            success = False
            while not success:
                if self.model == "R520":
                    self.power_control.force_power_off_R520()
                else:
                    self.power_control.force_power_off()
                success = self.power_control.await_power_state(False, timeout=30)
                if not success: self.logger.warning("Power OFF failed, retrying")
                time.sleep(1)  # wait before retrying
            self.logger.info("Power OFF successful")

        while True:
            self.logger.info("Powering ON the module")
            success = False
            while not success:
                if self.model == "R520":
                    self.power_control.power_on_wake_R520()
                else:
                    self.power_control.power_on_wake()
                success = self.power_control.await_power_state(True, timeout=30)
                if not success: 
                    self.logger.warning("Power ON failed, retrying")
                else: 
                    self.logger.info("Power ON successful")
                time.sleep(1)  # wait before retrying

            time.sleep(3)  # wait for boot
            self._reset_input_buffers()  # remove noise from any preceding power cycles

            for _ in range(7):
                try:
                    self.send_command("AT", expected_reply=False, timeout=1)
                    self.send_command("AT+UPSV=0", expected_reply=False, timeout=1) #in case module is in power saving UART mode
                    self.send_command("ATE0", expected_reply=False, timeout=1)
                    responding = True
                    break
                except ATTimeoutError as e:
                    self.logger.debug(e)
                    responding = False

            if responding:
                wait_time = 1
                self.logger.info(f"Module is responding, waiting {wait_time}s for RX to clear")
                time.sleep(wait_time) #wait for any URCs to finish
                self.logger.info("Module is responding, cleaning input buffer")
                self._reset_input_buffers()  # start from a clean slate
                time.sleep(wait_time)
                self.logger.info(f"receive queue size: {self.serial_read_queue.qsize()}")
                break
            
            # if not responding, try hard resets until retry_thredhold then try power cycles until retry_threshold
            
            if hard_reset_count < -1: # TODO replace with 'retry_threshold' once reset is fixed
                self.logger.info("Hard Resetting the module, attempt #%s of %s", hard_reset_count, retry_threshold)
                self.hard_reset() 
                hard_reset_count += 1
                time.sleep(1) 
            elif power_cycles_count < retry_threshold:
                self.logger.info("Powering OFF the module (30 second process), attempt #%s of %s", power_cycles_count, retry_threshold)
                success = False
                while not success:
                    if self.model == "R520":
                        self.power_control.force_power_off_R520()
                    else:
                        self.power_control.force_power_off()
                    
                    success = self.power_control.await_power_state(False, timeout=30)
                    if not success: self.logger.warning("Power OFF failed, retrying")
                    time.sleep(1)  # wait before retrying
                power_cycles_count += 1
            else:
                raise ModuleNotRespondingError("Module not responding, tried %s hard resets and %s power cycles" % (hard_reset_count, power_cycles_count))
            
        self.at_set_echo(self.serial_config.echo)
        self.at_set_power_saving_uart_mode(SaraR5Module.PowerSavingUARTMode.DISABLED) #in case module is about to enter PSM
        self.at_set_error_format(SaraR5Module.ErrorFormat.VERBOSE) # verbose format

    def refresh_state(self):
        power_mode: SaraR5Module.ModulePowerMode
        stk_mode: SaraR5Module.STK_Mode

        self.logger.info('***Refreshing module state***')
        
        power_mode, stk_mode = self.at_read_module_functionality()
        self.at_get_eps_network_reg_status()
        if power_mode == SaraR5Module.ModulePowerMode.ON \
            and stk_mode in [SaraR5Module.STK_Mode.STK_DEDICATED_MODE, 
                             SaraR5Module.STK_Mode.STK_RAW_MODE]:
            self.at_get_pdp_context()
        
        if self.model == "R510S":
            self.at_get_psd_to_cid_mapping(profile_id=0)
            self.at_get_psd_protocol_type(profile_id=0)
            self.at_get_psd_profile_status(profile_id=0, parameter=SaraR5Module.PSDParameters.ACTIVATION_STATUS)
            self.at_get_psd_profile_status(profile_id=0, parameter=SaraR5Module.PSDParameters.IP_ADDRESS)
        
    def is_config_synced(self):
        active_mno_profile = self.at_read_mno_profile()
        if active_mno_profile != self.module_config.mno_profile:
            self.logger.info("MNO profile is not synced")
            self.logger.debug("configured MNO profile: %s, active MNO profile: %s", self.module_config.mno_profile, active_mno_profile)
            return False
        #TODO: PSD profile (which profile is active, how is it configured?)
        # active_edrx = self.at_read_edrx()
        # if active_edrx["mode"] != self.module_config.edrx_mode:
        #     self.logger.info("eDRX mode is not synced")
        #     self.logger.debug("configured eDRX mode: %s, active eDRX mode: %s", self.module_config.edrx_mode, active_edrx["mode"])
        #     return False
        #TODO: other edrx config params
        active_power_saving_mode_urc = self.at_read_power_saving_mode_urc()
        active_signalling_cx_urc = self.at_read_signalling_cx_urc()
        active_lwm2m_activation = self.at_read_lwm2m_activation()
        active_psm_mode = self.at_read_psm_mode()

        if self.module_config.power_saving_mode:
            if not active_psm_mode["mode"]:
                self.logger.info("PSM mode is not synced")
                self.logger.debug("configured PSM mode: %s, active PSM mode: %s", self.module_config.power_saving_mode, active_psm_mode)
                return False
            if active_psm_mode["periodic_tau"] != self.module_config.tau:
                self.logger.info("PSM periodic tau is not synced")
                self.logger.debug("configured PSM periodic tau: %s, active PSM periodic tau: %s", self.module_config.tau, active_psm_mode["periodic_tau"])
                return False
            if active_psm_mode["active_time"] != self.module_config.active_time:
                self.logger.info("PSM active time is not synced")
                self.logger.debug("configured PSM active time: %s, active PSM active time: %s", self.module_config.active_time, active_psm_mode["active_time"])
                return False
            if not active_power_saving_mode_urc:
                self.logger.info("Power saving mode URC is not synced")
                self.logger.debug("configured power saving mode URC: %s, active power saving mode URC: %s", self.module_config.power_saving_mode, active_power_saving_mode_urc)
                return False
            if active_signalling_cx_urc != SaraR5Module.SignalCxReportConfig.ENABLED_MODE_ONLY:
                self.logger.info("Signalling connection URC is not synced")
                self.logger.debug("PSM is enabled, active signalling connection URC: %s", active_signalling_cx_urc)
                return False
            if active_lwm2m_activation:
                self.logger.info("LWM2M activation is not synced")
                self.logger.debug("PSM is enabled, active LWM2M activation: %s", active_lwm2m_activation)
                return False
        else:
            if active_psm_mode["mode"]:
                self.logger.info("PSM mode is not synced")
                self.logger.debug("configured PSM mode: %s, active PSM mode: %s", self.module_config.power_saving_mode, active_psm_mode)
                return False
            
        active_deep_sleep_mode_options = self.at_read_deep_sleep_mode_options()
        if not active_deep_sleep_mode_options["eDRX_mode"] or not active_deep_sleep_mode_options["wake_up_suspended"]:
            self.logger.info("Deep sleep mode options are not synced")
            self.logger.debug("active deep sleep mode options: %s",active_deep_sleep_mode_options)
            return False
        
        return True

    def setup(self):
        self.at_read_imei()
        self.at_read_model_name()
        if not self.is_config_synced():
            self.setup_nvm()
        else:
            self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.SILENT_RESET)
        
        self.wake_from_sleep()
        self.register_after_wake()

    def setup_nvm(self):
        """
        Sets up the module with the SaraR5ModuleConfig.

        """
        #TODO: support manually connecting to specific operator
        #TODO: support NB-IoT
        cid_profile_id, psd_profile_id = 1, 0 #TODO: support multiple profiles

        # in case module had protocol stack disabled, need CFUN=126 before CFUN=1
        self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.RESTORE_PROTOCOL_STACK)
        self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.MINIMUM_FUNCTIONALITY)

        self.at_set_mno_profile(self.module_config.mno_profile)
        self._await_iccid()
        self.at_set_pdp_context(cid_profile_id, SaraR5Module.PDPType.IPV4, self.module_config.apn)
        self.at_set_edrx(EDRXMode.DISABLED)
        self.at_set_power_saving_mode_urc(self.module_config.power_saving_mode)
        self.at_set_signalling_cx_urc(
            SaraR5Module.SignalCxReportConfig.ENABLED_MODE_ONLY if self.module_config.power_saving_mode
            else SaraR5Module.SignalCxReportConfig.DISABLED)
        
        if self.module_config.power_saving_mode:
            #disable lwm2m client so doesn't block psm
            self.at_set_lwm2m_activation(False)
            self.at_set_psm_mode(
            SaraR5Module.PSMMode.ENABLED,
            periodic_tau=self.module_config.tau, active_time=self.module_config.active_time)
        else:
            self.at_set_psm_mode(SaraR5Module.PSMMode.DISABLED)
        
        self.at_set_deep_sleep_mode_options(eDRX_mode=True, wake_up_suspended=True)

        #TODO: get this bug fixed by ublox
        self.send_command("AT+UHPPLMN=0", expected_reply=False) #disable manual PLMN selection as bug workaround 

        if self.model == "R510S":

            self.at_set_psd_protocol_type(psd_profile_id, SaraR5Module.PSDProtocolType.IPV4)
            self.at_set_psd_to_cid_mapping(psd_profile_id, cid_profile_id)
            self.at_get_psd_profile_status(psd_profile_id, SaraR5Module.PSDParameters.ACTIVATION_STATUS)
            if not self.module_state.psd["is_active"]:
                self.at_psd_action(psd_profile_id, SaraR5Module.PSDAction.ACTIVATE)
            self.at_psd_action(psd_profile_id, SaraR5Module.PSDAction.STORE)

        self.at_store_current_configuration()
        #TODO: implement dedicated function
        self.send_command("AT+CPWROFF", expected_reply=False)
        self.power_control.await_power_state(False, timeout=30)

    def close(self):
        """
        Closes the module.

        This method terminates the read thread, closes the serial connection,
        closes the GPIO, and sets the `terminate` flag to True.

        """
        self.logger.info('Closing module')
        self.terminate = True
        self.read_uart_thread.join()
        self.logger.debug('joined receive thread')
        self.logger.debug('closed logs')
        self._serial.close()
        self.logger.debug('closed serial')
        self.power_control.close()
        self.logger.debug('closed power control')

    def wake_from_sleep(self, re_init=True, restore_HTTP_profiles=[]):
        if re_init:
            self.serial_init()
        self.at_set_eps_network_reg_status(self.module_config.registration_status_reporting) #not stored in profile
        self.refresh_state()
        #TODO: call function self.restore_NVM(). Track non-volatile settings in module class and restore them to device from this function    
        #e.g. MQTT settings, security profiles, etc
        self.mqtt_client.at_set_mqtt_nonvolatile(MQTTClient.NonVolatileOption.RESTORE_FROM_NVM)
        for profile in restore_HTTP_profiles:
            if isinstance(profile, HTTPClient):
                profile.restore_profile()
            else:
                raise TypeError("restore_HTTP_profiles must be a list of HTTPClient objects")


        if not self.model == "R510S":
            return

        if not self.module_state.psd["is_active"]:
            self.at_psd_action(profile_id=0, action=SaraR5Module.PSDAction.LOAD)
        else:
            self.logger.warning("PSD profile is active after wake from sleep, possibly module was not asleep")
        
        
    def register_after_wake(self):
        if self.module_state.psm == SaraR5Module.PSMState.ENTERING_PSM:
            self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.RESTORE_PROTOCOL_STACK)
            #UART power save should already be disabled if we woke from PSM
        else:
            self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.FULL_FUNCTIONALITY)

        if self.model == "R510S":
            self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.FULL_FUNCTIONALITY)
        self._await_registration(timeout=60)
        #result = self.send_command("AT+COPS?", expected_reply=True)

        if not self.model == "R510S":
            return
        self.at_get_psd_profile_status(0, SaraR5Module.PSDParameters.ACTIVATION_STATUS)
        if not self.module_state.psd["is_active"]:
            self.at_psd_action(0, SaraR5Module.PSDAction.ACTIVATE)   

    def prep_for_sleep(self):
        #self.send_command('AT+UPING="www.google.com"', expected_reply=False)
        self.send_command('AT+UCPSMS?', expected_reply=True)
        self.send_command('AT+CEDRXRDP', expected_reply=True)
        self.at_set_lwm2m_activation(False)
        self.at_set_power_saving_uart_mode(SaraR5Module.PowerSavingUARTMode.ENABLED,
                                            timeout=40)


# Client profile management

    def create_http_profile(self, profile_id, security_profile:SecurityProfile):
        """
        Creates an HTTP profile with the given profile ID and security profile
        and registers it with the module.

        Args:
            profile_id (int): The ID of the HTTP profile.
            security_profile (SecurityProfile): The security profile for the HTTP profile.

        Returns:
            HTTPClient: The created HTTP profile.

        """
        self.logger.debug('security_profile: %s',security_profile)
        self.http_profiles[profile_id] = HTTPClient(
            profile_id, self, security_profile=security_profile)
        return self.http_profiles[profile_id]

    def create_security_profile(self, profile_id=0):
        """
        Creates a security profile with the given profile ID.

        Args:
            profile_id (int, optional): The ID of the security profile. Defaults to 0.

        Returns:
            SecurityProfile: The created security profile.

        """
        self.security_profiles[profile_id] = SecurityProfile(profile_id, self)
        return self.security_profiles[profile_id]

    def create_socket(self, socket_type='UDP', port: int = None):
        """
        Will return a socket-like object that mimics normal python
        sockets. The socket will then translate the commands to correct method
        calls on the module.
        It will also register the socket on the module class so that they can be
        reused in the future if they are not closed.

        :param socket_type:
        :param port:
        :return: UbloxSocket
        """
        return NotImplementedError
        # self.logger.info(f'Creating {socket_type} socket')

        # if socket_type.upper() not in self.SUPPORTED_SOCKET_TYPES:
        #     raise ValueError(f'Module does not support {socket_type} sockets')

        # sock = None
        # if socket_type.upper() == 'UDP':
        #     sock = self._create_upd_socket(port)

        # elif socket_type.upper() == 'TCP':
        #     sock = self._create_tcp_socket(port)

        # self.logger.info(f'{socket_type} socket created')

        # self.sockets[sock.socket_id] = sock

        # return sock

# High level control

    def upload_local_file_to_fs(self, filepath_in, filename_out, overwrite=False):
        """
        Uploads a local file to the filesystem of the device.

        Args:
            filepath_in (str): The path of the local file to be uploaded.
            filename_out (str): The name of the file to be created on the device's filesystem.
            overwrite (bool, optional): If True, overwrites the file if it already exists. 
                If False and the file exists, a ValueError is raised. Defaults to False.
            check_space (bool, optional): If True, checks the available space on the device's
                filesystem before uploading the file. Defaults to False.
        """
        if not os.path.exists(filepath_in):
            raise FileNotFoundError(f'File {filepath_in} not found')
        if os.path.getsize(filepath_in) == 0:
            raise ValueError(f'File {filepath_in} is empty')
        file_exists = True
        try:
            self.at_read_file_blocks(filename_out, 0, 0)
        except CMEError as e:
            file_exists = False

        if file_exists and not overwrite:
            raise FileExistsError(f'File {filename_out} already exists')
        if file_exists and overwrite:
            self.at_delete_file(filename_out)

        with open(filepath_in, 'rb') as f:
            data = f.read()
            length = len(data)
        try:
            self.at_upload_to_filesystem(filename_out, length, data)
        except CMEError as e:
            raise OSError(errno.ENOSPC, f'Not enough space on the device to upload {filepath_in}.')

    def delete_all_files(self, except_files=None):
        """
        Deletes all files on the device's filesystem.

        Args:
            except_files (list, optional): A list of files to exclude from deletion. Defaults to None.
        """
        self.logger.info(f'Deleting all files on module{ " except " + ", ".join(except_files) if except_files else ""}.')
        files = self.at_list_files()
        if files == ['']:
            self.logger.info('No files to delete on module.')
            return
        for file in files:
            if except_files and file in except_files:
                continue
            self.logger.debug('Deleting file %s', file)
            self.at_delete_file(file)
            

    def update_radio_statistics(self):
        """
        Updates the radio statistics by retrieving data from the AT command 
        'AT_get_radio_statistics'and parsing the received data using the 
        '_parse_radio_stats' method.
        """
        radio_data = self.at_get_radio_statistics()
        self._parse_radio_stats(radio_data)

    def _await_registration(self, polling_interval=2, timeout=180):
        """
        Continuously poll the carrier registration status and see if the connection status has changed.

        Args:
            roaming (bool, optional): Flag indicating whether roaming is enabled or not.
                Defaults to False.
            polling_interval (int, optional): The interval in seconds between each poll.
                Defaults to 2s.
            timeout (int, optional): Timeout value in seconds. Defaults to 180.

        Raises:
            ConnectionTimeoutError: Raised if the connection could not be 
                established within the specified timeout.

        """
        self.logger.info('Awaiting Carrier Registration')
        start_time = time.time()
        while True:

            self.at_get_eps_network_reg_status() #triggers URC

            if (not self.module_config.roaming) and self.module_state.registration_status == \
                SaraR5Module.EPSNetRegistrationStatus.REGISTERED_HOME_NET:
                break

            if self.module_config.roaming and self.module_state.registration_status == \
            SaraR5Module.EPSNetRegistrationStatus.REGISTERED_AND_ROAMING:
                break

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'Could not register in {timeout} seconds')
            
            time.sleep(polling_interval)

    def _await_iccid(self, polling_interval=2, timeout=180):
        """
        Continuously poll the ICCID and see if it has been retrieved.

        Args:
            polling_interval (int, optional): The interval in seconds between each poll.
                Defaults to 2s.
            timeout (int, optional): Timeout value in seconds. Defaults to 180.

        Raises:
            ConnectionTimeoutError: Raised if the ICCID could not be 
                retrieved within the specified timeout.

        """
        self.logger.info('Awaiting ICCID')
        start_time = time.time()
        while True:
            try:
                self.at_read_sim_iccid()
            except CMEError as e:
                self.logger.warning(e)

            iccid = self.module_state.iccid
            if iccid:
                self.logger.info('ICCID: %s', iccid)
                break

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'Could not retrieve ICCID in {timeout} seconds')
            
            time.sleep(polling_interval)


    def sync_location_with_file(self, file_path: str):
        """
        Synchronize the in-memory dictionary (self.module_state.location)
        with the JSON file at file_path, ensuring the latest timestamped data is retained.
        
        Behavior:
            - Writes to the file if it doesn't exist, or if the in-memory data is valid and newer.
            
            
        Returns:
            The latest data as a Python dictionary.
        """
        def is_valid(data):
            # Checks if data is a dict with a valid 'timestamp' (an integer)
            return isinstance(data, dict) and 'datetime' in data and isinstance(data['datetime'], float)
        
        file_data = None
        
        # Try loading the JSON file if it exists
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    file_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                # If file reading fails or JSON is invalid, treat file_data as None.
                file_data = None
        
        # Retrieve the in-memory data
        memory_data = self.module_state.location
        memory_valid = is_valid(memory_data)
        file_valid = is_valid(file_data)

        self.logger.debug(f"Memory data: {memory_data} (valid: {memory_valid})")
        self.logger.debug(f"File data: {file_data} (valid: {file_valid})")
        
        # Determine which source has the latest data (using 'timestamp').
        # Case 1: Write to file and return file data if:
        #   - The file doesn't exist or its data is invalid, and the in-memory data is valid, OR
        #   - The in-memory data is valid and has a newer timestamp.
        if memory_valid and (not file_valid or memory_data['datetime'] > file_data.get('datetime', 0)):
            try:
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, 'w') as f:
                    json.dump(memory_data, f)
            except IOError as e:
                # Handle file write error gracefully
                print(f"Error writing to file: {e}")
            self.logger.debug(f"Selected memory_data. Data written to file: {file_path}")
            return memory_data
        
        # Case 2: Return file data if
        #   - The file's data is valid and newer than in-memory data,
        #   - Or the in-memory data is missing or invalid.
        if file_valid and (not memory_valid or file_data['datetime'] > memory_data.get('datetime', 0)):
            self.logger.debug(f"Selected file_data")
            return file_data
        
        # Fallback: if neither condition applies, return whichever is valid (or an empty dict)
        result = memory_data if memory_valid else file_data if file_valid else {}
        self.logger.debug(f"Selected fallback data. result: {result}")
        return result
    
    def pop_location(self):
        """
        Pops the location data from the module state and returns it.

        Returns:
            dict: The location data.
        """
        location = self.module_state.location
        self.module_state.location = {}
        return location

# Serial control

    def at_set_echo(self, enabled: bool):
        """
        Sets the echo mode for AT commands.

        Args:
            enabled (bool): True to enable echo, False to disable echo.
        """
        self.send_command(f'ATE{int(enabled)}', expected_reply=False)
        self.logger.info('Echo %s', "enabled" if enabled else "disabled")

    def at_set_error_format(self, error_format: ErrorFormat):
        """
        Sets the error format for AT commands.

        Args:
            error_format (ErrorFormat): The error format to set.
        """
        self.send_command(f'AT+CMEE={error_format.value}', expected_reply=False)
        self.logger.info('Verbose errors %s', error_format.name)

    def at_set_data_format(self, mode: HEXMode):
        """
        Sets the data format for the module.

        Args:
            mode (HEXMode): The data format mode to set.
        """
        self.send_command(f'AT+UDCONF=1,{mode}', expected_reply=False)
        self.logger.info('%s set to %s', mode.name, mode.value)

# Identifiers, hardware info / status

    def at_read_sim_iccid(self):
        result = self.send_command('AT+CCID?')
        self.module_state.iccid = int(result[0])
        return self.module_state.iccid

    def at_read_imei(self):
        """
        Reads the International Mobile Equipment Identity (IMEI) number.

        Returns:
            int: The IMEI number.
        """
        result = self.send_command('AT+CGSN=1')
        imei = int(result[0].strip('"'))
        self.module_state.imei = imei
        return imei

    def at_read_model_name(self):
        """
        Reads the model name of the module.

        Returns:
            str: The model name.
        """
        result = self.send_command('ATI7',expected_reply="SARA-")
        model_name = result[0]
        self.module_state.model_name = model_name
        return model_name


# Networking / radio config

    def at_set_mno_profile(self, profile_id:MobileNetworkOperator):
        """
        Sets the Mobile Network Operator (MNO) profile.

        Args:
            profile_id (MobileNetworkOperator): The profile ID of the MNO.
        """
        self.send_command(f'AT+UMNOPROF={profile_id.value}', expected_reply=False)
        self.logger.info('Mobile Network Operator Profile set to %s', profile_id.name)

    def at_read_mno_profile(self):
        """
        Reads the Mobile Network Operator (MNO) profile.

        Returns:
            MobileNetworkOperator: The MNO profile.
        """
        result = self.send_command('AT+UMNOPROF?', expected_reply=True)
        profile_id = MobileNetworkOperator(int(result[0]))
        self.logger.info('Mobile Network Operator Profile: %s', profile_id.name)
        return profile_id

    def at_set_band_mask(self, bands: list = None):
        """
        Band is set using a bit for each band. Band 1=bit 0, Band 64=Bit 63

        .. note:
            Only supports NB IoT RAT.
        """
        raise NotImplementedError
        # DEFAULT_BANDS = [20]
        # self.logger.info(f'Setting Band Mask for bands {bands}')
        # bands_to_set = bands or DEFAULT_BANDS
        # total_band_mask = 0

        # for band in bands_to_set:
        #     individual_band_mask = 1 << (band - 1)
        #     total_band_mask = total_band_mask | individual_band_mask

        # self._at_action(f'AT+UBANDMASK=1,{total_band_mask},{total_band_mask}')

    def at_get_eps_network_reg_status(self):
        """
        Get the EPS network registration status.

        This method sends the 'AT+CEREG?' command to the module to retrieve the EPS network 
        registration status. The response is handled by the URC (Unsolicited Result Code) mechanism.

        Returns:
            None
        """
        self.send_command('AT+CEREG?', expected_reply=False)
        # NOTE: URC handles reply

        # self.logger.info(f'{config.name} set to {config.value}')

    def at_set_eps_network_reg_status(self, config:EPSNetRegistrationReportConfig):
        """
        Sets the EPS network registration status.

        Args:
            config (EPSNetworkRegistrationReportConfig): The configuration value to set.
        """
        self.send_command(f'AT+CEREG={config.value}', expected_reply=False)

        self.logger.info('EPS Network Registration Reporting set to %s', config.name)

    def at_set_module_functionality(self, function: ModuleFunctionality, reset: bool = None):
        """
        Sets the module functionality to the specified value.

        Args:
            function (ModuleFunctionality): The desired module functionality.
            reset (bool, optional): Whether to reset the module. 
                Only applicable when function is FULL_FUNCTIONALITY
                or AIRPLANE_MODE. Defaults to None.
        """
        if reset and function not in [SaraR5Module.ModuleFunctionality.FULL_FUNCTIONALITY,
                                      SaraR5Module.ModuleFunctionality.AIRPLANE_MODE]:
            raise ValueError('Reset can only be used with FULL_FUNCTIONALITY or AIRPLANE_MODE')

        at_command = f'AT+CFUN={function.value}'
        logger_str = f'Module Functionality set to {function.name}'
        if reset is not None:
            at_command += f',{int(reset)}'
            logger_str += f' with reset {reset}'
        self.send_command(at_command, expected_reply=False, timeout=180)
        self.logger.info(logger_str)

    def at_read_module_functionality(self):
        """
        Reads the functionality of the module.

        """
        result = self.send_command('AT+CFUN?')
        power_mode = SaraR5Module.ModulePowerMode(int(result[0]))
        stk_mode = SaraR5Module.STK_Mode(int(result[1])) #simcard toolkit mode
        self.logger.info('Module Functionality: %s, STK Mode: %s', power_mode.name, stk_mode.name)
        return power_mode, stk_mode

    def at_set_radio_mode(self, mode:RadioAccessTechnology):
        """
        Sets the radio access technology (e.g. LTE, NB-IoT) for the module.

        Args:
            mode (RadioAccessTechnology): The desired radio access technology.
        """
        response = self.send_command(f'AT+URAT={mode.value},',expected_reply=False,timeout=10)

        self.current_rat = mode.name
        self.logger.info('Radio Access Technology set to %s', mode.name)
        return response

    def at_get_pdp_context(self):
        """
        Get the PDP context.
        """

        result = self.send_command('AT+CGDCONT?', expected_reply=True)
        pdp_contexts = []
        for i in range(0, len(result), 15):
            cid = int(result[i])
            pdp_type = SaraR5Module.PDPType(result[i+1].strip('"'))
            apn = result[i+2]
            pdp_address = result[i+3]
            data_compression = int(result[i+4])
            header_compression = int(result[i+5])
            ipv4_addr_alloc = bool(result[i+6])
            request_type = int(result[i+7])
            pcscf_discovery = int(result[i+8])
            im_cn_signalling_flag = bool(result[i+9])
            nslpi = int(result[i+10])
            secure_pco = bool(result[i+11])
            ipv4_mtu_discovery = bool(result[i+12])
            local_addr_indication = bool(result[i+13])
            non_ip_mtu_discovery = bool(result[i+14])
            pdp_contexts.append({
            "cid": cid,
            "pdp_type": pdp_type,
            "apn": apn,
            "pdp_address": pdp_address,
            "data_compression": data_compression,
            "header_compression": header_compression,
            "ipv4_addr_alloc": ipv4_addr_alloc,
            "request_type": request_type,
            "pcscf_discovery": pcscf_discovery,
            "im_cn_signalling_flag": im_cn_signalling_flag,
            "nslpi": nslpi,
            "secure_pco": secure_pco,
            "ipv4_mtu_discovery": ipv4_mtu_discovery,
            "local_addr_indication": local_addr_indication,
            "non_ip_mtu_discovery": non_ip_mtu_discovery
            })
        self.logger.info('PDP Contexts: %s', pdp_contexts)
        return pdp_contexts




    def at_set_pdp_context(self, cid:int=1, pdp_type:PDPType=PDPType.IPV4,
                               apn:str="", pdp_address:str="0.0.0.0", data_compression:bool=False,
                                header_compression:bool=False):
        """
        Sets the PDP context for the module.

        Args:
            cid (int): Context ID. Must be between 0 and 11.
            pdp_type (PDPType): PDP type. Options are PDPType.IPV4, PDPType.IPV4V6, PDPType.IPV6.
            apn (str): Access Point Name.
            pdp_address (str): PDP address.
            data_compression (bool): Enable or disable data compression.
            header_compression (bool): Enable or disable header compression.
        """
        # NOTE: AT+CFUN=0 needed for R5 to set PDP context

        if cid not in range (0, 12):
            raise ValueError('CID must be between 0 and 11')
        if len(apn) > 99:
            raise ValueError('APN must be less than 100 characters')
        if pdp_type==SaraR5Module.PDPType.IPV4 and not validators.ipv4(pdp_address):
            raise ValueError("Invalid IPV4 address")
        if pdp_type==SaraR5Module.PDPType.IPV4V6 and not any [validators.ipv4(pdp_address),
                                                                validators.ipv6(pdp_address)]:
            raise ValueError("Invalid IPV4 or IPV6 address")
        if pdp_type==SaraR5Module.PDPType.IPV6 and not validators.ipv6(pdp_address):
            raise ValueError("Invalid IPV6 address")

        self.send_command(f'AT+CGDCONT={cid},"{pdp_type.value}","{apn}","{pdp_address}",'
                            f'{int(data_compression)},{int(header_compression)}',
                            expected_reply=False)
        self.logger.info('PDP Context set to %s with APN %s and PDP Address %s',
                    pdp_type.name,apn,pdp_address)

    def at_get_radio_statistics(self):
        """
        Retrieves radio statistics from the module.

        Raises:
            NotImplementedError: This method is not implemented yet.
        """
        raise NotImplementedError

        # result = self._send_command('AT+UCGED?', capture_urc=True)
        # if result[0] != b'+UCGED: 2':
        #     raise ValueError("Unexpected response received: {}".format(result[0]))

        # self.logger.info('Updating radio statistics')

        # return result[1:]
    def at_get_psd_protocol_type(self, profile_id:int=0):
        """
        Get the PSD protocol type for a given profile ID.

        Args:
            profile_id (int): The profile ID. Must be between 0 and 6.

        Returns:
            PSDProtocolType: The PSD protocol type.
        """
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        response_list = self.send_command(f'AT+UPSD={profile_id},0', expected_reply=True)
        protocol_type = SaraR5Module.PSDProtocolType(int(response_list[2]))
        self.logger.info('PSD Protocol Type for profile %s is %s',profile_id,protocol_type.name)
        return protocol_type

    def at_set_psd_protocol_type(self, profile_id:int=0,
                                     protocol_type:PSDProtocolType=PSDProtocolType.IPV4):
        """
        Sets the PSD protocol type for a given profile ID.

        Args:
            profile_id (int): The profile ID. Must be between 0 and 6.
            protocol_type (PSDProtocolType): The PSD protocol type to set.
        """
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        self.send_command(f'AT+UPSD={profile_id},0,{protocol_type.value}',expected_reply=False)
        self.logger.info('PSD Protocol Type set to %s',protocol_type.name)

    def at_get_psd_to_cid_mapping(self, profile_id:int=0):
        """
        Get the PSD (Packet Switched Data) profile to CID (Context Identifier) mapping.

        Args:
            profile_id (int): The profile ID to map (0-6).

        Returns:
            int: The CID mapped to the profile ID.
        """
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        response_list = self.send_command(f'AT+UPSD={profile_id},100', expected_reply=True)
        cid = int(response_list[2])
        self.logger.info('PSD Profile %s mapped to CID %s',profile_id,cid)
        return cid

    def at_set_psd_to_cid_mapping(self, profile_id:int=0, cid:int=1):
        """
        Sets the PSD (Packet Switched Data) profile to CID (Context Identifier) mapping.

        Args:
            profile_id (int): The profile ID to map (0-6).
            cid (int): The CID to map (0-8).
        """
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        if cid not in range (0, 9):
            raise ValueError('CID must be between 0 and 8')
        self.send_command(f'AT+UPSD={profile_id},100,{cid}',expected_reply=False)
        self.logger.info('PSD Profile %s mapped to CID %s',profile_id,cid)

    def at_psd_action(self, profile_id:int=0, action:PSDAction=PSDAction.RESET):
        """
        Perform an action on a PSD profile.

        Args:
            profile_id (int): The ID of the PSD profile. Must be between 0 and 6.
            action (PSDAction): The action to perform on the PSD profile.
        """
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        self.send_command(f'AT+UPSDA={profile_id},{action.value}',expected_reply=False,timeout=180)
        self.logger.info('PSD Profile %s took action %s',profile_id,action.name)

    def at_get_psd_profile_status(self, profile_id:int=0,
                                       parameter:PSDParameters=PSDParameters.IP_ADDRESS):
        """
        Get the status of a PSD profile.

        Args:
            profile_id (int): The ID of the PSD profile. Must be between 0 and 6.
            parameter (PSDParameters): The parameter to retrieve. Default is IP_ADDRESS.

        Returns:
            The value of the specified parameter for the PSD profile.
        """
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        response_list = self.send_command(f'AT+UPSND={profile_id},{parameter.value}')

        if parameter == SaraR5Module.PSDParameters.IP_ADDRESS:
            ip = response_list[2]
            self.module_state.psd = {**self.module_state.psd, "ip": ip}
            self.logger.info('PSD Profile %s IP Address is %s',profile_id,ip)
            return ip

        if parameter == SaraR5Module.PSDParameters.ACTIVATION_STATUS:
            is_active = bool(int(response_list[2]))
            self.module_state.psd = {**self.module_state.psd, "is_active": is_active}
            self.logger.info('PSD Profile %s Activation Status is %s', profile_id, is_active)
            return is_active

        return None

        #TODO: support other parameters, e.g. QoS.

# Low power

    def at_set_power_saving_uart_mode(self, mode:PowerSavingUARTMode=PowerSavingUARTMode.DISABLED,
                                      timeout:int=None, idle_optimization:bool=None):
        """
        Sets the power saving UART mode for the module.

        Args:
            mode (PowerSavingUARTMode): The power saving UART mode to set.
            timeout (int, optional): The timeout value in GSM frames. One GSM frame
                is 4.615 ms. 
                Only applicable when mode is ENABLED or ENABLED_2. 
                Must be between 40 and 65000. Defaults to None.
            idle_optimization (bool, optional): Whether to enable idle optimization. 
                Only applicable when mode is not DISABLED. Defaults to None.

        """
        if idle_optimization is not None and mode == SaraR5Module.PowerSavingUARTMode.DISABLED:
            raise ValueError('Idle optimization can only be used with \
                                PowerSavingUARTMode other than DISABLED')
        if timeout is not None and mode != SaraR5Module.PowerSavingUARTMode.ENABLED \
            and mode != SaraR5Module.PowerSavingUARTMode.ENABLED_2:
            raise ValueError('Timeout can only be used with \
                                PowerSavingUARTMode ENABLED or ENABLED_2')

        if timeout is not None and timeout not in range(40, 65001):
            raise ValueError('Timeout must be between 40 and 65000')

        logger_str = f'UART power saving mode set to {mode.name}'
        at_command = f'AT+UPSV={mode.value}'
        if timeout is not None:
            logger_str += f' with timeout {timeout*4.615} ms'
            at_command += f',{timeout}'
        if idle_optimization is not None:
            logger_str += f' and idle optimization {idle_optimization}'
            at_command += f',{int(idle_optimization)}'
        self.send_command(at_command, expected_reply=False)
        self.logger.info(logger_str)

    def at_set_edrx(self, mode:EDRXMode, access_technology:EDRXAccessTechnology=None,
                         requested_edrx_cycle:EDRXCycle=None, requested_ptw:EDRXCycle=None):
        """
        Sets the eDRX (extended Discontinuous Reception) parameters.

        Args:
            mode (EDRXMode): The eDRX mode.
            access_technology (EDRXAccessTechnology, optional): The eDRX access technology.
                (e.g. LTE, NB-IoT, etc.)
            requested_eDRX_cycle (EDRXCycle, optional): The requested eDRX cycle.
            requested_PTW (EDRXCycle, optional): The requested Paging Time Window (PTW).
        """
        if mode != EDRXMode.DISABLED and \
        not all([access_technology, requested_edrx_cycle, requested_ptw]):
            raise ValueError('Access technology, eDRX cycle and PTW must be specified '
                             'when eDRX is enabled')

        logger_string = f'eDRX configured with mode {mode.name}'
        command = f'AT+CEDRXS={mode.value}'
        if access_technology is not None:
            logger_string += f', access technology {access_technology.name}'
            command += f',{access_technology.value}'
        if requested_edrx_cycle is not None:
            logger_string += f', requested eDRX cycle {requested_edrx_cycle.name}'
            command += f',{requested_edrx_cycle.value}'
        if requested_ptw is not None:
            logger_string += f' and requested PTW {requested_ptw.name}'
            command += f',{requested_ptw.value}'
        self.send_command(command, expected_reply=False)
        self.logger.info(logger_string)

    def at_read_edrx(self):
        #TODO: fix, does not return mode
        """
        Reads the eDRX (extended Discontinuous Reception) parameters.

        Returns:
            dict: A dictionary containing the eDRX parameters.
        """
        result = self.send_command('AT+CEDRXS?', expected_reply=True)
        access_technology = EDRXAccessTechnology(int(result[0]))
        requested_edrx_cycle = EDRXCycle(int(result[1]))
        requested_ptw = EDRXCycle(int(result[2]))
        self.logger.info('Access Technology: %s, Requested eDRX Cycle: %s, Requested PTW: %s',
                        access_technology.name, requested_edrx_cycle.name, requested_ptw.name)
        return {"access_technology": access_technology, "requested_edrx_cycle": requested_edrx_cycle,
                "requested_ptw": requested_ptw}

    def at_set_psm_mode(self, mode:PSMMode, periodic_tau:PSMPeriodicTau=None,
                         active_time:PSMActiveTime=None):
        """
        Sets the Power Saving Mode (PSM) for the module.

        Args:
            mode (PSMMode): The PSM mode to set.
            periodic_tau (PSMPeriodicTau, optional): The periodic tau value for PSM.
            active_time (PSMActiveTime, optional): The active time value for PSM.
        """

        if mode != SaraR5Module.PSMMode.DISABLED and not all([periodic_tau, active_time]):
            raise ValueError('Periodic Tau and Active Time must be provided for'
                             'PSM mode other than DISABLED')
        
        if mode == SaraR5Module.PSMMode.DISABLED and any([periodic_tau, active_time]):
            raise ValueError('Periodic Tau and Active Time must not be provided when PSM mode is DISABLED')

        command = f'AT+CPSMS={mode.value}'
        logger_str = f'PSM Mode set to {mode.name}'
        if periodic_tau is not None:
            command += f',,,"{periodic_tau.value}"'
            logger_str += f' with Periodic Tau "{periodic_tau.name}"'
        if active_time is not None:
            command += f',"{active_time.value}"'
            logger_str += f' and Active Time {active_time.name}'
        self.send_command(command, expected_reply=False, timeout=10)
        self.logger.info(logger_str)

    def at_read_psm_mode(self):
        """
        Reads the Power Saving Mode (PSM) for the module.

        Returns:
            dict: A dictionary containing the PSM parameters.
        """
        result = self.send_command('AT+CPSMS?', expected_reply=True)
        mode = SaraR5Module.PSMMode(int(result[0]))
        #periodic_rau is result[1]
        #gprs read timer is result[2]
        periodic_tau = PSMPeriodicTau(result[3].strip('"'))
        active_time = PSMActiveTime(result[4].strip('"'))
        self.logger.info('PSM Mode: %s, Periodic Tau: %s, Active Time: %s',
                        mode.name, periodic_tau.name, active_time.name)
        return {"mode": mode, "periodic_tau": periodic_tau, "active_time": active_time}
    
    def at_set_deep_sleep_mode_options(self, eDRX_mode:bool, wake_up_suspended:bool):
        """
        Sets the deep sleep mode options for the module.

        Args:
            eDRX_mode (bool): Enable or disable eDRX mode.
            wake_up_suspended (bool): Enable or disable wake up suspended mode.
        """
        if not isinstance(eDRX_mode, bool) or not isinstance(wake_up_suspended, bool):
            raise ValueError('eDRX_mode and wake_up_suspended must be boolean values')

        combined_bits = (int(eDRX_mode) << 3) | (int(wake_up_suspended) << 4)
        if combined_bits < 0 or combined_bits > 24:
            raise ValueError('Combined bits value must be between 0 and 24')

        self.send_command(f'AT+UPSMVER={combined_bits}', expected_reply=False)
        self.logger.info('Deep sleep mode options set with eDRX_mode=%s and wake_up_suspended=%s',
                 eDRX_mode, wake_up_suspended)
    
    def at_read_deep_sleep_mode_options(self):
        """
        Reads the deep sleep mode options for the module.

        Returns:
            dict: A dictionary containing the deep sleep mode options.
        """
        result = self.send_command('AT+UPSMVER?', expected_reply=True)
        combined_bits = int(result[0])
        eDRX_mode = bool(combined_bits & 0b00001000)
        wake_up_suspended = bool(combined_bits & 0b00010000)
        self.logger.info('Deep sleep mode options: eDRX_mode=%s, wake_up_suspended=%s',
                        eDRX_mode, wake_up_suspended)
        return {"eDRX_mode": eDRX_mode, "wake_up_suspended": wake_up_suspended}    

    def at_set_lwm2m_activation(self, enabled: bool):
        self.send_command(f'AT+ULWM2M={int(not enabled)}',expected_reply=False) 
        self.logger.info('LWM2M activation set to %s', 'enabled' if enabled else 'disabled')

    def at_read_lwm2m_activation(self):
        result = self.send_command('AT+ULWM2M?', expected_reply=True)
        enabled = not bool(int(result[0]))
        self.logger.info('LWM2M activation is %s', 'enabled' if enabled else 'disabled')
        return enabled

# URC configuration

    def at_set_power_saving_mode_urc(self, enabled:bool):
        """
        Enables or disables the +UUPSMR URC that conveys information on the 
        Power Saving Mode (PSM) states, e.g. if the module can enter PSM, 
        or has exited from it, or if some embedded SW client or peripheral activity is
        postponing the entrance into PSM state

        Args:
            enabled (bool): Enables or disables URC indication
        """
        self.send_command(f'AT+UPSMR={int(enabled)}',expected_reply=False)
        self.logger.info('Power Saving Mode URC set to %s', 'enabled' if enabled else 'disabled')

    def at_read_power_saving_mode_urc(self):
        """
        Reads the power saving mode URC configuration.

        Returns:
            bool: True if enabled, False if disabled.
        """
        result = self.send_command('AT+UPSMR?', expected_reply=True)
        enabled = bool(int(result[0]))
        self.logger.info('Power Saving Mode URC is %s', 'enabled' if enabled else 'disabled')
        return enabled

    def at_set_signalling_cx_status_urc(self, enabled: bool):
        """
        Sets the signalling connection status URC.

        Args:
            enabled (bool): Enables or disables signalling connection status URC indication.
        """
        raise NotImplementedError

    def at_set_signalling_cx_urc(self, config: SignalCxReportConfig):
        """
        Sets the signalling connection URC (Unsolicited Result Code) configuration.

        Args:
            config (SignallingCxStatusReportConfig): The configuration value to set.
        """
        self.send_command(f'AT+CSCON={config.value}', expected_reply=False)
        self.logger.info('Signalling connection URC set to %s', config.name)

    def at_read_signalling_cx_urc(self):
        """
        Reads the signalling connection URC configuration.

        Returns:
            SignalCxReportConfig: The signalling connection URC configuration.
        """
        result = self.send_command('AT+CSCON?', expected_reply=True)
        config = SaraR5Module.SignalCxReportConfig(int(result[0]))
        #mode = 
        self.logger.info('Signalling connection URC is %s', config.name)
        return config
    
# Filesystem

    
    def at_list_files(self):
        """
        Lists all files in the module's filesystem.

        Returns:
            A list of filenames.
        """
        result = self.send_command('AT+ULSTFILE=0', expected_reply=True)
        result = [item.strip('"') for item in result]
        return result

    def at_get_filesystem_free_space(self):
        """
        Gets the available space in the module's filesystem.

        Returns:
            An int representing available space in bytes.
        """
        result = self.send_command('AT+ULSTFILE=1', expected_reply=True)
        return int(result[0])
    
    def at_get_file_size(self,filename):
        """
        Gets the size of a file in the module's filesystem.

        Returns:
            An int representing the size of the file in bytes.
        """
        SaraR5Module.validate_filename(filename)

        result = self.send_command(f'AT+ULSTFILE=2,{filename}', expected_reply=True)
        return result

    def at_upload_to_filesystem(self, filename, length, data):
        """
        Uploads a data to the filesystem of the SaraR5 module.

        Args:
            filename (str): The name of the desired destination filename in the module's
                internal filesystem.
            length (int): The length of the data in bytes.
            data (bytes): The data to be uploaded.
        """
        SaraR5Module.validate_filename(filename)
        if min(length, len(data)) <= 0:
            raise ValueError('Length must be greater than 0')
        upload_command_module_response = 10 #seconds to receive the ">" prompt and OK after data sent.
        upload_time_margin = 0.5 # an extra 50% in case of transmission errors
        upload_time = (len(data)*8 / self.serial_config.baudrate) * (1+upload_time_margin) + upload_command_module_response
        self.send_command(f'AT+UDWNFILE="{filename}",{length}',
                           expected_reply=False, input_data=data,timeout=upload_time)
        self.logger.info('Uploaded %s bytes to %s', length, filename)

    def at_read_file(self, filename, file_out=False, timeout=10):
        """
        Reads a file from the module.

        Args:
            filename (str): The name of the file to read.
            timeout (int, optional): The timeout value in seconds. Defaults to 10.
            file_out (str): The name of the file to write the data to if it shouldn't be returned in memory

        Returns:
            str: The byte contents of the file.
        """

        SaraR5Module.validate_filename(filename)
        self.large_binary_xfer = True
        result = self.send_command(f'AT+URDFILE="{filename}"', expected_multiline_reply=True, file_out=file_out, timeout=timeout)
        self.large_binary_xfer = False

        data_to_process = file_out if file_out else result
        size, data = SaraR5Module._process_URDFILE_data(data_to_process)

        if file_out is None:
            return data
        return file_out


    def at_read_file_blocks(self, filename, offset:int, length:int):
        """
        Reads a specified number of blocks from a file starting at a given offset.

        Args:
            filename (str): The name of the file to read from.
            offset (int): The offset in bytes from where to start reading.
            length (int): The number of blocks to read.

        Returns:
            str: The byte contents of the file.
        """
        SaraR5Module.validate_filename(filename)
        if not isinstance(offset, int) or not isinstance(length, int):
            raise ValueError('Offset and length must be integers')

        return self.send_command(f'AT+URDBLOCK="{filename}",{offset},{length}')

    def at_delete_file(self, filename):
        """
        Deletes a file on the module.

        Args:
            filename (str): The name of the file to be deleted.
        """
        SaraR5Module.validate_filename(filename)

        self.send_command(f'AT+UDELFILE="{filename}"', expected_reply=False)
        self.logger.info('Deleted file %s', filename)

# Localization SVCS

    def at_configure_thingstream_ZTP_spotnow(self, token, device_name, tags: list = None):
        """
        Configures the module for Zero Touch Provisioning (ZTP) with Thingstream SpotNow.

        Args:
            token (str): The Thingstream token.
            device_name (str): The name of the device.
            tags (list, optional): A list of tags to associate with the device.
        """
        if not isinstance(token, str) or not isinstance(device_name, str):
            raise ValueError('Token and device name must be strings')
        if tags and not isinstance(tags, list):
            raise ValueError('Tags must be a list')
        if len(tags) > 5:
            raise ValueError('Maximum of 5 tags allowed')
        # the type of all elements of tags must be string
        if tags and not all(isinstance(tag, str) for tag in tags):
            raise ValueError('All tags must be strings')
        
        tags_str = ','.join(f'"{tag}"' for tag in tags) if tags else ''
        self.send_command(f'AT+UGLAASZTP=2,"{token}","{device_name}",{tags_str}', expected_reply=False, timeout=10)
        self.logger.info('Thingstream ZTP SpotNow configured')

    def at_get_spotnow_localization_data(self, timeout=10, accuracy=30):
        """
        Gets the localization data from Thingstream SpotNow.

        Args:
            timeout (int, optional): The timeout value in seconds. Defaults to 10.
            accuracy (int, optional): The accuracy value in meters. Defaults to 30.
        """
        mode = 2 #others reserved
        sensor = 16 #spotnow
        response_type = 1 #detailed URC response

        if not 1 <= timeout <= 999:
            raise ValueError('Timeout must be between 1 and 999 seconds')
        if not 1 <= accuracy <= 999999:
            raise ValueError('Accuracy must be between 1 and 999999 meters')
        
        result = self.send_command(f'AT+ULOC={mode},{sensor},{response_type},{timeout},{accuracy}', expected_reply=False)
        self.logger.info('SpotNow localization data requested')
            
    def _parse_radio_stats(self, radio_data):
        """
        Parses the radio statistics data and translates the values into meaningful information.

        Args:
            radio_data (tuple): A tuple containing two elements - metadata and stats data.

        Returns:
            tuple: A tuple containing the parsed metadata and stats.

        """
        def translate_rsrq(rsrq):
            rsrq = int(rsrq)
            result = None
            if rsrq == 255:
                result = None
            elif rsrq == 46:
                result = 2.5
            elif 35 <= rsrq <= 45:
                result = -3 + (rsrq - 35) * 0.05
            elif 1 <= rsrq <= 33:
                result = -19.5 + (rsrq - 1) * 0.5
            elif -29 <= rsrq <= -1:
                result = -34 + (rsrq + 29) * 0.5
            elif rsrq == -30:
                result = -34
            else:
                result = None  # for any other value
            return result

        metadata_values = radio_data[0].decode().split(',')
        stats_values = radio_data[1].decode().split(',')

        metadata_keys = ['rat', 'svc', 'MCC', 'MNC']
        stats_keys = [
            'EARFCN', 'Lband', 'ul_BW', 'dl_BW', 'TAC', 'LcellId', 'P-CID', 'mTmsi',
            'mmeGrId', 'mmeCode', 'RSRP', 'RSRQ', 'Lsinr', 'LTE_rrc', 'RI', 'CQI',
            'avg_rsrp', 'totalPuschPwr', 'avgPucchPwr', 'drx', 'l2w', 'volte_mode',
            'meas_gap', 'rai_support'
        ]
        parsed_metadata = {key: value for key, value in zip(metadata_keys, metadata_values)}
        parsed_stats = {key: value for key, value in zip(stats_keys, stats_values)}

        key_mappings = {
            'rat': 'radio_access_technology',
            'svc': 'radio_service_state',
            'MCC': 'mobile_country_code',
            'MNC': 'mobile_network_code',
            'EARFCN': 'E-UTRAN_absolute_radio_frequency_channel',
            'Lband': 'band',
            'ul_BW': 'uplink_bandwidth',
            'dl_BW': 'downlink_bandwidth',
            'TAC': 'tracking_area_code',
            'LcellId': 'cell_id',
            'P-CID': 'physical_cell_id',
            'mTmsi': 'temp_mobile_subscriber_identity',
            'mmeGrId': 'mme_group_id',
            'mmeCode': 'mme_code',
            'RSRP': 'RSRP',
            'RSRQ': 'RSRQ',
            'Lsinr': 'SINR',
            'LTE_rrc': 'LTE_radio_resource_control_state',
            'RI': 'rank_indicator',
            'CQI': 'channel_quality_indicator',
            'avg_rsrp': 'avg_rsrp',
            'totalPuschPwr': 'total_pusch_power',
            'avgPucchPwr': 'avg_pucch_power',
            'drx': 'drx_inactivity_timer',
            'l2w': 'SIB3_LTE_to_WCDMA_reselection_criteria',
            'volte_mode': 'volte_mode',
            'meas_gap': 'measurement_gap_config',
            'rai_support': 'release_assistance_indication_support'
        }

        translated_meta = {key_mappings.get(key, key):
                           value for key, value in parsed_metadata.items()}
        translated_stats = {key_mappings.get(key, key):
                            value for key, value in parsed_stats.items()}

        translated_meta['radio_access_technology'] = SaraR5Module.CurrentRadioAccessTechnology(
            int(translated_meta['radio_access_technology'])).name
        translated_meta['radio_service_state'] = SaraR5Module.CurrentRadioServiceState(
            int(translated_meta['radio_service_state'])).name
        translated_stats['LTE_radio_resource_control_state'] = \
        SaraR5Module.LTERadioResourceControlState(
            int(translated_stats['LTE_radio_resource_control_state'])).name

        if int(translated_stats['RSRP']) == 255:
            translated_stats['RSRP'] = None
        else:
            translated_stats['RSRP'] = int(translated_stats['RSRP']) - 141
        if int(translated_stats['avg_rsrp']) == 255:
            translated_stats['avg_rsrp'] = None
        else:
            translated_stats['avg_rsrp'] = int(translated_stats['avg_rsrp']) - 141
        translated_stats['RSRQ'] = translate_rsrq(translated_stats['RSRQ'])
        self.module_state.radio_status = translated_meta
        self.module_state.radio_stats = translated_stats
        return self.module_state.radio_status, self.module_state.radio_stats

    @staticmethod
    def validate_filename(filename):
        """
        Validates the given filename to ensure it meets SARA-R5 filesystem criteria.

        Args:
            filename (str): The filename to be validated.

        Raises:
            ValueError: If the filename is too long, too short, or contains invalid characters.
        """
        invalid_chars = ['/', '*', ':', '%', '|', '"', '<', '>', '?']
        length_minimum = 1
        length_maximum = 248
        if len(filename) > length_maximum:
            raise ValueError(f'Filename must be less than {length_maximum} characters')
        if len(filename) == length_minimum:
            raise ValueError(f'Filename must be at least {length_minimum} characters long')
        if filename.startswith('.'):
            raise ValueError('Filename cannot start with a period')

        for char in invalid_chars:
            if char in filename:
                raise ValueError(f'Invalid character {char} in filename')

#AT Command Handling

    def send_command(self, command:str, input_data:bytes=None, expected_reply=True, expected_multiline_reply=False, file_out=None, timeout=10):
        """
        Sends a command to the module and waits for a response.

        Args:
            command (str): The command to send to the module.
            input_data (bytes, optional): Additional data to send after receiving a ">" prompt.
                Defaults to None.
            expected_reply (bool or str, optional): The expected reply from the module.
                - If True, expects a reply with a prefix matching the command.
                - If False, no reply is expected.
                - If a string, expects a reply with the specified prefix.
                Defaults to True.
            expected_multiline_reply (bool, optional): Specifies whether a multiline reply is expected.
                Only applicable if expected_reply is True or a string. Defaults to False.

            timeout (int, optional): The maximum time to wait for a response, in seconds.
                Defaults to 10.

        Returns:
            list or None: The response from the module, split into a list if 
                it's a single-line reply. Returns None if no response is expected.

        Raises:
            TypeError: If expected_reply is not of type bool or str.
            ValueError: If multiline_reply is True and expected_reply is False.
            ATTimeoutError: If a response is not received within the specified timeout.
            ATError: If the module returns an "ERROR" response.
            CMEError: If the module returns a "+CME ERROR" response.

        """
        return self.at_cmd_handler.send_cmd(command, input_data, expected_reply, expected_multiline_reply, file_out, timeout)

    def _read_serial_and_log(self):
        data = self._serial.readline()
        timestamp=datetime.datetime.now()
        timestamp_str = timestamp.strftime("%Y-%m-%d_%H-%M-%S-%f")
        if len(data) > 0 and not self.large_binary_xfer:
            self.tx_rx_logger.debug(f'RX: {data},                           T={timestamp_str}')
            #self.receive_log.write(f'{timestamp_str};{data}\n')
        return data, timestamp

    def _write_serial_and_log(self,data,timeout=5):
        timestamp = self._write_serial(data,timeout=timeout)
        timestamp_str = timestamp.strftime("%Y-%m-%d_%H-%M-%S-%f")
        if len(data) < 1024:
            self.tx_rx_logger.debug(f'TX: {data},                           T={timestamp_str}')
        else:
            #data too big to log
            self.tx_rx_logger.debug('TX: %s',data[:1024])
        return timestamp


    def _write_serial(self, data, chunk_size=512, timeout=5):

        """Writes data to serial with timeout, respecting hardware flow control (CTS) and buffer limits."""

        start_time = time.time()
        end_time = datetime.datetime.now() #will be incremented
        total_bytes_written = 0

        while total_bytes_written < len(data):
            time_remaining = timeout - (time.time() - start_time)

            if time_remaining <= 0:
                raise TimeoutError(f"Write timed out after {timeout} seconds")

            # Check CTS (Clear to Send) before attempting to write
            if self._serial.cts:
                try:
                    # If output buffer is full, wait for space
                    while self._serial.out_waiting >= chunk_size:
                        time.sleep(0.01)  # Small wait before checking again

                    # Use select.select to check if the port is ready for writing
                    _, wlist, _ = select.select([], [self._serial], [], time_remaining)

                    if wlist:  # Device is ready for writing
                        bytes_to_write = min(len(data) - total_bytes_written, chunk_size)
                        bytes_written = self._serial.write(data[total_bytes_written:total_bytes_written + bytes_to_write])
                        end_time = datetime.datetime.now()
                        total_bytes_written += bytes_written
                    else:
                        time.sleep(0.001)  # Small delay to avoid busy-waiting

                except serial.SerialTimeoutException:
                    raise TimeoutError("Write timed out (SerialTimeoutException)")
                except OSError as e:
                    if "Input/output error" in str(e):
                        raise OSError("Serial port disconnected") from e
                    else:
                        raise  # Re-raise other OSError exceptions
            else:
                time.sleep(0.01)  # Wait briefly before checking CTS again
            #self.send_log.write(f'{timestamp_str};{data}\n')
        return end_time


    def _read_from_uart(self):
        """
        Reads data from the device and processes it.

        This method continuously reads data from the serial port until the `terminate` 
        flag is set to True. It checks the received data for a URC (Unsolicited Result Code) 
        preceeded by a linefeed and calls the corresponding handler function. If the data 
        doesn't match the <linefeed, URC> pattern it adds the data to a queue along with its 
        timestamp for the main thread to process.

        Note:
            - URCs and their handlers are identified based on their prefixes defined 
                in `urc_mappings`.

        Returns:
            None
        """

        
        linefeed = b'\r\n'
        linefeed_buffered = False
        linefeed_timestamp = None

        while not self.terminate:
            if self._serial_flush_event.is_set():
                self._serial.reset_input_buffer()
                self._serial_flush_event.clear()
                linefeed_buffered = False
                linefeed_timestamp = None


            data, timestamp = self._read_serial_and_log()
            if len(data) < 1:
                continue
            
            #linefeed
            if data == linefeed:
                if linefeed_buffered:
                    raise ValueError('Two linefeeds received in a row')
                linefeed_buffered = True
                linefeed_timestamp = timestamp
                continue

            #URC case
            try:
                data_decoded = data.decode()
            except UnicodeDecodeError as e:
                #TODO: handle lots of \x00 from PSM
                #Assumption - URCs are always ASCII
                if not self.large_binary_xfer:
                    self.logger.debug('Received non-UTF-8 data')
                    self.logger.debug('BAD DATA:%s          %s', chr(10), data)
                    #raise e
                if linefeed_buffered:
                    self.serial_read_queue.put((linefeed, linefeed_timestamp))
                    linefeed_buffered = False
                self.serial_read_queue.put((data, timestamp))
                continue

            if any(data_decoded.startswith(prefix) for prefix in self.urc_mappings):
                if not linefeed_buffered:
                    #raise ValueError('URC received before linefeed')
                    self.logger.warning('URC received before linefeed. Can occur on first init of module')
                    linefeed_buffered = True
                    linefeed_timestamp = timestamp
                urc = data.split(b":",1)[0].decode()
                urc_data = data.split(b":",1)[1].decode().lstrip()

                # disambiguate CSCON URC from synchronous reply
                if urc == "+CSCON" and len(urc_data.split(",")) > 1: #only happens in synchronous reply
                    self.serial_read_queue.put((linefeed, linefeed_timestamp))
                    linefeed_buffered = False
                    self.serial_read_queue.put((data, timestamp))
                    continue

                linefeed_timestamp_str = linefeed_timestamp.strftime("%Y-%m-%d_%H-%M-%S-%f")
                timestamp_str = timestamp.strftime("%Y-%m-%d_%H-%M-%S-%f")
                self.logger.debug('URC:\n'
                             '          %s: %s\n'
                             '          %s: %s',linefeed_timestamp_str,linefeed,timestamp_str,data)
                handler_function = self.urc_mappings[urc]
                handler_function(urc_data)
                linefeed_buffered = False
                continue

            # Handle OK, ERROR, command response, or other case
            if linefeed_buffered:
                self.serial_read_queue.put((linefeed, linefeed_timestamp))
                linefeed_buffered = False


            # Handle multiline reply case, no linefeed
            self.serial_read_queue.put((data, timestamp))

    def _reset_input_buffers(self):
        """
        Clears the input buffer by removing all pending items from the queue.
        """
        
        self._serial_flush_event.set()
        while self._serial_flush_event.is_set():
            time.sleep(1)

        with self.serial_read_queue.mutex:

            self.serial_read_queue.queue.clear()
            # Reset the counter of unfinished tasks (used when calling task_done() and join())
            self.serial_read_queue.unfinished_tasks = 0
            # Notify all waiting threads that the queue's state has changed.
            self.serial_read_queue.not_empty.notify_all()
            self.serial_read_queue.not_full.notify_all()
            self.serial_read_queue.all_tasks_done.notify_all()

    @staticmethod
    def _process_URDFILE_data(input_data):
        """
        Processes a URDFILE, accepting filepath or list of binary strings,
        avoiding loading entire data into memory for file input.

        If input_data is a string (filepath), it processes the file in-place
        to extract and return size, streaming data directly to file.
        If input_data is a list of binary strings, it processes the content
        directly from the binary strings and returns size and data (in memory).

        To support large files, the file input method streams data directly to
        the file, avoiding loading the entire file into memory. For smaller
        inputs, the binary string method accumulates data in memory.

        Args:
            input_data (str or list[bytes]): Either the filepath to the URDFILE
                                            or a list containing binary strings
                                            representing the URDFILE content.

        Returns:
            tuple[int, bytes] or tuple[int, None] or None:
                For binary string input: (size (int), data (bytes))
                For filepath input: (size (int), None) - data is in the modified file
                Returns None if an error occurs.
        """
        try:
            if isinstance(input_data, str):
                # --- Filepath input (process file in-place - memory efficient) ---
                filepath = input_data
                file = open(filepath, 'rb+') # Open in binary read+write mode
                is_file_input = True
            elif isinstance(input_data, list) and all(isinstance(item, bytes) for item in input_data):
                # --- List of binary strings input (process in memory) ---
                binary_content = b''.join(input_data)
                file = io.BytesIO(binary_content) # Treat as file-like object
                filepath = None
                is_file_input = False
            else:
                raise TypeError("Input must be a filepath (string) or a list of binary strings.")

            header_line = file.readline()
            header_str = header_line.decode('utf-8')

            if not header_str.startswith('+URDFILE:'):
                file.close()
                raise URDFFileFormatError(f"Header missing.")


            header_content = header_str[len('+URDFILE:'):]

            first_comma_index = header_content.find(',')
            if first_comma_index == -1:
                file.close()
                raise URDFFileFormatError(f"Missing comma after filename.")

            remaining_content_after_filename = header_content[first_comma_index+1:]
            second_comma_index = remaining_content_after_filename.find(',')
            if second_comma_index == -1:
                file.close()
                raise URDFFileFormatError(f"Missing comma after size.")

            size_str = remaining_content_after_filename[:second_comma_index].strip()

            try:
                size = int(size_str)
            except ValueError:
                raise URDFFileFormatError(f"Size is not an integer.")
                file.close()
                return None

            remaining_content_after_size = remaining_content_after_filename[second_comma_index+1:]
            third_quote_index = remaining_content_after_size.find('"')
            if third_quote_index == -1:
                file.close()
                raise URDFFileFormatError(f"Missing quote before data.")

            data_start_header_pos = len('+URDFILE:') + first_comma_index + 1 + second_comma_index + 1 + third_quote_index + 1
            data_start_pos = data_start_header_pos

            # Prepare to read data
            file.seek(data_start_pos)

            chunk_size = 4096
            current_pos = 0 # Track write position in file (for file input)
            bytes_read = 0  # Track bytes read to ensure we read only 'size' bytes

            if is_file_input:
                # --- Filepath input: Stream data directly to file (memory efficient) ---
                try:
                    while bytes_read < size:
                        bytes_to_read = min(chunk_size, size - bytes_read)
                        # Seek to the correct read position
                        file.seek(data_start_pos + bytes_read)
                        chunk = file.read(bytes_to_read)
                        if not chunk:
                            break # Safety break

                        # Seek to current write position
                        file.seek(current_pos)
                        file.write(chunk)
                        current_pos += len(chunk)
                        bytes_read += len(chunk)

                    # Truncate the file to the exact size
                    os.ftruncate(file.fileno(), size)
                    return (size, None)
                finally:
                    file.close()

            else:
                # --- Binary string input: Accumulate data (in memory) - for smaller inputs ---
                data_bytes = b'' # Initialize data_bytes for binary string input only
                while bytes_read < size:
                    bytes_to_read = min(chunk_size, size - bytes_read)
                    chunk = file.read(bytes_to_read)
                    if not chunk:
                        break # Safety break
                    data_bytes += chunk # Accumulate data for binary string input
                    bytes_read += len(chunk)
                file.close()
                return size, data_bytes # Return size and accumulated data for binary string input


        except FileNotFoundError as e: # Catch specific FileNotFoundError and re-raise
            raise # Re-raise FileNotFoundError to signal file not found
        except TypeError as e:        # Catch specific TypeError and re-raise
            raise # Re-raise TypeError for input type issues
        except Exception as e:         # Generic Exception - could be more specific in a real application
            raise Exception(f"An unexpected error occurred during URDFILE processing: {e}") from e # Re-raise with more context
        
#URC handlers

    def handle_uupsdd(self, data):
        """
        Handle the UUPSDD message which indicates the PSD has been deactivated.

        Args:
            data (str): The data received from the UUPSDD message.
        """
        data = int(data.rstrip('\r\n').strip())
        self.module_state.psd = {**self.module_state.psd, "is_active": False, "ip": None}

    def handle_uupsda(self, data):
        """
        Handle UUPSDA message which indicates the PSD has been activated.

        Args:
            data (str): The UUPSDA message data.
        """
        data = data.rstrip('\r\n').split(",")
        is_active = not bool(int(data[0]))
        self.module_state.psd = {**self.module_state.psd, "is_active": is_active}
        logger_str = 'MODULE: PSD Profile is active ' if is_active \
            else 'MODULE: PSD Profile is inactive'
        if len(data) > 1:
            ip = data[1].strip('"')
        self.module_state.psd = {**self.module_state.psd, "ip": ip}
        self.logger.info('%s and has ip: %s', logger_str, ip)

    def handle_cereg(self, data):
        """
        Handles the CEREG URC received from the module indicating changes in the 
        EPS network registration status.

        Args:
            data (str): The data received from the module.

        Notes:
            - CEREG is a special case that can be a read response or a URC.
            - The function determines the mode (read or URC) based on the data received.
            - The function parses the data and updates the relevant parameters.
            - If the registration status has changed, it logs the change.
            - Other parameters are not currently handled and require implementation.
        """
        data = data.rstrip('\r\n').split(",")
        mode = None


        if len(data) == 1:
            mode = "URC" # if there's only one parameter it's a URC because min 2 
                         # parameters in a read response
        elif self.module_config.registration_status_reporting == SaraR5Module.EPSNetRegistrationReportConfig.DISABLED:
            mode = "Read" # no URC if it's disabled
        elif int(data[0]) != self.module_config.registration_status_reporting.value:
            mode = "URC" # if 1st parameter doesn't match config it's a URC
        elif self.module_config.registration_status_reporting == SaraR5Module.EPSNetRegistrationReportConfig.ENABLED:
            mode = "Read" # if 1st parameter matches config and is 1, since there's at least 2 params
                          # it's a read
        elif int(data[0]) != SaraR5Module.EPSNetRegistrationStatus.REGISTERED_AND_ROAMING:
            mode = "Read"
                # for any status other than 1 or 5, no other params should be present
                          # if there's 2+ params and the first one is a 2, 3, or 4 this is a read    
        else:
            raise NotImplementedError("roaming and/or n=5 not yet supported") 



        read_parameters=["mode","registration_status","tracking_area_code","cell_id",
                            "access_tech","reject_cause_type","assigned_active_time",
                            "assigned_tau","rac_or_mme"]
        parsed_result = {}

        if mode == "Read":
            for i, parameter in enumerate(read_parameters):
                if i < len(data):
                    parsed_result[parameter] = data[i].strip()
        if mode == "URC":
            for i, parameter in enumerate(read_parameters[1:]):
                if i < len(data):
                    parsed_result[parameter] = data[i].strip()

        #iterate through parsed parameters
        for key, value in parsed_result.items():
            if key == "mode":
                parsed_result[key] = SaraR5Module.EPSNetRegistrationReportConfig(int(value))
            if key == "registration_status":
                parsed_result[key] = SaraR5Module.EPSNetRegistrationStatus(int(value))
            if key == "tracking_area_code":
                parsed_result[key] = str(value).strip('"')
            if key == "cell_id":
                parsed_result[key] = str(value).strip('"')
            if key == "access_tech":
                parsed_result[key] = int(value)
            if key == "cause_type":
                parsed_result[key] = int(value)
            if key == "assigned_active_time":
                parsed_result[key] = str(value)
            if key == "assigned_tau":
                parsed_result[key] = str(value)
            if key == "rac_or_mme":
                parsed_result[key] = str(value)

        self.module_state.registration_status = parsed_result["registration_status"]

    def handle_cscon(self, data):
        data = data.rstrip('\r\n').split(",")
        signalling_cs_status = bool(int(data[0]))
        self.module_state.signalling_cx_status = signalling_cs_status
        #TODO: parse state and access

    def handle_uupsmr(self, data):
        data = data.rstrip('\r\n').split(",")
        self.module_state.psm = SaraR5Module.PSMState(int(data[0]))

    def handle_uuloc(self,data):
        data = data.rstrip('\r\n').split(",")
        self.logger.debug(data)
        self.logger.debug(len(data))
        response_type = None

        if len(data) == 6:
            response_type = 0
        elif len(data) == 10:
            response_type = 2
        elif len(data) == 13:
            response_type = 1
        elif len(data) >= 15:
            response_type = 2
        else:
            raise ValueError('Unexpected number of parameters in UULOC URC')
        
        if response_type == 2:
            raise NotImplementedError('UULOC response type 2 not yet supported')
        
        date = datetime.datetime.strptime(data[0], "%d/%m/%Y").date()
        time = datetime.datetime.strptime(data[1], "%H:%M:%S.%f").time()
        dt = datetime.datetime.combine(date, time).replace(tzinfo=datetime.timezone.utc)
        

        location = {
            "datetime": dt.timestamp(),
            "latitude": float(data[2]),
            "longitude": float(data[3]),
            "altitude": float(data[4]),
            "uncertainty": int(data[5]) #Estimated 50% confidence level error, in meters (0 - 20000000)
        }

        if response_type == 1:

            location["speed"] = float(data[6])
            location["course"] = float(data[7])
            location["vertical_accuracy"] = int(data[8])
            location["response_source"] = int(data[9])
            location["satellites_used"] = int(data[10])
            location["antenna_status"] = int(data[11])
            location["jamming_status"] = int(data[12])

        self.module_state.location = location

# Misc

    def at_store_current_configuration(self, profile_id:int=0):
        """
        Stores the current configuration to the specified profile ID in
        non-volatile memory.

        Args:
            profile_id (int, optional): The profile ID to store the configuration to. 
                Defaults to 0.
        """
        if profile_id not in range (0, 2):
            raise ValueError('Profile ID must be between 0 and 1')

        self.send_command(f'AT&W{profile_id}',expected_reply=False)

        self.logger.info('Stored current configuration to profile %s', profile_id)

    def __repr__(self):
        return f'IoTModule(serial_port="{self.serial_config.serial_port}")'
    
