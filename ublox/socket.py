import time
import binascii

SUPPORTED_SOCKET_TYPES = ['UDP', 'TCP']

class UbloxSocket:

    def __init__(self, socket_id, module, source_port=None):
        self.socket_id = socket_id
        self.module = module
        self.source_port = source_port

        # When setting a socket to listening this is set to true.
        # But when receiving on the same socket that you sent on you need to
        # send at least once on the socket before you can receive.
        self.able_to_receive = False

    def sendto(self, bytes, address):
        pass

    def recvfrom(self, bufsize):
        pass

    def bind(self, address):
        pass

    def close(self):
        self.module.close_socket(self.socket_id)


class UDPSocket(UbloxSocket):

    def sendto(self, bytes, address):
        self.module.send_udp_data(socket=self.socket_id, host=address[0],
                                  port=address[1], data=bytes.decode())
        self.able_to_receive = True

    def bind(self, address):
        host, port = address
        # Since we can only have the ip of the module we dont care about the
        # hostvalue provided.
        self.module.set_listening_socket(socket=self.socket_id, port=port)
        self.able_to_receive = True

    def recvfrom(self, bufsize):
        """
        As of now there seems to be a problem with URC so there is no
        notification on data received. We continously poll the socket with a
        small delay for not to block the module compleatly until we get a
        result.
        """
        if not self.able_to_receive:
            raise IOError('The ublox socket cannot receive data yet. Either '
                          'set the socket to listening via .bind() or write '
                          'once on the socket.')

        result = self.module.read_udp_data(socket=self.socket_id, length=bufsize)
        if result:
            ip, port, length, hex_data = result
            address = (ip.decode(), int(port))
            data = binascii.unhexlify(hex_data)
            return data, address
        else:
            return None
        
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
    
    def _parse_udp_response(message: bytes):
        raise NotImplementedError
        # _message = message.replace(b'"', b'')
        # socket, ip, port, length, _data, remaining_bytes = _message.split(b',')
        # data = bytes.fromhex(_data.decode())
        # return data
