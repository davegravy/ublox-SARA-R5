from enum import Enum
import logging
import validators
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules import SaraR5Module, ATError

class SecurityProfile:
    """
    Represents a security profile for the HTTP module.

    Attributes:
        profile_id (int): The ID of the security profile.
        _module (SaraR5Module): The module associated with the security profile.
        hostname_ca_validation (str): The CA validation server hostname.
        hostname_sni (str): The server name indication (SNI).
    """

    class CAValidationLevel(Enum):
        """
        Enumeration of CA validation levels.

        AT Command: AT+USECPRF=<profile_id>,0,<CAValidationLevel>
        """
        LEVEL_0_NONE = 0
        LEVEL_1_LOCAL_CERT_CHECK = 1
        LEVEL_2_URL_INTEGRITY_CHECK = 2
        LEVEL_3_EXPIRY_DATE_CHECK = 3

    class TLSVersion(Enum):
        """
        Enumeration of TLS versions.

        AT Command: AT+USECPRF=<profile_id>,1,<TLSVersion>
        """
        ANY = 0
        TLS_1_0 = 1
        TLS_1_1 = 2
        TLS_1_2 = 3
        TLS_1_3 = 4

    class CertificateType(Enum):
        """
        Enumeration of certificate types.

        AT Command: AT+USECPRF=<profile_id>,<CertificateType>,<internal_name>
        """
        CA_CERT = 0
        CLIENT_CERT = 1
        CLIENT_PRIVATE_KEY = 2

    def __init__(self, profile_id, module:'SaraR5Module'):
        if profile_id not in range(0,4):
            raise ValueError("Profile id must be between 0 and 4")

        self.profile_id = profile_id
        self._module = module
        self.at_reset_security_profile()
        self.hostname_ca_validation = ""
        self.hostname_sni = ""

    def upload_cert_key(self, filepath, cert_type:CertificateType,
                            internal_name=None, filename_out=None):
        """
        Uploads a certificate or key file to the module's file system 
        and imports it as a security profile.

        Args:
            filepath (str): The local path to the certificate or key file.
            cert_type (CertificateType): The type of certificate or key.
            internal_name (str, optional): The target name for the imported 
                security profile. If not provided, it will be derived from the filename. 
                Defaults to None.
            filename_out (str, optional): The target filename to be used in the module's 
                file system. If not provided, it will be derived from the filepath. 
                Defaults to None.

        Returns:
            str: The internal name of the imported security profile.
        """
        if not filename_out:
            filename_out = filepath.split('/')[-1]
        if not internal_name:
            internal_name = filename_out.split('.')[0]
        self._module.upload_local_file_to_fs(filepath, filename_out, overwrite=True)
        SecurityProfile.at_import_cert_from_file(self._module, cert_type,
                                                    internal_name, filename_out)
        self._module.logger.info('Uploaded file %s to %s "%s"', filepath, cert_type.value, internal_name)
        return internal_name

    def configure_security_profile(self, hostname, ca_cert=None, client_cert=None, client_key=None,
                                   ca_validation_level=CAValidationLevel.LEVEL_1_LOCAL_CERT_CHECK,
                                   tls_version=TLSVersion.TLS_1_2, sni:bool=True):
        """
        Configures the security profile for the HTTP module.

        Args:
            hostname (str): The hostname of the server.
            ca_cert (str, optional): The CA certificate. 
                Defaults to None.
            client_cert (str, optional): The client certificate. 
                Defaults to None.
            client_key (str, optional): The client private key. 
                Defaults to None.
            ca_validation_level (CAValidationLevel, optional): The CA validation level. 
                Defaults to CAValidationLevel.LEVEL_1_LOCAL_CERT_CHECK.
            tls_version (TLSVersion, optional): The TLS version. 
                Defaults to TLSVersion.TLS_1_2.
            sni (bool, optional): Whether to use Server Name Indication (SNI). 
                Defaults to True.

        Raises:
            ValueError: If the CA cert, client cert, or client key is invalid.

        """
        self.at_reset_security_profile()
        self.at_set_ca_validation_level(ca_validation_level)
        self.at_set_tls_version(tls_version)
        #TODO: legacy cipher suite
        self.at_set_ca_validation_server_hostname(hostname)
        if sni:
            self.at_set_server_name_indication(hostname)

        if ca_cert:
            if SecurityProfile.at_get_cert_md5(self._module,
                                               SecurityProfile.CertificateType.CA_CERT,
                                               ca_cert) is None:
                raise ValueError(f'Invalid CA Cert: {ca_cert}, did you upload it?')
            self.at_set_ca_cert(ca_cert)
        if client_cert:
            if SecurityProfile.at_get_cert_md5(self._module,
                                               SecurityProfile.CertificateType.CLIENT_CERT,
                                               client_cert) is None:
                raise ValueError(f'Invalid Client Cert: {client_cert}, did you upload it?')
            self.at_set_client_cert(client_cert)
        if client_key:
            if SecurityProfile.at_get_cert_md5(self._module,
                                               SecurityProfile.CertificateType.CLIENT_PRIVATE_KEY,
                                               client_key) is None:
                raise ValueError(f'Invalid Client Key: {client_key}, did you upload it?')
            self.at_set_client_key(client_key)

    def at_reset_security_profile(self):
        """
        Resets the security profile to default settings.
        """
        self._module.send_command(f'AT+USECPRF={self.profile_id}', expected_reply=False)
        self.hostname_ca_validation = ""
        self.hostname_sni = ""

        self._module.logger.info('Reset security profile %s', self.profile_id)

    def at_set_ca_validation_level(self, level: CAValidationLevel):
        """
        Sets the CA validation level for the security profile.

        Args:
            level (CAValidationLevel): The CA validation level to set.
        """
        self._module.send_command(f'AT+USECPRF={self.profile_id},0,{level.value}',
                                  expected_reply=False)
        self._module.logger.info('Set CA validation level to %s for security profile %s',
                    level.name, self.profile_id)

    def at_set_tls_version(self, version:TLSVersion=TLSVersion.TLS_1_2):
        """
        Sets the TLS version for the security profile.

        Args:
            version (TLSVersion, optional): The TLS version to set. 
                Defaults to TLSVersion.TLS_1_2.
        """
        self._module.send_command(f'AT+USECPRF={self.profile_id},1,{version.value}',
                                    expected_reply=False)
        self._module.logger.info('Set TLS version to %s for security profile %s', version.name, self.profile_id)

    def at_set_ca_validation_server_hostname(self, hostname:str=""):
        """
        Sets the CA validation server hostname for the security profile.

        Args:
            hostname (str): The hostname of the CA validation server.
        """
        if len(hostname) > 256:
            raise ValueError("Server hostname must be 256 characters or less")
        if not validators.domain(hostname):
            raise ValueError("Invalid server hostname")

        self._module.send_command(f'AT+USECPRF={self.profile_id},4,"{hostname}"',
                                    expected_reply=False)
        self.hostname_ca_validation = hostname
        self._module.logger.info('Set CA validation server hostname to "%s" for security profile %s',
                    hostname, self.profile_id)

    def at_set_server_name_indication(self, sni=""):
        """
        Sets the server name indication (SNI) for the security profile. This
        should match the hostname of the target server.

        Args:
            sni (str): The server name indication to set.
        """
        if len(sni) > 128:
            raise ValueError("Server name indication must be 128 characters or less")
        if not validators.domain(sni):
            raise ValueError("Invalid server hostname")

        self._module.send_command(f'AT+USECPRF={self.profile_id},10,"{sni}"',expected_reply=False)
        self.hostname_sni = sni
        self._module.logger.info('Set server name indication to "%s" for security profile %s',
                    sni, self.profile_id)

    def at_set_ca_cert(self, internal_name: str = ""):
        """
        Sets the CA certificate for the security profile.

        Args:
            internal_name (str): The internal name of the CA certificate.

        Raises:
            SecurityProfileError: If the provided internal_name is invalid.

        Note:
            This method does not raise an error if the certificate does not exist.
            It is recommended to validate the certificate before calling this method.
        """
        SecurityProfile.validate_cert_name(internal_name)

        self._module.send_command(f'AT+USECPRF={self.profile_id},3,"{internal_name}"',
                                  expected_reply=False)
        self._module.logger.info('Set CA cert to "%s" for security profile %s',
                    internal_name, self.profile_id)

    def at_set_client_cert(self, internal_name:str=""):
        """
        Sets the client certificate for the security profile.

        Args:
            internal_name (str): The internal name of the client certificate.

        Note:
            This method does not raise an error if the certificate does not exist.
            It is recommended to validate the certificate before calling this method.
        """
        SecurityProfile.validate_cert_name(internal_name)

        self._module.send_command(f'AT+USECPRF={self.profile_id},5,"{internal_name}"',
                                    expected_reply=False)
        self._module.logger.info('Set client cert to "%s" for security profile %s',
                    internal_name, self.profile_id)

    def at_set_client_key(self, internal_name: str = ""):
        """
        Sets the client key for the security profile.

        Args:
            internal_name (str): The internal name of the client key.

        Raises:
            SecurityProfileError: If the provided internal name is invalid.

        Note:
            This method does not raise an error if the certificate does not exist. 
            It is recommended to validate the certificate before calling this method.
        """
        SecurityProfile.validate_cert_name(internal_name)

        self._module.send_command(f'AT+USECPRF={self.profile_id},6,"{internal_name}"',
                                  expected_reply=False)
        self._module.logger.info('Set client key to "%s" for security profile %s',
                    internal_name, self.profile_id)

    @staticmethod
    def at_get_cert_md5(module:'SaraR5Module', cert_type:CertificateType, internal_name):
        """
        Retrieves the MD5 hash of a certificate with the specified type and internal name.

        Args:
            module (SaraR5Module): The SaraR5Module object representing the module.
            cert_type (CertificateType): The type of the certificate.
            internal_name (str): The internal name of the certificate.

        Returns:
            str: The MD5 hash of the certificate, or None if the certificate 
                is not found or invalid.
        """
        from ublox import modules
        SecurityProfile.validate_cert_name(internal_name)

        # errors if not found or invalid
        try:
            result = module.send_command(f'AT+USECMNG=4,{cert_type.value},"{internal_name}"')
        except modules.ATError:
            return None
        return result[3].strip('"')

    @staticmethod
    def at_import_cert_from_file(module:'SaraR5Module', cert_type:CertificateType,
                                  internal_name:str, filename:str):
        """
        Imports a certificate from a file to the module. The file must already 
        exist in the module's file system.

        Args:
            module (SaraR5Module): The SaraR5Module instance.
            cert_type (CertificateType): The type of certificate to import.
            internal_name (str): The internal name to assign to the imported certificate.
            filename (str): The path to the file within the module fs 
                containing the certificate.

        Returns:
            None

        Raises:
            ValueError: If the internal name or filename is invalid.
        """
        from ublox.modules import SaraR5Module
        SecurityProfile.validate_cert_name(internal_name)
        SaraR5Module.validate_filename(filename)

        module.send_command(f'AT+USECMNG=1,{cert_type.value},"{internal_name}","{filename}"')
        module.logger.info('Imported %s from file "%s" to internal name %s',
                    cert_type.name, filename, internal_name)

    @staticmethod
    def validate_cert_name(internal_name:str):
        """
        Validates the internal name of a certificate complies with the module's requirements

        Args:
            internal_name (str): The internal name to be validated.

        Raises:
            ValueError: If the internal name is longer than 200 characters or empty.

        """
        if len(internal_name) > 200:
            raise ValueError("Internal name must be 200 characters or less")

        if not internal_name:
            raise ValueError("Internal name must be set")

        # if not validators.slug(internal_name):
        #     raise ValueError("Internal name must be a valid slug")
