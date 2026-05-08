"""
CAPcorder: LSL-Controlled Webcam Recorder

Overview
--------
CAPcorder is a webcam + audio recorder that is controlled via Lab Streaming Layer (LSL).
It listens for control commands on one or more LSL streams and records synchronized
video (and optional audio) while publishing frame counters and status updates.

Key Design
----------
- Multiple external apps (MATLAB, PsychoPy, etc.) can send commands simultaneously
- Each app creates its own LSL outlet (e.g., CAPcorderControl_MATLAB)
- CAPcorder listens to ALL streams with type="videocontrol" and name starting with "CAPcorderControl"
- Commands are merged locally (last command wins)

Supported Commands (LSL string format)
-------------------------------------
Format: "key: value; key: value; ..."

Common fields:
    action: start | stop
    filename: <string>
    output_dir: <path>
    timestamp: <float> (LSL or task time)
    width: <int>
    height: <int>
    fps: <float>
    frame_number: <int>
    audio: true | false
    preview: true | false
    debug: true | false
    camera: <int>

Example:
    "action: start; filename: test; width: 640; height: 480; fps: 30"

LSL Streams Used
----------------
Inputs:
    type="videocontrol"  (from MATLAB / PsychoPy / etc.)

Outputs:
    CAPcorderFrames  → frame index (int)
    CAPcorderStatus  → recording state + metadata (string)

Typical Workflow
----------------
1. Launch CAPcorder (this app)
2. Start LabRecorder (optional, to log streams)
3. Launch experiment (MATLAB / PsychoPy)
4. Send "action: start" over LSL
5. Run task
6. Send "action: stop"

Notes
-----
- Multiple control streams are supported (no shared stream required)
- Uses non-blocking multi-inlet listener for robustness
- Designed for multi-machine LSL setups
- Windows paths should use forward slashes or be quoted if containing colons

"""

from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import threading
import time
import textwrap
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import sys
import os

import cv2
import numpy as np
from pylsl import StreamInfo, StreamInlet, StreamOutlet, local_clock, resolve_byprop

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception:
    tk = None
    filedialog = None
    messagebox = None
    ttk = None

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

try:
    import sounddevice as sd
except Exception:
    sd = None


DEFAULT_RESOLUTION_PRESETS = {
    "480p": (640, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
}
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS  # PyInstaller temp folder
    except AttributeError:
        base_path = os.path.abspath(".")  # normal Python run

    return os.path.join(base_path, relative_path)

@dataclass
class RecorderSettings:
    camera_index: int | None = None
    width: int = 640
    height: int = 480
    fps: float = 30.0
    filename: str = "recording"
    output_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent / "recordings")
    initial_frame_number: int = 1
    control_stream_name: str = "CAPcorderControl"
    frame_stream_name: str = "CAPcorderFrames"
    status_stream_name: str = "CAPcorderStatus"
    preview_window_name: str = "CAPcorder Preview"
    codec: str = "XVID"
    audio_enabled: bool = True
    preview_enabled: bool = True
    show_debug_text: bool = False
    use_ui: bool = True
    trigger_timestamp: float | None = None


def parse_args() -> RecorderSettings:
    parser = argparse.ArgumentParser(description="CAPcorder webcam recorder")
    parser.add_argument("--camera", type=int, default=None, help="Camera index. Defaults to the first external camera.")
    parser.add_argument("--resolution", choices=sorted(DEFAULT_RESOLUTION_PRESETS), default="480p", help="Resolution preset.")
    parser.add_argument("--width", type=int, default=None, help="Capture width in pixels.")
    parser.add_argument("--height", type=int, default=None, help="Capture height in pixels.")
    parser.add_argument("--fps", type=float, default=30.0, help="Capture rate in Hz.")
    parser.add_argument("--filename", default="recording", help="Base filename for saved videos.")
    parser.add_argument("--output-dir", default=None, help="Folder for saved videos.")
    parser.add_argument("--frame-number", type=int, default=1, help="Initial frame number pushed to LSL.")
    parser.add_argument("--control-stream", default="CAPcorderControl", help="LSL inlet stream name for remote control.")
    parser.add_argument("--frame-stream", default="CAPcorderFrames", help="LSL outlet stream name for frame numbers.")
    parser.add_argument("--status-stream", default="CAPcorderStatus", help="LSL outlet stream name for recording status.")
    parser.add_argument("--audio", dest="audio", action="store_true", default=True, help="Enable microphone recording when supported.")
    parser.add_argument("--no-audio", dest="audio", action="store_false", help="Disable microphone recording.")
    parser.add_argument("--no-ui", action="store_true", help="Disable the Tk control window and use preview only.")
    parser.add_argument("--timestamp", type=float, default=None, help="Optional task timestamp to store with the recording.")

    args = parser.parse_args()
    width, height = DEFAULT_RESOLUTION_PRESETS[args.resolution]
    if args.width:
        width = args.width
    if args.height:
        height = args.height

    output_dir = Path(args.output_dir).expanduser() if args.output_dir else Path(__file__).resolve().parent / "recordings"
    print(output_dir)

    return RecorderSettings(
        camera_index=args.camera,
        width=width,
        height=height,
        fps=args.fps,
        filename=args.filename,
        output_dir=output_dir,
        initial_frame_number=args.frame_number,
        control_stream_name=args.control_stream,
        frame_stream_name=args.frame_stream,
        status_stream_name=args.status_stream,
        audio_enabled=args.audio,
        preview_enabled=True,
        show_debug_text=False,
        use_ui=not args.no_ui,
        trigger_timestamp=args.timestamp,
    )


