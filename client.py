"""
=============================================================
  BLUETOOTH CHAT — CLIENT (Slave / Peer)  v4
  Uses Python built-in socket ONLY — NO PyBluez needed!

  Install:  pip install colorama
  OS:       Windows 10 / 11 with Bluetooth adapter

  Commands:
    <message>          → send chat message
    /file <path>       → send a file to everyone via server
    /quit              → disconnect
=============================================================
"""

import socket
import threading
import sys
import time
import os
import json
from pathlib import Path
from colorama import init, Fore, Style

init(autoreset=True)

BT_PORT  = 3
RECV_DIR = Path("received_files")
CHUNK    = 8192

my_name  = "Client"
running  = False
sock: socket.socket | None = None

# ── Frame builders ──────────────────────────────────────────
def build_msg_frame(text: str) -> bytes:
    """MSG frame: MSG:<utf8 text>\n"""
    return b"MSG:" + text.encode("utf-8") + b"\n"

def build_file_frame(filename: str, size: int, sender: str, data: bytes) -> bytes:
    """
    FILE frame: FILE:<json header>\n<raw bytes>
    The header JSON contains name, size, from.
    No trailing newline after the binary payload — length is authoritative.
    """
    meta = json.dumps({"name": filename, "size": size, "from": sender})
    return b"FILE:" + meta.encode("utf-8") + b"\n" + data


# ── Logging ─────────────────────────────────────────────────
def log(msg: str, color: str = Fore.WHITE):
    ts = time.strftime("%H:%M:%S")
    print(f"\r{Fore.LIGHTBLACK_EX}[{ts}]{Style.RESET_ALL} {color}{msg}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{my_name}>{Style.RESET_ALL} ", end="", flush=True)


# ── Save incoming file ──────────────────────────────────────
def save_file(filename: str, data: bytes) -> Path:
    RECV_DIR.mkdir(exist_ok=True)
    safe = "".join(c for c in filename if c not in r'\/:*?"<>|')
    dest = RECV_DIR / safe
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        dest = RECV_DIR / f"{stem}_{int(time.time())}{suffix}"
    dest.write_bytes(data)
    return dest


# ── Send file to server (atomic: header + bytes in one sendall) ─
def send_file(s: socket.socket, filepath: Path):
    size = filepath.stat().st_size
    log(f"📤 Reading '{filepath.name}' ({size:,} bytes) into memory…", Fore.CYAN)
    try:
        data = filepath.read_bytes()
    except Exception as e:
        log(f"❌ Could not read file: {e}", Fore.RED)
        return

    frame = build_file_frame(filepath.name, size, my_name, data)
    log(f"📤 Sending '{filepath.name}' ({size:,} bytes)…", Fore.CYAN)
    try:
        # Send the entire frame atomically so header and body
        # are never split across separate TCP segments / recv() calls.
        s.sendall(frame)
        log(f"✅ '{filepath.name}' sent successfully.", Fore.GREEN)
    except Exception as e:
        log(f"❌ Send failed: {e}", Fore.RED)


# ── Receive loop (runs in background thread) ────────────────
def receive_loop(s: socket.socket):
    global running
    buf = b""
    while running:
        try:
            data = s.recv(CHUNK)
            if not data:
                break
            buf += data

            # Process all complete frames in the buffer
            while True:
                # ── FILE frame ────────────────────────────
                if buf.startswith(b"FILE:"):
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break   # wait for header newline

                    try:
                        meta = json.loads(buf[5:nl].decode("utf-8"))
                    except Exception:
                        # Malformed header — skip to next newline
                        buf = buf[nl + 1:]
                        continue

                    total_needed = nl + 1 + meta["size"]
                    if len(buf) < total_needed:
                        break   # wait for file bytes to arrive

                    file_bytes = buf[nl + 1 : total_needed]
                    buf = buf[total_needed:]

                    fname  = meta["name"]
                    sender = meta.get("from", "?")
                    dest   = save_file(fname, file_bytes)
                    log(
                        f"📁 '{fname}' received from {sender} "
                        f"({meta['size']:,} bytes) → {dest}",
                        Fore.GREEN
                    )

                # ── MSG frame ─────────────────────────────
                elif buf.startswith(b"MSG:"):
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break   # wait for newline
                    text = buf[4:nl].decode("utf-8", errors="replace").strip()
                    buf  = buf[nl + 1:]
                    if text:
                        col = Fore.YELLOW if text.startswith("[SERVER]") else Fore.CYAN
                        log(text, col)

                # ── Unknown / legacy plain-text fallback ──
                else:
                    nl = buf.find(b"\n")
                    if nl == -1:
                        break
                    text = buf[:nl].decode("utf-8", errors="replace").strip()
                    buf  = buf[nl + 1:]
                    if text:
                        col = Fore.YELLOW if text.startswith("[SERVER]") else Fore.CYAN
                        log(text, col)

        except Exception:
            break

    running = False
    log("Disconnected from server.", Fore.RED)


