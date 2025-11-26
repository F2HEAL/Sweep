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
import sys
import csv
from threading import Thread
from pylsl import StreamInlet, resolve_stream

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
        self.stream_name = device['Board'].get('StreamName', 'SynAmpsRT')

        self.serial_port = device['VHP']['Serial']
        self.verbose = args.verbose
        self.timestamp = datetime.now().strftime("%y%m%d-%H%M")


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
    parser = argparse.ArgumentParser(description="EEG/VHP sync (SynAmpsRT via LSL)")
    parser.add_argument('-m', '--measureconf', required=True, help="Path to YAML measurement configuration file")
    parser.add_argument('-d', '--deviceconf', required=True, help="Path to YAML device configuration file")
    parser.add_argument('-v', '--verbose', action='count', default=0, help="Verbose level up to 5")
    args = parser.parse_args()
    return args, Config(parse_yaml_file(args.measureconf), parse_yaml_file(args.deviceconf), args)


# --------------------------------------------------------------------
# LSL Setup
# --------------------------------------------------------------------

def setup_lsl_inlet(stream_name="SynAmpsRT"):
    logging.info(f"Resolving LSL stream: {stream_name}")
    streams = resolve_stream('name', stream_name)
    inlet = StreamInlet(streams[0])
    info = inlet.info()
    logging.info(f"Connected to LSL stream: {info.name()} ({info.channel_count()} ch @ {info.nominal_srate()} Hz)")
    return inlet


# --------------------------------------------------------------------
# Measurement Functions
# --------------------------------------------------------------------

def record_to_csv(inlet, duration, fname, marker=None):
    """Record samples from LSL inlet to CSV for given duration."""
    with open(fname, "w", newline="") as f:
        writer = csv.writer(f)
        start = time.time()
        while time.time() - start < duration:
            sample, ts = inlet.pull_sample()
            if marker is not None:
                sample.append(marker)
            writer.writerow([ts] + sample)


def do_measurement(com, inlet, config, channel, frequency, volume,
                   global_counter, global_total, global_start_time):
    """Full measurement including Baseline 3 + stim cycles with progress + ETA."""

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

    # Baseline 3
    print("\nBaseline 3 (contact) recording...")
    record_to_csv(inlet, config.baseline_3, fname, marker=333)

    # Stim cycles
    cycles = config.measurements_number
    on_dur = config.measurements_duration_on
    off_dur = config.measurements_duration_off

    print(f"\nStim cycles: {cycles} cycles (ON={on_dur}s, OFF={off_dur}s)\n")

    for cycle in range(1, cycles + 1):
        # ON
        print(f"Cycle {cycle}/{cycles} — ON period")
        com.start_stream()
        record_to_csv(inlet, on_dur, fname, marker=1)
        com.stop_stream()

        # OFF
        print(f"Cycle {cycle}/{cycles} — OFF period")
        record_to_csv(inlet, off_dur, fname, marker=11)

        global_counter[0] += 1
        progress_bar("Global sweep",
                     global_counter[0],
                     global_total,
                     global_start_time)
        print("\n")

    print(f"--- Measurement Completed CH={channel}, FREQ={frequency}, VOL={volume} ---\n")


# --------------------------------------------------------------------
# MAIN SCRIPT
# --------------------------------------------------------------------

def main():
    args, config = parse_cmdline()
    logging.basicConfig(format='[%(asctime)s] %(message)s', level=logging.INFO)

    inlet = setup_lsl_inlet(config.stream_name)

    fname1 = f"./Recordings/{config.timestamp}_{config.board_id}_baseline_with_VHP_powered_OFF.csv"
    fname2 = f"./Recordings/{config.timestamp}_{config.board_id}_baseline_with_VHP_powered_ON_stim_ON_no_contact_c{config.channel_start}_f{config.frequency_start}_v{config.volume_start}.csv"

    try:
        # Baseline 1
        if not is_vhp_connected(config.serial_port):
            print("\nRecording Baseline 1 (waiting for VHP ON)...")
            record_to_csv(inlet, config.baseline_1, fname1, marker=3)
            print("Baseline 1 completed.\n")

        # Baseline 2
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
        # No board_shim cleanup needed with pylsl

if __name__ == "__main__":
    main()