def now_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

''' # doesnt work to push to inlets, must be unique
def create_control_outlet(stream_name: str) -> StreamOutlet:
    info = StreamInfo(
        name=stream_name,
        type="videocontrol",
        channel_count=1,
        channel_format="string",
        source_id="capcorder-control-master",
    )
    return StreamOutlet(info)
'''

def create_frame_outlet(stream_name: str) -> StreamOutlet:
    info = StreamInfo(
        name=stream_name,
        type="videostream",
        channel_count=1,
        channel_format="int32",
        source_id=str(uuid.uuid4()),
    )
    info.desc().append_child_value("role", "frame_counter")
    return StreamOutlet(info)


def create_status_outlet(stream_name: str) -> StreamOutlet:
    info = StreamInfo(
        name=stream_name,
        type="videostatus",
        channel_count=1,
        channel_format="string",
        source_id=str(uuid.uuid4()),
    )
    info.desc().append_child_value("message_format", "key: value; key: value")
    return StreamOutlet(info)


def enumerate_cameras(max_index: int = 6) -> list[dict[str, Any]]:
    cameras: list[dict[str, Any]] = []
    for index in range(max_index):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            continue
        success, _frame = cap.read()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        cameras.append(
            {
                "index": index,
                "opened": bool(success),
                "width": width,
                "height": height,
                "fps": fps,
                "label": f"Camera {index} ({width}x{height} @ {fps:.2f} Hz)",
            }
        )
        cap.release()
    return cameras


def choose_default_camera(available_cameras: list[dict[str, Any]]) -> int:
    if not available_cameras:
        raise RuntimeError("No cameras were detected.")
    if len(available_cameras) == 1:
        return available_cameras[0]["index"]
    external_candidates = [camera for camera in available_cameras if camera["index"] != 0]
    return external_candidates[0]["index"] if external_candidates else available_cameras[0]["index"]


def create_capture(camera_index: int, width: int, height: int, fps: float) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {camera_index}.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap


def coerce_value(raw_value: str) -> Any:
    value = raw_value.strip()
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def parse_control_message(message: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for part in message.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        result[key.strip().lower()] = coerce_value(value)
    return result


class AudioRecorder:
    def __init__(self, wav_path: Path) -> None:
        if sd is None:
            raise RuntimeError("sounddevice is not available.")
        self.wav_path = wav_path
        self.stream = None
        self.wave_file = None
        self.channels = 1
        self.sample_rate = 44100
        self.lock = threading.Lock()

    def start(self) -> None:
        device_info = sd.query_devices(kind="input")
        self.channels = max(1, min(int(device_info.get("max_input_channels", 1) or 1), 2))
        default_samplerate = device_info.get("default_samplerate", 44100) or 44100
        self.sample_rate = int(default_samplerate)
        self.wave_file = wave.open(str(self.wav_path), "wb")
        self.wave_file.setnchannels(self.channels)
        self.wave_file.setsampwidth(2)
        self.wave_file.setframerate(self.sample_rate)

        def callback(indata, _frames, _time_info, status) -> None:
            if status:
                return
            with self.lock:
                if self.wave_file is not None:
                    self.wave_file.writeframes(indata.tobytes())

        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=callback,
        )
        self.stream.start()

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        with self.lock:
            if self.wave_file is not None:
                self.wave_file.close()
                self.wave_file = None
