"""
Reads data from Brainflow Device (real or playback) and stream it via LSL

Usage: python brainflow_lsl_server.py -c config/dev_playback.yaml
"""

import argparse
import time
import yaml

from brainflow.board_shim import BoardIds, BoardShim, BrainFlowInputParams
from pylsl import StreamInfo, StreamOutlet


def read_yaml_config(args):
    """Read YAML and return as config object"""
    if args.verbose:
        print(f"* Reading config from {args.config}")

    with open(args.config, encoding="utf8") as file:
        config = yaml.safe_load(file)
    return config


def parse_args():
    """Parse cmdline, read config and return as args,config objects"""
    parser = argparse.ArgumentParser(description="My script description.")

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
    params = BrainFlowInputParams()

    if "Master" in config["Board"]:
        # Playback
        params.file = config["Board"]["File"]
        params.master_board = BoardIds[config["Board"]["Master"]].value
        board_id = BoardIds[config["Board"]["Id"]].value
    else:
        # Live streaming
        if config.board_mac:
            params.mac_address = config["Board"]["Mac"]

        if config.board_serial:
            params.serial_port = config["Board"]["Serial"]

        board_id = BoardIds[config["Board"]["Id"]]

    board_shim = BoardShim(board_id, params)

    board_shim.prepare_session()

    if "Master" in config["Board"]:
        board_shim.config_board("loopback_true")

    return board_shim


def setup_lsl_stream(board_id):

    sampling_rate = BoardShim.get_sampling_rate(board_id)
    eeg_channels_indices = BoardShim.get_eeg_channels(board_id)
    n_channels = len(eeg_channels_indices)

    # Create LSL StreamInfo
    info = StreamInfo(
        name="BrainFlowEEG",
        type="EEG",
        channel_count=n_channels,
        nominal_srate=sampling_rate,
        channel_format="float32",
        source_id=f"brainflow_{board_id}",
    )

    # Add channel labels
    ch = info.desc().append_child("channels")
    for i in eeg_channels_indices:
        ch.append_child("channel").append_child_value("label", f"EEG_{i}")

    # Create the LSL outlet
    outlet = StreamOutlet(info)

    return outlet


def main():
    args, config = parse_args()

    board = setup_brainflow_board(config)

    if "Master" in config["Board"]:
        board_id = BoardIds[config["Board"]["Master"]].value
    else:
        board_id = BoardIds[config["Board"]["id"]]
    eeg_channels_indices = BoardShim.get_eeg_channels(board_id)

    try:
        board.start_stream()

        outlet = setup_lsl_stream(board_id)

        while True:
            data = board.get_board_data()

            if data.shape[1] > 0:
                eeg_data = data[eeg_channels_indices, :]
                outlet.push_chunk(eeg_data.T.tolist())

                time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nStopping stream...")
    finally:
        if board.is_prepared():
            print("Releasing BrainFlow session.")
            board.stop_stream()
            board.release_session()
        print("Script finished.")


if __name__ == "__main__":
    main()
