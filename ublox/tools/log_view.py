import csv

# Function to read a CSV file and return its contents as a list of dictionaries
def read_csv(file_name, source):
    with open(file_name, 'r') as file:
        reader = csv.reader(file,delimiter=';')
        headers = ['timestamp', 'source', 'message']  # Updated headers list
        data = [dict(zip(headers, [row[0], source] + row[1:] )) for row in reader]  # Updated zip function
    return data

# Read receive_log.csv and send_log.csv
receive_log = read_csv('receive_log.csv', 'receive')
send_log = read_csv('send_log.csv', 'send')

# Merge the two lists
merged_log = receive_log + send_log

# Sort the merged log chronologically based on the timestamp
sorted_log = sorted(merged_log, key=lambda x: x['timestamp'])

# Print the sorted log
for log in sorted_log:
    print(*map(repr, log.values()))
