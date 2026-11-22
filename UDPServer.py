import queue
import random
import sys
import threading
import time
from collections import deque
from socket import *
import tkinter as tk
from tkinter import font as tkfont


UDP_PORT = 12000
LOSS_RATE = 0.30
TRANSIT_SHARE = 0.5
SEND_INTERVAL = 1.5
GRACE = 0.7
OFFLINE_AFTER = 8.0

lock = threading.Lock()
out_lock = threading.Lock()
stop = threading.Event()
console_q = queue.Queue()
clients = {}
events = deque(maxlen=90)
stats = {'received': 0, 'lost': 0, 'echoed': 0, 'echo_dropped': 0}
event_counter = 0


def emit(lines):
    with out_lock:
        for text, tag in lines:
            print(text, flush=True)
            console_q.put((text, tag))
        print(flush=True)
        console_q.put(('', 'info'))


def fmt(thread, client, event, detail):
    return f"{'[' + thread + ']':<9}  {client:<9}  {event:<12}  {detail}"


def thread_label():
    if threading.current_thread() is threading.main_thread():
        return 'main'
    return threading.current_thread().name


def add_event(kind, client, seq):
    global event_counter
    event_counter += 1
    events.append({'id': event_counter, 'kind': kind, 'client': client, 'seq': seq})


def udp_server():
    sock = socket(AF_INET, SOCK_DGRAM)
    try:
        sock.bind(('', UDP_PORT))
    except OSError as e:
        emit([(f"Cannot bind UDP port {UDP_PORT}: {e}", 'alert')])
        stop.set()
        return
    sock.settimeout(0.5)
    emit([(f"UDP server listening on port {UDP_PORT}...", 'banner')])

    while not stop.is_set():
        try:
            message, address = sock.recvfrom(1024)
        except timeout:
            continue
        except ConnectionResetError:
            continue
        except OSError:
            break

        now = time.time()
        text = message.decode(errors='replace')
        parts = text.split()
        try:
            cid, color, seq, sent = parts[0], parts[1].capitalize(), int(parts[2]), float(parts[3])
            if color not in ('Green', 'Yellow', 'Red'):
                raise ValueError
        except (IndexError, ValueError):
            emit([(f"Ignored malformed packet from {address}: {text!r}", 'alert')])
            continue

        dropped = color != 'Red' and random.random() < LOSS_RATE
        if dropped and random.random() < TRANSIT_SHARE:
            continue

        me = thread_label()
        lines = []
        with lock:
            st = clients.get(cid)
            if st is None:
                st = clients[cid] = {'color': color, 'expected': 1, 'received': 0, 'lost': 0,
                                     'last_seq': seq, 'deadline': None, 'last_seen': now}
            if seq > st['expected']:
                gap = seq - st['expected']
                st['lost'] += gap
                stats['lost'] += gap
                add_event('lost', cid, st['expected'])
                lines.append((fmt(me, cid, 'Packet lost', f"{gap} packet missing before sequence {seq}"), 'loss'))
            st['expected'] = max(st['expected'], seq + 1)
            st['last_seq'] = seq
            st['last_seen'] = now
            st['color'] = color
            delay_ms = (now - sent) * 1000
            lines.append((fmt(me, cid, 'Heartbeat',
                              f"Sequence {seq} | Status: {color} | 1-Way Delay: {delay_ms:.2f} ms"), color))
            if color == 'Red':
                st['deadline'] = None
            else:
                st['deadline'] = now + SEND_INTERVAL + GRACE
                st['received'] += 1
                stats['received'] += 1
                add_event('arrived', cid, seq)
                if dropped:
                    stats['echo_dropped'] += 1
                    lines.append((fmt(me, cid, 'Echo dropped',
                                      f"Simulated packet loss: no reply for sequence {seq}, "
                                      f"{cid} will time out and turn Yellow"), 'warn'))
                else:
                    stats['echoed'] += 1
                    lines.append((fmt(me, cid, 'Echo sent',
                                      f"Reply for sequence {seq} sent back to {cid}"), 'echo'))

        emit(lines)

        if color != 'Red' and not dropped:
            try:
                sock.sendto(text.encode(), address)
            except OSError:
                pass
    sock.close()


def monitor():
    while not stop.is_set():
        time.sleep(0.1)
        now = time.time()
        found = []
        with lock:
            for cid, st in clients.items():
                if st['color'] in ('Red', 'Offline'):
                    continue
                if now - st['last_seen'] > OFFLINE_AFTER:
                    st['color'] = 'Offline'
                    st['deadline'] = None
                    found.append((fmt('monitor', cid, 'Offline',
                                      f"No heartbeat for {OFFLINE_AFTER:.0f} seconds | Status: Offline"), 'alert'))
                    continue
                while st['deadline'] is not None and now > st['deadline']:
                    missing = st['expected']
                    st['lost'] += 1
                    stats['lost'] += 1
                    add_event('lost', cid, missing)
                    st['expected'] += 1
                    st['deadline'] += SEND_INTERVAL
                    st['color'] = 'Yellow'
                    found.append((fmt('monitor', cid, 'Packet lost',
                                      f"Sequence {missing} did not arrive within {SEND_INTERVAL + GRACE:.1f} seconds | "
                                      f"Status: Yellow"), 'loss'))
        for item in found:
            emit([item])


