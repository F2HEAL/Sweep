"""
4572b9490b92dbcddfef8771c5f2fafda0e7bfc3
Performs EEG measurements following the sweep protocol

Usage: python sweep_lsl.py  -m config/sweep_dev.yaml -d config/dev_lsl_stream.yaml
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import csv
import serial
import sys
import yaml
from pylsl import StreamInlet, resolve_stream

# --- CONSTANTS ---
RANDOM_SEED: int = 42
SERIAL_BAUDRATE: int = 115200
SERIAL_TIMEOUT_SEC: float = 0.1
DEFAULT_STREAM_NAME: str = "SynAmpsRT"
EEG_CHANNELS_COUNT: int = 33
PROGRESS_BAR_LENGTH: int = 30


def wait_for_space(prompt: str) -> None:
    """
    Prompts the user and waits for a spacebar press.

    Args:
        prompt: The message to display to the user.
    """
    input(f"\n{prompt}\nPress SPACEBAR then ENTER when ready...\n")


class Config:
    """Holds information from parsed configuration files."""

    def __init__(
        self, measurement: Dict[str, Any], device: Dict[str, Any], args: argparse.Namespace
    ) -> None:
        """
        Initializes configuration from YAML dictionaries and arguments.

        Args:
            measurement: Dictionary containing measurement protocol.
            device: Dictionary containing device hardware info.
            args: Command line arguments.
        """
        self.channel_start: int = measurement["Channel"]["Start"]
        self.channel_end: int = measurement["Channel"]["End"]
        self.channel_steps: int = measurement["Channel"]["Steps"]
        self.volume_start: int = measurement["Volume"]["Start"]
        self.volume_end: int = measurement["Volume"]["End"]
        self.volume_steps: int = measurement["Volume"]["Steps"]
        self.frequency_start: int = measurement["Frequency"]["Start"]
        self.frequency_end: int = measurement["Frequency"]["End"]
        self.frequency_steps: int = measurement["Frequency"]["Steps"]
        self.measurements_number: int = measurement["Measurements"]["Number"]
        self.measurements_duration_on: float = measurement["Measurements"]["Duration_on"]
        self.measurements_duration_off: float = measurement["Measurements"]["Duration_off"]
        self.baseline_1: int = measurement["Baselines"]["Baseline_1"]
        self.baseline_2: int = measurement["Baselines"]["Baseline_2"]
        self.baseline_3: int = measurement["Baselines"]["Baseline_3"]

        self.board_id: str = str(device["Board"]["Id"])
        self.stream_name: str = device["Board"].get("StreamName", DEFAULT_STREAM_NAME)

        self.serial_port: str = device["VHP"]["Serial"]
        self.verbose: int = args.verbose
        self.timestamp: str = datetime.now().strftime("%y%m%d-%H%M")


class SerialCommunicator:
    """Handles serial communication with the VHP device."""

    def __init__(self, port: str) -> None:
        """
        Opens a serial connection to the VHP device.

        Args:
            port: Serial port name (e.g., 'COM3').
        """
        self.port: str = port
        self.ser: serial.Serial = serial.Serial(
            port=self.port, baudrate=SERIAL_BAUDRATE, timeout=SERIAL_TIMEOUT_SEC
        )
        if not self.ser.is_open:
            self.ser.open()
        time.sleep(2)

    def __del__(self) -> None:
        """Closes the serial connection upon object destruction."""
        if hasattr(self, "ser") and self.ser.is_open:
            self.ser.close()
            logging.info("Serial connection closed.")

    def _send_command(self, command: str) -> None:
        """
        Sends a command via serial and logs responses.

        Args:
            command: The command string to send.
        """
        self.ser.write((command + "\n").encode("utf-8"))
        time.sleep(0.05)
        while self.ser.in_waiting > 0:
            response = self.ser.readline().decode("utf-8", errors="ignore").strip()
            logging.debug("Serial VHP Received: %s", response)

    def set_channel(self, channel: int) -> None:
        """Sets the VHP channel."""
        self._send_command(f"C{max(0, min(8, channel))}")

    def set_volume(self, volume: int) -> None:
        """Sets the VHP volume."""
        self._send_command(f"V{max(0, min(100, volume))}")

    def set_frequency(self, frequency: int) -> None:
        """Sets the VHP stimulation frequency."""
        self._send_command(f"F{frequency}")

    def set_test_mode(self, enabled: bool) -> None:
        """Enables or disables VHP test mode."""
        self._send_command(f"M{1 if enabled else 0}")

    def start_stream(self) -> None:
        """Starts the VHP stimulation stream."""
        self._send_command("1")

    def stop_stream(self) -> None:
        """Stops the VHP stimulation stream."""
        self._send_command("0")


def parse_yaml_file(file_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Parses a YAML file and returns its content as a dictionary.

    Args:
        file_path: Path to the YAML file.

    Returns:
        The parsed dictionary.
    """
    with open(file_path, "r") as file:
        return yaml.safe_load(file)


