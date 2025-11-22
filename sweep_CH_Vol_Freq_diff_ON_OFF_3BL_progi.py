# sweep_CH_Vol_Freq_diff_ON_OFF_3BL.py
# Last updated:
# - Author: PG
# - Date: 2025-11-13

#!/usr/bin/env python

import argparse
import logging
import time
from datetime import datetime
import serial
import yaml
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from threading import Thread
import sys
import time
import logging
from threading import Thread


def wait_for_space(prompt):
    input(f"\n{prompt}\nPress SPACEBAR then ENTER when ready...\n")


class Config:
    """Holds information from parsed config files"""

    def __init__(self, measurement, device, args):
        self.channel_start = measurement['Channel']['Start']
        self.channel_end = measurement['Channel']['End']
        self.channel_steps = measurement['Channel']['Steps']
        self.volume_start = measurement['Volume']['Start']
        self.volume_end = measurement['Volume']['End']
        self.volume_steps = measurement['Volume']['Steps']
        self.frequency_start = measurement['Frequency']['Start']
        self.frequency_end = measurement['Frequency']['End']
        self.frequency_steps = measurement['Frequency']['Steps']
        self.measurements_number = measurement['Measurements']['Number']
        self.measurements_duration_on = measurement['Measurements']['Duration_on']
        self.measurements_duration_off = measurement['Measurements']['Duration_off']
        self.baseline_1 = measurement['Baselines']['Baseline_1']
        self.baseline_2 = measurement['Baselines']['Baseline_2']
        self.baseline_3 = measurement['Baselines']['Baseline_3']

        self.board_id = device['Board']['Id']
        self.board_master = device['Board']['Master']
        self.board_mac = device['Board']['Mac']
        self.board_file = device['Board']['File']
        self.board_serial = device['Board']['Serial']
        self.keep_ble_alive = device['Board']['Keep_ble_alive']
        self.serial_port = device['VHP']['Serial']
        self.verbose = args.verbose
        self.timestamp = datetime.now().strftime("%y%m%d-%H%M")

    def __str__(self):
        return (f"Volume Range: {self.volume_start} to {self.volume_end}, Steps: {self.volume_steps}\n"
                f"Frequency Range: {self.frequency_start} to {self.frequency_end}, Steps: {self.frequency_steps}\n"
                f"Measurements: Number={self.measurements_number}, Duration_on={self.measurements_duration_on}s, Duration_off={self.measurements_duration_off}s\n"
                f"Baselines: B1={self.baseline_1}s, B2={self.baseline_2}s, B3={self.baseline_3}s\n"
                f"Board: Id={self.board_id}, Master={self.board_master}, MAC={self.board_mac}, Serial={self.board_serial}")


class SerialCommunicator:
    BAUDRATE = 115200
    TIMEOUT_SEC = 1

    def __init__(self, port):
        self.port = port
        self.ser = serial.Serial(port=self.port, baudrate=self.BAUDRATE, timeout=self.TIMEOUT_SEC)
        if not self.ser.is_open:
            self.ser.open()
        time.sleep(2)

    def __del__(self):
        if hasattr(self, 'ser') and self.ser.is_open:
            self.ser.close()
            logging.info("Serial closed")

    def _send_command(self, command):
        self.ser.write((command + '\n').encode('utf-8'))
        time.sleep(0.05)
        while self.ser.in_waiting > 0:
            response = self.ser.readline().decode('utf-8', errors='ignore').strip()
            logging.debug("Serial VHP Received: %s", response)

    def set_duration(self, duration): self._send_command(f'D{max(1, min(65535, duration))}')
    def set_cycle_period(self, cycle_period): self._send_command(f'Y{max(1, min(65535, cycle_period))}')
    def set_pause_cycle_period(self, pause_cycle_period): self._send_command(f'P{max(0, min(100, pause_cycle_period))}')
    def set_paused_cycles(self, paused_cycles): self._send_command(f'Q{max(0, min(100, paused_cycles))}')
    def set_jitter(self, jitter): self._send_command(f'J{max(0, min(1000, jitter))}')
    def set_test_mode(self, enabled): self._send_command(f'M{1 if enabled else 0}')
    def set_channel(self, channel): self._send_command(f'C{max(0, min(8, channel))}')
    def set_volume(self, volume): self._send_command(f'V{max(0, min(100, volume))}')
    def set_frequency(self, frequency): self._send_command(f'F{frequency}')
    def start_stream(self): self._send_command('1')
    def stop_stream(self): self._send_command('0')