# ── Main ────────────────────────────────────────────────────
def main():
    global my_name, running, sock

    print(Fore.MAGENTA + r"""
   ██████╗██╗     ██╗███████╗███╗   ██╗████████╗
  ██╔════╝██║     ██║██╔════╝████╗  ██║╚══██╔══╝
  ██║     ██║     ██║█████╗  ██╔██╗ ██║   ██║
  ██║     ██║     ██║██╔══╝  ██║╚██╗██║   ██║
  ╚██████╗███████╗██║███████╗██║ ╚████║   ██║
   ╚═════╝╚══════╝╚═╝╚══════╝╚═╝  ╚═══╝   ╚═╝
      SLAVE / CLIENT  v4  ·  Chat + File Transfer
""" + Style.RESET_ALL)

    n = input(f"{Fore.YELLOW}Your name: {Style.RESET_ALL}").strip()
    if n:
        my_name = n

    print()
    server_mac = input(
        f"{Fore.YELLOW}Server Bluetooth MAC  (AA:BB:CC:DD:EE:FF): {Style.RESET_ALL}"
    ).strip().upper()
    if not server_mac:
        print(f"{Fore.RED}No address. Exiting.{Style.RESET_ALL}")
        sys.exit(1)

    port_in = input(
        f"{Fore.YELLOW}RFCOMM channel (Enter = {BT_PORT}): {Style.RESET_ALL}"
    ).strip()
    connect_port = int(port_in) if port_in.isdigit() else BT_PORT

    print(f"\n  Connecting to {Fore.CYAN}{server_mac}{Style.RESET_ALL} "
          f"channel {Fore.CYAN}{connect_port}{Style.RESET_ALL} …")

    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    sock.settimeout(15)
    try:
        sock.connect((server_mac, connect_port))
    except socket.timeout:
        print(f"\n{Fore.RED}  Timed out. Is the server running? Are devices paired?{Style.RESET_ALL}")
        sys.exit(1)
    except OSError as e:
        print(f"\n{Fore.RED}  Connection failed: {e}{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}Pair devices in Windows Bluetooth settings first.{Style.RESET_ALL}")
        sys.exit(1)

    sock.settimeout(None)
    running = True
    RECV_DIR.mkdir(exist_ok=True)

    print(f"  {Fore.GREEN}Connected!{Style.RESET_ALL}")
    print(f"  Files saved to: {Fore.CYAN}{RECV_DIR.resolve()}{Style.RESET_ALL}")
    print(f"  {Fore.LIGHTBLACK_EX}Commands: /file <path>   /quit{Style.RESET_ALL}")
    print("─" * 60)

    threading.Thread(target=receive_loop, args=(sock,), daemon=True).start()

    print(f"{Fore.YELLOW}{my_name}>{Style.RESET_ALL} ", end="", flush=True)
    while running:
        try:
            text = input()
        except (EOFError, KeyboardInterrupt):
            break

        if not running:
            break

        text = text.strip()
        if not text:
            print(f"{Fore.YELLOW}{my_name}>{Style.RESET_ALL} ", end="", flush=True)
            continue

        if text.lower() == "/quit":
            break

        elif text.lower().startswith("/file "):
            path = Path(text[6:].strip().strip('"'))
            if not path.exists():
                log(f"File not found: {path}", Fore.RED)
            elif not path.is_file():
                log("Path is not a file.", Fore.RED)
            else:
                # Run in thread so UI stays responsive for large files
                threading.Thread(target=send_file, args=(sock, path), daemon=True).start()
                time.sleep(0.05)

        else:
            msg = f"[{my_name}] {text}"
            try:
                sock.sendall(build_msg_frame(msg))
            except Exception as e:
                log(f"Send failed: {e}", Fore.RED)
                break

        print(f"{Fore.YELLOW}{my_name}>{Style.RESET_ALL} ", end="", flush=True)

    running = False
    print(f"\n{Fore.RED}Disconnected. Goodbye!{Style.RESET_ALL}")
    try:
        sock.close()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        running = False
        print(f"\n{Fore.RED}Client stopped.{Style.RESET_ALL}")
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        sys.exit(0)