'''
class LSLControlServer(threading.Thread):
    def __init__(self, stream_name: str, port: int = 5005):
        super().__init__(daemon=True)
        self.stream_name = stream_name
        self.port = port
        self._stop_event = threading.Event()
        self.sock = None
        self.outlet = None

    def stop(self):
        self._stop_event.set()
        if self.sock:
            try:
                self.sock.close()
            except:
                pass

    def run(self):
        info = StreamInfo(
            name=self.stream_name,
            type="control",
            channel_count=1,
            channel_format="string",
            source_id="capcorder-control-server"
        )
        self.outlet = StreamOutlet(info)

        import socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.settimeout(0.5)

        while not self._stop_event.is_set():
            try:
                data, _ = self.sock.recvfrom(1024)
                msg = data.decode("utf-8")
                self.outlet.push_sample([msg])
            except socket.timeout:
                continue
            except Exception:
                break
'''
class LSLControlListener(threading.Thread):
    def __init__(self, stream_name: str, command_queue: queue.Queue[dict[str, Any]]) -> None:
        super().__init__(daemon=True)
        self.stream_name = stream_name
        self.command_queue = command_queue
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        inlets = {}

        while not self._stop_event.is_set():
            streams = resolve_byprop("type", "videocontrol", timeout=0.2)

            # add new streams
            for s in streams:
                name = s.name()
                if not name.startswith("CAPcorderControl"):
                    continue

                if s.uid() not in inlets:
                    inlets[s.uid()] = StreamInlet(s, recover=False)

            # poll all inlets
            for uid, inlet in list(inlets.items()):
                try:
                    sample, lsl_time = inlet.pull_sample(timeout=0.05)
                    if sample:
                        payload = parse_control_message(str(sample[0]))
                        payload["lsl_time"] = lsl_time
                        self.command_queue.put(payload)
                except:
                    del inlets[uid]  # clean dead stream