def parse_yaml_file(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)


def parse_cmdline():
    parser = argparse.ArgumentParser(description="EEG/VHP sync")
    parser.add_argument('-m', '--measureconf', required=True, help="Path to YAML measurement configuration file")
    parser.add_argument('-d', '--deviceconf', required=True, help="Path to YAML device configuration file")
    parser.add_argument('-v', '--verbose', action='count', default=0, help="Verbose level up to 5")
    args = parser.parse_args()
    return args, Config(parse_yaml_file(args.measureconf), parse_yaml_file(args.deviceconf), args)


def setup_brainflow_board(config):
    params = BrainFlowInputParams()
    if config.board_master:
        params.file = config.board_file
        params.master_board = BoardIds[config.board_master].value
        board_id = BoardIds[config.board_id].value
    else:
        if config.board_mac:
            params.mac_address = config.board_mac
        if config.board_serial:
            params.serial_port = config.board_serial
        board_id = BoardIds[config.board_id]
    return BoardShim(board_id, params)


def do_measurement(com, board_shim, config, channel, frequency, volume,
                   global_counter, global_total, global_start_time):
    """Full measurement including Baseline 3 + stim cycles with progress + ETA."""

    # ---------------------------------------------------------
    # Utility: Text progress + ETA
    # ---------------------------------------------------------
    def format_time(sec):
        sec = int(sec)
        if sec < 60:
            return f"{sec}s"
        return f"{sec//60}m{sec%60:02d}s"

    def progress_bar(prefix, current, total, start_time, length=30):
        pct = current / total
        filled = int(pct * length)
        bar = "█" * filled + "-" * (length - filled)

        elapsed = time.time() - start_time
        eta = elapsed / current * (total - current) if current > 0 else 0

        sys.stdout.write(
            f"\r{prefix} |{bar}| {pct*100:5.1f}%"
            f"  ETA: {format_time(eta)}"
            f"  Elapsed: {format_time(elapsed)}"
        )
        sys.stdout.flush()

    # ---------------------------------------------------------
    # Header
    # ---------------------------------------------------------
    logging.info(f"Measuring: CH={channel}, FREQ={frequency}, VOL={volume}")
    print(f"\n--- Measurement Start: CH={channel}  FREQ={frequency}  VOL={volume} ---")

    # ---------------------------------------------------------
    # Setup EEG recording file
    # ---------------------------------------------------------
    fname = f"./Recordings/{config.timestamp}_{config.board_id}_c{channel}_f{frequency}_v{volume}.csv"
    streamer_params = f"file://{fname}:w"
    board_shim.add_streamer(streamer_params)

    # ---------------------------------------------------------
    # Baseline 3 (with progress bar + ETA)
    # ---------------------------------------------------------
    baseline3_start = time.time()
    baseline3_total = config.baseline_3

    board_shim.insert_marker(333)

    print("\nBaseline 3 (contact) recording...")
    for t in range(baseline3_total):
        remaining = baseline3_total - t
        elapsed = time.time() - baseline3_start
        sys.stdout.write(
            f"\rBaseline 3: {remaining:2d}s remaining"
            f"  ETA: {format_time(remaining)}"
            f"  Elapsed: {format_time(elapsed)}"
        )
        sys.stdout.flush()
        time.sleep(1)
    print("")  # newline

    # ---------------------------------------------------------
    # Stim cycles (each ON + OFF period)
    # ---------------------------------------------------------
    cycles = config.measurements_number
    on_dur = config.measurements_duration_on
    off_dur = config.measurements_duration_off

    print(f"\nStim cycles: {cycles} cycles (ON={on_dur}s, OFF={off_dur}s)\n")

    for cycle in range(1, cycles + 1):

        # --------------------------- ON PERIOD ---------------------------
        print(f"Cycle {cycle}/{cycles} — ON period")
        board_shim.insert_marker(1)
        com.start_stream()

        on_start = time.time()
        for t in range(on_dur):
            pct = (t + 1) / on_dur
            sys.stdout.write(
                f"\r  ON  [{cycle}/{cycles}]  {pct*100:5.1f}%  "
                f"ETA: {format_time(on_dur - t - 1)}"
            )
            sys.stdout.flush()
            time.sleep(1)
        print("")
        board_shim.insert_marker(11)
        com.stop_stream()

        # --------------------------- OFF PERIOD ---------------------------
        print(f"Cycle {cycle}/{cycles} — OFF period")
        off_start = time.time()
        for t in range(off_dur):
            pct = (t + 1) / off_dur
            sys.stdout.write(
                f"\r  OFF [{cycle}/{cycles}]  {pct*100:5.1f}%  "
                f"ETA: {format_time(off_dur - t - 1)}"
            )
            sys.stdout.flush()
            time.sleep(1)
        print("")

        # --------------------------- GLOBAL PROGRESS UPDATE ---------------------------
        global_counter[0] += 1  # increment counter stored as list reference
        progress_bar("Global sweep",
                     global_counter[0],
                     global_total,
                     global_start_time)
        print("\n")  # give space

    # ---------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------
    board_shim.delete_streamer(streamer_params)
    print(f"--- Measurement Completed CH={channel}, FREQ={frequency}, VOL={volume} ---\n")


