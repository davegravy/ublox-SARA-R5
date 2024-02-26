import time
import serial
import binascii
import validators

from enum import Enum
import logging

from ublox.http import HTTPClient, SecurityProfile
#from ublox.socket import UDPSocket
from collections import namedtuple

logger = logging.getLogger(__name__)

Stats = namedtuple('Stats', 'type name value')


class CMEError(Exception):
    """CME ERROR on Module"""


class ATError(Exception):
    """AT Command Error"""


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

    class PDPType(Enum):
        IPV4 = 'IP'
        NONIP = 'NONIP'
        IPV4V6 = 'IPV4V6'
        IPV6 = 'IPV6'

    SUPPORTED_SOCKET_TYPES = ['UDP', 'TCP']
    
    def __init__(self, serial_port: str, baudrate=115200, rtscts=False, roaming=False, echo=True):
        self._serial_port = serial_port
        self._serial = serial.Serial(self._serial_port, baudrate=baudrate, rtscts=rtscts,bytesize=8,parity='N',stopbits=1,timeout=5)
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
        self.radio_signal_power = None
        self.radio_total_power = None
        self.radio_tx_power = None
        self.radio_tx_time = None
        self.radio_rx_time = None
        self.radio_cell_id = None
        self.radio_ecl = None
        self.radio_snr = None
        self.radio_earfcn = None
        self.radio_pci = None
        self.radio_rsrq = None
        self.radio_rsrp = None
        self.current_rat = None

    def setup(self, radio_mode='LTE-M'):
        """
        Running all commands to get the module up an working
        """
        #TODO: check GPIO and power on if needed

        self.read_imei()
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

        self._at_action(f'AT+UDCONF=1,{mode}')  # Set data format to HEX
        logger.info(f'{mode.name} set to {mode.value}')

    def AT_read_imei(self):
        logger.info(f'Reading IMEI from module')
        result = self._at_action('AT+CGSN')
        self.imei = int(result[0])

    def AT_set_error_format(self, format: ErrorFormat=ErrorFormat.DISABLED):
        self._at_action('AT+CMEE={format}')  # enable verbose errors
        logger.info('Verbose errors enabled')

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

    def AT_set_psm_mode(self):
        raise NotImplementedError
    #     #TODO: AT+NPSMR doesn't exist?    
    #     """
    #     Enable Power Save Mode
    #     """
    #     self._at_action(self.AT_ENABLE_POWER_SAVING_MODE)
    #     logger.info('Enabled Power Save Mode')

    def AT_config_signaling_connection_urc(self,config:SignallingConnectionStatusReportConfig=SignallingConnectionStatusReportConfig.DISABLED):
        """
        Configure Signaling Connection URC
        """
        self._at_action(f'AT+CSCON={config.value}')
        logger.info(f'{config.name} set to {config.value}')

    def AT_config_EPS_network_reg_urc(self, config:EPSNetworkRegistrationReportConfig=EPSNetworkRegistrationReportConfig.DISABLED):
        """
        Configure EPS Network Registration URC
        """
        self._at_action(f'AT+CEREG={config.value}')
        logger.info(f'{config.name} set to {config.value}')

    def AT_set_module_functionality(self,function:ModuleFunctionality=ModuleFunctionality.FULL_FUNCTIONALITY,reset:bool=False):

        if reset and function not in [SaraR5Module.ModuleFunctionality.FULL_FUNCTIONALITY,SaraR5Module.ModuleFunctionality.AIRPLANE_MODE]:
            raise ValueError('Reset can only be used with FULL_FUNCTIONALITY or AIRPLANE_MODE')
        
        self._at_action(f'AT+CFUN={function},{int(reset)}')
        logger.info(f'Module Functionality set to {function.name} with reset {reset}')

    def AT_read_module_functionality(self):
        raise NotImplementedError
    

    def AT_set_radio_mode(self, mode:RadioAccessTechnology=RadioAccessTechnology.LTE_CAT_M1):

        response = self._at_action(f'AT+URAT={mode.value}')

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
    
        self._at_action(f'AT+CGDCONT={cid},"{pdp_type}","{apn}","{pdp_address}",{int(data_compression)},{int(header_compression)}')
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
            
        result = self._at_action('AT+UCGED?', capture_urc=True)
        if result[0] != b'+UCGED: 2':
            raise ValueError("Unexpected response received: {}".format(result[0]))
        
        logger.info('Updating radio statistics')
        
        return result[1:]
        
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
        self._at_action(f'AT+UDWNFILE="{filename}",{length}', data_input=data)
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
        return self._at_action(f'AT+URDFILE="{filename}"',timeout=timeout, capture_urc=True, binary_cmd=True, preserve_endings=True)
    
    def AT_read_file_blocks(self, filename, offset:int, length:int):
        SaraR5Module.validate_filename(filename)
        if not isinstance(offset, int) or not isinstance(length, int):
            raise ValueError('Offset and length must be integers')
                             
        return self._at_action(f'AT+URDBLOCK="{filename}",{offset},{length}',binary_cmd=True)
    
    def AT_delete_file(self, filename):
        SaraR5Module.validate_filename(filename)

        self._at_action(f'AT+UDELFILE="{filename}"')
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
            self._at_action('AT+CEREG?')

            if self.registration_status == 0:
                continue

            if roaming and self.registration_status == 5:
                break

            if (not roaming) and self.registration_status == 1:
                break

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'Could not connect')
            
    def _at_action(self, at_command, timeout=10, capture_urc=False, binary_cmd=False, data_input=None, preserve_endings=False):
        """
        Small wrapper to issue a AT command. Will wait for the Module to return
        OK. Some modules return answers to AT actions as URC:s before the OK
        and to handle them as IRCs it is possible to set the capture_urc flag
        and all URCs between the at action and OK will be returned as result.
        """
        logger.debug(f'Applying AT Command: {at_command}')
        self._write(at_command,binary_cmd=binary_cmd)
        time.sleep(0.02)  # To give the end devices some time to answer.
        if data_input:
            self._read_line_until_contains(b'>', timeout=timeout,
                                             capture_urc=False)
            self._write(data_input)
        
        irc = self._read_line_until_contains(b'OK', timeout=timeout,
                                                capture_urc=capture_urc,preserve_endings=preserve_endings)
        if irc is not None:
            logger.debug(f'AT Command response = {irc}')
        return irc

    def _write(self, data, binary_cmd=False):
        """
        Writing data to the module is simple. But it needs to end with \r\n
        to accept the command. The module will answer with an empty line as
        acknowledgement. If echo is enabled everything that the is sent to the
        module is returned in the serial line. So we just need to omit it from
        the acknowledge.
        """
        data_to_send = data
        if isinstance(data, str):  # if someone sent in a string make it bytes
            data_to_send = data.encode()

        if not data_to_send.endswith(b'\r\n'):
            # someone didnt add the CR an LN so we need to send it
            data_to_send += b'\r\n'

        # start_time = time.time()

        self._serial.write(data_to_send)
        time.sleep(0.02)  # To give the module time to respond.
        logger.debug(f'Sent: {data_to_send}')

        ack = self._serial.read_until()
        logger.debug(f'Recieved ack: {ack}')

        if self.echo:
            # when echo is on we will have recieved the message we sent and
            # will get it in the ack response read. But it will not send \n.
            # so we can omitt the data we send + i char for the \r
            #TODO ack can be \n or \r\n
            _echo = ack[:-1] if binary_cmd else ack[:-2] 
            wanted_echo = data_to_send[:-2] + b'\r'
            if _echo != wanted_echo:
                raise ValueError(f'Data echoed from module: {_echo} is not the '
                                 f'same data as sent to the module. Expected echo: {wanted_echo}')
            ack = ack[len(wanted_echo):]

        wanted_ack = [b'\n',b'\r\n'] if binary_cmd else [b'\r\n']
        if ack not in wanted_ack:
            raise ValueError(f'Ack was not received properly, received {ack}, expected {wanted_ack}')
        
    @staticmethod
    def _remove_line_ending(line: bytes):
        """
        To not have to deal with line endings in the data we can use this to
        remove them.
        """
        if line.endswith(b'\r\n'):
            return line[:-2]
        else:
            return line
        
    def _read_line_until_contains(self, slice, capture_urc=False, timeout=5, preserve_endings=False):
        """
        Similar to read_until, but will read whole lines so we can use proper
        timeout management. Any URC:s that is read will be handled and we will
        return the IRC:s collected. If capture_urc is set we will return all
        data as IRCs.
        """
        _slice = slice
        if isinstance(slice, str):
            _slice = slice.encode()

        data_list = list()
        irc_list = list()
        start_time = time.time()
        while True:
            try:
                data = self._serial.read_until()
            except serial.SerialTimeoutException:
                # continue to read lines until AT Timeout
                duration = time.time() - start_time
                if duration > timeout:
                    raise ATTimeoutError
                continue
            line_stripped = self._remove_line_ending(data) 
            line = data if preserve_endings else line_stripped

            if line_stripped.startswith(b'+'):
                if capture_urc:
                    irc_list.append(line_stripped)  # add the urc as an irc
                else:
                    self._process_urc(line_stripped)

            elif line_stripped == b'OK':
                pass

            elif line_stripped.startswith(b'ERROR'):
                raise ATError('Error on AT Command')

            elif line_stripped == b'':
                pass

            else:
                irc_list.append(line)  # the can only be an IRC

            if _slice == line_stripped:              
                data_list.append(line)
                break
            else:
                data_list.append(line)

            duration = time.time() - start_time
            if duration > timeout:
                raise ATTimeoutError

        clean_list = [response for response in data_list if not response == b'']

        logger.debug(f'Received: {clean_list}')

        return irc_list
    
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
