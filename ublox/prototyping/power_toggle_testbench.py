import time
from power_control import AT91PowerControl

def toggle_power_test():


    power_control = AT91PowerControl()

    # Initialize counters and maximum time tracker
    success_count = 0
    failure_count = 0
    test_count = 0
    max_time_to_change = 0  # Track the maximum time to change state
    timeout = 60  # seconds

    while True:
        success = False
        time_to_change = 0
        test_count += 1

        # Get initial vin state
        start_vin = power_control.get_power_state()
        print(f"Test {test_count}: Initial vin: {start_vin}")
        start_time = time.time()

        if start_vin:
            print("Powering OFF")
            power_control.force_power_off_alt()
        else:
            print("Powering ON")
            power_control.power_on_wake()

        adjusted_timeout = timeout - (time.time() - start_time)
        if adjusted_timeout < 0:
            print("Timeout exceeded")
            break

        success = power_control.await_power_state(not start_vin, adjusted_timeout)
        time_to_change = time.time() - start_time

        # Check if the vin state changed
        if success:
            success_count += 1
            print(f"Test {test_count}: Success! vin changed to {not start_vin} within {time_to_change:.2f} seconds.")

            # Update maximum time to change if the current time is greater
            if time_to_change > max_time_to_change:
                max_time_to_change = time_to_change
            
            power_control.gpio_reset_n.set(not start_vin) #TODO remove once reset is fixed

        else:
            failure_count += 1
            print(f"Test {test_count}: Failure. vin did not change within 5 seconds.")

        # Print accumulated results and the maximum time recorded
        print(f"Accumulated results: {success_count} successes, {failure_count} failures")
        print(f"Maximum time for vin to change: {max_time_to_change:.2f} seconds\n")

        # Optional delay before the next test
        time.sleep(2)

    power_control.close()

if __name__ == "__main__":
    toggle_power_test()