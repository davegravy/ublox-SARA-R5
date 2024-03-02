import time
import serial
import binascii
import validators
import mpio
import threading
import queue
import datetime
import time

from enum import Enum
import logging

from ublox.http import HTTPClient, SecurityProfile
from ublox.utils import PSMActiveTime, PSMPeriodicTau
#from ublox.socket import UDPSocket
from collections import namedtuple

from typing import Callable

logger = logging.getLogger(__name__)

Stats = namedtuple('Stats', 'type name value')


class CMEError(Exception):
    """CME ERROR on Module"""


class ATError(Exception):
    """AT Command Error"""

class ATAckError(ATError):
    """Didn't receive an expected ACK"""

class ATAckMissingError(ATError):
    """Didn't receive any ACK"""

class ATTimeoutError(ATError):
    """Making an AT Action took to long"""


class ConnectionTimeoutError(ATTimeoutError):
    """Module did not connect within the specified time"""


class SaraR5Module:
    """
    Represents a Ublox SARA R5XX module.
    """

    class HEXMode(Enum):
        DISABLED = 0
        ENABLED = 1

    class ErrorFormat(Enum):
        DISABLED = 0
        NUMERIC = 1
        VERBOSE = 2
    
    class ModuleFunctionality(Enum):
        MINIMUM_FUNCTIONALITY = 0 #no TxRx
        FULL_FUNCTIONALITY = 1
        AIRPLANE_MODE = 4
        DISABLE_RF_AND_SIM = 7
        DISABLE_RF_AND_SIM_2 = 8
        FAST_SAFE_POWEROFF = 10
        SILENT_RESET = 16
        DEEP_SLEEP_PREP = 126
        
    class RadioAccessTechnology(Enum):
        LTE_CAT_M1 = 7
        NB_IOT = 8

    class CurrentRadioAccessTechnology(Enum):
        _2G = 2
        _3G = 3
        _4G = 4
        UNKNOWN = 5
        LTE_CAT_M1 = 6
        NB_IOT = 7

    class CurrentRadioServiceState(Enum):
        NOT_KNOWN = 0
        RADIO_OFF = 1
        SEARCHING = 2
        NO_SERVICE = 3
        REGISTERED = 4

    class LTERadioResourceControlState(Enum):
        NULL = 0
        IDLE = 1
        ATTEMPT_TO_CONNECT = 2
        CONNECTED = 3
        LEAVING_CONNECTED_STATE = 4
        ATTEMPT_LEAVING_E_UTRA = 5
        ATTEMPT_ENTERING_E_UTRA = 6
        NOT_KNOWN = 255

    class SignallingConnectionStatusReportConfig(Enum):
        DISABLED = 0
        ENABLED_MODE_ONLY = 1
        ENABLED_MODE_AND_STATE = 2
        ENABLED_MODE_AND_STATE_AND_ACCESS = 3

    class EPSNetworkRegistrationReportConfig(Enum):
        DISABLED = 0
        ENABLED = 1
        ENABLED_WITH_LOCATION = 2
        ENABLED_WITH_LOCATION_AND_EMM_CAUSE = 3
        ENABLED_WITH_LOCATION_AND_PSM = 4
        ENABLED_WITH_LOCATION_AND_EMM_CAUSE_AND_PSM = 5

    class EPSNetworkRegistrationStatus(Enum):
        NOT_REGISTERED = 0
        REGISTERED_HOME_NET = 1
        NOT_REGGISTERED_AND_SEARCHING = 2
        REGISTRATION_DENIED = 3
        UNKNOWN = 4
        REGISTERED_AND_ROAMING = 5
        EMERGENCY_BEARER_ONLY = 8

    class MobileNetworkOperator(Enum):
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
        IPV4 = 0
        IPV6 = 1
        IPV4V6_WITH_IPV4_PREFERRED = 2
        IPV4V6_WITH_IPV6_PREFERRED = 3
    
    class PSDAction(Enum):
        RESET = 0
        STORE = 1
        LOAD = 2
        ACTIVATE = 3
        DEACTIVATE = 4

    class PSDParameters(Enum):
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
        QOS_SIGNALING_INDICATOR = 20
        QOS_SOURCE_STATISTICS_DESCRIPTOR = 21
        QOS_TRAFFIC_CLASS = 22
        QOS_TRAFFIC_PRIORITY = 23
        QOS_TRANSFER_DELAY = 24

    class PDPType(Enum):
        IPV4 = 'IP'
        NONIP = 'NONIP'
        IPV4V6 = 'IPV4V6'
        IPV6 = 'IPV6'

    class PowerSavingUARTMode(Enum):
        DISABLED = 0
        ENABLED = 1
        RTS_CONTROLLED = 2
        DTS_CONTROLLED = 3
        ENABLED_2 = 4 # same as ENABLED?
    
    class eDRXMode(Enum):
        DISABLED = 0
        ENABLED = 1
        ENABLED_WITH_URC = 2
        DISABLED_AND_RESET = 3
    
    class eDRXAccessTechnology(Enum):
        EUTRAN_WB_S1 = 4
        EUTRAN_NB_S1 = 5

    class eDRXCycle(Enum):
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

    class PSMMode(Enum):
        DISABLED = 0
        ENABLED = 1
        DISABLED_AND_RESET = 2


    SUPPORTED_SOCKET_TYPES = ['UDP', 'TCP']
    
    def __init__(self, serial_port: str, baudrate=115200, rtscts=False, roaming=False, echo=True, power_toggle: Callable[[], None] = None):
        self._serial_port = serial_port
        self._serial = serial.Serial(self._serial_port, baudrate=baudrate, rtscts=rtscts,bytesize=8,parity='N',stopbits=1,timeout=5)
        self.terminate = False
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.read_thread = threading.Thread(target=self._read_from_device)
        self.read_thread.daemon = True
        self.power_toggle = power_toggle if power_toggle else lambda: (_ for _ in ()).throw(NotImplementedError("Power toggle function needs to be configured"))
        self.psd = {}
        self.echo = echo
        self.roaming = roaming
        self.ip = None
        self.connected = False
        self.sockets = {}
        self.http_profiles = {}
        self.security_profiles = {}
        self.available_messages = list()
        self.imei = None
        # TODO: make a class containing all states
        self.registration_status = 0
        self.current_rat = None

        self.urc_mappings = {
            "+CEREG":  self.handle_cereg,
            "+UUPSDD": self.handle_uupsdd
            
        }

        self.read_thread.start()

    def serial_init(self, retry_threshold=5):
        logger.info('Initializing module')
        #TODO: update this for when we have VIN available
        responding = None
        #echo_configured = None
        ATAckErrors = 0
        ATTimeoutErrors = 0

        self._serial.reset_input_buffer()

        while True:
            try:
                self._send_command("AT", expected_reply=False, timeout=0.25)
                break
            # except ATAckError as e:
            #     logger.debug(e)
            #     ATAckErrors += 1
            #     responding = True
            #     echo_configured = False
            except ATTimeoutError as e:
                logger.debug(e)
                ATTimeoutErrors += 1
                responding = False
            
            if not responding:
                logger.info("toggling power")
                self.power_toggle()
                time.sleep(2) # wait for boot
            # if responding and not echo_configured:
            #     try:
            #         self._at_action("ATE0", timeout=0.25)
            #     except ATAckError:
            #         pass
            #     self._serial.reset_input_buffer()
            #     echo_configured = True
            if ATAckErrors + ATTimeoutErrors > retry_threshold:
                raise Exception("Module not responding")
            logger.info(f'retrying init attempt #{ATAckErrors + ATTimeoutErrors}')

        
    def setup(self, mno_profile, apn):

        #TODO: support manually connecting to specific operator
        #TODO: support NB-IoT
        cid_profile_id, psd_profile_id = 1, 0 #TODO: support multiple profiles
        self.AT_set_echo(False)

        self.AT_set_error_format(SaraR5Module.ErrorFormat.VERBOSE) # verbose format
        self.AT_read_imei()
        self.AT_set_module_functionality(SaraR5Module.ModuleFunctionality.MINIMUM_FUNCTIONALITY)
        self.AT_set_MNO_profile(mno_profile)
        self.AT_set_pdp_context(cid_profile_id, SaraR5Module.PDPType.IPV4, apn)
        self.AT_set_module_functionality(SaraR5Module.ModuleFunctionality.FULL_FUNCTIONALITY)
        #self._await_connection(roaming=False, timeout=60)
        self.AT_set_EPS_network_reg_status(SaraR5Module.EPSNetworkRegistrationReportConfig.ENABLED)
        self.AT_get_EPS_network_reg_status()
        # self.AT_set_PSD_protocol_type(psd_profile_id, SaraR5Module.PSDProtocolType.IPV4)
        # self.AT_set_PSD_to_CID_mapping(psd_profile_id, cid_profile_id)
        # self.AT_get_PSD_profile_status(psd_profile_id, SaraR5Module.PSDParameters.ACTIVATION_STATUS)
        # if not self.psd["is_active"]:
        #     self.AT_PSD_action(psd_profile_id, SaraR5Module.PSDAction.ACTIVATE)

    def close(self):
        logger.info('Closing module')
        self.terminate = True
        self.read_thread.join()
        self._serial.close()

    def setup_old(self, radio_mode='LTE-M'):
        """
        Running all commands to get the module up an working
        """


        #TODO: figure out why NBIOT won't work
        #self.set_radio_mode(mode=radio_mode)
        self.enable_radio_functions()
        self.enable_network_registration()
        self.set_error_format()
        self.set_data_format()

    def connect(self, operator: int, apn: str, roaming=False):
        """
        Will initiate commands to connect to operators network and wait until
        connected.
        """
        logger.info(f'Trying to connect to operator {operator} network')
        # TODO: Handle connection independent of home network or roaming.

        self.set_pdp_context(apn)

        # TODO: this breaks R5, figure out why
        # if operator:
        #     at_command = f'AT+COPS=1,2,"{operator}"'

        # else:
        #     at_command = f'AT+COPS=0'

        #self._at_action(at_command, timeout=300)
        self._await_connection(roaming or self.roaming)
        logger.info(f'Connected to {operator}')

    def AT_set_data_format(self, mode: HEXMode=HEXMode.DISABLED):

        self._send_command(f'AT+UDCONF=1,{mode}')  # Set data format to HEX
        logger.info(f'{mode.name} set to {mode.value}')

    def AT_read_imei(self):
        logger.info(f'Reading IMEI from module')
        result = self._send_command('AT+CGSN=1')
        self.imei = int(result[0])

    def AT_set_echo(self, enabled:bool=False):
        self._send_command(f'ATE{int(enabled)}', expected_reply=False)
        logger.info(f'Echo {"enabled" if enabled else "disabled"}')

    def AT_set_error_format(self, format: ErrorFormat=ErrorFormat.DISABLED):
        self._send_command(f'AT+CMEE={format.value}',expected_reply=False)  # enable verbose errors
        logger.info(f'Verbose errors {format.name}')

    def AT_set_MNO_profile(self, profile_id:MobileNetworkOperator):
        self._send_command(f'AT+UMNOPROF={profile_id.value}',expected_reply=False)
        logger.info(f'Mobile Network Operator Profile set to {profile_id.name}')



    def set_band_mask(self, bands: list = None):
        """
        Band is set using a bit for each band. Band 1=bit 0, Band 64=Bit 63

        .. note:
            Only supports NB IoT RAT.
        """
        raise NotImplementedError
        # DEFAULT_BANDS = [20]
        # logger.info(f'Setting Band Mask for bands {bands}')
        # bands_to_set = bands or DEFAULT_BANDS
        # total_band_mask = 0

        # for band in bands_to_set:
        #     individual_band_mask = 1 << (band - 1)
        #     total_band_mask = total_band_mask | individual_band_mask

        # self._at_action(f'AT+UBANDMASK=1,{total_band_mask},{total_band_mask}')

    # def enable_quality_reporting(self):
    #     #TODO: this is now a read-only command
    #     logger.info('Enables reporting of RSRP and RSRQ via AT+UCGED')
    #     self._at_action('AT+UCGED=5')

    def AT_set_psm_mode(self, mode:PSMMode, periodic_tau:PSMPeriodicTau, active_time:PSMActiveTime):
        self._send_command(f'AT+CPSMS={mode.value},,,{periodic_tau.value},{active_time.value}', expected_reply=False, timeout=10)
        logger.info(f'PSM Mode set to {mode.name} with Periodic Tau {periodic_tau.name} and Active Time {active_time.name}')

    def AT_set_signaling_connection_urc(self,config:SignallingConnectionStatusReportConfig=SignallingConnectionStatusReportConfig.DISABLED):
        """
        Configure Signaling Connection URC
        """
        self._send_command(f'AT+CSCON={config.value}',expected_reply=False)
        logger.info(f'{config.name} set to {config.value}')

    def AT_get_EPS_network_reg_status(self):
        """
        Configure EPS Network Registration URC
        """
        result = self._send_command(f'AT+CEREG?',expected_reply=False) #False is correct!
        #NOTE: URC handles reply 

        #logger.info(f'{config.name} set to {config.value}')

    def AT_set_EPS_network_reg_status(self, config:EPSNetworkRegistrationReportConfig=EPSNetworkRegistrationReportConfig.DISABLED):
        """
        Configure EPS Network Registration URC
        """
        self._send_command(f'AT+CEREG={config.value}',expected_reply=False)
        logger.info(f'{config.name} set to {config.value}')

    def AT_set_module_functionality(self,function:ModuleFunctionality=ModuleFunctionality.FULL_FUNCTIONALITY,reset:bool=None):

        if reset and function not in [SaraR5Module.ModuleFunctionality.FULL_FUNCTIONALITY,SaraR5Module.ModuleFunctionality.AIRPLANE_MODE]:
            raise ValueError('Reset can only be used with FULL_FUNCTIONALITY or AIRPLANE_MODE')
        
        at_command = f'AT+CFUN={function.value}'
        logger_str = f'Module Functionality set to {function.name}'
        if reset is not None:
            at_command+=f',{int(reset)}'
            logger_str+=f' with reset {reset}'
        self._send_command(at_command,expected_reply=False,timeout=180)
        logger.info(logger_str)

    def AT_read_module_functionality(self):
        raise NotImplementedError
    

    def AT_set_radio_mode(self, mode:RadioAccessTechnology=RadioAccessTechnology.LTE_CAT_M1):

        response = self._send_command(f'AT+URAT={mode.value},',expected_reply=False,timeout=10)

        self.current_rat = mode.name
        logger.info(f'Radio Access Technology set to {mode.name}')
        return response

    def AT_set_pdp_context(self, cid:int=1, pdp_type:PDPType=PDPType.IPV4, apn:str="", pdp_address:str="0.0.0.0", data_compression:bool=False, header_compression:bool=False):
        # NOTE: AT+CFUN=0 needed for R5 to set PDP context

        if cid not in range (0, 12):
            raise ValueError('CID must be between 0 and 11')
        if len(apn) > 99:
            raise ValueError('APN must be less than 100 characters')
        if pdp_type==SaraR5Module.PDPType.IPV4 and not validators.ipv4(pdp_address): 
            raise ValueError("Invalid IPV4 address")
        if pdp_type==SaraR5Module.PDPType.IPV4V6 and not any [validators.ipv4(pdp_address),validators.ipv6(pdp_address)]:
            raise ValueError("Invalid IPV4 or IPV6 address")
        if pdp_type==SaraR5Module.PDPType.IPV6 and not validators.ipv6(pdp_address):
            raise ValueError("Invalid IPV6 address")
    
        self._send_command(f'AT+CGDCONT={cid},"{pdp_type.value}","{apn}","{pdp_address}",{int(data_compression)},{int(header_compression)}',expected_reply=False)
        logger.info(f'PDP Context set to {pdp_type.name} with APN {apn} and PDP Address {pdp_address}')

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
        # logger.info(f'Creating {socket_type} socket')

        # if socket_type.upper() not in self.SUPPORTED_SOCKET_TYPES:
        #     raise ValueError(f'Module does not support {socket_type} sockets')

        # sock = None
        # if socket_type.upper() == 'UDP':
        #     sock = self._create_upd_socket(port)

        # elif socket_type.upper() == 'TCP':
        #     sock = self._create_tcp_socket(port)

        # logger.info(f'{socket_type} socket created')

        # self.sockets[sock.socket_id] = sock

        # return sock

    def AT_get_radio_statistics(self):
        raise NotImplementedError
            
        result = self._send_command('AT+UCGED?', capture_urc=True)
        if result[0] != b'+UCGED: 2':
            raise ValueError("Unexpected response received: {}".format(result[0]))
        
        logger.info('Updating radio statistics')
        
        return result[1:]
    
    def AT_set_PSD_protocol_type(self, profile_id:int=0,protocol_type:PSDProtocolType=PSDProtocolType.IPV4):
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        self._send_command(f'AT+UPSD={profile_id},0,{protocol_type.value}',expected_reply=False)
        logger.info(f'PSD Protocol Type set to {protocol_type.name}')

    def AT_set_PSD_to_CID_mapping(self, profile_id:int=0, cid:int=1):
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        if cid not in range (0, 9):
            raise ValueError('CID must be between 0 and 8')
        self._send_command(f'AT+UPSD={profile_id},100,{cid}',expected_reply=False)
        logger.info(f'PSD Profile {profile_id} mapped to CID {cid}')

    def AT_PSD_action(self, profile_id:int=0, action:PSDAction=PSDAction.RESET):
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        self._send_command(f'AT+UPSDA={profile_id},{action.value}',expected_reply=False,timeout=180)
        logger.info(f'PSD Profile {profile_id} took action {action.name}')
    
    def AT_set_power_saving_uart_mode(self, mode:PowerSavingUARTMode=PowerSavingUARTMode.DISABLED, idle_optimization:bool=None, timeout:int=None):
        if idle_optimization is not None and mode == SaraR5Module.PowerSavingUARTMode.DISABLED:
            raise ValueError('Idle optimization can only be used with PowerSavingUARTMode other than DISABLED')
        if timeout is not None and mode != SaraR5Module.PowerSavingUARTMode.ENABLED and mode != SaraR5Module.PowerSavingUARTMode.ENABLED_2:
            raise ValueError('Timeout can only be used with PowerSavingUARTMode ENABLED or ENABLED_2')
        if timeout not in range (40, 65001):
            raise ValueError('Timeout must be between 40 and 65000')
        
        raise NotImplementedError
        #self._send_command(f'AT+UPSV={mode.value}',expected_reply=False)
    
    def AT_get_PSD_profile_status(self, profile_id:int=0, parameter:PSDParameters=PSDParameters.IP_ADDRESS):
        if profile_id not in range (0, 7):
            raise ValueError('Profile ID must be between 0 and 6')
        response_list = self._send_command(f'AT+UPSND={profile_id},{parameter.value}')

        if parameter == SaraR5Module.PSDParameters.IP_ADDRESS:
            self.psd["ip"] = response_list[2]
            logger.info(f'PSD Profile {profile_id} IP Address is {self.psd["ip"]}')

        if parameter == SaraR5Module.PSDParameters.ACTIVATION_STATUS:
            self.psd["is_active"] = bool(int(response_list[2]))
            logger.info(f'PSD Profile {profile_id} Activation Status is {self.psd["is_active"]}')

        #TODO: support other parameters, e.g. QoS.

    def AT_store_current_configuration(self, profile_id:int=0):
        if profile_id not in range (0, 2):
            raise ValueError('Profile ID must be between 0 and 1')

        self._send_command(f'AT&W{profile_id}',expected_reply=False)

        logger.info(f'Stored current configuration to profile {profile_id}')

    def AT_set_eDRX(self, mode:eDRXMode, access_technology:eDRXAccessTechnology, requested_eDRX_cycle:eDRXCycle, requested_PTW:eDRXCycle):
        self._send_command(f'AT+CEDRXS={mode.value},{access_technology.value},{requested_eDRX_cycle.value},{requested_PTW.value}',expected_reply=False)
        
        logger.info(f'eDRX configured with mode {mode.name}, access technology {access_technology.name}, requested eDRX cycle {requested_eDRX_cycle.name} and requested PTW {requested_PTW.name}')

    def AT_set_power_saving_mode_indication(self, enabled:bool):
        self._send_command(f'AT+UPSMR={int(enabled)}',expected_reply=False)
        logger.info(f'Power Saving Mode Indication set to {enabled}',expected_reply=False)

    def AT_set_signalling_connection_status_indication(self, enabled:bool):
        raise NotImplementedError

    def _create_upd_socket(self, port):
        raise NotImplementedError
        # at_command = f'{AT+USOCR=17}'
        # if port:
        #     at_command = at_command + f',{port}'
        # response = self._at_action(at_command, capture_urc=True)
        # socket_id = int(chr(response[0][-1]))
        # sock = UDPSocket(socket_id, self, port)
        # self.sockets[sock.socket_id] = sock
        # return sock
    
    def close_socket(self, socket_id):
        """
        Will send the correct AT action to close specified socket and remove
        the reference of it on the module object.
        """
        raise NotImplementedError
        # logger.info(f'Closing socket {socket_id}')
        # if socket_id not in self.sockets.keys():
        #     raise ValueError('Specified socket id does not exist')
        # result = self._at_action(f'{self.AT_CLOSE_SOCKET}={socket_id}')
        # del self.sockets[socket_id]
        # return result

    def send_udp_data(self, socket: int, host: str, port: int, data: str):
        """
        Send a UDP message
        """
        raise NotImplementedError
        # logger.info(f'Sending UDP message to {host}:{port}  :  {data}')
        # _data = binascii.hexlify(data.encode()).upper().decode()
        # length = len(data)
        # atc = f'AT+USOST={socket},"{host}",{port},{length},"{_data}"'
        # result = self._at_action(atc)
        # return result

    def read_udp_data(self, socket, length, timeout=10):
        """
        Reads data from a udp socket.

        ..note

            there is an issue on the R410 module that it is not issuing URCs
            So to get the data we poll for data until we get some.
        """
        raise NotImplementedError
        # start_time = time.time()
        # while True:
        #     time.sleep(2)
        #     data = self._at_action(f'AT+USORF={socket},{length}',
        #                            capture_urc=True)
        #     result = data[0].replace(b'"', b'').split(b',')[1:]  # remove URC
        #     if result[0]:  # the IP address part
        #         return result
        #     duration = time.time() - start_time
        #     if duration > timeout:
        #         break
        # logger.info('No UDP response read')
        # return None

    def set_listening_socket(self, socket: int, port: int):
        """Set a socket into listening mode to be able to receive data on
        the socket."""
        raise NotImplementedError
        # self._at_action(f'AT+USOLI={socket},{port}')

    def create_http_profile(self, profile_id, security_profile:SecurityProfile=None):
        """
        Create a HTTP client object
        """
        logger.debug(f'security_profile: {security_profile}')
        self.http_profiles[profile_id] = HTTPClient(profile_id, self, security_profile=security_profile)
        return self.http_profiles[profile_id]
    
    def create_security_profile(self, profile_id=0):
        self.security_profiles[profile_id] = SecurityProfile(profile_id, self)
        return self.security_profiles[profile_id]
    
    def AT_upload_to_filesystem(self, filename, length, data):
        """
        Upload data to the module's file system
        """
        SaraR5Module.validate_filename(filename)
        self._send_command(f'AT+UDWNFILE="{filename}",{length}',expected_reply=False, input_data=data)
        logger.info(f'Uploaded {length} bytes to {filename}')

    def upload_local_file_to_filesystem(self, filepath_in, filename_out, overwrite=False):
        file_exists = True
        try:
            self.AT_read_file_blocks(filename_out, 0, 0)
        except CMEError:
            file_exists = False

        if file_exists and not overwrite: raise ValueError(f'File {filename_out} already exists')
        if file_exists and overwrite: 
            self.AT_delete_file(filename_out)
    
        with open(filepath_in, 'rb') as f:
            data = f.read()
            length = len(data)
            self.AT_upload_to_filesystem(filename_out, length, data)

    def AT_read_file(self, filename, timeout=10):
        SaraR5Module.validate_filename(filename)
        return self._send_command(f'AT+URDFILE="{filename}"',timeout=timeout)
    
    def AT_read_file_blocks(self, filename, offset:int, length:int):
        SaraR5Module.validate_filename(filename)
        if not isinstance(offset, int) or not isinstance(length, int):
            raise ValueError('Offset and length must be integers')
                             
        return self._send_command(f'AT+URDBLOCK="{filename}",{offset},{length}')
    
    def AT_delete_file(self, filename):
        SaraR5Module.validate_filename(filename)

        self._send_command(f'AT+UDELFILE="{filename}"')
        logger.info(f'Deleted file {filename}')

    def _await_connection(self, roaming, timeout=180):
        """
        Continuously poll the connection
        status and see if the connection status has changed.
        """
        logging.info(f'Awaiting Connection')
        start_time = time.time()
        while True:
            time.sleep(2)
            self.AT_get_EPS_network_reg_status() #triggers URC

            if self.registration_status == 0:
                continue

            if roaming and self.registration_status == 5:
                break

            if (not roaming) and self.registration_status == 1:
                break

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'Could not connect')
            
    # def _at_action(self, at_command, timeout=10, capture_urc=False, binary_cmd=False, data_input=None, preserve_endings=False):
    #     """
    #     Small wrapper to issue a AT command. Will wait for the Module to return
    #     OK. Some modules return answers to AT actions as URC:s before the OK
    #     and to handle them as IRCs it is possible to set the capture_urc flag
    #     and all URCs between the at action and OK will be returned as result.
    #     """
    #     logger.debug(f'Applying AT Command: {at_command}')
    #     self._write(at_command,binary_cmd=binary_cmd)
    #     time.sleep(0.02)  # To give the end devices some time to answer.
    #     if data_input:
    #         self._read_line_until_contains(b'>', timeout=timeout,
    #                                          capture_urc=False)
    #         self._write(data_input)
        
    #     irc = self._read_line_until_contains(b'OK', timeout=timeout,
    #                                             capture_urc=capture_urc,preserve_endings=preserve_endings)
    #     if irc is not None:
    #         logger.debug(f'AT Command response = {irc}')
    #     return irc

    # def _write(self, data, binary_cmd=False):
    #     """
    #     Writing data to the module is simple. But it needs to end with \r\n
    #     to accept the command. The module will answer with an empty line as
    #     acknowledgement. If echo is enabled everything that the is sent to the
    #     module is returned in the serial line. So we just need to omit it from
    #     the acknowledge.
    #     """
    #     data_to_send = data
    #     if isinstance(data, str):  # if someone sent in a string make it bytes
    #         data_to_send = data.encode()

    #     if not data_to_send.endswith(b'\r\n'):
    #         # someone didnt add the CR an LN so we need to send it
    #         data_to_send += b'\r\n'

    #     # start_time = time.time()

    #     self._serial.write(data_to_send)
    #     time.sleep(0.02)  # To give the module time to respond.
    #     logger.debug(f'Sent: {data_to_send}')

    #     ack = self._serial.read_until()
    #     logger.debug(f'Recieved ack: {ack}')

    #     if self.echo:
    #         # when echo is on we will have recieved the message we sent and
    #         # will get it in the ack response read. But it will not send \n.
    #         # so we can omitt the data we send + i char for the \r
    #         #TODO ack can be \n or \r\n
    #         _echo = ack[:-1] if binary_cmd else ack[:-2] 
    #         wanted_echo = data_to_send[:-2] + b'\r'
    #         if _echo != wanted_echo:
    #             raise ValueError(f'Data echoed from module: {_echo} is not the '
    #                              f'same data as sent to the module. Expected echo: {wanted_echo}')
    #         ack = ack[len(wanted_echo):]

    #     wanted_ack = [b'\n',b'\r\n'] if binary_cmd else [b'\r\n']
    #     if ack == b'':
    #         raise ATAckMissingError(f'Ack was not received')
    #     if ack not in wanted_ack:
    #         raise ATAckError(f'Ack was not received properly, received {ack}, expected {wanted_ack}')
        
    # @staticmethod
    # def _remove_line_ending(line: bytes):
    #     """
    #     To not have to deal with line endings in the data we can use this to
    #     remove them.
    #     """
    #     if line.endswith(b'\r\n'):
    #         return line[:-2]
    #     else:
    #         return line
        
    # def _read_line_until_contains(self, slice, capture_urc=False, timeout=5, preserve_endings=False):
    #     """
    #     Similar to read_until, but will read whole lines so we can use proper
    #     timeout management. Any URC:s that is read will be handled and we will
    #     return the IRC:s collected. If capture_urc is set we will return all
    #     data as IRCs.
    #     """
    #     _slice = slice
    #     if isinstance(slice, str):
    #         _slice = slice.encode()

    #     data_list = list()
    #     irc_list = list()
    #     start_time = time.time()
    #     while True:
    #         try:
    #             data = self._serial.read_until()
    #         except serial.SerialTimeoutException:
    #             # continue to read lines until AT Timeout
    #             duration = time.time() - start_time
    #             if duration > timeout:
    #                 raise ATTimeoutError
    #             continue
    #         line_stripped = self._remove_line_ending(data) 
    #         line = data if preserve_endings else line_stripped

    #         if line_stripped.startswith(b'+'):
    #             if capture_urc:
    #                 irc_list.append(line_stripped)  # add the urc as an irc
    #             else:
    #                 self._process_urc(line_stripped)

    #         elif line_stripped == b'OK':
    #             pass

    #         elif line_stripped.startswith(b'ERROR'):
    #             raise ATError('Error on AT Command')

    #         elif line_stripped == b'':
    #             pass

    #         else:
    #             irc_list.append(line)  # the can only be an IRC

    #         if _slice == line_stripped:              
    #             data_list.append(line)
    #             break
    #         else:
    #             data_list.append(line)

    #         duration = time.time() - start_time
    #         if duration > timeout:
    #             raise ATTimeoutError

    #     clean_list = [response for response in data_list if not response == b'']

    #     logger.debug(f'Received: {clean_list}')

    #     return irc_list
    
    def toggle_power(self, at_test=True):
        """
        Toggles the power of the module
        """
        #TODO: read current power state from VIN
        self.power_toggle() 
        #TODO: _await_vin() to check if power has been toggled
        #TODO: if power is on, wait for AT command reply
        #loop until AT returns OK
        while at_test:
            try: 
                self._send_command('AT',expected_reply=False,timeout=1)
                break 
            except ATTimeoutError:
                continue 
    
    @staticmethod
    def _parse_udp_response(message: bytes):
        raise NotImplementedError
        # _message = message.replace(b'"', b'')
        # socket, ip, port, length, _data, remaining_bytes = _message.split(b',')
        # data = bytes.fromhex(_data.decode())
        # return data

    def _process_urc(self, urc: bytes):
        """
        URC = unsolicited result code
        When waiting on answer from the module it is possible that the module
        sends urcs via +commands. So after the urcs are
        collected we run this method to process them.
        """

        _urc = urc.decode()
        logger.debug(f'Processing URC: {_urc}')
        urc_id = _urc[1:_urc.find(':')]
        if urc_id == 'CSCON':
            self._update_connection_status_callback(urc)
        elif urc_id == 'CEREG':
            self._update_eps_reg_status_callback(urc)
        elif urc_id == 'CGPADDR':
            self._update_ip_address_callback(urc)
        elif urc_id == 'NSONMI':
            self._add_available_message_callback(urc)
        elif urc_id == 'UUHTTPCR':
            HTTPClient._update_http_callback(self, urc)
        elif urc_id == 'CME ERROR':
            self._handle_cme_error(urc)
        else:
            logger.debug(f'Unhandled urc: {urc}')

    def _handle_cme_error(self, urc: bytes):
        """
        Callback to raise CME Error.
        """
        raise CMEError(urc.decode())

    def _add_available_message_callback(self, urc: bytes):
        """
        Callback to handle recieved messages.
        """
        _urc, data = urc.split(b':')
        result = data.lstrip()
        logger.debug(f'Recieved data: {result}')
        self.available_messages.append(result)

    def update_radio_statistics(self):
        """
        Read radio statistics and update the module object.
        """
        radio_data = self.AT_get_radio_statistics()
        self._parse_radio_stats(radio_data)

    def _update_connection_status_callback(self, urc):
        """
        In the AT urc +CSCON: 1 the last char is indication if the
        connection is idle or connected
        """
        status = bool(int(urc[-1]))
        self.connected = status
        logger.info(f'Changed the connection status to {status}')

    def _update_eps_reg_status_callback(self, urc):
        """
        The command could return more than just the status.
        Maybe a regex would be good
        But for now we just check the last as int
        """
        status = int(chr(urc[-1]))
        self.registration_status = status
        logger.info(f'Updated status EPS Registration = {status}')

    def _update_ip_address_callback(self, urc: bytes):
        """
        Update the IP Address of the module
        """
        # TODO: this is per socket. Need to implement socket handling
        _urc = urc.decode()
        ip_addr = _urc[(_urc.find('"') + 1):-1]
        self.ip = ip_addr
        logger.info(f'Updated the IP Address of the module to {ip_addr}')

    def _parse_radio_stats(self, radio_data):
        """
        Parser for radio statistic result
        """
        def translate_rsrq(rsrq):
            rsrq = int(rsrq)
            if rsrq == 255:
                return None
            elif rsrq == 46:
                return 2.5
            elif 35 <= rsrq <= 45:
                return -3 + (rsrq - 35) * 0.05
            elif 1 <= rsrq <= 33:
                return -19.5 + (rsrq - 1) * 0.5
            elif -29 <= rsrq <= -1:
                return -34 + (rsrq + 29) * 0.5
            elif rsrq == -30:
                return -34
            else:
                return None  # for any other value

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

        translated_meta = {key_mappings.get(key, key): value for key, value in parsed_metadata.items()}
        translated_stats = {key_mappings.get(key, key): value for key, value in parsed_stats.items()}

        translated_meta['radio_access_technology'] = SaraR5Module.CurrentRadioAccessTechnology(int(translated_meta['radio_access_technology'])).name
        translated_meta['radio_service_state'] = SaraR5Module.CurrentRadioServiceState(int(translated_meta['radio_service_state'])).name
        translated_stats['LTE_radio_resource_control_state'] = SaraR5Module.LTERadioResourceControlState(int(translated_stats['LTE_radio_resource_control_state'])).name
        
        if int(translated_stats['RSRP']) == 255: translated_stats['RSRP'] = None
        else: translated_stats['RSRP'] = int(translated_stats['RSRP']) - 141
        if int(translated_stats['avg_rsrp']) == 255: translated_stats['avg_rsrp'] = None
        else: translated_stats['avg_rsrp'] = int(translated_stats['avg_rsrp']) - 141
        translated_stats['RSRQ'] = translate_rsrq(translated_stats['RSRQ'])

        self.radio_status=translated_meta
        self.radio_stats=translated_stats

    def __repr__(self):
        return f'NBIoTModule(serial_port="{self._serial_port}")'
    
    def validate_filename(filename):
        invalid_chars = ['/', '*', ':', '%', '|', '"', '<', '>', '?']
        length_minimum = 1
        length_maximum = 248
        if len(filename) > length_maximum:
            raise ValueError(f'Filename must be less than {length_maximum} characters')
        if len(filename) == length_minimum:
            raise ValueError('Filename must be at least {length_minimum} characters long')
        if filename.startswith('.'):
            raise ValueError('Filename cannot start with a period')

        for char in invalid_chars:
            if char in filename:
                raise ValueError(f'Invalid character {char} in filename') 
    
    def _read_from_device(self):
        while not self.terminate:
            data = self._serial.readline()
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            if data:
                timestamp = datetime.datetime.now()
                data_with_timestamp = (data, timestamp)
                if any(data.decode().startswith(prefix) for prefix in self.urc_mappings.keys()):
                    logger.debug(f"URC:{chr(10)}          {timestamp}: {data}")
                    urc = data.split(b":")[0].decode()
                    urc_data = data.split(b":")[1].decode()
                    handler_function = self.urc_mappings[urc]
                    handler_function(urc_data)
                elif data == b'':
                    pass
                else:
                    self.queue.put(data_with_timestamp)

    def handle_uupsdd(self, data):
        # Handle unsolicited result code (URC) here
        # with self.lock:
        #     self.disconnected = True
        pass
    def handle_cereg(self, data):
        # CEREG is a special case, may be a read response or a URC. Some logic needed to determine which.
        data = data.lstrip('\r\n').split(",")
        Mode = None

        if int(data[0]) not in [0,1]: 
            mode = "Read"
        elif len(data) > 2:
            mode = "URC"
        elif len(data) == 2:
            mode = "Read"
        else:
            assert(len(data) == 1)
            mode = "URC" 
        
        read_parameters=["mode","registration_status","tracking_area_code","cell_id","access_tech","reject_cause_type","assigned_active_time","assigned_tau","rac_or_mme"]
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
                parsed_result[key] = SaraR5Module.EPSNetworkRegistrationReportConfig(int(value))
            if key == "registration_status":
                parsed_result[key] = SaraR5Module.EPSNetworkRegistrationStatus(int(value))
            if key == "tracking_area_code":
                parsed_result[key] = str(value)
            if key == "cell_id":
                parsed_result[key] = str(value)
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


        logger.info(f"CEREG {mode} response: {parsed_result}")

    def _send_command(self, command:str, expected_reply=True, input_data:bytes=None, timeout=10):
        """
        expected reply is None, str or bool
            str:            reply expected with prefix (e.g. "UPSND")
            bool(True):     reply expected with prefix matching command (e.g. "AT+UPSND=0,8" expects "+UPSND: 0,8")
            bool(False):    no reply expected
        """

        if not isinstance(expected_reply, (bool, str)): 
            raise TypeError("expected_reply is not of type bool or str")
       
        result = None 
        got_ok = False
        got_reply = False
        debug_log = []

        if expected_reply == True:
            expected_reply_bytes = command.lstrip("AT").split("=")[0].encode() + b":"
        if expected_reply == False:
            got_reply=True
        if isinstance(expected_reply,str):
            expected_reply_bytes = b"+" + expected_reply.encode() + b":"

        command_unterminated = command.encode().rstrip(b"\r\n")
        command = command_unterminated + b"\r\n"

        self._serial.write(command)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        logger.debug(f"Sent:{chr(10)}          {timestamp}: {command}")
        
        timeout_time = time.time() + timeout
        try:
            while not (got_ok and got_reply):
                time_remaining = timeout_time - time.time()
                if time_remaining <= 0:
                    raise ATTimeoutError("Timeout waiting for response")
                try:
                    response_with_timestamp:tuple = self.queue.get(timeout=time_remaining)
                    response, timestamp = response_with_timestamp
                    debug_log.append((timestamp, response))
                except queue.Empty:
                    continue

                if response.startswith(b"OK"):
                    got_ok = True
                elif expected_reply != False and response.startswith(expected_reply_bytes):
                    got_reply = True
                    result = response.lstrip(expected_reply_bytes).rstrip(b"\r\n").decode().strip().split(",")
                elif response.startswith(b"ERROR"):
                    raise ATError
                elif response.startswith(b"+CME ERROR:"):
                    code = response.lstrip(b"+CME ERROR:").rstrip(b"\r\n").decode()
                    raise CMEError(code) #TODO: convert code to error message
                elif response == b"\r\n" or response.startswith(command_unterminated): # ack or echo
                    pass
                elif input_data and len(input_data) > 0 and response.startswith(b">"):
                    self.ser.write(input_data)
                else:
                    logger.warn(f'WARNING: got unexpected {response}')
        except Exception as e:
            raise e
        finally:
            output = '\n          '.join([f'{timestamp.strftime("%Y-%m-%d_%H-%M-%S")}: {response}' for timestamp, response in debug_log])
            logger.debug(f"Received:{chr(10)}          {output}")

        return result
