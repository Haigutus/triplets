#!/bin/bash

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check for pyenv
if ! command_exists pyenv; then
    echo "pyenv not found. Please install pyenv manually to manage Python versions."
    echo "Installation instructions: https://github.com/pyenv/pyenv#installation"
    echo "After installation, ensure pyenv is in your PATH and initialized in your shell config (e.g., .bashrc or .zshrc)."
    echo "You may need to restart your shell or source your config file."
    exit 1
fi

# Check for pipx
if ! command_exists pipx; then
    echo "pipx not found. Please install pipx manually."
    echo "Recommended: sudo dnf install pipx
pipx ensurepath or sudo apt update
sudo apt install pipx
pipx ensurepath"
    echo "Then add $HOME/.local/bin to your PATH if not already (e.g., export PATH=\"$HOME/.local/bin:$PATH\" in .bashrc or .zshrc)."
    echo "You may need to restart your shell or source your config file."
    exit 1
fi

# Install pipenv using pipx if not found
if ! command_exists pipenv; then
    echo "pipenv not found. Installing via pipx..."
    pipx install pipenv

    # Verify installation
    if ! command_exists pipenv; then
        echo "Failed to install pipenv via pipx. Please install it manually."
        exit 1
    fi
fi

# Set up the environment
echo "Setting up development environment with pipenv..."
# If Pipfile specifies a Python version, pipenv will use pyenv to install it if needed
pipenv install --dev

# Optional: Sync for lockfile consistency
pipenv sync --dev

echo "Development environment setup complete."
echo "To activate the virtual environment, run: pipenv shell"
echo "Or to run commands within it: pipenv run <command>"