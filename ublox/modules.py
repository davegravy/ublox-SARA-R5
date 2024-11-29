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
from functools import partial
from collections import namedtuple
from typing import Callable

import os
import threading
import queue
import datetime
import logging
import time
import serial
import validators
import errno


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

class AT_Command_Handler():

    def __init__(self, response_queue, output_fn, logger=None):
        self.logger = logger or logging.getLogger(__name__)

        self.response_queue = response_queue
        self.output_fn = output_fn
    
    def send_cmd(self, command:str, input_data:bytes=None, expected_reply=True, expected_multiline_reply=False, timeout=10):
        
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
        self._validate()
        self._prepare_expected_reply()

        self.output_fn(self._command_bytes(terminated=True))
        timestamp_write = datetime.datetime.now()
        timestamp_write_str = timestamp_write.strftime("%Y-%m-%d_%H-%M-%S")
        #self.logger.debug('Sent:%s          %s: %s', chr(10), timestamp_write_str, self._command_bytes(terminated=True))

        self.got_reply = True if not self.expected_reply_bytes else False
        self.got_ok = False
        self.result, self.multiline_result = None, []
        self.debug_log = []
        self.timeout_time = time.time() + timeout

        try:
            while not time.time() > self.timeout_time:
                if self.got_ok and self.got_reply and self.input_data is None:
                    break
                response = self._get_response()
                self._process_response(response)
                if input_data and response.startswith(b">"):
                    self.output_fn(self.input_data)
                    self.input_data = None
            else:   
                self.logger.error(f"Timeout waiting for response to '{self.command_str}'. State: got_ok={self.got_ok}, got_reply={self.got_reply}, input_data={self.input_data}")
                raise ATTimeoutError("Timeout waiting for response")
        except Exception as e:
            raise e
        finally:
            self._log_debug_info()

        return self.multiline_result if self.expected_multiline_reply else self.result

    
    def _validate(self):
        if not isinstance(self.expected_reply, (bool, str)):
            raise TypeError("expected_reply is not of type bool or str")
        if self.expected_multiline_reply and not self.expected_reply:
            raise ValueError("multiline_reply cannot be True if expected_reply is False")

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
            expected_reply_bytes = b"+" + self.expected_reply.encode() + b":"
        self.expected_reply_bytes=expected_reply_bytes
    
    def _get_response(self):
        time_remaining = self.timeout_time - time.time()
        try:
            response, timestamp_read = self.response_queue.get(timeout=time_remaining)
            self.debug_log.append((timestamp_read, response))
            return response
        except queue.Empty:
            return None
        
    def _process_response(self, response):
        if response is None:
            return
        if response.startswith(b"OK"):
            self.got_ok = True
        elif self.expected_reply_bytes and response.startswith(self.expected_reply_bytes):
            self.got_reply = True
            self.result = response.lstrip(self.expected_reply_bytes).rstrip(b"\r\n").decode().strip().split(",")
            self.multiline_result.append(response)
        elif response.startswith(b"ERROR"):
            raise ATError
        elif response.startswith(b"+CME ERROR:"):
            code = response.lstrip(b"+CME ERROR:").rstrip(b"\r\n").decode()
            #TODO: convert code to error message
            raise CMEError(code)
        elif response == b"\r\n" or response.startswith(self._command_bytes(terminated=False)):
            pass
        elif self.input_data and response.startswith(b">"):
            # raw data input prompt, handled elsewhere
            pass
        elif self.expected_multiline_reply and self.expected_reply_bytes and self.got_reply:
            self.multiline_result.append(response)
        else:
            self.logger.warning('got unexpected %s', response)

    def _log_debug_info(self):
        debug_str = [f'{timestamp.strftime("%Y-%m-%d_%H-%M-%S")}: {response}' for timestamp, response in self.debug_log]
        debug_output = '\n          '.join(debug_str)
        #self.logger.debug('Received:%s          %s', chr(10), debug_output)

