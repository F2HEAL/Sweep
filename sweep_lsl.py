"""
4572b9490b92dbcddfef8771c5f2fafda0e7bfc3
Performs EEG measurements following the sweep protocol

Usage: python sweep_lsl.py  -m config/sweep_dev.yaml -d config/dev_lsl_stream.yaml
"""

import argparse
import logging
import time
from datetime import datetime
import serial
import yaml
import sys
import csv
from threading import Thread
from pylsl import StreamInlet, resolve_stream


def wait_for_space(prompt):
    input(f"\n{prompt}\nPress SPACEBAR then ENTER when ready...\n")


class Config:
    """Holds information from parsed config files"""

    def __init__(self, measurement, device, args):
        self.channel_start = measurement["Channel"]["Start"]
        self.channel_end = measurement["Channel"]["End"]
        self.channel_steps = measurement["Channel"]["Steps"]
        self.volume_start = measurement["Volume"]["Start"]
        self.volume_end = measurement["Volume"]["End"]
        self.volume_steps = measurement["Volume"]["Steps"]
        self.frequency_start = measurement["Frequency"]["Start"]
        self.frequency_end = measurement["Frequency"]["End"]
        self.frequency_steps = measurement["Frequency"]["Steps"]
        self.measurements_number = measurement["Measurements"]["Number"]
        self.measurements_duration_on = measurement["Measurements"]["Duration_on"]
        self.measurements_duration_off = measurement["Measurements"]["Duration_off"]
        self.baseline_1 = measurement["Baselines"]["Baseline_1"]
        self.baseline_2 = measurement["Baselines"]["Baseline_2"]
        self.baseline_3 = measurement["Baselines"]["Baseline_3"]

        self.board_id = device["Board"]["Id"]
        self.stream_name = device["Board"].get("StreamName", "SynAmpsRT")

        self.serial_port = device["VHP"]["Serial"]
        self.verbose = args.verbose
        self.timestamp = datetime.now().strftime("%y%m%d-%H%M")


class SerialCommunicator:
    BAUDRATE = 115200
    TIMEOUT_SEC = 0.1

    def __init__(self, port):
        self.port = port
        self.ser = serial.Serial(
            port=self.port, baudrate=self.BAUDRATE, timeout=self.TIMEOUT_SEC
        )
        if not self.ser.is_open:
            self.ser.open()
        time.sleep(2)

    def __del__(self):
        if hasattr(self, "ser") and self.ser.is_open:
            self.ser.close()
            logging.info("Serial closed")

    def _send_command(self, command):
        self.ser.write((command + "\n").encode("utf-8"))
        time.sleep(0.05)
        while self.ser.in_waiting > 0:
            response = self.ser.readline().decode("utf-8", errors="ignore").strip()
            logging.debug("Serial VHP Received: %s", response)

    def set_channel(self, channel):
        self._send_command(f"C{max(0, min(8, channel))}")

    def set_volume(self, volume):
        self._send_command(f"V{max(0, min(100, volume))}")

    def set_frequency(self, frequency):
        self._send_command(f"F{frequency}")

    def set_test_mode(self, enabled):
        self._send_command(f"M{1 if enabled else 0}")

    def start_stream(self):
        self._send_command("1")

    def stop_stream(self):
        self._send_command("0")


def parse_yaml_file(file_path):
    with open(file_path, "r") as file:
        return yaml.safe_load(file)


def parse_cmdline():
    parser = argparse.ArgumentParser(description="EEG/VHP sync (SynAmpsRT via LSL)")
    parser.add_argument(
        "-m",
        "--measureconf",
        required=True,
        help="Path to YAML measurement configuration file",
    )
    parser.add_argument(
        "-d",
        "--deviceconf",
        required=True,
        help="Path to YAML device configuration file",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Verbose level up to 5"
    )
    args = parser.parse_args()
    return args, Config(
        parse_yaml_file(args.measureconf), parse_yaml_file(args.deviceconf), args
    )


# --------------------------------------------------------------------
# LSL Setup
# --------------------------------------------------------------------


