import serial
import threading
import queue

class CMEError(Exception):
    """CME ERROR on Module"""


class ATError(Exception):
    """AT Command Error"""

class DeviceInterface:
    def __init__(self, port, baudrate):
        self.terminate = False
        self.ser = serial.Serial(port, baudrate, timeout=1)
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.disconnected = False
        self.thread = threading.Thread(target=self.read_from_device)
        self.thread.daemon = True
        self.thread.start()
        self.urc_mappings = {
            "+UUPSDD": self.handle_uupsdd,
            "+UCEREG": self.handle_ucereg
        }


    # def read_from_device(self):
    #     while not self.terminate:
    #         data = self.ser.readline()
    #         if data:
    #             timestamp = datetime.datetime.now()
    #             data_with_timestamp = (data, timestamp)
    #             if any(data.decode().startswith(prefix) for prefix in self.urc_mappings.keys()):
    #                 urc = data.split(b":")[0].decode()
    #                 urc_data = data.split(b":")[1].decode()
    #                 handler_function = self.urc_mappings[urc]
    #                 handler_function(urc_data)
    #             elif data == b'':
    #                 pass
    #             else:
    #                 self.queue.put(data_with_timestamp)


    # def handle_ucereg(self, data):
    #     # Handle unsolicited result code (URC) here
    #     pass
    # def handle_uupsdd(self, data):
    #     # Handle unsolicited result code (URC) here
    #     with self.lock:
    #         self.disconnected = True
    #     pass

    # def send_command(self, command:str, expected_reply=True, input_data:bytes=None, timeout=10):
    #     """
    #     expected reply is None, str or bool
    #         str:            reply expected with prefix (e.g. "UPSND")
    #         bool(True):     reply expected with prefix matching command (e.g. "AT+UPSND=0,8" expects "+UPSND: 0,8")
    #         bool(False):    no reply expected
    #     """

    #     if not isinstance(expected_reply, (bool, str)): 
    #         raise TypeError("expected_reply is not of type bool or str")
       
    #     result = None 
    #     got_ok = False
    #     got_reply = False
    #     debug_log = []

    #     if expected_reply == True:
    #         expected_reply_bytes = command.lstrip("AT").split("=")[0].encode() + b":"
    #     if expected_reply == False:
    #         got_reply=True
    #     if isinstance(expected_reply,str):
    #         expected_reply_bytes = b"+" + expected_reply.encode() + b":"

    #     command_unterminated = command.encode().rstrip(b"\r\n")
    #     command = command_unterminated + b"\r\n"

    #     self.ser.write(command)
    #     timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    #     print(f"Sent:{chr(10)}          {timestamp}: {command}")
        
    #     timeout_time = time.time() + timeout
    #     try:
    #         while not (got_ok and got_reply):
    #             time_remaining = timeout_time - time.time()
    #             if time_remaining <= 0:
    #                 raise TimeoutError("Timeout waiting for response")
    #             try:
    #                 response_with_timestamp:tuple = self.queue.get(timeout=time_remaining)
    #                 response, timestamp = response_with_timestamp
    #                 debug_log.append((timestamp, response))
    #             except queue.Empty:
    #                 continue

    #             if response.startswith(b"OK"):
    #                 got_ok = True
    #             elif expected_reply != False and response.startswith(expected_reply_bytes):
    #                 got_reply = True
    #                 result = response.lstrip(expected_reply_bytes).rstrip(b"\r\n").decode().strip().split(",")
    #             elif response.startswith(b"ERROR"):
    #                 raise ATError
    #             elif response.startswith(b"+CME ERROR:"):
    #                 code = response.lstrip(b"+CME ERROR:").rstrip(b"\r\n").decode()
    #                 raise CMEError(code) #TODO: convert code to error message
    #             elif response == b"\r\n" or response.startswith(command_unterminated): # ack or echo
    #                 pass
    #             elif input_data and len(input_data) > 0 and response.startswith(b">"):
    #                 self.ser.write(input_data)
    #             else:
    #                 print(f'WARNING: got unexpected {response}')
    #     except Exception as e:
    #         raise e
    #     finally:
    #         output = '\n          '.join([f'{timestamp.strftime("%Y-%m-%d_%H-%M-%S")}: {response}' for timestamp, response in debug_log])
    #         print(f"Received:{chr(10)}          {output}")

    #     return result
    
    def close(self):
        self.terminate = True
        self.thread.join()
        self.ser.close()

# Usage:
import datetime
import time
device = DeviceInterface('/dev/ttyS1', 115200)
try:
    while True:
        with device.lock:
            if device.disconnected:
                print("Device disconnected")
                break
        if datetime.datetime.now().second in [0,10,20,30,40,50]:
            response = device.send_command(f'AT+UDWNFILE="test2",5',expected_reply=False, input_data=b"12345")
            #response = device.send_command(f'AT',expected_reply=False)
            print(response)
            time.sleep(1)
except KeyboardInterrupt:
    print("CTRL-C pressed. Exiting...")
finally:    
    print("sending terminate")
    device.close()



