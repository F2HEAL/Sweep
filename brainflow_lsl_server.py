"""
Reads data from Brainflow Device (real or playback) and stream it via LSL

This script is intended to be run from the command line. It loads a YAML
configuration describing the hardware (or playback file) to use, initializes
a BrainFlow board interface, and forwards EEG channel samples continuously
to a Lab Streaming Layer (LSL) outlet.  Other applications can connect as
LSL consumers to record or process the data in real time.

Usage:
    python brainflow_lsl_server.py -c config/dev_playback.yaml [-v]

The configuration file must specify a `Board` section. For playback mode the
`Master` field is present along with a CSV `File` path; otherwise the script
attempts to open a live device by serial port or MAC address.
"""

import argparse
import time
import yaml

# brainflow provides a hardware-agnostic API for EEG/physiological devices.
from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams
# pylsl is the Python wrapper for the Lab Streaming Layer (LSL) realtime
# data transport protocol.
from pylsl import StreamInfo, StreamOutlet


def read_yaml_config(args):
    """Load the YAML configuration specified on the command line.

    The parser does not itself validate contents; downstream functions expect
    a `Board` key with appropriate sub-fields. Enabling `--verbose` prints the
    path being read which can help when debugging multiple configs.
    """
    if args.verbose:
        print(f"* Reading config from {args.config}")

    with open(args.config, encoding="utf8") as file:
        config = yaml.safe_load(file)
    return config


def parse_args():
    """Parse command-line arguments and load configuration.

    Returns a tuple of the `args` namespace and the parsed config dict.  The
    only required flag is `-c/--config`; verbose logging can be toggled with
    `-v`.
    """
    parser = argparse.ArgumentParser(description="BrainFlow -> LSL bridge")

    parser.add_argument(
        "-c", "--config", type=str, required=True, help="Device configuration file path"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    config = read_yaml_config(args)

    return args, config


def setup_brainflow_board(config):
    """Create and prepare a BrainFlow `BoardShim` instance.

    The configuration dictionary is expected to contain a `Board` section.
    If the section contains a `Master` key, the script will operate in
    playback mode: it reads from the CSV file specified by `File` and treats
    the `Master` board ID as the source of timestamps.  Otherwise it attempts
    to connect to a physical board given by `Id` with optional `Mac` or
    `Serial` fields.

    The returned `board_shim` is prepared but not yet streaming; the caller
    must call `start_stream` when ready.
    """
    params = BrainFlowInputParams()

    if "Master" in config["Board"]:
        # Playback configuration; BrainFlow will read from a CSV file instead of
        # talking to hardware.  Set the master board so that time stamps are
        # generated correctly for the “slave” device.
        params.file = config["Board"]["File"]
        params.master_board = BoardIds[config["Board"]["Master"]].value
        board_id = BoardIds[config["Board"]["Id"]].value
    else:
        # Live streaming: populate connection parameters if supplied.
        if config["Board"].get("Mac"):
            params.mac_address = config["Board"]["Mac"]

        if config["Board"].get("Serial"):
            params.serial_port = config["Board"]["Serial"]

        board_id = BoardIds[config["Board"]["Id"]]

    board_shim = BoardShim(board_id, params)

    # Allocate resources and perform initial handshake with the board.
    board_shim.prepare_session()

    if "Master" in config["Board"]:
        # In playback we enable loopback mode so that the board will replay the
        # data continuously rather than stopping at end-of-file.
        board_shim.config_board("loopback_true")

    return board_shim


def setup_lsl_stream(board_id, stream_name="BrainFlowEEG"):
    """Construct and return an LSL `StreamOutlet` for the specified board.

    The BrainFlow API is queried to determine the device’s sampling rate and
    which channel indices correspond to EEG electrodes.  These values are
    baked into the stream’s metadata, which is important for any consumer
    applications that rely on channel labels or timing information.
    """

    sampling_rate = BoardShim.get_sampling_rate(board_id)
    eeg_channels_indices = BoardShim.get_eeg_channels(board_id)
    n_channels = len(eeg_channels_indices)

    # Basic stream metadata describing the number of channels and rate.
    info = StreamInfo(
        name=stream_name,
        type="EEG",
        channel_count=n_channels,
        nominal_srate=sampling_rate,
        channel_format="float32",
        source_id=f"brainflow_{board_id}",
    )

    # Append individual channel descriptors so that clients can see labels.
    ch = info.desc().append_child("channels")
    for i in eeg_channels_indices:
        ch.append_child("channel").append_child_value("label", f"EEG_{i}")

    outlet = StreamOutlet(info)  # The object used to push samples to LSL.

    return outlet


def main():
    # parse command line and read yaml
    args, config = parse_args()

    # initialize board object (playback or live depending on config)
    board = setup_brainflow_board(config)

    # determine which board ID provides the EEG channels used for LSL
    if "Master" in config["Board"]:
        board_id = BoardIds[config["Board"]["Master"]].value
    else:
        board_id = BoardIds[config["Board"]["Id"]].value
    eeg_channels_indices = BoardShim.get_eeg_channels(board_id)

    try:
        board.start_stream()  # begin collecting data from the device

        # Check if a custom stream name is provided in the config
        stream_name = config["Board"].get("StreamName", "BrainFlowEEG")
        outlet = setup_lsl_stream(board_id, stream_name)  # prepare LSL outlet

        # continuously pull new samples and forward them to LSL
        while True:
            data = board.get_board_data()

            # data is a 2D numpy array; second dimension length zero means no
            # new samples available yet.
            if data.shape[1] > 0:
                # select only the EEG channels and transpose to shape (n,Ch)
                eeg_data = data[eeg_channels_indices, :]
                outlet.push_chunk(eeg_data.T.tolist())

                # sleep briefly to avoid spinning the CPU for no reason
                time.sleep(0.1)

    except KeyboardInterrupt:
        # allow clean shutdown when user presses Ctrl+C
        print("\nStopping stream...")
    finally:
        # always release resources regardless of how loop exited
        if board.is_prepared():
            print("Releasing BrainFlow session.")
            board.stop_stream()
            board.release_session()
        print("Script finished.")


if __name__ == "__main__":
    main()
