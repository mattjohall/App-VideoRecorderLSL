"""PsychoPy demo that controls CAPcorder and streams key presses over LSL."""

from __future__ import annotations

from psychopy import core, event, visual
from pylsl import StreamInfo, StreamOutlet, local_clock


def build_control_outlet() -> StreamOutlet:
    info = StreamInfo(
        name="CAPcorderControl_Py",
        type="videocontrol",
        channel_count=1,
        channel_format="string",
        source_id="psychopy-CAPcorder-control-demo",
    )
    return StreamOutlet(info)


def build_key_outlet() -> StreamOutlet:
    info = StreamInfo(
        name="CAPcorderKeys",
        type="markers",
        channel_count=1,
        channel_format="string",
        source_id="psychopy-CAPcorder-key-demo",
    )
    return StreamOutlet(info)


def send_string(outlet: StreamOutlet, payload: str) -> None:
    outlet.push_sample([payload], local_clock())
    print(payload)


def run_clap_countdown(
    win: visual.Window,
    instructions: visual.TextStim,
    status: visual.TextStim,
    last_key: visual.TextStim,
    key_outlet: StreamOutlet,
    clock: core.Clock,
) -> None:
    for seconds_left in range(5, 0, -1):
        status.text = f"Clap sync in {seconds_left}..."
        countdown_until = core.getTime() + 1.0
        while core.getTime() < countdown_until:
            instructions.draw()
            status.draw()
            last_key.draw()
            win.flip()
            if "escape" in event.getKeys():
                win.close()
                core.quit()
                raise SystemExit
            core.wait(0.01)

    clap_time = clock.getTime()
    clap_payload = f"marker: clap; task_time: {clap_time:0.6f}; lsl_time: {local_clock():.6f}"
    send_string(key_outlet, clap_payload)
    last_key.text = f"Last key: clap sync at {clap_time:0.3f}s"
    status.text = f"CLAP now at {clap_time:0.3f}s"
    highlight_until = core.getTime() + 1.0
    while core.getTime() < highlight_until:
        instructions.draw()
        status.draw()
        last_key.draw()
        win.flip()
        if "escape" in event.getKeys():
            win.close()
            core.quit()
            raise SystemExit
        core.wait(0.01)


def main() -> None:
    win = visual.Window(size=(1000, 700), color="black", units="pix")
    instructions = visual.TextStim(
        win,
        text=(
            "CAPcorder PsychoPy Demo\n\n"
            "Press SPACE to send a START command.\n"
            "Press S to send a STOP command.\n"
            "Press C for a 5-second clap sync countdown.\n"
            "Press any other key to log it to LSL.\n"
            "Press ESC to quit."
        ),
        color="white",
        height=32,
        wrapWidth=820,
    )
    status = visual.TextStim(win, text="", color="white", height=24, pos=(0, -210), wrapWidth=860)
    last_key = visual.TextStim(win, text="Last key: none", color="#7FDBFF", height=28, pos=(0, -270), wrapWidth=860)

    control_outlet = build_control_outlet()
    key_outlet = build_key_outlet()
    clock = core.Clock()

    while True:
        instructions.draw()
        status.draw()
        last_key.draw()
        win.flip()

        keys = event.getKeys(timeStamped=clock)
        for key_name, key_time in keys:
            if key_name == "escape":
                win.close()
                core.quit()
                return

            last_key.text = f"Last key: {key_name} at {key_time:0.3f}s"
            key_payload = f"key: {key_name}; task_time: {key_time:0.6f}; lsl_time: {local_clock():.6f}"
            send_string(key_outlet, key_payload)

            if key_name == "space":
                command = (
                    f"action: start; filename: psychopy_demo; timestamp: {local_clock():.6f}; "
                    "width: 640; height: 480; fps: 30; frame_number: 1"
                )
                send_string(control_outlet, command)
                status.text = f"START sent at {key_time:0.3f}s"
            elif key_name == "c":
                run_clap_countdown(win, instructions, status, last_key, key_outlet, clock)
            elif key_name == "s":
                command = f"action: stop; filename: psychopy_demo; timestamp: {local_clock():.6f}"
                send_string(control_outlet, command)
                status.text = f"STOP sent at {key_time:0.3f}s"
            else:
                status.text = f"Key marker sent: {key_name} at {key_time:0.3f}s"

        core.wait(0.01)


if __name__ == "__main__":
    main()
