# User Manual: BrainFlow LSL Server

This script (`brainflow_lsl_server.py`) acts as a bridge to stream EEG data from a supported hardware device or a recorded CSV file to the **Lab Streaming Layer (LSL)** network.

---

## 1. Prerequisites
Ensure you have the required Python libraries installed:
```powershell
pip install -r requirements.txt
```

## 2. Configuration
The script is driven by YAML files located in the `config/` folder. These files define the hardware parameters.

- **Live Hardware**: Use or create a config like `config/dev_freeeg.yaml`.
    - Ensure the `Serial` port (e.g., `COM10` on Windows or `/dev/ttyUSB0` on Linux) matches your physical connection.
- **Playback Mode**: Use `config/dev_playback.yaml`.
    - Ensure the `File` path points to a valid BrainFlow-formatted CSV recording.
- **Custom Naming**: You can add `StreamName: "MyStream"` under the `Board` section in your YAML to change how the stream appears in LSL.

## 3. Usage
Open a terminal in the project folder and run the script with the `-c` flag.

**Example (Playback):**
```powershell
python brainflow_lsl_server.py -c config/dev_playback.yaml
```

**Example (Live Device):**
```powershell
python brainflow_lsl_server.py -c config/dev_freeeg.yaml -v
```
*(The `-v` flag enables verbose mode, which prints configuration details on startup.)*

## 4. Verifying the Stream
Once the terminal displays "Streaming...", the data is live on your local network. You can verify it using:
1.  **LabRecorder**: The stream will appear in the list as `BrainFlowEEG` (or your custom name).
2.  **LSL View / OpenViBE**: Use any LSL-compatible viewer to visualize the real-time EEG waves.

## 5. Stopping
To stop the stream and safely disconnect the hardware:
- Press **`Ctrl + C`** in the terminal.
- The script will automatically stop the board and release the session to prevent port locking.

---

## Troubleshooting
- **PermissionError**: Ensure no other software (like OpenViBE or another script) is currently occupying the device's COM port.
- **KeyError 'Id'**: The script is case-sensitive. Ensure your YAML file uses `Id` (uppercase I) for the board ID.
- **Firewall**: If other computers can't see the stream, ensure your firewall isn't blocking UDP port `16571`.
