import random
import string
import logging
import json
import copy
import os
import time
from enum import Enum
from urllib.parse import urlparse
from typing import TYPE_CHECKING
import validators


if TYPE_CHECKING:
    from ublox.modules import SaraR5Module
    from ublox.security_profile import SecurityProfile

class HTTPClientError(Exception):
    """UUHTTPCR on Module"""

class HTTPClient:
    """
    A class representing an HTTP client.

    This class provides methods to send HTTP GET and POST requests to a server,
    as well as set the parameters for the target server.

    Attributes:
        profile_id (int): The profile ID for the HTTP client.
        _module (SaraR5Module): The module used for communication.
        security_profile (SecurityProfile): The security profile for the HTTP client.
        error_code (int): The error code for the last HTTP request.
        hostname (str): The hostname of the target server.
        server_port (int): The port number of the target server.
        ssl (bool): Whether SSL/TLS is enabled for the connection.
        timeout (int): The timeout value for the HTTP request.
        server_path (str): The path on the server to send the HTTP request to.
    """

    class HTTPSConfig(Enum):
        """
        Enum representing whether or not TLS/SSL is enabled.
        AT+UHTTP=<profile_id>,6,<HTTPSConfig>

        Attributes:
            DISABLED (int): HTTPS is disabled.
            ENABLED (int): HTTPS is enabled.
        """
        DISABLED = 0
        ENABLED = 1

    class ContentType(Enum):
        """
        Enum representing the content types for HTTP requests.
        """
        APPLICATION_X_WWW_FORM_URLENCODED = 0
        TEXT_PLAIN = 1
        APPLICATION_OCTET_STREAM = 2
        MULTIPART_FORM_DATA = 3
        APPLICATION_JSON = 4
        APPLICATION_XML = 5
        USER_DEFINED = 6

    def __init__(self, profile_id:int, module:'SaraR5Module', security_profile: 'SecurityProfile'):
        """
        Initializes an instance of the HTTPClient class.

        Args:
            profile_id (int): The profile ID for the HTTP client.
            module (SaraR5Module): The module used for communication.
            security_profile (SecurityProfile): The security profile for the HTTP client.

        """
        if profile_id not in range(0,4):
            raise ValueError("Profile id must be between 0 and 3")
        if not module:
            raise ValueError("Module must be set")

        self.profile_id = profile_id
        self._module = module
        self.at_reset_http_profile()
        self.security_profile = security_profile
        self.error_code = None
        self.hostname = ""
        self.ip = None
        self.server_port = 80
        self.ssl = False
        self.timeout = 180
        self.server_path = "/"
        self.headers = {}

    def restore_profile(self):
        """
        Restores the HTTP profile on the module from locally stored values. Useful after wake since the HTTP profile is volatile.
        """
        params = {
            "hostname": self.hostname,
            "ip": self.ip,
            "port": self.server_port,
            "ssl": self.ssl,
            "timeout": self.timeout,
            "headers": self.headers
        }

        if self.hostname == "" or not self.hostname:
            del params["hostname"]
        else: 
            del params["ip"]
        
        if self.ip is None:
            params.pop("ip", None)

        self.set_server_params(**params)

    def set_server_params(self, hostname=None, ip=None, port=None, ssl=False,
                            timeout=180, headers:dict=None):
        """
        Set the parameters for the target HTTP server.

        Args:
            hostname (str, optional): The hostname of the server. 
                Overrides the IP address if provided.
            ip (str, optional): The IP address of the server. 
                Only used if hostname is not provided.
            port (int, optional): The port number of the server. 
                Default is 80 for HTTP, 443 for HTTPS.
            ssl (bool, optional): Enable SSL/TLS for the connection. 
                Default is False.
            timeout (int, optional): The timeout value for the HTTP request. 
                Default is 180 seconds.
            headers (dict, optional): Additional headers to be included in the HTTP request. 
                Default is an empty dictionary.
        """
        if headers is None:
            headers = {}
        ssl = HTTPClient.HTTPSConfig.ENABLED if ssl else HTTPClient.HTTPSConfig.DISABLED

        #we can only use hostname or ip, not both. Hostname overrides ip
        if hostname:
            self.at_set_http_server_hostname(hostname)
        else:
            if not ip:
                raise ValueError("Either hostname or ip must be set")
            self.at_set_http_server_ip(ip)

        profile_id = self.security_profile.profile_id if self.security_profile else None

        if port:
            self.at_set_http_server_port(port)
        self.at_set_http_ssl(ssl, profile_id)
        self.at_set_http_timeout(timeout)
        self.set_header_string(headers)

    def set_header_string(self, headers):
        """
        Sets the HTTP headers for the request.

        Args:
            headers (dict): A dictionary containing the headers to be set.

        Raises:
            ValueError: If the number of headers exceeds the maximum limit of 5.
            ValueError: If the combined length of the header key and value exceeds 64 characters.
        """
        if len(headers.keys()) > 5:
            raise ValueError("Too many headers. Max 5 headers allowed")
        for header_id in range(0,5):
            if header_id > len(headers.keys()) - 1:
                #clear the header id
                header_string = str(header_id) + ":"
            else:
                key = list(headers.keys())[header_id]
                value = headers[key]
                if len(key) + len(value) > 64:
                    raise ValueError("Header key and value must be less than 64 characters")
                header_string = str(header_id) + ":" + key + ":" + value
            self.at_set_http_header(header_string)
            self.headers = headers

    def get(self, server_path="/", file_out=None):
        """
        Sends an HTTP GET request to the specified server path and returns the response.

        Args:
            server_path (str, optional): The path on the server to send the GET request to. 
                Defaults to "/".
            file_out (str, optional): The filename to save the response to. If not provided,
                the response will be returned as a string

        Returns:
            HTTPResponse: The response object containing the data and error information, if any.
        """
        data = None

        filename = ''.join(random.choice(string.ascii_lowercase) for i in range(10))
        self.at_http_get(server_path, filename)
        if self.error:
            error_class, error_code = self.at_http_get_error()
            if error_class in [3,10,11]:
                self.error_code = error_code
                error_description = self.error_code_description
            error_message = "HTTP GET failed"
            if error_code is not None and error_description is not None:
                error_message += f" with error code {error_code}: {error_description}"
            raise HTTPClientError(error_message)
        else:
            #TODO: get filesize first to set timeout
            data = self._module.at_read_file(filename,file_out=file_out,timeout=600)

        self._module.at_delete_file(filename)
        return HTTPResponse(data, copy.copy(self))

    def post(self, payload_file, content_type, server_path="/"):
        """
        Sends an HTTP POST request with the given payload file to the specified server path.

        Args:
            payload_file (str): The path to the payload file to be sent.
            content_type (str): The content type of the payload.
            server_path (str, optional): The server path to send the request to. Defaults to "/".

        Returns:
            HTTPResponse: The response object containing the data received from the server.
        """
        #TODO: split into two functions: prep and send, prep can be done with radio disabled
        # since it only needs to write to the file system

        data = None

        payload_filename = payload_file.split('/')[-1]
        result_filename = ''.join(random.choice(string.ascii_lowercase) for i in range(10))
        self._module.upload_local_file_to_fs(
            payload_file, payload_filename, overwrite=True)
        #TODO: timeout for large files
        self.at_http_post(server_path, result_filename, payload_filename, content_type)
        if self.error:
            error_class, error_code = self.at_http_get_error()
            if error_class == 3:
                self.error_code = error_code
                error_description = self.error_code_description
                self._module.logger.error('HTTP POST failed with error code %s: %s',
                             error_code, error_description)
        else:
            data = self._module.at_read_file(result_filename,timeout=60)

        self._module.at_delete_file(payload_filename)
        self._module.at_delete_file(result_filename)

        return HTTPResponse(data,copy.copy(self))

    @property
    def error_code_description(self):
        """
        Returns the description of the error code.

        Returns:
            dict: A dictionary mapping error codes to their descriptions.
        """

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

    def at_reset_http_profile(self):
        """
        Resets the HTTP profile to its default values.

        This method resets the HTTP profile by setting the hostname, port, SSL, server path,
        security profile, completed status, and error status to their default values.

        """
        self._module.send_command(f'AT+UHTTP={self.profile_id}',expected_reply=False)
        self.hostname = ""
        self.port = 80
        self.ssl = False
        self.server_path = ""
        self.security_profile = None
        self.completed = False
        self.error = False

        self._module.logger.info("Reset HTTP profile %s", self.profile_id)

    def at_set_http_server_ip(self, ip:str):
        """
        Sets the HTTP server IP address for the HTTP profile.

        Args:
            ip (str): The IP address of the HTTP server.

        """
        if not validators.ipv4(ip):
            raise ValueError("Invalid IPV4 address")

        self._module.send_command(f'AT+UHTTP={self.profile_id},0,"{ip}"',expected_reply=False)
        self.ip = ip
        self._module.logger.info("Set HTTP server IP to %s for HTTP profile %s",ip,self.profile_id)

    def at_set_http_server_hostname(self, hostname):
        """
        Sets the HTTP server hostname for the HTTP profile.

        Args:
            hostname (str): The hostname to set.

        """
        if len(hostname) not in range (1,1025):
            raise ValueError("Hostname must be 1 to 1024")
        if not validators.domain(hostname):
            raise ValueError("Invalid hostname")

        self._module.send_command(f'AT+UHTTP={self.profile_id},1,"{hostname}"',expected_reply=False)
        self.hostname = hostname
        self._module.logger.info('Set HTTP server hostname to "%s" for HTTP profile %s',
                    hostname, self.profile_id)

    def at_set_http_server_port(self, port:int):
        """
        Sets the HTTP server port for the HTTP profile.

        Args:
            port (int): The port number to set.

        """
        if port < 1 or port > 65535:
            raise ValueError("Port must be between 1 and 65535")
        if not isinstance(port, int):
            raise ValueError("Port must be an integer")

        self._module.send_command(f'AT+UHTTP={self.profile_id},5,{port}',expected_reply=False)
        self.server_port = port
        self._module.logger.info("Set HTTP server port to %s for HTTP profile %s", port, self.profile_id)

    def at_set_http_ssl(self, ssl:HTTPSConfig=HTTPSConfig.DISABLED, security_profile_id:int=None):
        """
        Sets the HTTP SSL configuration for the HTTP profile.

        Args:
            ssl (HTTPSConfig): The SSL configuration to be set. 
                Defaults to HTTPSConfig.DISABLED.
            security_profile_id (int): The security profile ID. 
                Must be None or an integer between 0 and 3.

        """
        if not (security_profile_id is None or security_profile_id in range(0,4)):
            raise ValueError(f"Security profile id must be None or an int between 0 and 3, got: {security_profile_id}")
        if ssl == HTTPClient.HTTPSConfig.DISABLED and security_profile_id is not None:
            raise ValueError("Security profile id must be None if SSL is disabled")

        at_command = f'AT+UHTTP={self.profile_id},6,{ssl.value}'
        self._module.logger.debug('security_profile_id: %s',security_profile_id)
        if isinstance(security_profile_id, int):
            at_command = at_command + f',{security_profile_id}'
        self._module.send_command(at_command, expected_reply=False)
        self.ssl = ssl == HTTPClient.HTTPSConfig.ENABLED
        self._module.logger.info("Set HTTP SSL to %s for HTTP profile %s", ssl.name, self.profile_id)

    def at_set_http_timeout(self, timeout:int=180):
        """
        Sets the HTTP timeout for the HTTP profile.

        Parameters:
        - timeout (int): The timeout value in seconds. 
            Must be between 30 and 180 seconds.

        """
        if timeout not in range(30,181):
            raise ValueError(f"Timeout must be between 30 and 180 seconds, got: {timeout}")
        self._module.send_command(f'AT+UHTTP={self.profile_id},7,{timeout}',expected_reply=False)
        self.timeout = timeout
        self._module.logger.info("Set HTTP timeout to %s seconds for HTTP profile %s",timeout,self.profile_id)

    def at_set_http_header(self, header_string:str="0:"):
        """
        Sets the HTTP header for the HTTP profile.

        Args:
            header_string (str): The HTTP header string in the format <id:key:value> or <id:>.
                                    Default is "0:".

        Raises:
            ValueError: If the header string is longer than 256 characters or has an invalid format.

        """
        maximum_length = 256
        if len(header_string) > maximum_length:
            raise ValueError(f"Header string must be {maximum_length} characters or fewer")
        if header_string.count(":") != 2 and not (len(header_string) == 2 and \
                                                    header_string[0].isdigit() and \
                                                    header_string[1] == ':'):
            raise ValueError(f'Invalid format for header string "{header_string}", '
                                'required format <id:key:value> or <id:>')

        self._module.send_command(f'AT+UHTTP={self.profile_id},9,"{header_string}"',
                                    expected_reply=False)
        self._module.logger.info('Set HTTP header to "%s" for HTTP profile %s', header_string, self.profile_id)

    def at_http_get(self, server_path:str, response_filename:str):
        """
        Sends an HTTP GET request to the specified server path and saves the 
        response to the given filename in the module's file system.

        Args:
            server_path (str): The path of the server to send the GET request to.
            response_filename (str): The filename within the module fs to save 
                the response to.

        """
        from ublox.modules import SaraR5Module
        HTTPClient.validate_server_path(server_path)
        SaraR5Module.validate_filename(response_filename)

        self.completed = False
        self._module.send_command(f'AT+UHTTPC={self.profile_id},1,"{server_path}",'
                                    f'"{response_filename}"', expected_reply=False)
        self.server_path = server_path
        self._await_http_response(timeout = self.timeout)

        self._module.logger.info('HTTP GET request to "%s" for HTTP profile %s', self.url, self.profile_id)


    def at_http_post(self, server_path, response_filename, send_filename, content_type:ContentType):
        """
        Sends an HTTP POST request to the specified server path.

        Args:
            server_path (str): The path of the server to send the request to.
            response_filename (str): The filename in the module's filesystem 
                to save the response to.
            send_filename (str): The filename of the file in the module's filesystem 
                to send in the request.
            content_type (ContentType): The content type of the file being sent.

        Raises:
            ValueError: If the server path, response filename, or send filename is invalid.

        """
        from ublox.modules import SaraR5Module
        HTTPClient.validate_server_path(server_path)
        SaraR5Module.validate_filename(response_filename)
        SaraR5Module.validate_filename(send_filename)

        self.completed = False

        if self.security_profile is not None:
            for attr in ['hostname_sni', 'hostname_ca_validation']:
                if self.hostname != getattr(self.security_profile, attr):
                    self._module.logger.warning(
                        'Security profile %s "%s" does not match HTTP profile hostname "%s"',
                        attr,getattr(self.security_profile, attr),self.hostname)

        self._module.send_command(f'AT+UHTTPC={self.profile_id},4,"{server_path}",'
                                f'"{response_filename}","{send_filename}", {content_type.value}',
                                    expected_reply=False)
        self.server_path = server_path
        self._await_http_response(timeout = self.timeout)
        self._module.logger.info('HTTP POST request to "%s" for HTTP profile %s', self.url, self.profile_id)

    def at_http_get_error(self):
        """
        Retrieves the HTTP error information related to the last request for the 
        current profile.

        Returns:
            Tuple[int, int]: A tuple containing the error class and error code.

        """
        result = self._module.send_command(f'AT+UHTTPER={self.profile_id}')

        # if not type(result) == list and len(result) == 1:
        #     raise ValueError(f'error format unexpected: {result}')
        error_profile_id = int(result[0])
        error_class = int(result[1])
        error_code = int(result[2])

        if error_profile_id != self.profile_id:
            raise ValueError(f'Error profile id {error_profile_id}'
                             f'does not match {self.profile_id}')

        return error_class, error_code

    def _await_http_response(self, timeout=180):
        """
        Waits for the HTTP response and checks if the connection status has changed.

        Args:
            timeout (int): The maximum time to wait for the response in seconds. 
                Defaults to 180 seconds.

        Raises:
            ConnectionTimeoutError: If the connection status does not change 
            within the specified timeout.

        Returns:
            None
        """
        from ublox.modules import ConnectionTimeoutError
        self._module.logger.info('Awaiting HTTP Response')

        start_time = time.time()

        while True:
            time.sleep(0.25)

            if not self.completed:
                continue

            if self.completed:
                self._module.logger.debug('HTTP request completed')
                break

            elapsed_time = time.time() - start_time
            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'Could not connect in {timeout} seconds')

    @staticmethod
    def handle_uuhttpcr(module, data):
        """
        Handle the URC response from the u-blox HTTP client.

        Args:
            module: The u-blox module object.
            data: The response data received from the module.

        """
        data = data.rstrip('\r\n').split(",")
        profile_id = int(data[0])
        command_type = int(data[1]) #the http command type (get,post,etc)
        status = int(data[2])
        http_profile:HTTPClient = module.http_profiles[profile_id]
        http_profile.completed = True
        http_profile.error = status == 0
        if http_profile.error:
            module.logger.error(f"HTTP action failed. Command type: {command_type}, profile: {profile_id}")

    @property
    def url(self):
        """
        Returns the URL of the HTTP connection.

        The URL is constructed based on the SSL status, hostname, server port, and server path.

        Returns:
            str: The URL of the HTTP connection.
        """
        self._module.logger.debug('SSL status: %s',self.ssl)
        protocol = "https" if self.ssl else "http"
        url = f'{protocol}://{self.hostname}:{self.server_port}{self.server_path}'
        return url

    @staticmethod
    def validate_server_path(server_path:str):
        """
        Validates the server path.

        Args:
            server_path (str): The server path to be validated.

        Raises:
            ValueError: If the server path is longer than 1024 characters or if it's invalid.

        """
        if len(server_path) > 1024:
            raise ValueError("Server path must be less than 1024 characters")

        parsed_path = urlparse(server_path)
        if not parsed_path.path:
            raise ValueError(f"Invalid server path {server_path}")