def is_vhp_connected(port, baudrate=115200, timeout=1):
    try:
        with serial.Serial(port, baudrate=baudrate, timeout=timeout) as ser:
            time.sleep(1)
            return True
    except (serial.SerialException, OSError):
        return False


def keep_ble_alive(board_shim, interval=1):
    while True:
        try:
            board_shim.get_board_data()
            time.sleep(interval)
        except Exception as e:
            logging.warning(f"BLE keepalive thread error: {e}")
            break


def write_metadata(args, config, fname1, fname2):
    fname = f"./Recordings/{config.timestamp}_metadata.txt"
    with open(fname, "w") as f:
        readable_timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        f.write(f"Recording on: {readable_timestamp}\n\n")
        f.write("*** Measure Configuration ***\n")
        f.write(open(args.measureconf).read())
        f.write("\n*** Device Configuration ***\n")
        f.write(open(args.deviceconf).read())
        f.write(f"\nBaseline 1 (VHP OFF>ON): {fname1}\n")
        f.write(f"Baseline 2 (VHP ON, STIM ON, no contact): {fname2}\n")

# --------------------------------------------------------------------
# ETA + PROGRESS FUNCTIONS
# --------------------------------------------------------------------

def format_time(seconds):
    """Convert seconds → H:MM:SS or M:SS."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def print_progress(prefix, current, total, start_time, length=30):
    """Progress bar + ETA."""
    percent = current / total
    filled = int(length * percent)
    bar = "█" * filled + "-" * (length - filled)

    elapsed = time.time() - start_time
    if current > 0:
        rate = elapsed / current
        eta = rate * (total - current)
    else:
        eta = 0

    sys.stdout.write(
        f"\r{prefix} |{bar}| {percent*100:6.2f}%"
        f"  ETA: {format_time(eta)}"
        f"  Elapsed: {format_time(elapsed)}"
    )
    sys.stdout.flush()


def countdown_eta(label, seconds):
    """Baseline countdown with ETA."""
    start = time.time()
    for t in range(seconds):
        elapsed = time.time() - start
        remaining = seconds - t
        sys.stdout.write(
            f"\r{label}: {remaining:3d}s remaining"
            f"  ETA: {format_time(remaining)}"
            f"  Elapsed: {format_time(elapsed)}"
        )
        sys.stdout.flush()
        time.sleep(1)
    print("")


# --------------------------------------------------------------------
# MAIN SCRIPT
# --------------------------------------------------------------------

def main():
    args, config = parse_cmdline()
    BoardShim.enable_dev_board_logger()
    logging.basicConfig(format='[%(asctime)s] %(message)s', level=logging.INFO)

    board_shim = setup_brainflow_board(config)
    board_shim.prepare_session()
    board_shim.start_stream()

    if config.keep_ble_alive:
        Thread(target=keep_ble_alive, args=(board_shim,), daemon=True).start()

    fname1 = f"./Recordings/{config.timestamp}_{config.board_id}_baseline_with_VHP_powered_OFF.csv"
    fname2 = f"./Recordings/{config.timestamp}_{config.board_id}_baseline_with_VHP_powered_ON_stim_ON_no_contact_c{config.channel_start}_f{config.frequency_start}_v{config.volume_start}.csv"

    try:
        # ---------------------------- BASELINE 1 ----------------------------
        if not is_vhp_connected(config.serial_port):

            streamer1 = f"file://{fname1}:w"
            board_shim.add_streamer(streamer1)

            board_shim.insert_marker(3)
            print("\nRecording Baseline 1 (waiting for VHP ON)...")

            while not is_vhp_connected(config.serial_port):
                print("Waiting for VHP to power ON...")
                time.sleep(2)

            print("\n▶ Baseline 1 started")
            board_shim.insert_marker(33)

            countdown_eta("Baseline 1", config.baseline_1)

            board_shim.delete_streamer(streamer1)
            print("Baseline 1 completed.\n")

        # ---------------------------- BASELINE 2 ----------------------------
        vhpcom = SerialCommunicator(config.serial_port)
        wait_for_space("\n➡️  Place finger(s) 5 cm away from tactors (NO CONTACT)")

        streamer2 = f"file://{fname2}:w"
        board_shim.add_streamer(streamer2)
        board_shim.insert_marker(34)

        print("\nRecording Baseline 2 (VHP ON, STIM ON, no contact)...")

        vhpcom.set_channel(config.channel_start)
        vhpcom.set_volume(config.volume_start)
        vhpcom.set_frequency(config.frequency_start)
        vhpcom.start_stream()
        board_shim.insert_marker(31)

        countdown_eta("Baseline 2", config.baseline_2)

        vhpcom.stop_stream()
        board_shim.insert_marker(311)
        board_shim.delete_streamer(streamer2)
        print("Baseline 2 completed.\n")

        # ---------------------------- SWEEP ----------------------------
        wait_for_space("\n➡️  Place finger(s) ON tactors (CONTACT) – Sweep starts...")

        vhpcom.set_test_mode(1)

        # Total cycles = all parameter combinations × number of stim cycles each
        num_ch = (config.channel_end - config.channel_start) // config.channel_steps + 1
        num_freq = (config.frequency_end - config.frequency_start) // config.frequency_steps + 1
        num_vol = (config.volume_end - config.volume_start) // config.volume_steps + 1

        global_total = num_ch * num_freq * num_vol * config.measurements_number
        global_counter = [0]  # mutable so it can be updated inside function
        global_start_time = time.time()

        print(f"\nTotal stim cycles in sweep: {global_total}\n")

        for ch in range(config.channel_start, config.channel_end + 1, config.channel_steps):
            for freq in range(config.frequency_start, config.frequency_end + 1, config.frequency_steps):
                for vol in range(config.volume_start, config.volume_end + 1, config.volume_steps):

                    # Set parameters
                    vhpcom.set_channel(ch)
                    vhpcom.set_volume(vol)
                    vhpcom.set_frequency(freq)

                    # Perform measurement + full progress
                    do_measurement(
                        vhpcom,
                        board_shim,
                        config,
                        ch, freq, vol,
                        global_counter,
                        global_total,
                        global_start_time
                    )

        print("\nSweep completed.\n")

        board_shim.stop_stream()
        board_shim.release_session()
        write_metadata(args, config, fname1, fname2)

    except Exception as e:
        logging.error(f"Error: {e}")
        if board_shim.is_prepared():
            board_shim.stop_stream()
            board_shim.release_session()


if __name__ == "__main__":
    main()
