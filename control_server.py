# control_server.py
from pylsl import StreamInfo, StreamOutlet
import socket

info = StreamInfo("CAPcorderControl", "control", 1, 0, "string", "control_server")
outlet = StreamOutlet(info)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", 5005))

print("Control server running...")

while True:
    data, _ = sock.recvfrom(1024)
    msg = data.decode("utf-8")
    outlet.push_sample([msg])