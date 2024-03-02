import random
import string
import logging
import json
import copy
import time
from enum import Enum
from urllib.parse import urlparse
import validators

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from modules import SaraR5Module, ATError

logger = logging.getLogger(__name__)


class SecurityProfile:

    class CAValidationLevel(Enum):
        LEVEL_0_NONE = 0
        LEVEL_1_LOCAL_CERT_CHECK = 1
        LEVEL_2_URL_INTEGRITY_CHECK = 2
        LEVEL_3_EXPIRY_DATE_CHECK = 3

    class TLSVersion(Enum):
        ANY = 0
        TLS_1_0 = 1
        TLS_1_1 = 2
        TLS_1_2 = 3
        TLS_1_3 = 4

    class CertificateType(Enum):
        CA_CERT = 0
        CLIENT_CERT = 1
        CLIENT_PRIVATE_KEY = 2


    def __init__(self, profile_id, module:'SaraR5Module'):
        if profile_id not in range(0,4): raise ValueError("Profile id must be between 0 and 4")

        self.profile_id = profile_id
        self._module = module
        self.AT_reset_security_profile()

    def upload_cert_key(self, filepath, type:CertificateType, internal_name=None, filename_out=None):
        if not filename_out: filename_out = filepath.split('/')[-1]
        if not internal_name: internal_name = filename_out.split('.')[0]
        self._module.upload_local_file_to_filesystem(filepath, filename_out, overwrite=True)
        #TODO: condense after firmware update?
        # with open(filepath, 'rb') as f:
        #     data = f.read()
        #     length = len(data)
        #     self._at_action(f'AT+USECMNG=0,{type.value},"{type.name}_{profile_id}",{length}', binary_input=True)
        #     self._write(data)
        #self._at_action(f'AT+USECMNG=1,{type.value},"{internal_name}","{filename_out}"',capture_urc=True)
        SecurityProfile.AT_import_cert_from_file(self._module, type, internal_name, filename_out)
        logger.info(f'Uploaded file {filepath} to {type.value} "{internal_name}"')
        return internal_name
    
    def configure_security_profile(self, hostname, ca_cert=None, client_cert=None, client_key=None, ca_validation_level=CAValidationLevel.LEVEL_1_LOCAL_CERT_CHECK, tls_version=TLSVersion.TLS_1_2, sni:bool=True):
        self.AT_reset_security_profile()
        self.AT_set_ca_validation_level(ca_validation_level)
        self.AT_set_tls_version(tls_version)
        #TODO: legacy cipher suite
        self.AT_set_ca_validation_server_hostname(hostname)
        if sni: 
            self.AT_set_server_name_indication(hostname)

        if ca_cert:
            if SecurityProfile.AT_get_cert_md5(self._module,SecurityProfile.CertificateType.CA_CERT, ca_cert) is None:
                raise ValueError(f'Invalid CA Cert: {ca_cert}, did you upload it?')
            self.AT_set_ca_cert(ca_cert)
        if client_cert:
            if SecurityProfile.AT_get_cert_md5(self._module,SecurityProfile.CertificateType.CLIENT_CERT, client_cert) is None:
                raise ValueError(f'Invalid Client Cert: {client_cert}, did you upload it?')
            self.AT_set_client_cert(client_cert)
        if client_key:
            if SecurityProfile.AT_get_cert_md5(self._module,SecurityProfile.CertificateType.CLIENT_PRIVATE_KEY, client_key) is None:
                raise ValueError(f'Invalid Client Key: {client_key}, did you upload it?')
            self.AT_set_client_key(client_key)

    def AT_reset_security_profile(self):
        self._module._send_command(f'AT+USECPRF={self.profile_id}') # reset
        self.hostname_ca_validation = ""
        self.hostname_sni = ""

        logger.info(f"Reset security profile {self.profile_id}")

    def AT_set_ca_validation_level(self, level:CAValidationLevel=CAValidationLevel.LEVEL_1_LOCAL_CERT_CHECK):
        self._module._send_command(f'AT+USECPRF={self.profile_id},0,{level.value}')
        logger.info(f"Set CA validation level to {level.name} for security profile {self.profile_id}")
    
    def AT_set_tls_version(self, version:TLSVersion=TLSVersion.TLS_1_2):
        self._module._send_command(f'AT+USECPRF={self.profile_id},1,{version.value}')
        logger.info(f"Set TLS version to {version.name} for security profile {self.profile_id}")

    def AT_set_ca_validation_server_hostname(self, hostname:str=""):
        if len(hostname) > 256:
            raise ValueError("Server hostname must be 256 characters or less")
        if not validators.domain(hostname):
            raise ValueError("Invalid server hostname")

        self._module._send_command(f'AT+USECPRF={self.profile_id},4,"{hostname}"')
        self.hostname_ca_validation = hostname
        logger.info(f'Set CA validation server hostname to "{hostname}" for security profile {self.profile_id}')
    
    def AT_set_server_name_indication(self, sni=""):
        if len(sni) > 128:
            raise ValueError("Server name indication must be 128 characters or less")
        if not validators.domain(sni):
            raise ValueError("Invalid server hostname")

        self._module._send_command(f'AT+USECPRF={self.profile_id},10,"{sni}"')
        self.hostname_sni = sni
        logger.info(f'Set server name indication to "{sni}" for security profile {self.profile_id}')

    def AT_set_ca_cert(self, internal_name:str=""):
    # DOES NOT ERROR IF CERT DOES NOT EXIST, VALIDATE BEFORE CALLING THIS
        SecurityProfile.validate_cert_name(internal_name)
        
        self._module._send_command(f'AT+USECPRF={self.profile_id},3,"{internal_name}"')
        logger.info(f'Set CA cert to "{internal_name}" for security profile {self.profile_id}')

    def AT_set_client_cert(self, internal_name:str=""):
    # DOES NOT ERROR IF CERT DOES NOT EXIST, VALIDATE BEFORE CALLING THIS
        SecurityProfile.validate_cert_name(internal_name)
        
        self._module._send_command(f'AT+USECPRF={self.profile_id},5,"{internal_name}"')
        logger.info(f'Set client cert to "{internal_name}" for security profile {self.profile_id}')

    def AT_set_client_key(self, internal_name:str=""):
    # DOES NOT ERROR IF CERT DOES NOT EXIST, VALIDATE BEFORE CALLING THIS
        SecurityProfile.validate_cert_name(internal_name)
        
        self._module._send_command(f'AT+USECPRF={self.profile_id},6,"{internal_name}"')
        logger.info(f'Set client key to "{internal_name}" for security profile {self.profile_id}')
    
    def AT_get_cert_md5(module:'SaraR5Module', type:CertificateType, internal_name):
        from ublox import modules
        SecurityProfile.validate_cert_name(internal_name)
        
        # errors if not found or invalid
        try:
            result = module._send_command(f'AT+USECMNG=4,{type.value},"{internal_name}"',capture_urc=True)
        except modules.ATError:
            return None
        
        return result[0].decode().split(',')[3].strip('"')
    
    def AT_import_cert_from_file(module:'SaraR5Module', type:CertificateType, internal_name:str, filename:str):
        from ublox.modules import SaraR5Module
        SecurityProfile.validate_cert_name(internal_name)
        SaraR5Module.validate_filename(filename)
        
        module._send_command(f'AT+USECMNG=1,{type.value},"{internal_name}","{filename}"',capture_urc=True)
        logger.info(f'Imported {type.name} from file "{filename}" to internal name {internal_name}')

    def validate_cert_name(internal_name:str):
        if len(internal_name) > 200:
            raise ValueError("Internal name must be 200 characters or less")

        if not internal_name:
            raise ValueError("Internal name must be set")

        # if not validators.slug(internal_name):
        #     raise ValueError("Internal name must be a valid slug")