def setup_lsl_inlet(stream_name="SynAmpsRT"):
    logging.info(f"Resolving LSL stream: {stream_name}")
    streams = resolve_stream("name", stream_name)
    inlet = StreamInlet(streams[0])
    info = inlet.info()
    logging.info(
        f"Connected to LSL stream: {info.name()} ({info.channel_count()} ch @ {info.nominal_srate()} Hz)"
    )
    return inlet


# --------------------------------------------------------------------
# Recording Functions
# --------------------------------------------------------------------
def record_to_csv(inlet, duration, writer, marker=None):
    """Record samples from LSL inlet to CSV for given duration using an existing writer."""
    start = time.time()
    marker_written = False

    while time.time() - start < duration:
        # Use pull_chunk to be more efficient than pull_sample
        samples, timestamps = inlet.pull_chunk(timeout=0.1)
        if not timestamps:
            continue

        for sample, ts in zip(samples, timestamps):
            eeg_values = sample[:33]
            label = ""

            if marker is not None and not marker_written:
                label = marker
                marker_written = True

            writer.writerow([ts] + eeg_values + [label])

    return marker_written


def record_buffer_to_csv(inlet, fname):
    """Record all available samples from an LSL inlet to a CSV file.
    Layout: [timestamp] + 33 EEG channels + [marker column]."""

    samples, timestamps = inlet.pull_chunk()

    if not timestamps:
        return

    # Prepare rows for CSV writer
    rows = [[ts] + sample[:33] + [""] for ts, sample in zip(timestamps, samples)]

    with open(fname, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    # """Record samples from LSL inlet to CSV for given duration.
    # Layout: [timestamp] + 33 EEG channels + [marker column]."""

    # mode = "a"

    # with open(fname, mode, newline="") as f:
    #     writer = csv.writer(f)

    #     sample, ts = inlet.pull_sample(timeout=1.0)
    #     if sample is None:
    #         return

    #     eeg_values = sample[:33]
    #     label = ""

    #     row = [ts] + eeg_values + [label]
    #     writer.writerow(row)

    #     return


# --------------------------------------------------------------------
# Measurement Loop
# --------------------------------------------------------------------


def do_measurement(
    com,
    inlet,
    config,
    channel,
    frequency,
    volume,
    global_counter,
    global_total,
    global_start_time,
):

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

    logging.info(f"Measuring: CH={channel}, FREQ={frequency}, VOL={volume}")
    print(f"\n--- Measurement Start: CH={channel}  FREQ={frequency}  VOL={volume} ---")

    fname = f"./Recordings/{config.timestamp}_{config.board_id}_c{channel}_f{frequency}_v{volume}.csv"
    
    with open(fname, "w", newline="") as f:
        writer = csv.writer(f)
        # ---------------------------------------------------------
        # Baseline 3 (with progress bar + ETA)
        # ---------------------------------------------------------
        print("\nBaseline 3 (contact) recording...")
        record_to_csv(inlet, config.baseline_3, writer, marker=333)

        # Stim cycles
        cycles = config.measurements_number
        on_dur = config.measurements_duration_on
        off_dur = config.measurements_duration_off

        print(f"\nStim cycles: {cycles} cycles (ON={on_dur}s, OFF={off_dur}s)\n")

        for cycle in range(1, cycles + 1):
            # ON
            print(f"Cycle {cycle}/{cycles} — ON period")
            record_to_csv(inlet, on_dur, writer, marker=0)
            com.start_stream()
            
            record_to_csv(inlet, on_dur, writer, marker=1)
            com.stop_stream()

            # OFF
            print(f"Cycle {cycle}/{cycles} — OFF period")
            record_to_csv(inlet, off_dur, writer, marker=11)

            global_counter[0] += 1
            progress_bar("Global sweep", global_counter[0], global_total, global_start_time)
            print("\n")

    print(
        f"--- Measurement Completed CH={channel}, FREQ={frequency}, VOL={volume} ---\n"
    )


# --------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------


def is_vhp_connected(port, baudrate=115200, timeout=1):
    try:
        with serial.Serial(port, baudrate=baudrate, timeout=timeout) as ser:
            time.sleep(1)
            return True
    except (serial.SerialException, OSError):
        return False


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
    logging.basicConfig(format="[%(asctime)s] %(message)s", level=logging.INFO)

    inlet = setup_lsl_inlet(config.stream_name)

    fname1 = f"./Recordings/{config.timestamp}_{config.board_id}_baseline_with_VHP_powered_OFF.csv"
    fname2 = f"./Recordings/{config.timestamp}_{config.board_id}_baseline_with_VHP_powered_ON_stim_ON_no_contact_c{config.channel_start}_f{config.frequency_start}_v{config.volume_start}.csv"

    try:
        # ---------------------------- BASELINE 1 ----------------------------
        if not is_vhp_connected(config.serial_port):
            print("\nRecording Baseline 1 (waiting for VHP ON)...")

            # Record baseline with marker 3
            with open(fname1, "a", newline="") as f:
                writer = csv.writer(f)
                record_to_csv(inlet, 10, writer, marker=3)
            
            while not is_vhp_connected(config.serial_port):
                print("Waiting for VHP to power ON...")
                time.sleep(0.5)

            print("\n▶ Baseline 1 started")
            with open(fname1, "a", newline="") as f:
                writer = csv.writer(f)
                record_to_csv(inlet, config.baseline_1, writer, marker=33)

            # countdown_eta("Baseline 1", config.baseline_1)

            print("\n▶ Baseline 1 completed.\n")

        # ---------------------------- BASELINE 2 ----------------------------
        vhpcom = SerialCommunicator(config.serial_port)
        wait_for_space("\n➡️  Place finger(s) 5 cm away from tactors (NO CONTACT)")

        streamer2 = f"file://{fname2}:w"
        # record_to_csv(inlet, 5, fname1, marker=34)

        print("\nRecording Baseline 2 (VHP ON, STIM ON, no contact)...")

        vhpcom.set_channel(config.channel_start)
        vhpcom.set_volume(config.volume_start)
        vhpcom.set_frequency(config.frequency_start)
        vhpcom.start_stream()

        # Record baseline with marker 31
        with open(fname2, "a", newline="") as f:
            writer = csv.writer(f)
            record_to_csv(inlet, config.baseline_2, writer, marker=31)

            # countdown_eta("Baseline 2", config.baseline_2)

            vhpcom.stop_stream()
            record_to_csv(inlet, config.baseline_2, writer, marker=33)

        print("Baseline 2 completed.\n")

        # ---------------------------- SWEEP ----------------------------
        wait_for_space("\n➡️  Place finger(s) ON tactors (CONTACT) – Sweep starts...")

        vhpcom.set_test_mode(1)

        # Total cycles = all parameter combinations × number of stim cycles each
        num_ch = (config.channel_end - config.channel_start) // config.channel_steps + 1
        num_freq = (
            config.frequency_end - config.frequency_start
        ) // config.frequency_steps + 1
        num_vol = (config.volume_end - config.volume_start) // config.volume_steps + 1

        global_total = num_ch * num_freq * num_vol * config.measurements_number
        global_counter = [0]  # mutable so it can be updated inside function
        global_start_time = time.time()

        print(f"\nTotal stim cycles in sweep: {global_total}\n")

        for ch in range(
            config.channel_start, config.channel_end + 1, config.channel_steps
        ):
            for freq in range(
                config.frequency_start, config.frequency_end + 1, config.frequency_steps
            ):
                for vol in range(
                    config.volume_start, config.volume_end + 1, config.volume_steps
                ):

                    # Set parameters
                    vhpcom.set_channel(ch)
                    vhpcom.set_volume(vol)
                    vhpcom.set_frequency(freq)

                    do_measurement(
                        vhpcom,
                        inlet,
                        config,
                        ch,
                        freq,
                        vol,
                        global_counter,
                        global_total,
                        global_start_time,
                    )

        print("\nSweep completed.\n")

        # Write metadata file
        write_metadata(args, config, fname1, fname2)

    except Exception as e:
        logging.error(f"Error: {e}")
        # No board_shim cleanup needed in pylsl version


if __name__ == "__main__":
    main()