def parse_cmdline() -> Tuple[argparse.Namespace, Config]:
    """
    Parses command-line arguments and initializes configuration.

    Returns:
        A tuple containing (parsed_arguments, Config_object).
    """
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


def setup_lsl_inlet(stream_name: str = DEFAULT_STREAM_NAME) -> StreamInlet:
    """
    Resolves an LSL stream by name and sets up an inlet.

    Args:
        stream_name: The name of the LSL stream to connect to.

    Returns:
        The established StreamInlet object.
    """
    logging.info(f"Resolving LSL stream: {stream_name}")
    streams = resolve_stream("name", stream_name)
    inlet = StreamInlet(streams[0])
    info = inlet.info()
    logging.info(
        f"Connected to LSL stream: {info.name()} ({info.channel_count()} "
        f"ch @ {info.nominal_srate()} Hz)"
    )
    return inlet


# --------------------------------------------------------------------
# Recording Functions
# --------------------------------------------------------------------
def record_to_csv(
    inlet: StreamInlet,
    duration: float,
    writer: Any,
    marker: Optional[Union[int, float, str]] = None,
) -> bool:
    """
    Records samples from an LSL inlet to a CSV writer for a given duration.

    Args:
        inlet: The LSL stream inlet.
        duration: Duration of recording in seconds.
        writer: An open CSV writer object.
        marker: Optional marker code to associate with the first sample.

    Returns:
        True if the marker was written, False otherwise.
    """
    start: float = time.time()
    marker_written: bool = False

    while time.time() - start < duration:
        # Use pull_chunk to be more efficient than pull_sample
        samples, timestamps = inlet.pull_chunk(timeout=0.1)
        if not timestamps:
            continue

        for sample, ts in zip(samples, timestamps):
            eeg_values: List[float] = sample[:EEG_CHANNELS_COUNT]
            label: str = ""

            if marker is not None and not marker_written:
                label = str(marker)
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
    com: SerialCommunicator,
    inlet: StreamInlet,
    config: Config,
    channel: int,
    frequency: int,
    volume: int,
    global_counter: List[int],
    global_total: int,
    global_start_time: float,
) -> None:
    """
    Executes a single parameter combination measurement cycle.

    Args:
        com: Serial communicator for VHP.
        inlet: LSL stream inlet.
        config: Configuration object.
        channel: Target VHP channel.
        frequency: Target VHP frequency.
        volume: Target VHP volume.
        global_counter: Mutable global step counter.
        global_total: Total steps in the sweep.
        global_start_time: Start time of the entire sweep.
    """

    def format_time(sec: float) -> str:
        """Formats seconds into human-readable time."""
        total_sec: int = int(sec)
        if total_sec < 60:
            return f"{total_sec}s"
        return f"{total_sec//60}m{total_sec%60:02d}s"

    def progress_bar(
        prefix: str, current: int, total: int, start_time: float, length: int = PROGRESS_BAR_LENGTH
    ) -> None:
        """Displays a progress bar with ETA."""
        pct: float = current / total
        filled: int = int(pct * length)
        bar: str = "█" * filled + "-" * (length - filled)
        elapsed: float = time.time() - start_time
        eta: float = elapsed / current * (total - current) if current > 0 else 0
        sys.stdout.write(
            f"\r{prefix} |{bar}| {pct*100:5.1f}%"
            f"  ETA: {format_time(eta)}"
            f"  Elapsed: {format_time(elapsed)}"
        )
        sys.stdout.flush()

    logging.info(f"Measuring: CH={channel}, FREQ={frequency}, VOL={volume}")
    
    recordings_dir: Path = Path("./Recordings")
    recordings_dir.mkdir(exist_ok=True)
    
    fname: Path = (
        recordings_dir
        / f"{config.timestamp}_{config.board_id}_c{channel}_f{frequency}_v{volume}.csv"
    )

    with open(fname, "w", newline="") as f:
        writer = csv.writer(f)
        # ---------------------------------------------------------
        # Baseline 3 (with progress bar + ETA)
        # ---------------------------------------------------------
        print("\nBaseline 3 (contact) recording...")
        record_to_csv(inlet, config.baseline_3, writer, marker=333)

        # Stim cycles
        cycles: int = config.measurements_number
        on_dur: float = config.measurements_duration_on
        off_dur: float = config.measurements_duration_off

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


