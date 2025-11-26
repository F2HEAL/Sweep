#!/bin/bash
echo "Installing EEG Sweep Dependencies..."
echo "==================================="

echo "Step 1: Installing NumPy with pre-compiled wheels..."
pip install numpy==1.26.4 --only-binary=all

echo "Step 2: Installing other dependencies..."
pip install -r requirements.txt

echo
echo "==================================="
echo "Installation completed!"
echo "You can now run: python sweep_CH_Vol_Freq_diff_ON_OFF_3BL_progi.py -h"