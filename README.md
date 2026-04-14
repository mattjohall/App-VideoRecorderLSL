# VideoSyncLSL

## Run In Your `lsl` Environment

```powershell
conda activate lsl
cd C:\Users\CNEL_Vega\Documents\GitHub\VideoSyncLSL
pip install -r requirements.txt
python .\VideoSyncLSL.py
```

Quick checks:

```powershell
python .\VideoSyncLSL.py --help
python .\psychopy_dummy_task.py
```

MATLAB viewer:

```matlab
addpath('C:\path\to\xdf-Matlab')
VideoSyncLSL_xdf_viewer('C:\path\to\recording.xdf')
```

`VideoSyncLSL` is a Python webcam recorder that can be started either manually or from an incoming Lab Streaming Layer (LSL) control stream. It saves `.avi` video files, pushes webcam frame numbers to LSL, emits onset/offset recording status messages with the active filename, and can optionally record microphone audio into the final video file.

## Requirements

See [requirements.txt](C:/Users/CNEL_Vega/Documents/GitHub/VideoSyncLSL/requirements.txt).

Main packages:

- `opencv-python`
- `pylsl`
- `Pillow`
- `sounddevice`
- `psychopy`

## UI Behavior

- The main application window contains the preview video and menu bar.
- The `File` menu starts and stops recording or exits the app.
- The `Settings` menu shows or hides the built-in settings panel inside the same preview window.
- The preview area shrinks and grows with the window while preserving aspect ratio.
- Overlay text scales with the displayed preview size, and long save messages wrap instead of being cropped.
- The settings panel becomes scrollable when the window is too small to show all controls at once.
- While recording, the `Start` button changes to `Recording...` and the `Stop` button is enabled.
- When idle, `Stop` is disabled.
- The preview overlay shows recording state, elapsed time, filename, frame number, audio on/off state, and save status.
- After a recording stops and the output finishes saving, the bottom-left overlay message changes to `Saved to ...` in green.

## Editable Settings

These are editable from the in-window settings panel:

- `Filename`: base filename stem used for the saved video.
- `Output folder`: where video and metadata files are written.
- `Task timestamp`: optional task-side timestamp for alignment with PC time.
- `Width`: requested capture width in pixels.
- `Height`: requested capture height in pixels.
- `Video Hz`: requested video frame rate.
- `Starting frame`: initial frame number pushed to the LSL frame outlet.
- `Camera`: selected webcam index.
- `Audio`: microphone capture on or off. Default is on.
- `Preview`: shows or hides the live camera image while keeping the overlay/status in the preview region.
- `Debug text`: shows or hides the lower debug/status text in the app window. Default is off.

These are editable from the command line:

- `--camera`
- `--resolution`
- `--width`
- `--height`
- `--fps`
- `--filename`
- `--output-dir`
- `--frame-number`
- `--control-stream`
- `--frame-stream`
- `--status-stream`
- `--audio`
- `--no-audio`
- `--no-ui`
- `--timestamp`

## Command Line Example

```powershell
python .\VideoSyncLSL.py --camera 1 --resolution 480p --fps 30 --filename task_run --frame-number 1 --audio
```

## Features

- Manual control from the preview window menu and buttons.
- Remote control from an LSL inlet named `VideoSyncLSLControl` by default.
- Preview overlay with `RECORDING` / `NOT RECORDING`, elapsed recording time, active filename, frame number, `Audio: ON/OFF`, a blinking red indicator, and save status.
- Status outlet named `VideoSyncLSLStatus` that announces onset/offset messages.
- Frame counter outlet named `VideoSyncLSLFrames`.
- Crash-recovery notice on restart if the previous session ended while recording.
- Camera selection with external-camera preference when multiple cameras are found.
- Optional microphone capture, enabled by default when supported by the environment.

## LSL Communication Format

The control inlet expects a single-string message with `key: value` pairs separated by semicolons.

Example start command:

```text
action: start; filename: stroop_001; timestamp: 1712845123.125; width: 640; height: 480; fps: 30; camera: 1; frame_number: 1; audio: true
```

Example stop command:

```text
action: stop; filename: stroop_001
```

Recognized keys:

- `action`: `start` or `stop`
- `filename`: filename stem for the saved video
- `timestamp`: task timestamp to store alongside PC time
- `width`: capture width
- `height`: capture height
- `fps`: video frequency in Hz
- `audio`: `true` or `false`
- `camera`: camera index
- `frame_number`: initial webcam frame number pushed to the frame outlet

## LSL Outputs

### `VideoSyncLSLFrames`

One integer sample per recorded frame, containing the current webcam frame number.

### `VideoSyncLSLStatus`

One string sample on recording onset and one string sample on recording offset. Format:

```text
recording: 1; filename: C:\...\recordings\stroop_001_20260414_110000.avi; camera: 1; timestamp: 1713092400.250000; elapsed_sec: 0.000
```

```text
recording: 0; filename: C:\...\recordings\stroop_001_20260414_110000.avi; camera: 1; timestamp: 1713092415.900000; elapsed_sec: 15.650
```

## Audio Notes

- Audio is on by default when `sounddevice` is available.
- The recorder captures microphone audio to a temporary `.wav` file during recording.
- On stop, it attempts to mux the audio into the final `.avi` file with `ffmpeg` if `ffmpeg` is available on your `PATH`.
- If `ffmpeg` is not available, the video file is still saved and audio may be omitted from the final `.avi`.

## Crash Recovery

If the program closes unexpectedly while recording, the next launch shows a warning with the last active file and start time. The recorder writes a small state file while recording and clears it on a normal stop.

## Timing Notes

The recorder stores:

- PC start time
- optional task timestamp from your task script
- a computed `pc_time_offset_seconds` value in the sidecar `.json` metadata file

That gives you a simple way to compare task timestamps to the machine time used by the recorder.

## Files Written Per Recording

- `your_name_YYYYMMDD_HHMMSS.avi`
- `your_name_YYYYMMDD_HHMMSS.json`

Temporary working files may appear during recording when audio is enabled:

- `your_name_YYYYMMDD_HHMMSS_video_only.avi`
- `your_name_YYYYMMDD_HHMMSS_audio.wav`

## PsychoPy Example

See [psychopy_dummy_task.py](C:/Users/CNEL_Vega/Documents/GitHub/VideoSyncLSL/psychopy_dummy_task.py) for a minimal script that:

- sends `start` and `stop` control messages to the recorder
- displays the last key pressed on screen
- sends each key press to an LSL outlet named `VideoSyncLSLKeys`
- provides a `C`-key 5-second countdown that emits a `clap` marker for offline audio sync checks

## MATLAB XDF Viewer

See [VideoSyncLSL_xdf_viewer.m](C:/Users/CNEL_Vega/Documents/GitHub/VideoSyncLSL/VideoSyncLSL_xdf_viewer.m).

What it does:

- loads an XDF file with `load_xdf`
- resolves the saved video file from `VideoSyncLSLStatus`
- lists event samples from other streams
- jumps the video to a selected event and leaves playback paused
- provides `Play/Pause`
- lets you step left and right by frames with the arrow keys
- lets you change the frame-step size in the GUI
- attempts to load and play audio from the video file using MATLAB-native audio playback

Notes:

- `load_xdf.m` must already be on your MATLAB path.
- Audio playback depends on whether MATLAB can read the audio track from the saved video file with `audioread`.
- If audio cannot be read from the video container, the viewer still works for frame-accurate visual inspection and event jumping.