# --------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------


def is_vhp_connected(port: str, baudrate: int = SERIAL_BAUDRATE, timeout: float = 1.0) -> bool:
    """
    Checks if the VHP device is reachable via serial.

    Args:
        port: Serial port name.
        baudrate: Serial baudrate.
        timeout: Connection timeout in seconds.

    Returns:
        True if connected, False otherwise.
    """
    try:
        with serial.Serial(port, baudrate=baudrate, timeout=timeout) as ser:
            time.sleep(1)
            return True
    except (serial.SerialException, OSError):
        return False


def write_metadata(
    args: argparse.Namespace, config: Config, fname1: Path, fname2: Path
) -> None:
    """
    Writes measurement metadata to a text file.

    Args:
        args: Command line arguments.
        config: Configuration object.
        fname1: Path to the first baseline file.
        fname2: Path to the second baseline file.
    """
    recordings_dir: Path = Path("./Recordings")
    fname: Path = recordings_dir / f"{config.timestamp}_metadata.txt"
    
    with open(fname, "w") as f:
        readable_timestamp: str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        f.write(f"Recording on: {readable_timestamp}\n\n")
        f.write("*** Measure Configuration ***\n")
        f.write(Path(args.measureconf).read_text())
        f.write("\n*** Device Configuration ***\n")
        f.write(Path(args.deviceconf).read_text())
        f.write(f"\nBaseline 1 (VHP OFF>ON): {fname1}\n")
        f.write(f"Baseline 2 (VHP ON, STIM ON, no contact): {fname2}\n")


# --------------------------------------------------------------------
# ETA + PROGRESS FUNCTIONS
# --------------------------------------------------------------------


def format_time_hms(seconds: float) -> str:
    """
    Converts seconds into human-readable HH:MM:SS or MM:SS format.

    Args:
        seconds: Total seconds.

    Returns:
        Formatted time string.
    """
    total_seconds: int = int(seconds)
    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        return f"{total_seconds // 60}m{total_seconds % 60:02d}s"
    return f"{total_seconds // 3600}h{(total_seconds % 3600) // 60:02d}m"


def print_progress(
    prefix: str, current: int, total: int, start_time: float, length: int = PROGRESS_BAR_LENGTH
) -> None:
    """
    Displays a progress bar with an ETA.

    Args:
        prefix: Label for the progress bar.
        current: Current step index.
        total: Total number of steps.
        start_time: Operation start timestamp.
        length: Character length of the bar.
    """
    percent: float = current / total
    filled: int = int(length * percent)
    bar: str = "█" * filled + "-" * (length - filled)

    elapsed: float = time.time() - start_time
    rate: float = elapsed / current if current > 0 else 0
    eta: float = rate * (total - current) if current > 0 else 0

    sys.stdout.write(
        f"\r{prefix} |{bar}| {percent*100:6.2f}%"
        f"  ETA: {format_time_hms(eta)}"
        f"  Elapsed: {format_time_hms(elapsed)}"
    )
    sys.stdout.flush()


