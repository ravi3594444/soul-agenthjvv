#!/bin/bash
# Build the Rust scoring core as a Python extension module
# Run this after installing Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

set -e

echo "=== Building Rust scoring core (PyO3) ==="

cd "$(dirname "$0")/rust_scorer"

# Check if Rust is installed
if ! command -v cargo &> /dev/null; then
    echo "ERROR: Rust not found. Install it first:"
    echo "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    echo "  source ~/.cargo/env"
    exit 1
fi

# Check if maturin is installed (PyO3 build tool)
if ! command -v maturin &> /dev/null; then
    echo "Installing maturin (PyO3 build tool)..."
    pip install maturin
fi

# Build and install the Rust extension
echo "Compiling Rust → Python extension..."
maturin develop --release

echo ""
echo "=== Build complete ==="
echo "The scorer_core module is now available in Python."
echo "Run 'python -c \"from scorer_core import calculate_score; print(calculate_score(True, False, 50, True, 3, 200))\"' to verify."
