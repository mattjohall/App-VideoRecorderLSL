# CAPcorder

LSL-controlled webcam recorder for synchronized experiments (MATLAB, PsychoPy, etc.)

---

## 🚀 What it does

CAPcorder records:
- 🎥 Webcam video
- 🎤 Optional audio
- 🧠 LSL-synchronized metadata

…and is controlled entirely through **Lab Streaming Layer (LSL)**.

---

## 🧠 Core Idea

Instead of clicking “record”, your experiment controls recording:

- MATLAB / PsychoPy → send LSL command  
- CAPcorder → starts/stops recording  

No manual interaction needed.

---

## 🏗️ Architecture

CAPcorder listens to **multiple control streams**:
CAPcorderControl_MATLAB
CAPcorderControl_PsychoPy
CAPcorderControl_Whatever

JUST make sure each app sends to its own stream

All streams must:
- `type = "videocontrol"`
- `name` starts with `"CAPcorderControl"`

CAPcorder:
- auto-detects all of them
- merges commands
- executes immediately

👉 No shared stream needed  
👉 No networking setup needed  
👉 Works across machines via LSL  

---

## 📡 LSL Streams

### Inputs (control)
| Name prefix              | Type          | Description |
|------------------------|--------------|-------------|
| CAPcorderControl*      | videocontrol | start/stop + settings |

---

### Outputs
| Name                | Type         | Description |
|---------------------|-------------|-------------|
| CAPcorderFrames     | videostream | frame counter |
| CAPcorderStatus     | videostatus | recording status |

---

## 🎮 Commands

Format:

## Audio Test

Use File -> Test Audio or the Test Audio button to record about 3 seconds from the current microphone and immediately play it back. This is meant as a quick machine check before a real recording session.

LSL control paths also accept output-dir as well as output_dir, and single-letter slash paths such as C/Users/... are normalized to Windows drive paths automatically.

