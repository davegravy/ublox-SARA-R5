from multiprocessing import Process
import time
import os
import threading
import queue
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s.%(msecs)03d %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger("test")
logger.level = logging.DEBUG

class SaraR5Module:
    def __init__(self):
        self.uart_read_queue = queue.Queue()
        reader_thread = threading.Thread(target=self.uart_reader)
        reader_thread.daemon = True
        reader_thread.start()

    def uart_reader(self):
        while True:
            logger.debug("uart reader writing data to queue")
            self.uart_read_queue.put("sample data from uart")
            time.sleep(1)

    def response_processor(self):
        logger.debug("running response_processor")
        if not self.uart_read_queue.empty():
            data = self.uart_read_queue.get()
            logger.debug(f"response processor received: {data}")

class SensorPlatform:
    def __init__(self):
        self.IotProcessor = IotProcessor()
        self.iot = Process(name="IOT", target=self.IotProcessor.iot_process)
        self.iot.start()

class IotProcessor:
    def __init__(self):
        self.logger = logger
        

    def iot_process(self):
        self.module = SaraR5Module()
        while True:
            self.module.response_processor()
            time.sleep(1)
   
if __name__ == "__main__":



    iot_meter = SensorPlatform()






    

