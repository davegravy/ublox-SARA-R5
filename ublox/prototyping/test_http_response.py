import os
import io
import json

class URDFFileFormatError(ValueError):
    """Custom exception raised for invalid URDFILE format."""
    pass


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


def process_urdf_file(input_data):
    """
    Processes a URDFILE, accepting filepath or list of binary strings,
    avoiding loading entire data into memory for file input.

    If input_data is a string (filepath), it processes the file in-place
    to extract and return size, streaming data directly to file.
    If input_data is a list of binary strings, it processes the content
    directly from the binary strings and returns size and data (in memory).

    To support large files, the file input method streams data directly to
    the file, avoiding loading the entire file into memory. For smaller
    inputs, the binary string method accumulates data in memory.

    Args:
        input_data (str or list[bytes]): Either the filepath to the URDFILE
                                        or a list containing binary strings
                                        representing the URDFILE content.

    Returns:
        tuple[int, bytes] or tuple[int, None] or None:
            For binary string input: (size (int), data (bytes))
            For filepath input: (size (int), None) - data is in the modified file
            Returns None if an error occurs.
    """
    try:
        if isinstance(input_data, str):
            # --- Filepath input (process file in-place - memory efficient) ---
            filepath = input_data
            file = open(filepath, 'rb+') # Open in binary read+write mode
            is_file_input = True
        elif isinstance(input_data, list) and all(isinstance(item, bytes) for item in input_data):
            # --- List of binary strings input (process in memory) ---
            binary_content = b''.join(input_data)
            file = io.BytesIO(binary_content) # Treat as file-like object
            filepath = None
            is_file_input = False
        else:
            raise TypeError("Input must be a filepath (string) or a list of binary strings.")

        header_line = file.readline()
        header_str = header_line.decode('utf-8')

        if not header_str.startswith('+URDFILE:'):
            file.close()
            raise URDFFileFormatError(f"Header missing.")


        header_content = header_str[len('+URDFILE:'):]

        first_comma_index = header_content.find(',')
        if first_comma_index == -1:
            file.close()
            raise URDFFileFormatError(f"Missing comma after filename.")

        remaining_content_after_filename = header_content[first_comma_index+1:]
        second_comma_index = remaining_content_after_filename.find(',')
        if second_comma_index == -1:
            file.close()
            raise URDFFileFormatError(f"Missing comma after size.")

        size_str = remaining_content_after_filename[:second_comma_index].strip()

        try:
            size = int(size_str)
        except ValueError:
            raise URDFFileFormatError(f"Size is not an integer.")
            file.close()
            return None

        remaining_content_after_size = remaining_content_after_filename[second_comma_index+1:]
        third_quote_index = remaining_content_after_size.find('"')
        if third_quote_index == -1:
            file.close()
            raise URDFFileFormatError(f"Missing quote before data.")

        data_start_header_pos = len('+URDFILE:') + first_comma_index + 1 + second_comma_index + 1 + third_quote_index + 1
        data_start_pos = data_start_header_pos

        # Prepare to read data
        file.seek(data_start_pos)

        chunk_size = 4096
        current_pos = 0 # Track write position in file (for file input)
        bytes_read = 0  # Track bytes read to ensure we read only 'size' bytes

        if is_file_input:
            # --- Filepath input: Stream data directly to file (memory efficient) ---
            try:
                while bytes_read < size:
                    bytes_to_read = min(chunk_size, size - bytes_read)
                    # Seek to the correct read position
                    file.seek(data_start_pos + bytes_read)
                    chunk = file.read(bytes_to_read)
                    if not chunk:
                        break # Safety break

                    # Seek to current write position
                    file.seek(current_pos)
                    file.write(chunk)
                    current_pos += len(chunk)
                    bytes_read += len(chunk)

                # Truncate the file to the exact size
                os.ftruncate(file.fileno(), size)
                return (size, None)
            finally:
                file.close()

        else:
            # --- Binary string input: Accumulate data (in memory) - for smaller inputs ---
            data_bytes = b'' # Initialize data_bytes for binary string input only
            while bytes_read < size:
                bytes_to_read = min(chunk_size, size - bytes_read)
                chunk = file.read(bytes_to_read)
                if not chunk:
                    break # Safety break
                data_bytes += chunk # Accumulate data for binary string input
                bytes_read += len(chunk)
            file.close()
            return size, data_bytes # Return size and accumulated data for binary string input


    except FileNotFoundError as e: # Catch specific FileNotFoundError and re-raise
        raise # Re-raise FileNotFoundError to signal file not found
    except TypeError as e:        # Catch specific TypeError and re-raise
        raise # Re-raise TypeError for input type issues
    except Exception as e:         # Generic Exception - could be more specific in a real application
        raise Exception(f"An unexpected error occurred during URDFILE processing: {e}") from e # Re-raise with more context

data_file = "ublox/tests/test-binary.tar.gz"
# with open(data_file, 'rb') as file:
#     binary_lines = file.readlines()
size, data_out = process_urdf_file(data_file)
print(data_out)
#data = "ublox/tests/request.success"
my_response = HTTPResponse(data_file, None)
print(my_response)