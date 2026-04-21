"""
Build script for Windows (build_rust.bat)
Run this after installing Rust: https://rustup.rs
"""

import subprocess
import sys
import os

os.chdir(os.path.join(os.path.dirname(__file__), "rust_scorer"))

print("=== Building Rust scoring core (PyO3) ===")

# Check if Rust is installed
try:
    subprocess.run(["cargo", "--version"], check=True, capture_output=True)
except FileNotFoundError:
    print("ERROR: Rust not found. Install it from https://rustup.rs")
    sys.exit(1)

# Install maturin if needed
try:
    subprocess.run(["maturin", "--version"], check=True, capture_output=True)
except FileNotFoundError:
    print("Installing maturin...")
    subprocess.run([sys.executable, "-m", "pip", "install", "maturin"], check=True)

# Build
print("Compiling Rust -> Python extension...")
subprocess.run(["maturin", "develop", "--release"], check=True)

print("\n=== Build complete ===")