class VideoSyncRecorder:
    def __init__(self, settings: RecorderSettings) -> None:
        self.settings = settings
        self.available_cameras = enumerate_cameras()
        self.camera_index = settings.camera_index if settings.camera_index is not None else choose_default_camera(self.available_cameras)
        self.capture = create_capture(self.camera_index, settings.width, settings.height, settings.fps)
        self.actual_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH) or settings.width)
        self.actual_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or settings.height)
        self.actual_fps = float(self.capture.get(cv2.CAP_PROP_FPS) or settings.fps)
        #self.control_outlet = create_control_outlet(self.settings.control_stream_name)
        self.frame_outlet = create_frame_outlet(settings.frame_stream_name)
        self.status_outlet = create_status_outlet(settings.status_stream_name)
        self.command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        #self.control_server = LSLControlServer(self.settings.control_stream_name)
        self.listener = LSLControlListener(settings.control_stream_name, self.command_queue)
        self.recording = False
        self.writer: cv2.VideoWriter | None = None
        self.audio_recorder: AudioRecorder | None = None
        self.frame_number = settings.initial_frame_number
        self.recording_start_frame_number = settings.initial_frame_number
        self.current_file: Path | None = None
        self.video_work_file: Path | None = None
        self.audio_work_file: Path | None = None
        self.recording_started_monotonic: float | None = None
        self.recording_started_unix: float | None = None
        self.trigger_timestamp: float | None = settings.trigger_timestamp
        self.last_status_text = "Idle"
        self.crash_state_path = Path(__file__).resolve().parent / ".CAPcorder_active_recording.json"
        self.stop_requested = False
        self.root: tk.Tk | None = None
        self.preview_label = None
        self.preview_photo = None
        self.settings_frame = None
        self.settings_canvas = None
        self.settings_inner = None
        self.ui_vars: dict[str, Any] = {}
        self.status_label: ttk.Label | None = None
        self.info_label: ttk.Label | None = None
        self.start_button = None
        self.stop_button = None
        self.save_status_text = "No file saved yet."
        self.save_status_color = (180, 180, 180)

    @property
    def audio_enabled(self) -> bool:
        return self.settings.audio_enabled and sd is not None

    def build_ui(self) -> None:
        if not self.settings.use_ui or tk is None or ImageTk is None:
            return
        self.root = tk.Tk()
        # --- set icon here ---
        self.root.iconbitmap(resource_path("capcordericon.ico"))  # Windows-friendly

        self.root.title("CAPcorder")
        self.root.protocol("WM_DELETE_WINDOW", self.request_stop)
        self.root.bind("<Configure>", self.on_window_configure)
        self.root.geometry(f"{max(self.actual_width, 300)}x{max(self.actual_height + 120, 250)}")

        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Start Recording", command=self.start_recording_from_ui)
        file_menu.add_command(label="Stop Recording", command=self.stop_recording)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.request_stop)
        menu.add_cascade(label="File", menu=file_menu)
        settings_menu = tk.Menu(menu, tearoff=False)
        settings_menu.add_command(label="Show/Hide Settings", command=self.toggle_settings_panel)
        menu.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menu)

        camera_labels = [camera["label"] for camera in self.available_cameras] or ["No cameras found"]
        current_label = next((camera["label"] for camera in self.available_cameras if camera["index"] == self.camera_index), camera_labels[0])

        self.ui_vars = {
            "filename": tk.StringVar(value=self.settings.filename),
            "output_dir": tk.StringVar(value=str(self.settings.output_dir)),
            "timestamp": tk.StringVar(value="" if self.trigger_timestamp is None else str(self.trigger_timestamp)),
            "width": tk.StringVar(value=str(self.settings.width)),
            "height": tk.StringVar(value=str(self.settings.height)),
            "fps": tk.StringVar(value=str(self.settings.fps)),
            "frame_number": tk.StringVar(value=str(self.frame_number)),
            "camera": tk.StringVar(value=current_label),
            "audio_enabled": tk.BooleanVar(value=self.settings.audio_enabled),
            "preview_enabled": tk.BooleanVar(value=self.settings.preview_enabled),
            "show_debug_text": tk.BooleanVar(value=self.settings.show_debug_text),
        }

        frame = ttk.Frame(self.root, padding=12)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        frame.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(frame)
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        self.info_label = ttk.Label(frame, text="Use File or Settings from the menu bar to configure the recorder.")
        self.info_label.grid(row=1, column=0, sticky="w", pady=(8, 0))

        button_row = ttk.Frame(frame)
        button_row.grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.start_button = tk.Button(button_row, text="Start", command=self.start_recording_from_ui)
        self.start_button.grid(row=0, column=0, padx=(0, 6))
        self.stop_button = tk.Button(button_row, text="Stop", command=self.stop_recording)
        self.stop_button.grid(row=0, column=1, padx=(0, 6))
        ttk.Button(button_row, text="Settings", command=self.toggle_settings_panel).grid(row=0, column=2)

        self.status_label = ttk.Label(frame, text="Idle")
        self.status_label.grid(row=3, column=0, sticky="w", pady=(10, 0))

        self.settings_frame = ttk.LabelFrame(frame, text="Settings", padding=12)
        self.settings_frame.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.settings_frame.grid_remove()
        self.settings_frame.columnconfigure(0, weight=1)

        self.settings_canvas = tk.Canvas(self.settings_frame, height=260, highlightthickness=0)
        settings_scrollbar = ttk.Scrollbar(self.settings_frame, orient="vertical", command=self.settings_canvas.yview)
        self.settings_inner = ttk.Frame(self.settings_canvas)
        self.settings_inner.bind(
            "<Configure>",
            lambda _event: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all")),
        )
        self.settings_canvas.bind(
            "<Configure>",
            lambda event: self.settings_canvas.itemconfigure("settings_inner", width=event.width),
        )
        self.settings_canvas.create_window((0, 0), window=self.settings_inner, anchor="nw", tags="settings_inner")
        self.settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        self.settings_canvas.grid(row=0, column=0, sticky="ew")
        settings_scrollbar.grid(row=0, column=1, sticky="ns")
        self.settings_inner.columnconfigure(1, weight=1)

        fields = [
            ("Filename", "filename"),
            ("Output folder", "output_dir"),
            ("Task timestamp", "timestamp"),
            ("Width", "width"),
            ("Height", "height"),
            ("Video Hz", "fps"),
            ("Starting frame", "frame_number"),
        ]
        for row, (label_text, var_name) in enumerate(fields):
            ttk.Label(self.settings_inner, text=label_text).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(self.settings_inner, textvariable=self.ui_vars[var_name], width=36)
            entry.grid(row=row, column=1, sticky="ew", pady=4)

        ttk.Label(self.settings_inner, text="Camera").grid(row=7, column=0, sticky="w", pady=4)
        camera_box = ttk.Combobox(self.settings_inner, textvariable=self.ui_vars["camera"], values=[camera["label"] for camera in self.available_cameras] or ["No cameras found"], state="readonly")
        camera_box.grid(row=7, column=1, sticky="ew", pady=4)
        ttk.Checkbutton(self.settings_inner, text="Audio", variable=self.ui_vars["audio_enabled"]).grid(row=8, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(self.settings_inner, text="Preview", variable=self.ui_vars["preview_enabled"]).grid(row=9, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(self.settings_inner, text="Debug text", variable=self.ui_vars["show_debug_text"]).grid(row=10, column=0, columnspan=2, sticky="w", pady=4)

        settings_buttons = ttk.Frame(self.settings_inner)
        settings_buttons.grid(row=11, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(settings_buttons, text="Browse", command=self.choose_output_dir).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(settings_buttons, text="Apply", command=self.update_settings_from_ui).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(settings_buttons, text="Close", command=self.hide_settings_panel).grid(row=0, column=2)

        self.check_crash_state()
        self.update_button_states()
        self.update_debug_visibility()
        self.on_window_configure(None)
    ''' #depreciated master outlet doesnt allow inlet pushing
    def send_control_command(self, msg: str) -> None:
        self.control_outlet.push_sample([msg], local_clock())
    '''
    def toggle_settings_panel(self) -> None:
        if self.settings_frame is None:
            return
        if self.settings_frame.winfo_ismapped():
            self.settings_frame.grid_remove()
        else:
            self.settings_frame.grid()

    def hide_settings_panel(self) -> None:
        if self.settings_frame is not None:
            self.settings_frame.grid_remove()

    def update_debug_visibility(self) -> None:
        if self.status_label is None:
            return
        if self.settings.show_debug_text:
            self.status_label.grid()
        else:
            self.status_label.grid_remove()

    def update_button_states(self) -> None:
        if self.start_button is not None:
            if self.recording:
                self.start_button.configure(text="Recording...", state="disabled", bg="#1f7a1f", fg="#ffffff", disabledforeground="#ffffff")
            else:
                self.start_button.configure(text="Start", state="normal", bg=self.root.cget("bg") if self.root is not None else "SystemButtonFace", fg="#000000")
        if self.stop_button is not None:
            self.stop_button.configure(state="normal" if self.recording else "disabled")

    def on_window_configure(self, _event) -> None:
        if self.root is None:
            return
        wrap = max(self.root.winfo_width() - 48, 240)
        if self.info_label is not None:
            self.info_label.configure(wraplength=wrap)
        if self.status_label is not None:
            self.status_label.configure(wraplength=wrap)

    def choose_output_dir(self) -> None:
        if not filedialog or not self.root:
            return
        selected = filedialog.askdirectory(initialdir=self.ui_vars["output_dir"].get())
        if selected:
            self.ui_vars["output_dir"].set(selected)

    def request_stop(self) -> None:
        self.stop_requested = True

    def check_crash_state(self) -> None:
        if not self.crash_state_path.exists():
            return
        try:
            data = json.loads(self.crash_state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        warning = (
            "Crash recovery notice: previous session ended while recording.\n"
            f"Last file: {data.get('file', 'unknown')}\n"
            f"Started at: {data.get('start_iso', 'unknown')}"
        )
        self.last_status_text = warning
        if self.status_label is not None:
            self.status_label.config(text=warning)
        if messagebox:
            messagebox.showwarning("CAPcorder", warning)

    def update_settings_from_ui(self) -> None:
        if not self.ui_vars:
            return
        selected_label = self.ui_vars["camera"].get()
        camera = next((item for item in self.available_cameras if item["label"] == selected_label), None)
        self.settings.filename = self.ui_vars["filename"].get().strip() or self.settings.filename
        self.settings.output_dir = Path(self.ui_vars["output_dir"].get().strip() or self.settings.output_dir).expanduser()
        self.settings.width = int(float(self.ui_vars["width"].get()))
        self.settings.height = int(float(self.ui_vars["height"].get()))
        self.settings.fps = float(self.ui_vars["fps"].get())
        self.frame_number = int(float(self.ui_vars["frame_number"].get()))
        self.settings.audio_enabled = bool(self.ui_vars["audio_enabled"].get())
        self.settings.preview_enabled = bool(self.ui_vars["preview_enabled"].get())
        self.settings.show_debug_text = bool(self.ui_vars["show_debug_text"].get())
        timestamp_value = self.ui_vars["timestamp"].get().strip()
        self.trigger_timestamp = float(timestamp_value) if timestamp_value else None
        self.update_debug_visibility()
        if camera and camera["index"] != self.camera_index:
            self.switch_camera(camera["index"])
        elif not self.recording:
            self.reconfigure_capture()

    def switch_camera(self, camera_index: int) -> None:
        was_recording = self.recording
        if was_recording:
            self.stop_recording()
        self.capture.release()
        self.camera_index = camera_index
        self.capture = create_capture(camera_index, self.settings.width, self.settings.height, self.settings.fps)
        self.actual_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH) or self.settings.width)
        self.actual_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.settings.height)
        self.actual_fps = float(self.capture.get(cv2.CAP_PROP_FPS) or self.settings.fps)
        if was_recording:
            self.start_recording()

    def reconfigure_capture(self) -> None:
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        self.capture.set(cv2.CAP_PROP_FPS, self.settings.fps)
        self.actual_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH) or self.settings.width)
        self.actual_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.settings.height)
        self.actual_fps = float(self.capture.get(cv2.CAP_PROP_FPS) or self.settings.fps)

    def ensure_output_dir(self) -> None:
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)

    def build_output_path(self) -> Path:
        safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in self.settings.filename)
        return self.settings.output_dir / f"{safe_stem}_{now_timestamp()}.avi"

    def build_work_paths(self, final_path: Path) -> tuple[Path, Path]:
        video_work = final_path.with_name(f"{final_path.stem}_video_only{final_path.suffix}")
        audio_work = final_path.with_name(f"{final_path.stem}_audio.wav")
        return video_work, audio_work

    def mux_audio_video(self, final_file: Path, video_file: Path, audio_file: Path) -> bool:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            return False
        temp_output = final_file.with_name(f"{final_file.stem}_mux{final_file.suffix}")
        command = [
            ffmpeg_path,
            "-y",
            "-i",
            str(video_file),
            "-i",
            str(audio_file),
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s16le",
            str(temp_output),
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            temp_output.replace(final_file)
            return True
        except Exception:
            if temp_output.exists():
                temp_output.unlink()
            return False

    def write_crash_state(self) -> None:
        if not self.current_file or self.recording_started_unix is None:
            return
        payload = {
            "file": str(self.current_file),
            "start_unix": self.recording_started_unix,
            "start_iso": datetime.fromtimestamp(self.recording_started_unix).isoformat(),
            "trigger_timestamp": self.trigger_timestamp,
            "pc_time_offset_seconds": None if self.trigger_timestamp is None else self.recording_started_unix - self.trigger_timestamp,
        }
        self.crash_state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def clear_crash_state(self) -> None:
        if self.crash_state_path.exists():
            self.crash_state_path.unlink()

    def push_status(self, recording: bool) -> None:
        filename = "" if self.current_file is None else str(self.current_file)
        elapsed = self.recording_duration()
        message = (
            f"recording: {int(recording)}; filename: {filename}; camera: {self.camera_index}; "
            f"timestamp: {time.time():.6f}; elapsed_sec: {elapsed:.3f}"
        )
        self.status_outlet.push_sample([message], local_clock())
        self.last_status_text = message
        if self.status_label is not None:
            self.status_label.config(text=message)

    def set_save_status(self, message: str, color: tuple[int, int, int]) -> None:
        self.save_status_text = message
        self.save_status_color = color
        if self.status_label is not None:
            self.status_label.config(text=message)

    def write_metadata(self, stop_time: float | None = None) -> None:
        if not self.current_file:
            return
        metadata = {
            "video_file": str(self.current_file),
            "camera_index": self.camera_index,
            "capture_width": self.actual_width,
            "capture_height": self.actual_height,
            "capture_fps": self.actual_fps,
            "audio_enabled": self.audio_enabled,
            "recording_started_unix": self.recording_started_unix,
            "recording_stopped_unix": stop_time,
            "trigger_timestamp": self.trigger_timestamp,
            "pc_time_offset_seconds": None if self.trigger_timestamp is None or self.recording_started_unix is None else self.recording_started_unix - self.trigger_timestamp,
            "starting_frame_number": self.recording_start_frame_number,
        }
        metadata_path = self.current_file.with_suffix(".json")
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    def start_recording_from_ui(self) -> None:
        self.update_settings_from_ui()
        self.hide_settings_panel()
        self.start_recording()

    def start_recording(self) -> None:
        if self.recording:
            return
        self.set_save_status("Recording in progress...", (0, 0, 255))
        self.ensure_output_dir()
        self.current_file = self.build_output_path()
        self.video_work_file, self.audio_work_file = self.build_work_paths(self.current_file)
        writer_target = self.video_work_file if self.audio_enabled else self.current_file
        fourcc = cv2.VideoWriter_fourcc(*self.settings.codec)
        self.writer = cv2.VideoWriter(
            str(writer_target),
            fourcc,
            self.settings.fps,
            (self.actual_width, self.actual_height),
        )
        if not self.writer.isOpened():
            raise RuntimeError(f"Could not open video writer for {self.current_file}.")
        self.audio_recorder = None
        if self.audio_enabled and self.audio_work_file is not None:
            try:
                self.audio_recorder = AudioRecorder(self.audio_work_file)
                self.audio_recorder.start()
            except Exception:
                self.audio_recorder = None
                self.settings.audio_enabled = False
        self.recording_started_monotonic = time.perf_counter()
        self.recording_started_unix = time.time()
        self.recording_start_frame_number = self.frame_number
        self.recording = True
        self.update_button_states()
        self.write_crash_state()
        self.push_status(recording=True)

    def stop_recording(self) -> None:
        if not self.recording:
            return
        stop_time = time.time()
        finished_file = self.current_file
        video_work_file = self.video_work_file
        audio_work_file = self.audio_work_file
        self.recording = False
        self.update_button_states()
        if self.audio_recorder is not None:
            self.audio_recorder.stop()
            self.audio_recorder = None
        if self.writer is not None:
            self.writer.release()
            self.writer = None
        self.push_status(recording=False)
        if finished_file is not None:
            if self.audio_enabled and video_work_file is not None and audio_work_file is not None and audio_work_file.exists():
                muxed = self.mux_audio_video(finished_file, video_work_file, audio_work_file)
                if not muxed and video_work_file.exists():
                    shutil.move(str(video_work_file), str(finished_file))
                if video_work_file.exists():
                    video_work_file.unlink()
                if audio_work_file.exists():
                    audio_work_file.unlink()
            elif video_work_file is not None and video_work_file.exists():
                shutil.move(str(video_work_file), str(finished_file))
            self.current_file = finished_file
            self.write_metadata(stop_time=stop_time)
            self.set_save_status(f"Saved to {finished_file}", (0, 170, 0))
        self.clear_crash_state()
        self.current_file = None
        self.video_work_file = None
        self.audio_work_file = None
        self.recording_started_monotonic = None
        self.recording_started_unix = None

    def recording_duration(self) -> float:
        if self.recording_started_monotonic is None:
            return 0.0
        return time.perf_counter() - self.recording_started_monotonic

    def get_preview_target_size(self, frame_width: int, frame_height: int) -> tuple[int, int]:
        if self.preview_label is None or self.root is None:
            return frame_width, frame_height
        available_width = max(self.preview_label.winfo_width(), 240)
        available_height = max(self.preview_label.winfo_height(), 180)
        scale = min(available_width / frame_width, available_height / frame_height)
        if scale <= 0:
            scale = 1.0
        return max(1, int(frame_width * scale)), max(1, int(frame_height * scale))

    def scale_frame_for_preview(self, frame):
        target_width, target_height = self.get_preview_target_size(frame.shape[1], frame.shape[0])
        if frame.shape[1] == target_width and frame.shape[0] == target_height:
            return frame
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

    def build_placeholder_frame(self):
        width, height = self.get_preview_target_size(self.actual_width, self.actual_height)
        return np.zeros((height, width, 3), dtype=np.uint8)

    def wrap_overlay_text(self, text: str, max_width: int, font_scale: float, thickness: int) -> list[str]:
        approx_char_width = max(8, int(14 * font_scale))
        max_chars = max(10, max_width // approx_char_width)
        return textwrap.wrap(text, width=max_chars) or [text]

    def draw_multiline_text(
        self,
        frame,
        lines: list[str],
        x: int,
        y: int,
        font_scale: float,
        color: tuple[int, int, int],
        thickness: int,
    ) -> int:
        line_height = max(18, int(28 * font_scale))
        for index, line in enumerate(lines):
            cv2.putText(
                frame,
                line,
                (x, y + index * line_height),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                color,
                thickness,
                cv2.LINE_AA,
            )
        return y + max(0, len(lines) - 1) * line_height

    def overlay_status(self, frame):
        overlay = frame.copy()
        h, w = overlay.shape[:2]
        pad = max(10, int(min(w, h) * 0.025))
        font_scale = max(0.4, min(w, h) / 700.0)
        title_scale = max(0.55, font_scale * 1.2)
        thickness = max(1, int(round(font_scale * 2)))
        state_text = "RECORDING" if self.recording else "NOT RECORDING"
        color = (0, 0, 255) if self.recording else (128, 128, 128)
        cv2.putText(overlay, state_text, (pad, pad + int(22 * title_scale)), cv2.FONT_HERSHEY_SIMPLEX, title_scale, color, thickness, cv2.LINE_AA)
        info_lines = [
            f"Elapsed: {self.recording_duration():0.2f}s",
            f"File: {self.current_file.name if self.current_file else 'None'}",
            f"Frame: {self.frame_number}",
            f"Audio: {'ON' if self.audio_enabled else 'OFF'}",
        ]
        start_y = pad + int(48 * title_scale)
        line_height = max(18, int(28 * font_scale))
        for idx, line in enumerate(info_lines):
            cv2.putText(
                overlay,
                line,
                (pad, start_y + idx * line_height),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (255, 255, 255),
                thickness,
                cv2.LINE_AA,
            )
        wrapped_save = self.wrap_overlay_text(self.save_status_text, max(w - (pad * 2), 60), font_scale, thickness)
        bottom_y = h - pad - max(0, (len(wrapped_save) - 1) * line_height)
        self.draw_multiline_text(overlay, wrapped_save, pad, bottom_y, font_scale, self.save_status_color, thickness)
        if self.recording and int(time.time() * 2) % 2 == 0:
            radius = max(8, int(min(w, h) * 0.02))
            cv2.circle(overlay, (w - pad - radius, pad + radius), radius, (0, 0, 255), -1)
        return overlay

    def update_preview_widget(self, frame) -> None:
        if self.preview_label is None or Image is None or ImageTk is None:
            return
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb_frame)
        photo = ImageTk.PhotoImage(image=image)
        self.preview_label.configure(image=photo)
        self.preview_label.image = photo
        self.preview_photo = photo

    def get_preview_frame(self, frame, use_tk_preview: bool):
        base_frame = self.scale_frame_for_preview(frame) if use_tk_preview else frame
        if self.settings.preview_enabled:
            return self.overlay_status(base_frame)
        return self.overlay_status(self.build_placeholder_frame() if use_tk_preview else np.zeros_like(base_frame))
    
    def apply_remote_command(self, payload: dict[str, Any]) -> None:
        action = str(payload.get("action", "")).lower().strip()

        def normalize_path(p: str) -> Path:
            p = str(p).strip()
            # handle "C/Users/..." → "C:/Users/..."
            if len(p) >= 2 and (p[1] == "/" or p[1] == "\\") and p[0].isalpha():
                p = f"{p[0]}:{p[1:]}"
            print(Path(p).expanduser())
            return Path(p).expanduser()

        # --- string / path ---
        if "filename" in payload:
            self.settings.filename = str(payload["filename"])
            if "filename" in self.ui_vars:
                self.ui_vars["filename"].set(self.settings.filename)

        if "output_dir" in payload:
            self.settings.output_dir = normalize_path(payload["output_dir"])
            if "output_dir" in self.ui_vars:
                self.ui_vars["output_dir"].set(str(self.settings.output_dir))

        if "timestamp" in payload:
            self.trigger_timestamp = float(payload["timestamp"])
            if "timestamp" in self.ui_vars:
                self.ui_vars["timestamp"].set(str(self.trigger_timestamp))

        # --- numeric ---
        if "width" in payload:
            self.settings.width = int(payload["width"])
            if "width" in self.ui_vars:
                self.ui_vars["width"].set(str(self.settings.width))

        if "height" in payload:
            self.settings.height = int(payload["height"])
            if "height" in self.ui_vars:
                self.ui_vars["height"].set(str(self.settings.height))

        if "fps" in payload:
            self.settings.fps = float(payload["fps"])
            if "fps" in self.ui_vars:
                self.ui_vars["fps"].set(str(self.settings.fps))

        if "frame_number" in payload:
            self.frame_number = int(payload["frame_number"])
            if "frame_number" in self.ui_vars:
                self.ui_vars["frame_number"].set(str(self.frame_number))

        # --- toggles ---
        if "audio" in payload:
            self.settings.audio_enabled = bool(payload["audio"])
            if "audio_enabled" in self.ui_vars:
                self.ui_vars["audio_enabled"].set(self.settings.audio_enabled)

        if "preview" in payload:
            self.settings.preview_enabled = bool(payload["preview"])
            if "preview_enabled" in self.ui_vars:
                self.ui_vars["preview_enabled"].set(self.settings.preview_enabled)

        if "debug" in payload:
            self.settings.show_debug_text = bool(payload["debug"])
            if "show_debug_text" in self.ui_vars:
                self.ui_vars["show_debug_text"].set(self.settings.show_debug_text)
            self.update_debug_visibility()

        # --- camera ---
        if "camera" in payload:
            self.switch_camera(int(payload["camera"]))

        # --- reconfigure if needed ---
        elif any(k in payload for k in ("width", "height", "fps")) and not self.recording:
            self.reconfigure_capture()

        # --- actions ---
        if action == "start":
            self.start_recording()
        elif action == "stop":
            self.stop_recording()

    def poll_remote_commands(self) -> None:
        while True:
            try:
                payload = self.command_queue.get_nowait()
            except queue.Empty:
                break
            self.apply_remote_command(payload)

    def run(self) -> int:
        self.listener.start()
        self.build_ui()
        use_tk_preview = self.root is not None and self.preview_label is not None
        if not use_tk_preview:
            cv2.namedWindow(self.settings.preview_window_name, cv2.WINDOW_NORMAL)
        try:
            while not self.stop_requested:
                self.poll_remote_commands()
                if self.root is not None:
                    self.root.update()
                success, frame = self.capture.read()
                if not success:
                    self.last_status_text = "Camera frame grab failed."
                    time.sleep(0.05)
                    continue
                if self.recording and self.writer is not None:
                    self.writer.write(frame)
                    self.frame_outlet.push_sample([self.frame_number], local_clock())
                    self.frame_number += 1
                    if self.frame_number % 120 == 0:
                        self.write_crash_state()
                preview_frame = self.get_preview_frame(frame, use_tk_preview)
                if use_tk_preview:
                    self.update_preview_widget(preview_frame)
                else:
                    cv2.imshow(self.settings.preview_window_name, preview_frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        self.request_stop()
                    elif key == ord("r"):
                        self.start_recording()
                    elif key == ord("s"):
                        self.stop_recording()
        finally:
            #self.control_server.stop()
            self.listener.stop()
            self.stop_recording()
            self.capture.release()
            cv2.destroyAllWindows()
            if self.root is not None and self.root.winfo_exists():
                self.root.destroy()
        return 0


def main() -> int:
    settings = parse_args()
    recorder = VideoSyncRecorder(settings)
    return recorder.run()


if __name__ == "__main__":
    raise SystemExit(main())
