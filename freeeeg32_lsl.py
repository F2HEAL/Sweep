from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
from pylsl import StreamInfo, StreamOutlet
import time
import numpy as np

# ---- 1. Setup BrainFlow ----
params = BrainFlowInputParams()
params.serial_port = 'COM10'
board = BoardShim(BoardIds.FREEEEG32_BOARD.value, params)
board.prepare_session()
board.start_stream()

# ---- 2. Get data once to find number of channels ----
data = board.get_board_data()
n_channels = data.shape[0]  # total number of channels

# ---- 3. Setup LSL outlet ----
sfreq = 512     # default sampling rate for FreeEEG32
info = StreamInfo('FreeEEG32', 'EEG', n_channels, sfreq, 'float32', 'freeeeg32_eeg')
outlet = StreamOutlet(info)

print("Streaming FreeEEG32 data over LSL... Press Ctrl+C to stop.")

# ---- 4. Push data in real-time ----SS
try:
    while True:
        data = board.get_board_data()  # shape: (channels, samples)
        # push each sample to LSL
        for sample in data.T:
            outlet.push_sample(sample.tolist())
        time.sleep(0.01)
except KeyboardInterrupt:
    print("Stopping...")
finally:
    board.stop_stream()
    board.release_session()