class SaraR5Module:
    """
    Represents a u-blox SARA-R5 module.

    Args:
        serial_port (str): The serial port to communicate with the module.
        baudrate (int, optional): The baudrate for the serial communication. Defaults to 115200.
        rtscts (bool, optional): Enable RTS/CTS flow control. Defaults to False.
        roaming (bool, optional): Enable roaming. Defaults to False.
        echo (bool, optional): Enable echo. Defaults to True.
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

    def __init__(self, serial_port: str, baudrate=115200, rtscts=False,
                 roaming=False, echo=True, power_control: type = PowerControl, logger=None, tx_rx_logger=None):
        
        self.logger = logger or logging.getLogger(__name__)
        if logger is None:
            self.logger.setLevel(logging.DEBUG)
            self.logger.addHandler(logging.StreamHandler())

        self.tx_rx_logger = tx_rx_logger or logging.getLogger(__name__ + '.tx_rx')
        if tx_rx_logger is None:
            self.tx_rx_logger.setLevel(logging.DEBUG)
            self.tx_rx_logger.addHandler(logging.StreamHandler())

        self._serial_port = serial_port
        self._serial = serial.Serial(self._serial_port, baudrate=baudrate,
                                     rtscts=rtscts,bytesize=8,parity='N',
                                     stopbits=1,timeout=5)
        self.power_control:PowerControl = power_control(logger=self.logger)
        self.serial_read_queue = queue.Queue()
        self.echo = echo
        self.at_cmd_handler = AT_Command_Handler(self.serial_read_queue, self._write_serial_and_log, logger=self.logger)
        self.psm_state = SaraR5Module.PSMState.PSM_INACTIVE

        self.terminate = False

        self.read_uart_thread = threading.Thread(target=self._read_from_uart)
        self.read_uart_thread.daemon = True
        # self.read_vin_thread = threading.Thread(target=self._read_vin_loop)
        # self.read_vin_thread.daemon = True
                                            

        self.imei = None
        self.iccid = None
        self.psd = {}
        self.signalling_cx_status = False
        self.registration_status = SaraR5Module.EPSNetRegistrationStatus.NOT_REGISTERED
        self.registration_status_config:SaraR5Module.EPSNetRegistrationReportConfig = None
        self.roaming = roaming
        self.ip = None

        self.current_rat = None

        self.sockets = {}
        self.http_profiles = {}
        self.security_profiles = {}
        self.mqtt_client = MQTTClient(self)

        self.radio_status = None
        self.radio_stats = None

        self.urc_mappings = {
            "+CEREG":  self.handle_cereg,
            "+UUPSDD": self.handle_uupsdd,
            "+UUPSDA": self.handle_uupsda,
            "+UUHTTPCR": partial(HTTPClient.handle_uuhttpcr, self),
            "+CSCON": self.handle_cscon,
            "+UUPSMR": self.handle_uupsmr,
            "+UUMQTTC": self.mqtt_client.handle_uumqttc
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
            success = False
            while not success:
                #self.power_control.force_power_off_alt()
                self.power_control.force_power_off()
                success = self.power_control.await_power_state(False, timeout=30)
                if not success: self.logger.warning("Power OFF failed, retrying")
                time.sleep(1)  # wait before retrying
            self.logger.info("Power OFF successful")

        while True:
            self.logger.info("Powering ON the module")
            success = False
            while not success:
                self.power_control.power_on_wake()
                success = self.power_control.await_power_state(True, timeout=30)
                if not success: 
                    self.logger.warning("Power ON failed, retrying")
                else: 
                    self.logger.info("Power ON successful")
                time.sleep(1)  # wait before retrying

            time.sleep(3)  # wait for boot
            self._reset_input_buffer()  # remove noise from any preceding power cycles

            for _ in range(7):
                try:
                    self.send_command("AT", expected_reply=False, timeout=0.2)
                    responding = True
                    break
                except ATTimeoutError as e:
                    self.logger.debug(e)
                    responding = False

            if responding:
                if clean:
                    self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.SILENT_RESET)
                    time.sleep(2)
                break
            
            clean = True #if not responding we should treat this as a clean restart 

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
                    #self.power_control.force_power_off_alt()
                    self.power_control.force_power_off()
                    success = self.power_control.await_power_state(False, timeout=30)
                    if not success: self.logger.warning("Power OFF failed, retrying")
                    time.sleep(1)  # wait before retrying
                power_cycles_count += 1
            else:
                raise ModuleNotRespondingError("Module not responding, tried %s hard resets and %s power cycles" % (hard_reset_count, power_cycles_count))

    def setup(self, mno_profile, apn, power_saving_mode=False, tau:PSMPeriodicTau=None, 
              active_time:PSMActiveTime=None):
        """
        Sets up the module with the specified Mobile Network Operator profile and APN.

        Args:
            mno_profile: The MNO profile to use.
            apn: The APN to use.
        """
        #TODO: support manually connecting to specific operator
        #TODO: support NB-IoT
        cid_profile_id, psd_profile_id = 1, 0 #TODO: support multiple profiles
        self.at_set_echo(False)
        self.at_set_power_saving_uart_mode(SaraR5Module.PowerSavingUARTMode.DISABLED)
        self.at_set_error_format(SaraR5Module.ErrorFormat.VERBOSE) # verbose format
        
        #TODO: write functions for the below two items
        #self.send_command("AT+UPSMVER=24", expected_reply=False)
        #self.send_command("AT+CFUN=16", expected_reply=False)
        #self.serial_init()
        #self.at_set_echo(False)
        #self.at_set_power_saving_uart_mode(SaraR5Module.PowerSavingUARTMode.DISABLED)
        #self.at_set_error_format(SaraR5Module.ErrorFormat.VERBOSE) # verbose format
        
        self.at_read_imei()

        # in case module had protocol stack disabled, need CFUN=126 before CFUN=1
        self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.RESTORE_PROTOCOL_STACK)
        self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.MINIMUM_FUNCTIONALITY)
        if power_saving_mode is True:
            #disable lwm2m client so doesn't block psm
            self.at_set_lwm2m_activation(False)

        self.at_set_mno_profile(mno_profile)
        self._await_iccid()
        self.at_set_pdp_context(cid_profile_id, SaraR5Module.PDPType.IPV4, apn)
        self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.FULL_FUNCTIONALITY)
        self.at_set_eps_network_reg_status(
            SaraR5Module.EPSNetRegistrationReportConfig.ENABLED_WITH_LOCATION_AND_PSM)
        self._await_connection(roaming=False, timeout=60)
        self.at_set_psd_protocol_type(psd_profile_id, SaraR5Module.PSDProtocolType.IPV4)
        self.at_set_psd_to_cid_mapping(psd_profile_id, cid_profile_id)
        self.at_get_psd_profile_status(psd_profile_id, SaraR5Module.PSDParameters.ACTIVATION_STATUS)
        if not self.psd["is_active"]:
            self.at_psd_action(psd_profile_id, SaraR5Module.PSDAction.ACTIVATE)

        self.at_set_edrx(EDRXMode.DISABLED)
        self.at_set_power_saving_mode_urc(power_saving_mode)
        self.at_set_signalling_cx_urc(
            SaraR5Module.SignalCxReportConfig.ENABLED_MODE_ONLY if power_saving_mode
            else SaraR5Module.SignalCxReportConfig.DISABLED)

        self.at_store_current_configuration()

        self.at_set_psm_mode(
            SaraR5Module.PSMMode.ENABLED if power_saving_mode else SaraR5Module.PSMMode.DISABLED,
            periodic_tau=tau, active_time=active_time)

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

    def wake_from_sleep(self):
        self.serial_init()
        
        #TODO: call function self.restore_NVM(). Track non-volatile settings in module class and restore them to device from this function    
        #e.g. MQTT settings, security profiles, etc
        self.at_set_power_saving_uart_mode(SaraR5Module.PowerSavingUARTMode.DISABLED)
        self.at_set_lwm2m_activation(False)
        self.at_set_error_format(SaraR5Module.ErrorFormat.VERBOSE) # verbose format
        self.at_set_eps_network_reg_status(
             SaraR5Module.EPSNetRegistrationReportConfig.ENABLED_WITH_LOCATION_AND_PSM)
        self.at_get_eps_network_reg_status()
        self.mqtt_client.at_set_mqtt_nonvolatile(MQTTClient.NonVolatileOption.RESTORE_FROM_NVM)

        
        
    def register_after_wake(self):
        self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.RESTORE_PROTOCOL_STACK)
        self.at_set_module_functionality(SaraR5Module.ModuleFunctionality.FULL_FUNCTIONALITY)
        self._await_connection(timeout=60)
        self.at_get_psd_profile_status(0, SaraR5Module.PSDParameters.ACTIVATION_STATUS)
        if not self.psd["is_active"]:
            self.at_psd_action(0, SaraR5Module.PSDAction.ACTIVATE)   

    def prep_for_sleep(self):
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

    def _await_connection(self, roaming=False, polling_interval=2, timeout=180):
        """
        Continuously poll the connection status and see if the connection status has changed.

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
        self.logger.info('Awaiting Connection')
        start_time = time.time()
        while True:

            self.at_get_eps_network_reg_status() #triggers URC

            if (not roaming) and self.registration_status == \
                SaraR5Module.EPSNetRegistrationStatus.REGISTERED_HOME_NET:
                break

            if roaming and self.registration_status == \
            SaraR5Module.EPSNetRegistrationStatus.REGISTERED_AND_ROAMING:
                break

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'Could not connect in {timeout} seconds')
            
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

            if self.iccid:
                self.logger.info('ICCID: %s', self.iccid)
                break

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'Could not retrieve ICCID in {timeout} seconds')
            
            time.sleep(polling_interval)

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
        self.iccid = int(result[0])
        return self.iccid

    def at_read_imei(self):
        """
        Reads the International Mobile Equipment Identity (IMEI) number.

        Returns:
            int: The IMEI number.
        """
        result = self.send_command('AT+CGSN=1')
        self.imei = int(result[0])
        return self.imei