class HTTPClient:

    class HTTPSConfig(Enum):
        DISABLED = 0
        ENABLED = 1

    class ContentType(Enum):
        APPLICATION_X_WWW_FORM_URLENCODED = 0
        TEXT_PLAIN = 1
        APPLICATION_OCTET_STREAM = 2
        MULTIPART_FORM_DATA = 3
        APPLICATION_JSON = 4
        APPLICATION_XML = 5
        USER_DEFINED = 6

    def __init__(self, profile_id:int, module:'SaraR5Module', security_profile:SecurityProfile=None):
        if profile_id not in range(0,3): raise ValueError("Profile id must be between 0 and 3")
        if not module: raise ValueError("Module must be set")

        self.profile_id = profile_id
        self._module = module
        self.AT_reset_http_profile()
        self.security_profile = security_profile

    def set_server_params(self, hostname=None, ip=None, port=80, ssl=False, timeout=180, headers={}):
        #we can only use hostname or ip, not both. Hostname overrides ip
        ssl = HTTPClient.HTTPSConfig.ENABLED if ssl else HTTPClient.HTTPSConfig.DISABLED

        if hostname:
            self.AT_set_http_server_hostname(hostname)
        else:
            if not ip: raise ValueError("Either hostname or ip must be set")
            self.AT_set_http_server_ip(ip)


        self.AT_set_http_server_port(port)        
        self.AT_set_http_ssl(ssl, self.security_profile.profile_id if self.security_profile else None)
        self.AT_set_http_timeout(timeout)
        self.set_header_string(headers)

    def set_header_string(self, headers):

        if len(headers.keys()) > 5: raise ValueError("Too many headers. Max 5 headers allowed")
        for header_id in range(0,4):
            if header_id > len(headers.keys()) - 1:
                #clear the header id
                header_string = str(header_id) + ":"
            else:
                key = list(headers.keys())[header_id] 
                value = headers[key] 
                if len(key) + len(value) > 64: raise ValueError("Header key and value must be less than 64 characters")
                header_string = str(header_id) + ":" + key + ":" + value
            self.AT_set_http_header(header_string)

    def get(self, server_path="/"):
        data = None

        filename = ''.join(random.choice(string.ascii_lowercase) for i in range(10))
        self.AT_http_get(server_path, filename)
        if self.error:
            error_class, error_code = self.AT_http_get_error()
            if error_class == 3:
                self.error_code = error_code
                error_description = self.error_code_description
                logger.error(f'HTTP GET failed with error code {error_code}: {error_description}')
            
        else:
            data = self._module.AT_read_file(filename,timeout=60)

        self._module.AT_delete_file(filename)
        return HTTPResponse(data, copy.copy(self))
    
    def post(self, payload_file, content_type, server_path="/"):
        data = None

        payload_filename = payload_file.split('/')[-1]
        result_filename = ''.join(random.choice(string.ascii_lowercase) for i in range(10))
        self._module.upload_local_file_to_filesystem(payload_file, payload_filename, overwrite=True) # timeout for large files?
        self.AT_http_post(server_path, result_filename, payload_filename, content_type)
        if self.error:
            error_class, error_code = self.AT_http_get_error()
            if error_class == 3:
                self.error_code = error_code
                error_description = self.error_code_description
                logger.error(f'HTTP POST failed with error code {error_code}: {error_description}')
        else:
            
            data = self._module.AT_read_file(result_filename,timeout=60)
        
        self._module.AT_delete_file(payload_filename)
        self._module.AT_delete_file(result_filename)
        
        return HTTPResponse(data,copy.copy(self))
    
    @property
    def error_code_description(self):

        error_codes = {
        0: "No error",
        1: "Invalid profile ID",
        2: "Invalid input",
        3: "Server hostname too long",
        4: "Invalid server hostname",
        5: "Invalid server IP address",
        6: "Invalid authorization method",
        7: "Server missing",
        8: "Username length exceeded",
        9: "Password length exceeded",
        10: "Internal error",
        11: "Server connection error",
        12: "Error occurred in HTTP request",
        13: "Internal error",
        14: "Internal error",
        15: "Invalid POST data size",
        16: "Empty FFS filename",
        17: "Invalid FFS file length",
        18: "Invalid content-type specified",
        19: "Internal error",
        20: "Internal error",
        21: "Internal error",
        22: "PSD or CSD connection not established",
        23: "Server or proxy hostname lookup failed",
        24: "User authentication failed on server",
        25: "User authentication failed on proxy",
        26: "Connection timed out",
        27: "Request prepare timeout expired",
        28: "Response receive timeout expired",
        29: "Request send timeout expired",
        30: "HTTP operation in progress",
        31: "Invalid HTTP parameter TCP port not in range (1-65535)",
        32: "Invalid HTTP parameter secure",
        33: "Invalid HTTP parameter authentication username",
        34: "Invalid HTTP parameter authentication password",
        35: "Invalid HTTP parameter output filename",
        36: "Invalid HTTP parameter output filename length",
        37: "Invalid HTTP parameter server path",
        38: "Invalid HTTP parameter server path length",
        39: "Invalid HTTP parameter content filename length",
        40: "Invalid custom content type string",
        41: "Output file open error",
        42: "Output file close error",
        43: "Output file write error",
        44: "Connection lost",
        45: "Operation not allowed in current state",
        73: "Secure socket connect error"
        }

        # For error codes 46 to 72, the description is "Internal error"
        for i in range(46, 73):
            error_codes[i] = "Internal error"

        return error_codes[self.error_code]
    
    def AT_reset_http_profile(self):
        self._module._send_command(f'AT+UHTTP={self.profile_id}')
        self.hostname = ""
        self.port = 80
        self.ssl = False
        self.server_path = ""
        self.security_profile = None
        self.completed = False
        self.error = False

        logger.info(f"Reset HTTP profile {self.profile_id}")

    def AT_set_http_server_ip(self, ip:str):
        if not validators.ipv4(ip): raise ValueError("Invalid IPV4 address")

        self._module._send_command(f'AT+UHTTP={self.profile_id},0,"{ip}"')
        logger.info(f"Set HTTP server IP to {ip} for HTTP profile {self.profile_id}") 

    def AT_set_http_server_hostname(self, hostname):
        if len(hostname) not in range (1,1024): raise ValueError("Hostname must be 1 to 1024")
        if not validators.domain(hostname): raise ValueError("Invalid hostname")

        self._module._send_command(f'AT+UHTTP={self.profile_id},1,"{hostname}"')
        self.hostname = hostname
        logger.info(f'Set HTTP server hostname to "{hostname}" for HTTP profile {self.profile_id}')

    def AT_set_http_server_port(self, port:int):
        if port < 1 or port > 65535: raise ValueError("Port must be between 1 and 65535")
        if not isinstance(port, int): raise ValueError("Port must be an integer")

        self._module._send_command(f'AT+UHTTP={self.profile_id},5,{port}')
        self.server_port = port
        logger.info(f"Set HTTP server port to {port} for HTTP profile {self.profile_id}")
    
    def AT_set_http_ssl(self, ssl:HTTPSConfig=HTTPSConfig.DISABLED, security_profile_id:int=None):
        if not (security_profile_id == None or security_profile_id in range(0,3)): 
            raise ValueError("Security profile id must be None or and int between 0 and 3")
        if ssl == HTTPClient.HTTPSConfig.DISABLED and security_profile_id is not None:
            raise ValueError("Security profile id must be None if SSL is disabled")

        at_command = f'AT+UHTTP={self.profile_id},6,{ssl.value}'
        logger.debug(f'security_profile_id: {security_profile_id}')
        if isinstance(security_profile_id, int):
            at_command = at_command + f',{security_profile_id}'
        self._module._send_command(at_command)
        self.ssl = ssl == HTTPClient.HTTPSConfig.ENABLED
        logger.info(f"Set HTTP SSL to {ssl.name} for HTTP profile {self.profile_id}")

    def AT_set_http_timeout(self, timeout:int=180):
        if not timeout in range(30,180): 
            raise ValueError("Timeout must be between 30 and 180 seconds")
        self._module._send_command(f'AT+UHTTP={self.profile_id},7,{timeout}')
        self.timeout = timeout
        logger.info(f"Set HTTP timeout to {timeout} seconds for HTTP profile {self.profile_id}")

    def AT_set_http_header(self, header_string:str="0:"):
        maximum_length = 256
        if len(header_string) > maximum_length: 
            raise ValueError(f"Header string must be {maximum_length} characters or fewer")
        if header_string.count(":") != 2 and not (len(header_string) == 2 and header_string[0].isdigit() and header_string[1] == ':'):
            raise ValueError(f'Invalid format for header string "{header_string}", required format <id:key:value> or <id:>')

        self._module._send_command(f'AT+UHTTP={self.profile_id},9,"{header_string}"')
        logger.info(f'Set HTTP header to "{header_string}" for HTTP profile {self.profile_id}')
    
    def AT_http_get(self, server_path:str, response_filename:str):
        from ublox.modules import SaraR5Module
        HTTPClient.validate_server_path(server_path)
        SaraR5Module.validate_filename(response_filename)

        self._module._send_command(f'AT+UHTTPC={self.profile_id},1,"{server_path}","{response_filename}"')
        self.server_path = server_path
        self._await_http_response(self, timeout = self.timeout)
        
        logger.info(f'HTTP GET request to "{self.url}" for HTTP profile {self.profile_id}')
    
    def AT_http_post(self, server_path, response_filename, send_filename, content_type:ContentType):
        from ublox.modules import SaraR5Module
        HTTPClient.validate_server_path(server_path)
        SaraR5Module.validate_filename(response_filename)
        SaraR5Module.validate_filename(send_filename)

        if self.security_profile is not None:
            for attr in ['hostname_sni', 'hostname_ca_validation']:
                if self.hostname != getattr(self.security_profile, attr):
                    logger.warning(f'Security profile {attr} "{getattr(self.security_profile, attr)}" does not match HTTP profile hostname "{self.hostname}"')

        self._module._send_command(f'AT+UHTTPC={self.profile_id},4,"{server_path}","{response_filename}","{send_filename}", {content_type.value}')
        self.server_path = server_path
        self._await_http_response(self,timeout = self.timeout)
        logger.info(f'HTTP POST request to "{self.url}" for HTTP profile {self.profile_id}')

    def AT_http_get_error(self):
        result = self._module._send_command(f'AT+UHTTPER={self.profile_id}',capture_urc=True)

        if not type(result) == list and len(result) == 1:
            raise ValueError(f'error format unexpected: {result}')
        _urc, data = result[0].split(b':')
        data = data.split(b',')
        error_profile_id = int(data[0].decode())
        error_class = int(data[1].decode())
        error_code = int(data[2].decode())

        if error_profile_id != self.profile_id:
            raise ValueError(f'Error profile id {error_profile_id} does not match {self.profile_id}')
        
        return error_class, error_code
    
    def _await_http_response(self, http_profile, timeout=180):
        """
        We need continuously poll the connection
        status and see if the connection status has changed.
        """
        from ublox.modules import ConnectionTimeoutError
        logging.info(f'Awaiting HTTP Response')

        start_time = time.time()
        
        while True:
            time.sleep(2)
            self._module._send_command(f'AT') # poll for URCs

            if self.completed == 0:
                continue

            if self.completed == 1:
                break

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'Could not connect')
    
    def _update_http_callback(module:'SaraR5Module', urc: bytes):
        """
        Callback to handle HTTP responses
        """
        logger.info(f'HTTP Response: {urc}')
        _urc, data = urc.split(b':')
        data = data.split(b',')
        profile_id = int(data[0])
        #no use for data[1] which is the http command type (get,post,etc)
        status = int(data[2])
        http_profile:HTTPClient = module.http_profiles[profile_id]
        http_profile.completed = True
        http_profile.error = status == 0
    
    @property
    def url(self):
        logger.debug(f'SSL status: {self.ssl}')
        return f'http{"s" if self.ssl else ""}://{self.hostname}:{self.server_port}{self.server_path}'
        
    
    def validate_server_path(server_path:str):
        if len(server_path) > 1024: raise ValueError("Server path must be less than 1024 characters")

        parsed_path = urlparse(server_path)
        if not parsed_path.path:
            raise ValueError("Invalid server path {server_path}")
        
