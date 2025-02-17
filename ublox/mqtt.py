import logging
import re
import time
import traceback
import threading
import os
from enum import Enum
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from ublox.modules import SaraR5Module
    from ublox.security_profile import SecurityProfile

class MQTTBrokerError(Exception):
    """UMQTTER on Module"""

class MQTTMessage:
    def __init__(self, qos, topic_msg_length, topic_length, topic, read_msg_length, payload):
        self.qos = qos
        self.topic_msg_length = topic_msg_length
        self.topic_length = topic_length
        self.topic = topic
        self.read_msg_length = read_msg_length
        self.payload = payload

class MQTTClient:
    """
    A singleton class representing an MQTT client for the SARA-R5 module.

    Attributes:
        _instance (MQTTClient): The singleton instance of the MQTTClient.
        _module (SaraR5Module): The module used for communication.
        security_profile (SecurityProfile): The security profile for the MQTT client.
        hostname (str): The hostname of the MQTT broker.
        port (int): The port number of the MQTT broker.
        ssl (bool): Whether SSL/TLS is enabled for the connection.
        client_id (str): The client ID for the MQTT connection.
        username (str): The username for the MQTT connection.
        password (str): The password for the MQTT connection.
    """

    _instance = None

    class MQTTSConfig(Enum):
        """
        Enum representing whether or not TLS/SSL is enabled.
        AT+UMQTT=11,<MQTTSConfig>,<profile_id>

        Attributes:
            DISABLED (int): MQTTS is disabled.
            ENABLED (int): MQTTS is enabled.
        """
        DISABLED = 0
        ENABLED = 1
        
    class NonVolatileOption(Enum):
        """
        Enum representing the non-volatile storage options for MQTT.
        """
        FACTORY_DEFAULTS = 0
        RESTORE_FROM_NVM = 1
        STORE_TO_NVM = 2

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(MQTTClient, cls).__new__(cls)
        return cls._instance

    def __init__(self, module: 'SaraR5Module'):
        """
        Initializes an instance of the MQTTClient class.
        Args:
            module (SaraR5Module): The module used for communication.
        """
        if not module:
            raise ValueError("Module must be set")
  
        self._module = module
        self._command_handler=MQTTCommandHandler(self)
        self.security_profile = None
        self.hostname = ""
        self.port = 1883
        self.ssl = False
        self.client_id = None
        self.username = ""
        self.password = ""
        self.message_count = 0

    def configure(self, client_id, server_params:dict, security_profile):
        hostname = server_params.get('hostname')
        port = server_params.get('port')
        ssl = server_params.get('ssl')
        if not hostname:
            raise ValueError("hostname must be set")
        if not client_id:
            raise ValueError("client_id must be set")
        if not security_profile:
            raise ValueError("security_profile must be set")
        
        self.set_client_id(client_id)
        self.set_server_params(hostname=hostname, port=port, ssl=ssl)
        self.set_security_profile(security_profile)
        self.apply_config()
        self.at_set_mqtt_nonvolatile(MQTTClient.NonVolatileOption.STORE_TO_NVM)
    
    def set_security_profile(self, security_profile: 'SecurityProfile'):
        """
        Set the security profile for the MQTT client.
        Args:
            security_profile (SecurityProfile): The security profile for the MQTT client.
        """
        self.security_profile = security_profile

    def set_client_id(self, client_id: str):
        """
        Set the client ID for the MQTT connection.
        Args:
            client_id (str): The client ID for the MQTT connection.
        """
        self.client_id = client_id

    def set_server_params(self, hostname: str, port: int = 1883, ssl: bool = False, username=None, password=None):
        """
        Set the parameters for the MQTT broker.
        Args:
            hostname (str): The hostname of the MQTT broker.
            port (int, optional): The port number of the MQTT broker. Default is 1883.
            ssl (bool, optional): Enable SSL/TTLS for the connection. Default is False.
        """
        self.hostname = hostname
        self.port = port
        self.ssl = ssl
        self.username = username
        self.password = password

    def apply_config(self):
        """
        Apply the configuration to the module.
        """
        self.at_set_mqtt_client_id(self.client_id)
        self.at_set_mqtt_server(self.hostname, self.port)
        if self.ssl:
            self.at_set_mqtt_ssl(ssl=self.MQTTSConfig.ENABLED, security_profile_id=self.security_profile.profile_id)
        else:
            self.at_set_mqtt_ssl(ssl=self.MQTTSConfig.DISABLED)
        if self.username and self.password:
            self.at_set_mqtt_credentials(self.username, self.password)

    def _execute_command(self, command_func, error_message, *args, **kwargs):
        """
        Helper method to execute a command and handle common logic.
        Args:
            command_func (callable): The command function to execute.
            success_attr (str): The attribute to check for command success.
            error_message (str): The error message to log if the command fails.
            *args: Positional arguments to pass to the command function.
            **kwargs: Keyword arguments to pass to the command function.
        """
        with self._command_handler.lock:
            if self._command_handler.command_in_progress:
                raise RuntimeError("Another command is in progress: {}".format(self._command_handler.command_in_progress))
            if not self._command_handler.connected and command_func.__name__ != 'at_mqtt_connect':
                raise RuntimeError("Not connected to MQTT broker")
            self._command_handler.command_in_progress = command_func.__name__
            self._command_handler.broker_error = False
        
        try:
        
            command_func(*args, **kwargs)
            self._command_handler.await_command()

            with self._command_handler.lock:
                broker_error = self._command_handler.broker_error

            if broker_error:
                error_code = self._command_handler.at_get_command_error()
                #TODO lookup error code descriptions, translate to human readable
                raise MQTTBrokerError(f"{error_message}: MQTT Broker Error (code: {error_code})")
            else:
                self._module.logger.info(f"{command_func.__name__.replace('at_', '').replace('_', ' ').capitalize()} succeeded")
        except Exception as e:
            self._module.logger.error(f"{error_message}: {str(e)}")
            self._module.logger.error("Traceback: %s", traceback.format_exc())
            raise e
        finally:
            self._module.logger.debug("in _execute_command finally")
            with self._command_handler.lock:
                #in success case, command_in_progress is set to None in the URC handler
                self._command_handler.command_in_progress = None

    def connect(self):
        """
        Connect to the MQTT broker.
        """
        self._execute_command(
            self._command_handler.at_mqtt_connect,
            "Failed to connect to MQTT broker"
        )

    def publish(self, topic: str, message: str, qos=1):
        """
        Publish a message to a topic.
        Args:
            topic (str): The topic to publish the message to.
            message (str): The message to publish.
            qos (QoSLevel, optional): The Quality of Service level for the message. Default is QoSLevel.AT_MOST_ONCE.
        """
        qos_level = MQTTCommandHandler.QoSLevel(qos)

        self._execute_command(
            self._command_handler.at_mqtt_publish,
            f"Failed to publish message to topic {topic}",
            topic, message, qos_level
        )

    def publish_file_on_module(self, topic: str, send_filename: str, qos=1):
        """
        Publish a file to a topic.
        Args:
            topic (str): The topic to publish the file to.
            send_filename (str): The filename of the file in the module's filesystem to send as message.
            qos (QoSLevel, optional): The Quality of Service level for the message. Default is QoSLevel.AT_MOST_ONCE.
        """
        qos_level = MQTTCommandHandler.QoSLevel(qos)
        self._execute_command(
            self._command_handler.at_mqtt_publish_file,
            f"Failed to publish file to topic {topic}",
            topic, send_filename, qos_level
        )

    def publish_local_file(self, topic: str, in_file: str, qos=1, overwrite=False, delete_on_success=False):
        """
        Publish a file to a topic.
        Args:
            topic (str): The topic to publish the file to.
            in_file (str): The file on the local filesystem to send as message.
            qos (QoSLevel, optional): The Quality of Service level for the message. Default is QoSLevel.AT_MOST_ONCE.
            overwrite (bool, optional): Whether to overwrite the file on the module if it already exists. Default is False.
            delete_on_success (bool, optional): Whether to delete the file on the module filesystem after publishing. Default is False.
        """
        out_filename = os.path.basename(in_file)
        try:
            self._module.upload_local_file_to_fs(in_file,out_filename,overwrite)
        except FileExistsError as e:
            if not overwrite:
                self._module.logger.debug("File already exists on module, skipping upload")
                
        self.publish_file_on_module(topic, out_filename, qos)
        if delete_on_success:
            self._module.at_delete_file(out_filename)

    def subscribe(self, topic: str, qos=1):
        """
        Subscribe to a topic.
        Args:
            topic (str): The topic to subscribe to.
            qos (QoSLevel, optional): The maximum Quality of Service level for the subscription. Default is QoSLevel.AT_MOST_ONCE.
        """
        qos_level = MQTTCommandHandler.QoSLevel(qos)
        self._execute_command(
            self._command_handler.at_mqtt_subscribe,
            f"Failed to subscribe to topic {topic}",
            topic, qos_level
        )

    def unsubscribe(self, topic: str):
        """
        Unsubscribe from a topic.
        Args:
            topic (str): The topic to unsubscribe from.
        """
        self._execute_command(
            self._command_handler.at_mqtt_unsubscribe,
            f"Failed to unsubscribe from topic {topic}",
            topic
        )

    def disconnect(self):
        """
        Disconnect from the MQTT broker.
        """
        self._execute_command(
            self._command_handler.at_mqtt_disconnect,
            "Failed to disconnect from MQTT broker"
        )

    def await_message(self, timeout=10):
        """
        Wait for a message to be received.
        Args:
            timeout (int, optional): The maximum time to wait for a message in seconds. Default is 10 seconds.
        """
        self._module.logger.info("Waiting via subscription for message up to %d seconds", timeout)
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.message_count > 0:
                return
            time.sleep(0.25)
        raise TimeoutError("No message received within timeout")
    
    def fetch_messages(self, callback):
        """
        Fetch messages from the module and call the callback function for each message.
        Args:
            callback (callable): The callback function to call for each message.
        """
        
        self._module.logger.info("Fetching MQTT messages")
        message_count = self.message_count
        for i in range(message_count):
            message = self._command_handler.at_mqtt_read_message()
            callback(self, None, message)
            self.message_count -= 1

    def handle_uumqttc(self, data):
        """
        Handle the URC response from the u-blox MQTT client.

        Args:
            data: The response data received from the module.
        """
        self._command_handler.handle_urc(data)

    def at_set_mqtt_server(self, hostname: str, port: int=1883):
        """
        Sets the MQTT server hostname and port.
        Args:
            hostname (str): The hostname of the MQTT broker. Must be 128 characters or less.
            port (int): The port number of the MQTT broker. Must be in the range 1-65535.
        """
        if len(hostname) > 128:
            raise ValueError("hostname must be 128 characters or less")
        if not (1 <= port <= 65535):
            raise ValueError("port must be in the range 1-65535")
        self._module.send_command(f'AT+UMQTT=2,"{hostname}",{port}', expected_reply=False)
        self._module.logger.info("Set MQTT server to %s:%d", hostname, port)
        

    def at_set_mqtt_ssl(self, ssl:MQTTSConfig=MQTTSConfig.DISABLED, security_profile_id:int=None):
        """
        Enables SSL for the MQTT connection.

        Args:
            ssl (MQTTSConfig): The SSL configuration to be set. 
                Defaults to MQTTSConfig.DISABLED.
            security_profile_id (int): The security profile ID. 
                Must be None or an integer between 0 and 3.
        """

        if not (security_profile_id is None or security_profile_id in range(0,3)):
            raise ValueError("Security profile id must be None or an int between 0 and 3")
        if ssl == MQTTClient.MQTTSConfig.DISABLED and security_profile_id is not None:
            raise ValueError("Security profile id must be None if SSL is disabled")
        
        at_command = f'AT+UMQTT=11,{ssl.value}'
        if isinstance(security_profile_id, int):
            at_command = at_command + f',{security_profile_id}'
        else: self._module.logger.error("invalid profile id: ".format(security_profile_id))

        self._module.send_command(at_command, expected_reply=False)
        self._module.logger.info("Set MQTT SSL to %s", ssl.name)

    def at_set_mqtt_client_id(self, client_id: str):
        """
        Sets the MQTT client ID.
        Args:
            client_id (str): The client ID for the MQTT connection.
        """
        if len(client_id) > 256:
            raise ValueError("client_id must be 256 characters or less")
        self._module.send_command(f'AT+UMQTT=0,"{client_id}"', expected_reply=False)
        self._module.logger.info("Set MQTT client ID to %s", self.client_id)

    def at_set_mqtt_credentials(self, username: str, password: str):
        """
        Sets the MQTT username and password.
        Args:
            username (str): The username for the MQTT connection.
            password (str): The password for the MQTT connection.
        """
        if len(username) > 512:
            raise ValueError("username must be 512 characters or less")
        if len(password) > 512:
            raise ValueError("password must be 512 characters or less")
        self._module.send_command(f'AT+UMQTT=4,"{username}","{password}"', expected_reply=False)
        self._module.logger.info("Set MQTT credentials: username=%s, password=%s", self.username, self.password)

    def at_set_mqtt_nonvolatile(self, option: NonVolatileOption):
        """
        Sets the MQTT non-volatile storage option.
        Args:
            option (NonVolatileOption): The non-volatile storage option.
                - NonVolatileOption.FACTORY_DEFAULTS: Restore to factory defaults.
                - NonVolatileOption.RESTORE_FROM_NVM: Restore to the settings currently saved in NVM.
                - NonVolatileOption.STORE_TO_NVM: Store the current settings to NVM.
        """
        # TODO this will fail if there's an active MQTT connection
        if not isinstance(option, MQTTClient.NonVolatileOption):
            raise ValueError("option must be an instance of NonVolatileOption")
        self._module.send_command(f'AT+UMQTTNV={option.value}', expected_reply=False)
        self._module.logger.info("Set MQTT non-volatile storage option to %s", option.name)


