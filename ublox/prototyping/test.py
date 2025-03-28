import logging
from systemd.journal import JournalHandler
from ublox.modules import SaraR5Module
from ublox.power_control import AT91PowerControl
from ublox.utils import EDRXMode
from ublox.security_profile import SecurityProfile
from ublox.http import HTTPClient
from ublox.mqtt import MQTTClient, MQTTBrokerError
from ublox.utils import PSMPeriodicTau, PSMActiveTime
import time


logging.basicConfig(level=logging.DEBUG, format='%(asctime)s.%(msecs)03d %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger("test")
logger_journal_handler = JournalHandler()
logger_journal_handler.addFilter(lambda record: setattr(record, 'SYSLOG_IDENTIFIER', 'test') or True)
logger_journal_handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
logger.addHandler(logger_journal_handler)

sara_logger = logging.getLogger("SARA")
sara_logger_journal_handler = JournalHandler()
sara_logger_journal_handler.addFilter(lambda record: setattr(record, 'SYSLOG_IDENTIFIER', 'SARA') or True)
sara_logger_journal_handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
sara_logger.addHandler(sara_logger_journal_handler)

sara_txrx_logger = logging.getLogger("SARA_TXRX")
sara_txrx_logger_journal_handler = JournalHandler()
sara_txrx_logger_journal_handler.addFilter(lambda record: setattr(record, 'SYSLOG_IDENTIFIER', 'SARA_TXRX') or True)
sara_txrx_logger_journal_handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
sara_txrx_logger.addHandler(sara_txrx_logger_journal_handler)

#Commands for a new module
#TODO: for pytest: https://stackoverflow.com/questions/46492209/how-to-emulate-data-from-a-serial-port-using-python-3-unittest-mocks

#consider using watchtower for logging: https://pypi.org/project/watchtower/

SENTRY_KEY="a19b5d6d4c674f2bba4b1d184e105697"
SENTRY_HOST="o39395"
SENTRY_SECRET_KEY="6638668df7ad422ebd519e15c6b0c51f"
SENTRY_PROJECT_NUMBER="4504051945177088"

# ca_cert='/root/jsonplaceholder-ca.crt'
# ca_cert_name='isrg_root_x2'
# cert_md5='d39ec41e233ca6dfcfa37e6de014e6e5'
# hostname='jsonplaceholder.typicode.com'
# security_profile_id=0

def configure_sec_profiles(module:SaraR5Module):

    security_profiles_data = {
        # "arms":
        # {
        #     'ca_cert': '/root/arms-ca.crt',
        #     'ca_cert_name': 'usertrust_root',
        #     'ca_cert_md5': '1bfe69d191b71933a372a80fe155e5b5',
        #     'client_cert': None,
        #     'client_cert_name': None,
        #     'client_cert_md5': None,
        #     'client_key': None,
        #     'client_key_name': None,
        #     'client_key_md5': None,
        #     'hostname': 'arms-api.aercoustics.com',
        #     'profile_id': 0
        # },
        "sentry":
        {
            'ca_cert': '/root/sentry-ca.crt',
            'ca_cert_name': 'digicert_global_root_g2',
            'ca_cert_md5': 'e4a68ac854ac5242460afd72481b2a44',
            'client_cert': None,
            'client_cert_name': None,
            'client_cert_md5': None,
            'client_key': None,
            'client_key_name': None,
            'client_key_md5': None,
            'hostname': 'sentry.io',
            'profile_id': 1
        },
        "iot":
        {
            'ca_cert': '/root/iot_test/root-CA.crt',
            #'ca_cert': '/root/iot_test/pca3-g5.crt.pem',
            'ca_cert_name': 'aws_root',
            'ca_cert_md5': '43c6bfaeecfead2f18c6886830fcc8e6',
            'client_cert': '/root/iot_test/thing2.cert.pem',
            'client_cert_name': 'thing2_cert',
            'client_cert_md5': '592b645854d5ffb3a2f86b37247eeaa1',
            'client_key': '/root/iot_test/thing2.private.key',
            'client_key_name': 'thing2_private_key',
            'client_key_md5': 'f4e36ff4c6ae71638240356d5133c379',
            'hostname': 'a1k9ecto9j720o-ats.iot.us-east-1.amazonaws.com',
            'profile_id': 2
        }
        # "iot2":
        # {
        #     'ca_cert': '/root/iot_test/mosquitto.org.crt',
        #     'ca_cert_name': 'mosquitto_org_root',
        #     'ca_cert_md5': 'ee4a68ac854ac5242460afd72481b2a44',
        #     'client_cert': '/root/iot_test/testclient.crt',
        #     'client_cert_name': 'testclient_cert',
        #     'client_cert_md5': '592b645854d5ffb3a2f86b37247eeaa1',
        #     'client_key': '/root/iot_test/testclient.key',
        #     'client_key_name': 'testclient_key',
        #     'client_key_md5': 'f4e36ff4c6ae71638240356d5133c379',
        #     'hostname': 'test.mosquitto.org',
        #     'profile_id': 3
        # }

    }

    #client_cert='/root/privatecert.pem', client_key='/root/privatekey.pem

    # for profile in security_profiles_data:

    #     ca_cert = security_profiles_data[profile]["ca_cert"]
    #     ca_cert_name = security_profiles_data[profile]["ca_cert_name"]
    #     ca_cert_md5 = security_profiles_data[profile]["ca_cert_md5"]
    #     client_cert = security_profiles_data[profile]["client_cert"]
    #     client_cert_name = security_profiles_data[profile]["client_cert_name"]
    #     client_cert_md5 = security_profiles_data[profile]["client_cert_md5"]
    #     client_key = security_profiles_data[profile]["client_key"]
    #     client_key_name = security_profiles_data[profile]["client_key_name"]
    #     client_key_md5 = security_profiles_data[profile]["client_key_md5"]
    #     hostname = security_profiles_data[profile]["hostname"]
    #     profile_id = security_profiles_data[profile]["profile_id"]

    #     security_profile:SecurityProfile = module.create_security_profile(profile_id)
    #     security_profiles_data[profile]["security_profile"] = security_profile

    #     logger.info("-------------- getting cert md5")
    #     if not SecurityProfile.at_get_cert_md5(module, SecurityProfile.CertificateType.CA_CERT, ca_cert_name)==ca_cert_md5:
    #         security_profile.upload_cert_key(ca_cert, SecurityProfile.CertificateType.CA_CERT, ca_cert_name)

    #     if client_cert:
    #         logger.info("-------------- getting client cert")
    #         if not SecurityProfile.at_get_cert_md5(module, SecurityProfile.CertificateType.CLIENT_CERT, client_cert_name)==client_cert_md5:
    #             security_profile.upload_cert_key(client_cert, SecurityProfile.CertificateType.CLIENT_CERT, client_cert_name)

    #     if client_key:
    #         logger.info("-------------- getting client key")
    #         if not SecurityProfile.at_get_cert_md5(module, SecurityProfile.CertificateType.CLIENT_PRIVATE_KEY, client_key_name)==client_key_md5:
    #             security_profile.upload_cert_key(client_key, SecurityProfile.CertificateType.CLIENT_PRIVATE_KEY, client_key_name)

    #     logger.info("-------------- configuring security profile")
    #     security_profile.configure_security_profile(hostname, ca_cert=ca_cert_name, client_cert=client_cert_name, client_key=client_key_name, ca_validation_level=SecurityProfile.CAValidationLevel.LEVEL_2_URL_INTEGRITY_CHECK)
        
    #     logger.info("-------------- creating http profile")
    #     security_profiles_data[profile]["http_profile"] = module.create_http_profile(profile_id=profile_id, security_profile=security_profile)
    # # arms - http_profile.set_server_params(hostname=hostname, port=443, ssl=True, timeout=30, headers={"authentication-token":"34291120-7c19-448e-a0d8-bdb823bfdff8","accept":"application/json"})
    #     security_profiles_data[profile]["http_profile"].set_server_params(hostname=security_profiles_data[profile]["hostname"], port=443, ssl=True, timeout=30, headers={})

    # return security_profiles_data
    return SecurityProfile.create_security_profiles(module, security_profiles_data)
def test_socket():
    sock = module.create_socket()
    sock.sendto(b'Message To Echo Server', ('195.34.89.241', 7))
    sock.close()



def retry_command(command, max_retries, retry_delay, *args, **kwargs):
    for attempt in range(max_retries):
        try:
            command(*args, **kwargs)
            logger.debug("Command executed successfully")
            return
        except RuntimeError as e:
            logger.debug("in RuntimeError")
            if str(e) == "Not connected to MQTT broker":
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Reconnecting and retrying...")
                try:
                    mqtt.connect()
                except Exception as reconnect_error:
                    logger.warning(f"Reconnection attempt {attempt + 1} failed: {reconnect_error}")
                    time.sleep(retry_delay)
                else:
                    continue
            elif str(e) == "Another command is in progress":
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying after delay...")
                time.sleep(retry_delay)
            else:
                logger.warning(f"Attempt {attempt + 1} failed: {e}. No retry.")
                break
        except MQTTBrokerError as e:
            logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying after delay...")
            time.sleep(retry_delay)
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            break
    else:
        logger.error("Failed to execute command after maximum retries")

module = SaraR5Module(serial_port='/dev/ttyS1', echo=False, power_control=AT91PowerControl,rtscts=True, baudrate=115200, logger=sara_logger, tx_rx_logger=sara_txrx_logger)
apn = "ciot"
mno_profile = SaraR5Module.MobileNetworkOperator.ROGERS
#low power mode
lpm = True

# module.setup(radio_mode='LTEM')
# module.connect(operator=302720, apn="ciot")

max_retries = 5
retry_delay = 5  # seconds
topic_opus="sound_data/ARMS-GFY-P0/opus" #TODO: investgiate why a comma after topic didn't throw an error
topic_json="sound_data/ARMS-GFY-P0/json"
#message=f"hello {time.time()}"
#send_filename='2024-04-18T00-06-34+0-00_sEVT.opus'  
send_filename='2024-11-07T20-30-00+00-00_sINT.json'

try:
    module.serial_init(clean=True)
    
    if lpm: 
        module.setup_nvm(mno_profile, apn, power_saving_mode=True, tau=PSMPeriodicTau._4_hrs_30_mins, active_time=PSMActiveTime._14_secs)
    else:
        module.setup_nvm(mno_profile, apn, power_saving_mode=False)

    
    result = module.send_command(f'AT+CSQ',expected_reply=True)
    
    security_profiles_data = configure_sec_profiles(module)
    #arms_profile:HTTPClient = security_profiles_data["arms"]["http_profile"]
    sentry_http_client:HTTPClient = module.create_http_profile(profile_id=security_profiles_data["sentry"]["profile_id"], security_profile=security_profiles_data["sentry"]["security_profile"])

    
    mqtt:MQTTClient = module.mqtt_client
    mqtt.configure(client_id="ARMS-GFY-P0", server_params={"hostname":"a1k9ecto9j720o-ats.iot.us-east-1.amazonaws.com", "port":8883, "ssl":True}, security_profile=security_profiles_data["iot"]["security_profile"])

    #module.upload_local_file_to_fs('/root/iot_test/2024-04-18T00-06-34+0-00_sEVT.opus', '2024-04-18T00-06-34+0-00_sEVT.opus', overwrite=True)
    module.upload_local_file_to_fs('/root/iot_test/2024-11-07T20-30-00+00-00_sINT.json', '2024-11-07T20-30-00+00-00_sINT.json', overwrite=True)

    mqtt.connect()
    result = module.send_command(f'AT+CSQ',expected_reply=True)

    #retry_command(mqtt.publish, max_retries, retry_delay, topic=topic, message=message, qos=1)
    retry_command(mqtt.publish_file, max_retries, retry_delay, topic=topic_json, send_filename=send_filename, qos=1) 
    retry_command(mqtt.disconnect, max_retries, retry_delay)

    if lpm: 
        module.prep_for_sleep()

    time.sleep(80)
    #TODO: investigate CEPPI (power saving preference)

    while True:

        module.wake_from_sleep()
        security_profiles_data = configure_sec_profiles(module)


        #DO OFFLINE PREP HERE

        #arms_profile:HTTPClient = security_profiles_data["arms"]["http_profile"]
        #sentry_profile:HTTPClient = security_profiles_data["sentry"]["http_profile"]
        module.register_after_wake()
        #module.send_command(f'AT+UPING="www.google.com"',expected_reply=False)

       
        #USER CODE STARTS HERE

        # logger.info("-------------- starting ARMS post")
        # result = arms_profile.post('/root/testpost.json', content_type=HTTPClient.ContentType.APPLICATION_JSON, server_path='/switchboard/v1.5/file_ready')
        # logger.debug(result)
        mqtt.connect()
        result = module.send_command(f'AT+CSQ',expected_reply=True)
        #TODO: fix retry_command
        #retry_command(mqtt.publish, max_retries, retry_delay, topic=topic, message=message, qos=1)
        retry_command(mqtt.publish_file, max_retries, retry_delay, topic=topic_json, send_filename=send_filename, qos=1) 
        retry_command(mqtt.disconnect, max_retries, retry_delay)

        # logger.info("-------------- starting Sentry post")
        # result = sentry_http_client.post('/root/sentry-body.txt', content_type=HTTPClient.ContentType.APPLICATION_JSON, server_path=f'/api/{SENTRY_PROJECT_NUMBER}/envelope/')


        if lpm: module.prep_for_sleep()

        #USER CODE ENDS HERE
        # logger.debug(result)
        #TODO: investigate AT+UDCONF=89,1 
        # https://content.u-blox.com/sites/default/files/documents/SARA-R5-LEXI-R5_ATCommands_UBX-19047455.pdf#page=122
        logger.info("starting sleep")
        time.sleep(540)
        logger.info("finished sleep")
except Exception as e:
    logger.debug("test.py exception handling")
    raise e
finally:
    logger.debug("test.py finally method")
    module.close()

#print(result)
