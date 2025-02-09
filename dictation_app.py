#!/usr/bin/env python3

import sys
import traceback
import os
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QPushButton, QTextEdit, QLabel, QComboBox, QHBoxLayout,
                            QSystemTrayIcon, QMenu, QFrame, QSpacerItem, QSizePolicy)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QAction, QKeySequence, QShortcut, QFont, QPalette, QColor
import sounddevice as sd
import numpy as np
import torch
import whisper
from pynput import keyboard
import pyautogui
import threading
import queue
import wave
import tempfile
from datetime import datetime

CONFIG_DIR = os.path.expanduser("~/.config/dicktation")
DATA_DIR = os.path.expanduser("~/.local/share/dicktation")

class ModernButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
            }
            QPushButton:pressed {
                background-color: #404040;
            }
            QPushButton:disabled {
                background-color: #1d1d1d;
                color: #808080;
            }
        """)

class ModernComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                color: white;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 5px 10px;
                min-width: 200px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid #2d2d2d;
                border-right: 5px solid #2d2d2d;
                border-top: 5px solid white;
                width: 0;
                height: 0;
                margin-right: 10px;
            }
            QComboBox:hover {
                background-color: #3d3d3d;
            }
        """)

class ModernLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                padding: 5px;
            }
        """)

class RecordingThread(QThread):
    transcription_done = pyqtSignal(str)
    status_update = pyqtSignal(str)
    
    def __init__(self, sample_rate, model):
        super().__init__()
        self.sample_rate = sample_rate
        self.model = model
        self.recording = True
        self.audio_queue = queue.Queue()

    def audio_callback(self, indata, frames, time, status):
        if status:
            self.status_update.emit(f"Audio callback status: {status}")
        mono_data = np.mean(indata, axis=1) if len(indata.shape) > 1 else indata.flatten()
        normalized_data = np.clip(mono_data, -1, 1)
        self.audio_queue.put(normalized_data.copy())

    def run(self):
        try:
            recorded_chunks = []
            
            with sd.InputStream(callback=self.audio_callback,
                              channels=1,
                              samplerate=self.sample_rate,
                              blocksize=1024,
                              dtype=np.float32):
                
                self.status_update.emit("Recording...")
                while self.recording:
                    if not self.audio_queue.empty():
                        recorded_chunks.append(self.audio_queue.get())
                    else:
                        sd.sleep(10)
            
            if recorded_chunks:
                audio_data = np.concatenate(recorded_chunks)
                temp_file = tempfile.mktemp(suffix=".wav")
                
                with wave.open(temp_file, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.sample_rate)
                    wf.writeframes((audio_data * 32767).astype(np.int16).tobytes())
                
                self.status_update.emit("Transcribing...")
                result = self.model.transcribe(
                    temp_file,
                    language="en",
                    fp16=torch.cuda.is_available(),
                    beam_size=5,
                    best_of=5,
                    temperature=0.0,
                    initial_prompt="Convert speech to properly formatted text."
                )
                
                os.remove(temp_file)
                text = result["text"].strip()
                if text:
                    self.transcription_done.emit(text)
                    
                    # Save transcription
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    output_dir = os.path.join(DATA_DIR, "transcriptions")
                    os.makedirs(output_dir, exist_ok=True)
                    output_file = os.path.join(output_dir, f"transcription_{timestamp}.txt")
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(text)
            
            self.status_update.emit("Ready")
            
        except Exception as e:
            self.status_update.emit(f"Error: {str(e)}")

class DictationApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dicktation")
        self.setGeometry(100, 100, 800, 600)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QTextEdit {
                background-color: #2d2d2d;
                color: white;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
            }
            QStatusBar {
                background-color: #252525;
                color: white;
            }
        """)
        
        # Create necessary directories
        os.makedirs(CONFIG_DIR, exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        
        # Initialize audio
        self.setup_audio()
        
        # Setup UI first
        self.setup_ui()
        
        # Then initialize model (which uses UI elements)
        self.setup_model()
        
        # Setup system tray
        self.setup_tray()
        
        # Setup global hotkey
        self.setup_hotkey()
        
        self.recording_thread = None

    def setup_audio(self):
        self.devices = sd.query_devices()
        self.input_devices = [(i, d['name']) for i, d in enumerate(self.devices) 
                            if d['max_input_channels'] > 0]
        self.sample_rate = 16000
        
        # Find Blue Snowball and set as default
        snowball_id = None
        for idx, name in self.input_devices:
            if 'Blue Snowball' in name:
                snowball_id = idx
                break
        
        if snowball_id is not None:
            sd.default.device[0] = snowball_id

    def setup_model(self):
        try:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model = whisper.load_model("turbo", device=device)
            
            cuda_status = "🚀 Using GPU" if torch.cuda.is_available() else "💻 Using CPU"
            self.status_label.setText(f"Ready - {cuda_status}")
        except Exception as e:
            self.status_label.setText(f"Error loading model: {str(e)}")
            raise

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Top controls container
        top_container = QFrame()
        top_container.setStyleSheet("background-color: #252525; border-radius: 10px;")
        top_layout = QVBoxLayout(top_container)
        top_layout.setContentsMargins(20, 20, 20, 20)
        
        # Device selection
        device_layout = QHBoxLayout()
        device_label = ModernLabel("Input Device:")
        self.device_combo = ModernComboBox()
        
        # Populate device combo and select Blue Snowball if available
        snowball_index = 0
        for i, (idx, name) in enumerate(self.input_devices):
            self.device_combo.addItem(name, idx)
            if 'Blue Snowball' in name:
                snowball_index = i
        
        self.device_combo.setCurrentIndex(snowball_index)
        self.device_combo.currentIndexChanged.connect(self.change_device)
        
        device_layout.addWidget(device_label)
        device_layout.addWidget(self.device_combo)
        device_layout.addStretch()
        
        # Recording controls
        self.record_button = ModernButton("Start Recording (Ctrl+6)")
        self.record_button.clicked.connect(self.toggle_recording)
        
        top_layout.addLayout(device_layout)
        top_layout.addWidget(self.record_button)
        
        layout.addWidget(top_container)
        
        # Status label
        self.status_label = ModernLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        
        # Text display
        self.text_display = QTextEdit()
        self.text_display.setReadOnly(True)
        self.text_display.setMinimumHeight(300)
        layout.addWidget(self.text_display)
        
        # Keyboard shortcut
        self.shortcut = QShortcut(QKeySequence("Ctrl+6"), self)
        self.shortcut.activated.connect(self.toggle_recording)

    def setup_tray(self):
        # Load the system theme icon or fall back to a default
        icon = QIcon.fromTheme("audio-input-microphone", QIcon.fromTheme("audio-input-microphone-symbolic"))
        self.setWindowIcon(icon)  # Set window icon (shows in dock)
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(icon)  # Set same icon for tray
        self.tray_icon.setToolTip("Dicktation")
        
        tray_menu = QMenu()
        show_action = QAction("Show", self)
        quit_action = QAction("Quit", self)
        show_action.triggered.connect(self.show)
        quit_action.triggered.connect(self.quit_app)
        
        tray_menu.addAction(show_action)
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def setup_hotkey(self):
        self.keyboard_listener = keyboard.Listener(
            on_press=self.on_key_press,
            on_release=self.on_key_release
        )
        self.keyboard_listener.start()
        self.ctrl_pressed = False

    def on_key_press(self, key):
        if key == keyboard.Key.ctrl:
            self.ctrl_pressed = True
        elif hasattr(key, 'char') and key.char == '6' and self.ctrl_pressed:
            self.toggle_recording()

    def on_key_release(self, key):
        if key == keyboard.Key.ctrl:
            self.ctrl_pressed = False

    def change_device(self, index):
        device_id = self.device_combo.currentData()
        sd.default.device[0] = device_id
        self.status_label.setText(f"Changed input device to: {self.devices[device_id]['name']}")

    def toggle_recording(self):
        if not self.recording_thread or not self.recording_thread.isRunning():
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self):
        self.record_button.setText("Stop Recording")
        self.recording_thread = RecordingThread(self.sample_rate, self.model)
        self.recording_thread.transcription_done.connect(self.handle_transcription)
        self.recording_thread.status_update.connect(self.status_label.setText)
        self.recording_thread.start()

    def stop_recording(self):
        if self.recording_thread and self.recording_thread.isRunning():
            self.recording_thread.recording = False
            self.record_button.setText("Start Recording (Ctrl+6)")

    def handle_transcription(self, text):
        self.text_display.append(text)
        pyautogui.write(text + ' ')

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def quit_app(self):
        if self.recording_thread and self.recording_thread.isRunning():
            self.stop_recording()
            self.recording_thread.wait()
        self.keyboard_listener.stop()
        QApplication.quit()

def signal_handler(sig, frame):
    print("\nExiting...")
    # Stop any active recording
    if QApplication.instance():
        for window in QApplication.instance().topLevelWidgets():
            if isinstance(window, DictationApp):
                window.quit_app()
    sys.exit(0)

def main():
    import signal
    signal.signal(signal.SIGINT, signal_handler)
    app = QApplication(sys.argv)
    
    # Set application-wide dark style
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(35, 35, 35))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
    app.setPalette(palette)
    
    window = DictationApp()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()