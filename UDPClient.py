import sys
import threading
import time
from socket import *

SERVER = ('127.0.0.1', 12000)
CLIENT_IDS = ['Client-A', 'Client-B', 'Client-C']
SEND_INTERVAL = 1.5
REPLY_TIMEOUT = 1.0
PINGS = 10

print_lock = threading.Lock()


def say(client_id, *lines):
    with print_lock:
        for event, detail in lines:
            print(f"{client_id:<9}  {event:<16}  {detail}")
        print(flush=True)


def run_client(client_id, pings, start_delay, crash_after=None):
    sock = socket(AF_INET, SOCK_DGRAM)
    sock.settimeout(REPLY_TIMEOUT)

    time.sleep(start_delay)
    color = "Green"
    packets_lost = 0
    rtt_list = []
    next_send = time.time()

    for seq in range(1, pings + 1):
        if crash_after is not None and seq > crash_after:
            sock.close()
            return

        send_time = time.time()
        message = f"{client_id} {color} {seq} {send_time}"
        try:
            sock.sendto(message.encode(), SERVER)
            reply, _ = sock.recvfrom(1024)
            rtt = time.time() - send_time
            rtt_list.append(rtt)
            color = "Green"
            say(client_id,
                ("Echo", reply.decode()),
                ("Round-trip time", f"Sequence {seq}: {rtt:.6f} s | Status: {color}"))
        except timeout:
            packets_lost += 1
            color = "Yellow"
            say(client_id, ("Timeout", f"Request timed out | Sequence {seq} | Status: {color}"))

        next_send += SEND_INTERVAL
        time.sleep(max(0, next_send - time.time()))

    sock.sendto(f"{client_id} Red {pings + 1} {time.time()}".encode(), SERVER)
    sock.close()
    say(client_id, ("Shutdown", "Status: Red"))

    with print_lock:
        print(f"{client_id} statistics")
        print(f"Packet loss rate: {packets_lost / pings * 100:.1f}%")
        if rtt_list:
            print(f"Minimum round-trip time: {min(rtt_list):.6f} s")
            print(f"Maximum round-trip time: {max(rtt_list):.6f} s")
            print(f"Average round-trip time: {sum(rtt_list) / len(rtt_list):.6f} s")
        print(flush=True)


if __name__ == "__main__":
    crash = "crash" in sys.argv

    threads = []
    for i, cid in enumerate(CLIENT_IDS):
        crash_after = 8 if (crash and cid == 'Client-C') else None
        t = threading.Thread(target=run_client, name=cid,
                             args=(cid, PINGS, i * 0.5, crash_after))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()