class HTTPResponse:

    def __init__(self, data, request):
        self.request = None
        self.status_code = None
        self.reason = None
        self.content = None
        self.encoding = None
        self.text = None
        self.headers = None
    
        self.parse(data)

    def json(self, **kwargs):
        return json.loads(self.text, **kwargs)

    def __str__(self):
        return f"HTTPResponse: {self.status_code} {self.reason}\n"
    
    def parse(self, data):

        _, self.status_code,self.reason, _ = HTTPResponse.parse_http_metadata(data)

        #TODO: determine encoding

        lines = HTTPResponse.split_lines(data)

        self.headers = HTTPResponse.parse_headers(lines)

        #TODO: generate content (before decoding)
        self.text = lines[-2][:-1] # 2nd last line is content, remove the last character which is a double quote
    
    def parse_http_metadata(data):
        decoded = [x.decode() for x in data]
        urc = decoded[0].split(",")
        length = urc[1]
        http_data = urc[2].strip('"')


        parts = http_data.split(' ', 2)
        protocol = parts[0]
        code = parts[1]
        message = parts[2]

        return length, code, message, protocol

    def split_lines(data):

        decoded = [x.decode() for x in data]
        urc = decoded[0].split(",")
        length = urc[1]
        http_code = urc[2].strip('"')

        joined = ''.join(decoded[1:])
        lines = joined.split('\r\n')
        return lines

    def parse_headers(lines):
        headers = lines[:-2]
        header_dict = {}
        for header in headers:
            if ': ' in header:
                key, value = header.split(': ', 1)
                header_dict[key] = value
        return header_dict