def snapshot():
    with lock:
        return {
            'stats': dict(stats),
            'clients': {cid: dict(st) for cid, st in clients.items()},
            'events': list(events),
        }


BG, CARD, LINE = '#0d1117', '#161b22', '#30363d'
TEXT, MUTED = '#e6edf3', '#8b949e'
GREEN, YELLOW, RED, BLUE, GRAY = '#3fb950', '#d29922', '#f85149', '#58a6ff', '#6e7681'
BULBS = {
    'Red': (RED, '#3a1517', '#6b2220'),
    'Yellow': (YELLOW, '#3a2d08', '#6b5010'),
    'Green': (GREEN, '#10301a', '#1f5a2b'),
}
STATUS_COLOR = {'Green': GREEN, 'Yellow': YELLOW, 'Red': RED, 'Offline': GRAY}
EVENT_COLOR = {'arrived': GREEN, 'lost': RED}
ANIM_SECONDS = 0.7
SERVER_BOTTOM = 142


class Dashboard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("UDP Packet Monitor")
        self.root.geometry("1300x800")
        self.root.minsize(900, 700)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        fams = set(tkfont.families(self.root))
        self.ui = next((f for f in ('Segoe UI', 'SF Pro Text', 'Helvetica Neue', 'DejaVu Sans') if f in fams), 'Helvetica')
        self.mono = next((f for f in ('Consolas', 'Menlo', 'DejaVu Sans Mono', 'Courier New') if f in fams), 'Courier')

        self.cv = tk.Canvas(self.root, bg=BG, highlightthickness=0, height=440)
        self.cv.pack(fill='x')

        tk.Label(self.root, text="CONSOLE", bg=BG, fg=MUTED, anchor='w',
                 font=(self.ui, 9, 'bold')).pack(fill='x', padx=18, pady=(2, 4))
        box = tk.Frame(self.root, bg=LINE, padx=1, pady=1)
        box.pack(fill='both', expand=True, padx=16, pady=(0, 16))
        self.console = tk.Text(box, bg='#0a0d12', fg='#c9d1d9', font=(self.mono, 10), relief='flat',
                               wrap='word', state='disabled', padx=12, pady=8, height=10,
                               insertbackground='#c9d1d9', borderwidth=0)
        bar = tk.Scrollbar(box, command=self.console.yview, bg=CARD, troughcolor='#0a0d12', relief='flat')
        self.console.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.console.pack(side='left', fill='both', expand=True)
        for tag, col in (('Green', GREEN), ('Yellow', YELLOW), ('Red', RED), ('Offline', GRAY),
                         ('loss', '#ff9f43'), ('alert', RED), ('info', '#c9d1d9'),
                         ('echo', MUTED), ('warn', YELLOW), ('banner', BLUE)):
            self.console.tag_configure(tag, foreground=col)

        self.last_event_id = 0
        self.anims = []
        self.tick()

    def rrect(self, x1, y1, x2, y2, r=14, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.cv.create_polygon(pts, smooth=True, **kw)

    def dot(self, cx, cy, r, **kw):
        return self.cv.create_oval(cx - r, cy - r, cx + r, cy + r, **kw)

    def close(self):
        stop.set()
        self.root.destroy()

    def tick(self):
        if stop.is_set():
            self.root.destroy()
            return
        snap = snapshot()
        for e in snap['events']:
            if e['id'] > self.last_event_id:
                self.last_event_id = e['id']
                self.anims.append({'client': e['client'], 'kind': e['kind'], 't0': time.time()})
        self.drain_console()
        self.draw(snap)
        self.root.after(50, self.tick)

    def drain_console(self):
        wrote = False
        self.console.configure(state='normal')
        while True:
            try:
                text, tag = console_q.get_nowait()
            except queue.Empty:
                break
            self.console.insert('end', text + '\n', tag)
            wrote = True
        if wrote:
            if int(self.console.index('end-1c').split('.')[0]) > 1000:
                self.console.delete('1.0', '250.0')
            self.console.see('end')
        self.console.configure(state='disabled')

    def draw(self, snap):
        cv, now = self.cv, time.time()
        cv.delete('all')
        w = max(cv.winfo_width(), 900)
        cx = w / 2

        cv.create_text(24, 24, text="UDP Packet Monitor", anchor='w', fill=TEXT, font=(self.ui, 16, 'bold'))

        s = snap['stats']
        self.rrect(cx - 262, 46, cx + 262, SERVER_BOTTOM, fill='#112033', outline='#1f4f8f', width=2)
        cv.create_text(cx, 68, text=f"UDP SERVER   port {UDP_PORT}", fill=BLUE, font=(self.ui, 14, 'bold'))
        counters = (("ARRIVED", s['received'], GREEN), ("LOST", s['lost'], RED),
                    ("ECHOED", s['echoed'], TEXT), ("ECHO DROPPED", s['echo_dropped'], YELLOW))
        for i, (label, val, col) in enumerate(counters):
            x = cx - 262 + 66 + i * 130
            cv.create_text(x, 98, text=str(val), fill=col, font=(self.ui, 17, 'bold'))
            cv.create_text(x, 118, text=label, fill=MUTED, font=(self.ui, 8, 'bold'))

        gap, margin, y1, y2 = 20, 16, 196, 426
        cw = (w - 2 * margin - 2 * gap) / 3
        names = sorted(snap['clients'])[:3]
        slot_of = {n: i for i, n in enumerate(names)}
        geo = []
        for i in range(3):
            x1 = margin + i * (cw + gap)
            geo.append((x1, x1 + cw, x1 + cw / 2, cx + (i - 1) * 150))

        for i in range(3):
            x1, x2, ccx, sx = geo[i]
            c = snap['clients'].get(names[i]) if i < len(names) else None
            status = c['color'] if c else None
            colr = STATUS_COLOR.get(status, LINE)
            cv.create_line(ccx, y1, sx, SERVER_BOTTOM, fill=colr, width=3, dash=(5, 4) if status == 'Offline' else ())
            self.rrect(x1, y1, x2, y2, fill=CARD, outline=colr if c else LINE, width=2)

            lx, ly = x1 + 20, y1 + 18
            self.rrect(lx, ly, lx + 66, ly + 172, r=14, fill='#0a0d12', outline='#2b3340', width=2)
            for j, name in enumerate(('Red', 'Yellow', 'Green')):
                lit, dim, glow = BULBS[name]
                by = ly + 30 + j * 56
                if status == name:
                    self.dot(lx + 33, by, 27, fill=glow, outline='')
                    self.dot(lx + 33, by, 20, fill=lit, outline='')
                else:
                    self.dot(lx + 33, by, 20, fill=dim, outline='')

            tx = x1 + 104
            if c is None:
                cv.create_text(tx, y1 + 40, text=f"Client-{'ABC'[i]}", anchor='w', fill=MUTED, font=(self.ui, 13, 'bold'))
                cv.create_text(tx, y1 + 68, text="waiting for packets...", anchor='w', fill=MUTED, font=(self.ui, 10))
                continue
            cv.create_text(tx, y1 + 34, text=names[i], anchor='w', fill=TEXT, font=(self.ui, 14, 'bold'))
            cv.create_text(tx, y1 + 60, text=status.upper(), anchor='w', fill=colr, font=(self.ui, 12, 'bold'))
            rows = (("arrived", c['received'], TEXT),
                    ("lost", c['lost'], RED if c['lost'] else TEXT),
                    ("sequence", c['last_seq'], TEXT))
            for r, (k, v, col) in enumerate(rows):
                cv.create_text(tx, y1 + 98 + r * 24, text=f"{k:<10}{v}", anchor='w', fill=col, font=(self.mono, 11))

            recent = [e for e in snap['events'] if e['client'] == names[i]][-12:]
            for k, e in enumerate(recent):
                bx = x1 + 20 + k * 25
                self.rrect(bx, y2 - 34, bx + 21, y2 - 14, r=5, fill=EVENT_COLOR[e['kind']], outline='')
                cv.create_text(bx + 10.5, y2 - 24, text=str(e['seq']),
                               fill='#0d1117' if e['kind'] != 'lost' else '#fff', font=(self.ui, 7, 'bold'))

        self.anims = [a for a in self.anims if now - a['t0'] < 1.4]
        for a in self.anims:
            if a['client'] not in slot_of:
                continue
            _, _, ccx, sx = geo[slot_of[a['client']]]
            p = min((now - a['t0']) / ANIM_SECONDS, 1.0)
            if a['kind'] == 'lost':
                p = min(p, 0.55)
            px, py = ccx + (sx - ccx) * p, y1 + (SERVER_BOTTOM - y1) * p
            if a['kind'] == 'lost':
                if (now - a['t0']) > ANIM_SECONDS * 0.55:
                    cv.create_text(px, py, text="✗", fill=RED, font=(self.ui, 16, 'bold'))
                else:
                    self.dot(px, py, 7, fill=RED, outline='')
            elif p < 1.0:
                self.dot(px, py, 7, fill=GREEN, outline='')


def run_gui():
    app = Dashboard()
    app.root.mainloop()
    stop.set()


if __name__ == '__main__':
    threading.Thread(target=monitor, daemon=True, name='monitor').start()

    try:
        if tk is None or '--nogui' in sys.argv:
            udp_server()
        elif sys.platform == 'darwin':
            threading.Thread(target=udp_server, daemon=True, name='socket').start()
            run_gui()
        else:
            gui = threading.Thread(target=run_gui, name='gui')
            gui.start()
            udp_server()
            gui.join(timeout=3)
    except KeyboardInterrupt:
        stop.set()
        print("\nServer stopped.")