"""
=============================================================
  BLUETOOTH CHAT — SERVER (Master / Hub) + Auto-Save
  Uses Python built-in socket ONLY — NO PyBluez needed!

  Install:  pip install colorama
  OS:       Windows 10 / 11 with Bluetooth adapter
=============================================================
"""

import socket
import threading
import sys
import time
import json
import os
from pathlib import Path
from colorama import init, Fore, Style

init(autoreset=True)

# ── Config ─────────────────────────────────────────────────
BT_PORT     = 3            # RFCOMM channel (1-30)
MAX_CLIENTS = 7
RECV_DIR    = Path("server_received_files")
# ───────────────────────────────────────────────────────────

clients: dict[str, socket.socket] = {}
clients_lock = threading.Lock()
server_name  = "SERVER"

PALETTE = [Fore.CYAN, Fore.MAGENTA, Fore.YELLOW,
           Fore.GREEN, Fore.BLUE, Fore.RED, Fore.WHITE]
addr_color: dict[str, str] = {}
color_idx = 0

# Ensure the receive directory exists
RECV_DIR.mkdir(exist_ok=True)

def color_for(addr: str) -> str:
    global color_idx
    if addr not in addr_color:
        addr_color[addr] = PALETTE[color_idx % len(PALETTE)]
        color_idx += 1
    return addr_color[addr]

def log(msg: str, color: str = Fore.WHITE):
    ts = time.strftime("%H:%M:%S")
    print(f"{Fore.LIGHTBLACK_EX}[{ts}]{Style.RESET_ALL} {color}{msg}{Style.RESET_ALL}")

def save_file(filename: str, data: bytes) -> Path:
    """Helper to save a copy of relayed files to the server's disk."""
    safe = "".join(c for c in filename if c not in r'\/:*?"<>|')
    dest = RECV_DIR / safe
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        dest = RECV_DIR / f"{stem}_{int(time.time())}{suffix}"
    dest.write_bytes(data)
    return dest

def build_msg_frame(text: str) -> bytes:
    return b"MSG:" + text.encode("utf-8") + b"\n"

def broadcast(payload: bytes, exclude_addr: str | None = None):
    dead = []
    with clients_lock:
        for addr, s in clients.items():
            if addr == exclude_addr:
                continue
            try:
                s.sendall(payload)
            except Exception:
                dead.append(addr)
    for addr in dead:
        _remove(addr, "send error")

def broadcast_msg(text: str, exclude_addr: str | None = None):
    broadcast(build_msg_frame(text), exclude_addr=exclude_addr)

def broadcast_server_notice(text: str):
    broadcast_msg(f"[SERVER] {text}")

def _remove(addr: str, reason: str = "disconnected"):
    with clients_lock:
        s = clients.pop(addr, None)
    if s:
        try:
            s.close()
        except Exception:
            pass
        col = color_for(addr)
        log(f"[-] {col}{addr}{Style.RESET_ALL} {reason}.", Fore.RED)
        broadcast_server_notice(f"{addr} has left the chat.")
        with clients_lock:
            log(f"    Active clients: {len(clients)}", Fore.LIGHTBLACK_EX)