class HTTPResponse:
    """
    Represents an HTTP response.

    Attributes:
        request (str): The HTTP request associated with the response.
        status_code (int): The HTTP status code of the response.
        reason (str): The reason phrase associated with the status code.
        content (str or None): If parsed from a file, this holds the filename
            where the content is stored; otherwise it may hold raw content.
        encoding (str): The encoding of the response.
        text (str or None): The decoded text content of the response or,
            when file‐based, possibly None.
        headers (dict): The headers of the response.
    """

    def __init__(self, data, request):
        self.request = request
        self.status_code = None
        self.reason = None
        self.content = None
        self.encoding = None
        self.text = None
        self.headers = None

        # If data is a filename, process the file without loading it all into memory.

        if isinstance(data, str): 
            if os.path.isfile(data):
                self.parse_file(data)
        else:
            self.parse(data)

    def json(self, **kwargs):
        """
        Deserialize the response text as JSON.

        Args:
            **kwargs: Additional keyword arguments to be passed to `json.loads()`.

        Returns:
            The deserialized JSON object.
        """
        return json.loads(self.text, **kwargs)

    def __str__(self):
        return f"HTTPResponse: {self.status_code} {self.reason}\n"

    def parse(self, data):
        """
        Parses the HTTP response data provided as a list of byte strings.

        Args:
            data (byte-string): The HTTP response data.
        """
        # Parse metadata (first line)
        lines = data.split(b'\r\n')
        self.status_code, self.reason, _ = HTTPResponse.parse_http_metadata(lines[0])
        header_lines = []
        content_lines = []
        delineator = False

        for line in lines[1:]:
            try:
                if line == b'':
                    delineator = True
                    continue
                if not delineator:
                    header_lines.append(line.decode('utf-8'))
                else:
                    content_lines.append(line)
                
            except UnicodeDecodeError:
                pass
        
        #lines = HTTPResponse.split_lines(data)

        self.headers = HTTPResponse.parse_headers(header_lines)

        # As before, assume the second last line holds the content (with the trailing quote removed)
        self.content = b''.join(content_lines)

    def parse_file(self, file_path):
        with open(file_path, 'rb') as f:
            # Read the first line for metadata.
            first_line = f.readline()
            self.status_code, self.reason, _ = HTTPResponse.parse_http_metadata(first_line)

            # Read headers line by line.
            header_lines = []
            while True:
                line = f.readline()
                if not line:
                    break
                decoded_line = line.decode('utf-8', errors='replace').strip('\r\n')
                if decoded_line == '':
                    break  # End of headers.
                header_lines.append(decoded_line)
            self.headers = HTTPResponse.parse_headers(header_lines)

            content_file_path = file_path + ".content"
            with open(content_file_path, 'wb') as content_file:
                if "Content-Length" in self.headers:
                    # If Content-Length is provided, only read exactly that many bytes.
                    content_length = int(self.headers.get("Content-Length", 0))
                    bytes_remaining = content_length
                    chunk_size = 4096
                    while bytes_remaining > 0:
                        to_read = min(chunk_size, bytes_remaining)
                        chunk = f.read(to_read)
                        if not chunk:
                            break  # Handle unexpected EOF.
                        content_file.write(chunk)
                        bytes_remaining -= len(chunk)

                    # Check if there are additional unread bytes in the file.
                    remaining_data = f.read()
                    if remaining_data:
                        print(f"Warning: {len(remaining_data)} additional unread bytes left in the file.")
                else:
                    # No Content-Length provided; read until the end of the file.
                    chunk_size = 4096
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        content_file.write(chunk)

        # remove file_path and move content_file_path to file_path
        os.remove(file_path)
        os.rename(content_file_path, file_path)
        # Store the file name instead of loading content in memory.
        self.content_file_path = file_path
        self.text = None  # or set to the filename if preferred


    @staticmethod
    def parse_http_metadata(data):
        """
        Parse the HTTP metadata from the given data.

        Args:
            data (list of bytes): A list of bytes representing the HTTP metadata.

        Returns:
            tuple: A tuple containing the length, code, message, and protocol 
                extracted from the HTTP metadata.
        """
#        decoded = [x.decode('utf-8', errors='replace') for x in data][0]

        decoded = data.decode('utf-8')
        

        parts = decoded.split(' ', 3)
        protocol = parts[0]
        code = parts[1]
        message = parts[2].rstrip('\r\n')

        return code, message, protocol

    @staticmethod
    def split_lines(data):
        """
        Splits the provided data (list of byte strings) into individual lines.

        Args:
            data (list of bytes): The HTTP response data.

        Returns:
            list: A list of strings representing lines of the response.
        """
        decoded = [x.decode('utf-8', errors='replace') for x in data]
        joined = ''.join(decoded[1:])
        lines = joined.split('\r\n')
        return lines

    @staticmethod
    def parse_headers(lines):
        """
        Parse the headers from the HTTP response lines.

        Args:
            lines (list of str): A list of strings representing the header lines.

        Returns:
            dict: A dictionary containing the parsed headers.
        """
        header_dict = {}
        for header in lines:
            if ': ' in header:
                key, value = header.split(': ', 1)
                header_dict[key] = value
        return header_dict
