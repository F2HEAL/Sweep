# Sweep

# Installation Instructions for EEG Sweep Script

## Prerequisites
- Python 3.8-3.12 (recommended: Python 3.11)
- pip package manager

## Quick Installation

### Windows:
1. Run `install_dependencies.bat`
2. Or run these commands manually:
   ```cmd
   pip install numpy==1.26.4 --only-binary=all
   pip install -r requirements.txt


# Manual Installation (if above fails)
If you encounter issues, install packages individually:

## Install in this specific order:
pip install numpy==1.26.4 --only-binary=all
pip install pyserial==3.5
pip install pyyaml==6.0.1
pip install brainflow==5.10.0
pip install pylsl==1.16.1