# Networking / radio config

    def at_set_mno_profile(self, profile_id:MobileNetworkOperator):
        """
        Sets the Mobile Network Operator (MNO) profile.

        Args:
            profile_id (MobileNetworkOperator): The profile ID of the MNO.
        """
        self.send_command(f'AT+UMNOPROF={profile_id.value}', expected_reply=False)
        self.logger.info('Mobile Network Operator Profile set to %s', profile_id.name)

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
        self.registration_status_config=config
        self.logger.info('%s set to %s', config.name, config.value)

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

        Raises:
            NotImplementedError: This method is not implemented yet.
        """
        raise NotImplementedError

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
            self.psd["ip"] = response_list[2]
            self.logger.info('PSD Profile %s IP Address is %s',profile_id,self.psd["ip"])
            return self.psd["ip"]

        if parameter == SaraR5Module.PSDParameters.ACTIVATION_STATUS:
            self.psd["is_active"] = bool(int(response_list[2]))
            self.logger.info('PSD Profile %s Activation Status is %s', profile_id, self.psd["is_active"])
            return self.psd["is_active"]

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

    def at_set_lwm2m_activation(self, enabled: bool):
        self.send_command(f'AT+ULWM2M={int(not enabled)}',expected_reply=False) 
        self.logger.info('LWM2M activation set to %s', 'enabled' if enabled else 'disabled')

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
        self.send_command(f'AT+UDWNFILE="{filename}",{length}',
                           expected_reply=False, input_data=data,timeout=30)
        self.logger.info('Uploaded %s bytes to %s', length, filename)

    def at_read_file(self, filename, timeout=10):
        """
        Reads a file from the module.

        Args:
            filename (str): The name of the file to read.
            timeout (int, optional): The timeout value in seconds. Defaults to 10.

        Returns:
            str: The byte contents of the file.
        """
        SaraR5Module.validate_filename(filename)
        return self.send_command(f'AT+URDFILE="{filename}"', multiline_reply=True, timeout=timeout)

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

        self.radio_status=translated_meta
        self.radio_stats=translated_stats
        return self.radio_status, self.radio_stats

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

    def send_command(self, command:str, input_data:bytes=None, expected_reply=True, expected_multiline_reply=False, timeout=10):
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
        return self.at_cmd_handler.send_cmd(command, input_data, expected_reply, expected_multiline_reply, timeout)

    def _read_serial_and_log(self):
        data = self._serial.readline()
        if len(data) > 0:
            timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            self.tx_rx_logger.debug('RX: %s',data)
            #self.receive_log.write(f'{timestamp_str};{data}\n')
        return data

    def _write_serial_and_log(self,data):
        self._serial.write(data)
        timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        if len(data) < 1024:
            self.tx_rx_logger.debug('TX: %s',data)
        else:
            #data too big to log
            self.tx_rx_logger.debug('TX: %s',data[:1024])

        #self.send_log.write(f'{timestamp_str};{data}\n')

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
            data = self._read_serial_and_log()
            if len(data) < 1:
                continue
            timestamp = datetime.datetime.now()

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
                self.logger.debug('Received non-UTF-8 data')
                self.logger.debug('BAD DATA:%s          %s', chr(10), data)
                #TODO: handle lots of \x00 from PSM
                raise e
            if any(data_decoded.startswith(prefix) for prefix in self.urc_mappings):
                if not linefeed_buffered:
                    #raise ValueError('URC received before linefeed')
                    self.logger.warning('URC received before linefeed. Can occur on first init of module')
                    linefeed_buffered = True
                    linefeed_timestamp = timestamp
                urc = data.split(b":")[0].decode()
                urc_data = data.split(b":")[1].decode()
                linefeed_timestamp_str = linefeed_timestamp.strftime("%Y-%m-%d_%H-%M-%S")
                timestamp_str = timestamp.strftime("%Y-%m-%d_%H-%M-%S")
                self.logger.debug('URC:\n'
                             '          %s: %s\n'
                             '          %s: %s',linefeed_timestamp_str,linefeed,timestamp_str,data)
                handler_function = self.urc_mappings[urc]
                handler_function(urc_data)
                linefeed_buffered = False
                continue

            #OK, ERROR, command response, or other case
            if linefeed_buffered:
                data_with_timestamp = (linefeed, linefeed_timestamp)
                self.serial_read_queue.put(data_with_timestamp)
                linefeed_buffered = False
                data_with_timestamp = (data, timestamp)
                self.serial_read_queue.put(data_with_timestamp)
                continue

            #multiline reply case, no linefeed
            data_with_timestamp = (data, timestamp)
            self.serial_read_queue.put(data_with_timestamp)

    def _reset_input_buffer(self):
        """
        Clears the input buffer by removing all pending items from the queue.
        """
        while not self.serial_read_queue.empty():
            try:
                self.serial_read_queue.get_nowait()
            except queue.Empty:
                continue
            self.serial_read_queue.task_done()

#URC handlers

    def handle_uupsdd(self, data):
        """
        Handle the UUPSDD message which indicates the PSD has been deactivated.

        Args:
            data (str): The data received from the UUPSDD message.
        """
        data = int(data.rstrip('\r\n').strip())
        self.psd["is_active"] = False
        self.psd["ip"] = None
        self.logger.info('PSD Profile is inactive')

    def handle_uupsda(self, data):
        """
        Handle UUPSDA message which indicates the PSD has been activated.

        Args:
            data (str): The UUPSDA message data.
        """
        data = data.rstrip('\r\n').split(",")
        self.psd["is_active"] = not bool(int(data[0]))
        logger_str = 'MODULE: PSD Profile is active ' if self.psd["is_active"] \
            else 'MODULE: PSD Profile is inactive'
        if len(data) > 1:
            self.psd["ip"] = data[1].strip('"')
        self.logger.info('%s and has ip: %s', logger_str, self.psd["ip"])

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
        elif self.registration_status_config == SaraR5Module.EPSNetRegistrationReportConfig.DISABLED:
            mode = "Read" # no URC if it's disabled
        elif int(data[0]) != self.registration_status_config.value:
            mode = "URC" # if 1st parameter doesn't match config it's a URC
        elif self.registration_status_config == SaraR5Module.EPSNetRegistrationReportConfig.ENABLED:
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

        if self.registration_status != parsed_result["registration_status"]:
            self.logger.info("MODULE: Registration status changed from %s to %s",
                        self.registration_status.name, parsed_result['registration_status'].name)
        self.registration_status = parsed_result["registration_status"]

    def handle_cscon(self, data):
        data = data.rstrip('\r\n').split(",")
        signalling_cs_status = bool(int(data[0]))
        if signalling_cs_status != self.signalling_cx_status:
            self.logger.info('MODULE: Signalling connection status changed from %s to %s',
                        self.signalling_cx_status, signalling_cs_status)
        self.signalling_cx_status = signalling_cs_status
        #TODO: parse state and access

    def handle_uupsmr(self, data):
        data = data.rstrip('\r\n').split(",")
        psm_state = SaraR5Module.PSMState(int(data[0]))
        if psm_state != self.psm_state:
            self.logger.info('MODULE: PSM status changed from %s to %s',
                        self.psm_state.name, psm_state.name)
        self.psm_state = psm_state


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
        return f'IoTModule(serial_port="{self._serial_port}")'
    