# --------------------------------------------------------------------
# MAIN SCRIPT
# --------------------------------------------------------------------


def main() -> None:
    """Main execution entry point."""
    args, config = parse_cmdline()
    logging.basicConfig(format="[%(asctime)s] %(message)s", level=logging.INFO)

    inlet: StreamInlet = setup_lsl_inlet(config.stream_name)

    recordings_dir: Path = Path("./Recordings")
    recordings_dir.mkdir(exist_ok=True)
    
    fname1: Path = recordings_dir / f"{config.timestamp}_{config.board_id}_baseline_with_VHP_powered_OFF.csv"
    fname2: Path = (
        recordings_dir
        / f"{config.timestamp}_{config.board_id}_baseline_with_VHP_powered_ON_stim_ON_no_contact_"
        f"c{config.channel_start}_f{config.frequency_start}_v{config.volume_start}.csv"
    )

    try:
        # ---------------------------- BASELINE 1 ----------------------------
        if not is_vhp_connected(config.serial_port):
            logging.info("Recording Baseline 1 (waiting for VHP ON)...")

            # Record baseline with marker 3
            with open(fname1, "a", newline="") as f:
                writer = csv.writer(f)
                record_to_csv(inlet, 10.0, writer, marker=3)
            
            while not is_vhp_connected(config.serial_port):
                logging.info("Waiting for VHP to power ON...")
                time.sleep(0.5)

            logging.info("Baseline 1 started")
            with open(fname1, "a", newline="") as f:
                writer = csv.writer(f)
                record_to_csv(inlet, float(config.baseline_1), writer, marker=33)

            logging.info("Baseline 1 completed.")

        # ---------------------------- BASELINE 2 ----------------------------
        vhpcom: SerialCommunicator = SerialCommunicator(config.serial_port)
        wait_for_space("➡️  Place finger(s) 5 cm away from tactors (NO CONTACT)")

        logging.info("Recording Baseline 2 (VHP ON, STIM ON, no contact)...")

        vhpcom.set_channel(config.channel_start)
        vhpcom.set_volume(config.volume_start)
        vhpcom.set_frequency(config.frequency_start)
        vhpcom.start_stream()

        # Record baseline with marker 31
        with open(fname2, "a", newline="") as f:
            writer = csv.writer(f)
            record_to_csv(inlet, float(config.baseline_2), writer, marker=31)

            vhpcom.stop_stream()
            record_to_csv(inlet, float(config.baseline_2), writer, marker=33)

        logging.info("Baseline 2 completed.")

        # ---------------------------- SWEEP ----------------------------
        wait_for_space("➡️  Place finger(s) ON tactors (CONTACT) – Sweep starts...")

        vhpcom.set_test_mode(True)

        num_ch: int = (config.channel_end - config.channel_start) // config.channel_steps + 1
        num_freq: int = (
            config.frequency_end - config.frequency_start
        ) // config.frequency_steps + 1
        num_vol: int = (config.volume_end - config.volume_start) // config.volume_steps + 1

        global_total: int = num_ch * num_freq * num_vol * config.measurements_number
        global_counter: List[int] = [0]
        global_start_time: float = time.time()

        logging.info(f"Total stim cycles in sweep: {global_total}")

        for ch in range(
            config.channel_start, config.channel_end + 1, config.channel_steps
        ):
            for freq in range(
                config.frequency_start, config.frequency_end + 1, config.frequency_steps
            ):
                for vol in range(
                    config.volume_start, config.volume_end + 1, config.volume_steps
                ):
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

        logging.info("Sweep completed.")
        write_metadata(args, config, fname1, fname2)

    except Exception as e:
        logging.error(f"Error during execution: {e}")


if __name__ == "__main__":
    import numpy as np
    np.random.seed(RANDOM_SEED)
    main()


if __name__ == "__main__":
    main()
