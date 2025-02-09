#!/bin/bash

# Function to install system dependencies
install_dependencies() {
    echo "Installing system dependencies (requires sudo)..."
    sudo apt-get update
    sudo apt-get install -y \
        libxcb-cursor0 \
        libxcb1 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-icccm4 \
        libxcb-xinerama0 \
        libxcb-image0 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-xkb1 \
        libxkbcommon-x11-0
}

# Create user directories and install app
install_app() {
    # Create directories
    mkdir -p "$HOME/.local/share/dicktation"
    mkdir -p "$HOME/.local/bin"
    mkdir -p "$HOME/.local/share/applications"
    mkdir -p "$HOME/.config/dicktation"

    # Create virtual environment using pyenv's Python
    echo "Creating virtual environment..."
    eval "$(pyenv init -)"
    pyenv shell 3.11.0  # or whichever version you prefer
    
    # Ensure pip and venv are installed in the base Python
    python -m ensurepip --upgrade
    python -m pip install --upgrade pip
    python -m pip install virtualenv
    
    # Create virtualenv using virtualenv instead of venv
    python -m virtualenv "$HOME/.local/share/dicktation/venv"
    
    # Function to install dependencies in virtualenv
    install_dependencies() {
        source "$HOME/.local/share/dicktation/venv/bin/activate"
        python -m pip install --upgrade pip
        pip install sounddevice numpy torch openai-whisper pynput pyautogui PyQt6
        deactivate
    }
    
    # Install Python dependencies
    echo "Installing Python dependencies in virtualenv..."
    install_dependencies

    # Create wrapper script that includes pyenv initialization
    cat > "$HOME/.local/bin/dicktation" << 'EOL'
#!/bin/bash
eval "$(pyenv init -)"
pyenv shell 3.11.0
source "$HOME/.local/share/dicktation/venv/bin/activate"
python "$HOME/.local/share/dicktation/dictation_app.py"
deactivate
EOL
    chmod +x "$HOME/.local/bin/dicktation"

    # Copy the main script to the app directory
    cp dictation_app.py "$HOME/.local/share/dicktation/"

    # Create desktop entry with debugging enabled
    cat > "$HOME/.local/share/applications/dicktation.desktop" << EOL
[Desktop Entry]
Name=Dicktation
Comment=Speech-to-text application with GUI
Exec=/usr/bin/env bash -c "cd $HOME && PYENV_ROOT=$HOME/.pyenv PATH=$HOME/.pyenv/bin:$PATH $HOME/.local/bin/dicktation"
Icon=audio-input-microphone
Terminal=true
Type=Application
Categories=Utility;TextTools;
Keywords=dictation;speech;text;
EOL

    # Update desktop database
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$HOME/.local/share/applications"
    fi
}

# Main installation process
echo "Starting installation..."

# First install system dependencies
install_dependencies

# Then install the app as the current user
install_app

echo "Installation complete! You can now launch Dicktation from your application menu."
echo "You may need to log out and log back in for the application to appear in your menu."