def handle_client(addr: str, s: socket.socket):
    col = color_for(addr)
    log(f"[+] {col}{addr}{Style.RESET_ALL} connected.", Fore.GREEN)
    broadcast_server_notice(f"{addr} joined!")

    try:
        s.sendall(build_msg_frame(f"[SERVER] Welcome! You appear as {addr}."))
    except Exception:
        pass

    buf = b""
    while True:
        try:
            data = s.recv(8192)
            if not data:
                break
            buf += data

            while True:
                # ── FILE frame relay + Local Save ──────────
                if buf.startswith(b"FILE:"):
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break 
                    try:
                        meta = json.loads(buf[5:nl].decode("utf-8"))
                    except Exception:
                        buf = buf[nl + 1:]
                        continue

                    total_needed = nl + 1 + meta["size"]
                    if len(buf) < total_needed:
                        break 

                    # Extract file parts
                    file_payload = buf[:total_needed]
                    file_bytes = buf[nl+1:total_needed]
                    buf = buf[total_needed:]

                    fname  = meta["name"]
                    sender = meta.get("from", addr)
                    
                    # 1. Save locally to Server
                    try:
                        saved_path = save_file(fname, file_bytes)
                        log(f"💾 Saved '{fname}' from {sender} to {saved_path}", Fore.GREEN)
                    except Exception as e:
                        log(f"❌ Failed to save file locally: {e}", Fore.RED)

                    # 2. Relay to all other clients
                    log(f"📁 Relaying '{fname}' to all other clients.", Fore.CYAN)
                    broadcast(file_payload, exclude_addr=addr)

                # ── MSG frame relay ────────────────────────
                elif buf.startswith(b"MSG:"):
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break
                    text = buf[4:nl].decode("utf-8", errors="replace").strip()
                    buf  = buf[nl + 1:]
                    if not text:
                        continue
                    log(text, col)
                    broadcast(build_msg_frame(text), exclude_addr=addr)

                # ── Legacy plain-text fallback ─────────────
                else:
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break
                    text = buf[:nl].decode("utf-8", errors="replace").strip()
                    buf  = buf[nl + 1:]
                    if not text:
                        continue
                    log(text, col)
                    broadcast(build_msg_frame(text), exclude_addr=addr)

        except Exception:
            break

    _remove(addr, "disconnected")

def server_input_loop():
    while True:
        try:
            text = input()
        except EOFError:
            break
        if not text.strip():
            continue
        msg = f"[{server_name}] {text.strip()}"
        log(msg, Fore.CYAN)
        broadcast_msg(msg)

def accept_loop(srv: socket.socket):
    while True:
        try:
            cs, info = srv.accept()
            addr = info[0]
        except Exception as exc:
            log(f"Accept error: {exc}", Fore.RED)
            break
        with clients_lock:
            if len(clients) >= MAX_CLIENTS:
                try:
                    cs.sendall(build_msg_frame("[SERVER] Chat full. Try later."))
                    cs.close()
                except Exception:
                    pass
                log(f"Rejected {addr} — chat full.", Fore.YELLOW)
                continue
            clients[addr] = cs
        threading.Thread(target=handle_client, args=(addr, cs), daemon=True).start()

def detect_bt_mac() -> str | None:
    import subprocess, re
    mac_re = re.compile(r"([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Bluetooth'} "
             "| Select-Object -First 1 -ExpandProperty MacAddress"],
            capture_output=True, text=True, timeout=8, creationflags=0x08000000
        )
        mac = r.stdout.strip().replace("-", ":").upper()
        if mac_re.match(mac): return mac
    except: pass
    return None

def main():
    global server_name
    print(Fore.CYAN + r"""
  ██████╗ ████████╗     ██████╗██╗  ██╗ █████╗ ████████╗
  ██╔══██╗╚══██╔══╝    ██╔════╝██║  ██║██╔══██╗╚══██╔══╝
  ██████╔╝    ██║       ██║     ███████║███████║   ██║
  ██╔══██╗    ██║       ██║     ██╔══██║██╔══██║   ██║
  ██████╔╝    ██║       ╚██████╗██║  ██║██║  ██║   ██║
  ╚═════╝     ╚═╝        ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝
      MASTER SERVER + AUTO-SAVE  ·  built-in socket
""" + Style.RESET_ALL)

    n = input(f"{Fore.YELLOW}Your name (Enter = 'SERVER'): {Style.RESET_ALL}").strip()
    if n: server_name = n

    local_mac = detect_bt_mac()
    if not local_mac:
        local_mac = input(f"\n{Fore.YELLOW}Enter Bluetooth MAC (XX:XX:XX:XX:XX:XX): {Style.RESET_ALL}").strip().upper()
    
    if not local_mac: sys.exit(1)

    srv = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    used_port = BT_PORT
    for port in [BT_PORT, BT_PORT + 1, BT_PORT + 2]:
        try:
            srv.bind((local_mac, port))
            used_port = port
            break
        except OSError:
            if port == BT_PORT + 2: sys.exit(1)
    
    srv.listen(MAX_CLIENTS)
    log(f"Listening on RFCOMM channel {used_port}", Fore.GREEN)
    log(f"Files saved to: {RECV_DIR.resolve()}", Fore.CYAN)

    threading.Thread(target=accept_loop, args=(srv,), daemon=True).start()
    server_input_loop()

    with clients_lock:
        for s in clients.values(): s.close()
    srv.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)