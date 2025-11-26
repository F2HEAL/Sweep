
## Quick Verification Script

Create `quick_test.py` to verify the installation:

```python
# quick_test.py
import sys

def check_import(module_name):
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False

print("Dependency Check:")
print("=" * 50)

dependencies = {
    'serial': 'pyserial',
    'yaml': 'pyyaml', 
    'brainflow': 'brainflow',
    'pylsl': 'pylsl',
    'numpy': 'numpy'
}

all_ok = True
for module, package in dependencies.items():
    if check_import(module):
        print(f"✓ {package} installed")
    else:
        print(f"✗ {package} missing")
        all_ok = False

print("=" * 50)
if all_ok:
    print("All dependencies installed successfully!")
    print("You can now run the main script.")
else:
    print("Some dependencies are missing.")
    print("Run the installation script again.")

# Test basic functionality
if all_ok:
    try:
        import numpy as np
        print(f"NumPy version: {np.__version__}")
        print("Basic functionality test passed!")
    except Exception as e:
        print(f"Error in basic test: {e}")