class MQTTCommandHandler:
    """
    A class to handle MQTT commands and their status.
    """

    class QoSLevel(Enum):
        """
        Enum representing the Quality of Service (QoS) levels for MQTT messages.
        """
        AT_MOST_ONCE = 0
        AT_LEAST_ONCE = 1
        EXACTLY_ONCE = 2

    class Retain(Enum):
        """
        Enum representing whether or not the message should be retained.
        """
        NOT_RETAIN = 0
        RETAIN = 1

    def __init__(self, mqttc_client: MQTTClient):

        self._mqttc_client = mqttc_client 
        self._module = self._mqttc_client._module
        self.lock = threading.Lock()
        self.connected = False
        self.command_in_progress = None
        self.broker_error = False
        self.broker_error_code = None
        self.broker_error_message = None


    def await_command(self, timeout=180):
        """
        Waits for an MQTT command.

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
        self._module.logger.info('Awaiting MQTT Response')

        start_time = time.time()

        while True:
            time.sleep(0.25)
            elapsed_time = time.time() - start_time
        
            with self.lock:
                if not self.command_in_progress:
                    break

            if elapsed_time > timeout:
                raise ConnectionTimeoutError(f'No response in {timeout} seconds')

    def at_mqtt_connect(self):
        """
        Connects to the MQTT broker.
        """

        self._module.send_command(f'AT+UMQTTC=1', expected_reply=False)


    def at_mqtt_publish(self, topic: str, message: str, qos: QoSLevel=QoSLevel.AT_MOST_ONCE, retain: Retain=Retain.NOT_RETAIN):
        """
        Publishes a message to a topic.
        Args:
            topic (str): The topic to publish the message to. Must be 256 characters or less.
            message (str): The message to publish. Must be 1024 characters or less.
            qos (QoSLevel): The Quality of Service level for the message.
            retain (Retain): Whether the message should be retained.
        """

        if len(topic) > 256:
            raise ValueError("topic must be 256 characters or less")
        if len(message) > 1024:
            raise ValueError("message must be 1024 characters or less")
        
        hex_message = message.encode('utf-8').hex().upper()

        self._module.send_command(f'AT+UMQTTC=2,{qos.value},{retain.value},1,"{topic}","{hex_message}"', expected_reply=False)

    def at_mqtt_publish_file(self, topic: str, send_filename: str, qos: QoSLevel=QoSLevel.AT_MOST_ONCE, retain: Retain=Retain.NOT_RETAIN):
        """
        Publishes a file to a topic.
        Args:
            topic (str): The topic to publish the file to. Must be 256 characters or less.
            send_filename (str): The filename of the file in the module's filesystem 
                to send as message. Must be 250 characters or less.
            qos (QoSLevel): The Quality of Service level for the message.
            retain (Retain): Whether the message should be retained.
        """
        if len(topic) > 256:
            raise ValueError("topic must be 256 characters or less")
        if len(send_filename) > 250:
            raise ValueError("file_path must be 250 characters or less")
    
        self._module.send_command(f'AT+UMQTTC=3,{qos.value},{retain.value},"{topic}","{send_filename}"', expected_reply=False)    


    def at_mqtt_subscribe(self, topic: str, qos: QoSLevel):
        """
        Subscribes to a topic.
        Args:
            topic (str): The topic to subscribe to, wildcards supported. Must be 256 characters or less.
            qos (QoSLevel): The maximum Quality of Service level for the subscription.
        """

        if len(topic) > 256:
            raise ValueError("topic must be 256 characters or less")
    
        self._module.send_command(f'AT+UMQTTC=4,{qos.value},"{topic}"', expected_reply=False)

        
    def at_mqtt_unsubscribe(self, topic: str):
        """
        Unsubscribes from a topic.
        Args:
            topic (str): The topic to unsubscribe from, wildcards supported. Must be 256 characters or less.
        """
        if len(topic) > 256:
            raise ValueError("topic must be 256 characters or less")

        self._module.send_command(f'AT+UMQTTC=5,"{topic}"', expected_reply=False)


    def at_mqtt_disconnect(self):
        """
        Disconnects from the MQTT broker.
        """

        self._module.send_command(f'AT+UMQTTC=0', expected_reply=False)

    def at_mqtt_read_message(self, hex_mode=False):
        """
        Reads a message from the module.
        Args:
            hex_mode (bool, optional): Whether to read messages in hex mode. Default is False.
        """
        message_data = self._module.send_command(f'AT+UMQTTC=6,1{",1" if hex_mode else ""}', expected_reply=True, expected_multiline_reply=True)
        message:MQTTMessage = self.parse_mqtt_message(message_data)
        self._module.logger.debug("Parsed MQTTMessage: %s", vars(message))
        return message

    def parse_mqtt_message(self, message_data):
        """
        Parses MQTT message data from the module.
        Args:
            message_data (list of bytes): The raw message data from the module.
        Returns:
            dict: Parsed components, including qos, topic, and message content.
        """
        if not message_data:
            return {}

        # Combine all lines into one string and decode from bytes
        raw_message = b''.join(message_data)
        self._module.logger.debug("Raw MQTT message: %s", raw_message)
        
        # Use regex to match the initial metadata
        match = re.match(rb'\+UMQTTC: 6,(\d+),(\d+),(\d+),"([^"]+)",(\d+),"(.*)"', raw_message, re.DOTALL)
        if not match:
            raise ValueError("Message format not recognized")

        message = MQTTMessage(
                 qos=int(match.group(1)),
                 topic_msg_length=int(match.group(2)),
                 topic_length=int(match.group(3)),
                 topic=match.group(4).decode('utf-8'),
                 read_msg_length=int(match.group(5)),
                 payload=match.group(6)
             )

        return message
    
    def at_get_command_error(self):
        """
        Get the error code for a failed command.
        Returns:
            Optional[int]: The error code, or None if the command was successful.
        """
        return self._module.send_command('AT+UMQTTER', expected_reply=True)

    def handle_urc(self, urc_data: str):
        """
        Handle unsolicited result codes (URCs) specific to MQTTC.
        Args:
            urc_data (str): The URC data received from the module.
        """
        self._module.logger.debug('Received MQTT URC: %s', urc_data)
        parts = urc_data.split(',')

        try:
            command_id = int(parts[0])
            status_value = int(parts[1])
        except (IndexError, ValueError):
            self._module.logger.error('Malformed URC data: %s', urc_data)
            return

        command_status = status_value == 1
        disconnect_status = status_value  # Store the actual value to differentiate
        
        self._module.logger.debug('MQTT URC: command_id=%d, status=%d', command_id, status_value)
        with self.lock:
            if command_id == 0: #disconnect command or URC
                if disconnect_status == 1:
                    self.connected = False
                    self.command_in_progress = None
                elif disconnect_status in [100, 101, 102]:
                    self._module.logger.info("MQTT connection lost, reason code: %d", disconnect_status)
                    self.connected = False
                else:
                    self.broker_error = True
                    self._module.logger.error("MQTT connection error, reason code: %d", disconnect_status)
                    self.command_in_progress = None
            elif command_id == 1: #connect command
                if command_status:
                        self.connected = True
                else: 
                        self.broker_error = True
                        self._module.logger.error("MQTT connect failed: %s", urc_data)
            elif command_id in [2, 3, 4, 5]: # publish, publish file, subscribe, unsubscribe commands
                if not command_status:
                    self.broker_error = True
                    self._module.logger.error("MQTT command %s failed: %s", self.command_in_progress, urc_data)

            elif command_id == 6: # message count update
                self._mqttc_client.message_count = int(parts[1])

            if command_id in [1, 2, 3, 4, 5]: 
                self.command_in_progress = None 



# Example usage:
# from ublox.modules import SaraR5Module
# from ublox.security_profile import SecurityProfile
# 
# module = SaraR5Module(serial_port='/dev/ttyS1', echo=False, power_toggle=toggle_power, rtscts=True, baudrate=115200)
# security_profile = SecurityProfile(profile_id=0, module=module)
# mqtt_client = MQTTClient(module=module, security_profile=security_profile, client_id="my_client_id")
# mqtt_client.set_server_params(hostname="mqtt.example.com", port=8883, ssl=True)
# mqtt_client.connect()
# mqtt_client.publish(topic="test/topic", message="Hello, MQTT!")
# mqtt_client.subscribe(topic="test/topic")
# mqtt_client.disconnect()