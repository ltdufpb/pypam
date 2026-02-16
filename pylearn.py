#!/usr/bin/env python3
"""
PyLearn - True Terminal Experience
Uses PTY for real terminal behavior with mixed input/output.
"""

import os
import pty
import select
import signal
import tempfile
import uuid
import asyncio
import fcntl

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

TIMEOUT = 60
PORT = int(os.getenv("PORT", 8000))

app = FastAPI()


def set_nonblocking(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


@app.websocket("/ws")
async def run_code(ws: WebSocket):
    await ws.accept()
    
    master_fd = None
    pid = None
    tmp = None
    
    try:
        data = await ws.receive_json()
        code = data.get("code", "")
        
        tmp = f"/tmp/py_{uuid.uuid4().hex[:8]}.py"
        with open(tmp, "w") as f:
            f.write(code)
        
        pid, master_fd = pty.fork()
        
        if pid == 0:
            os.execvp("python3", ["python3", "-u", tmp])
        else:
            set_nonblocking(master_fd)
            await ws.send_json({"t": "start"})
            
            start_time = asyncio.get_event_loop().time()
            
            while True:
                if asyncio.get_event_loop().time() - start_time > TIMEOUT:
                    await ws.send_json({"t": "out", "d": f"\n[Tempo esgotado: {TIMEOUT}s]\n"})
                    break
                
                result = os.waitpid(pid, os.WNOHANG)
                if result[0] != 0:
                    try:
                        while True:
                            data = os.read(master_fd, 1024)
                            if not data:
                                break
                            await ws.send_json({"t": "out", "d": data.decode("utf-8", errors="replace")})
                    except:
                        pass
                    
                    exit_code = os.WEXITSTATUS(result[1]) if os.WIFEXITED(result[1]) else -1
                    await ws.send_json({"t": "end", "c": exit_code})
                    pid = None
                    break
                
                try:
                    r, _, _ = select.select([master_fd], [], [], 0.01)
                    if master_fd in r:
                        data = os.read(master_fd, 1024)
                        if data:
                            text = data.decode("utf-8", errors="replace")
                            await ws.send_json({"t": "out", "d": text})
                except (OSError, IOError):
                    pass
                
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=0.01)
                    if msg.get("t") == "in":
                        text = msg.get("d", "")
                        os.write(master_fd, text.encode())
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    break
                
                await asyncio.sleep(0.01)
    
    except Exception as e:
        try:
            await ws.send_json({"t": "out", "d": f"\nErro: {e}\n"})
            await ws.send_json({"t": "end", "c": 1})
        except:
            pass
    
    finally:
        if pid is not None and pid > 0:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except:
                pass
        
        if master_fd is not None:
            try:
                os.close(master_fd)
            except:
                pass
        
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except:
                pass


HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>PyLearn</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: system-ui, sans-serif;
    background: #0a0a0a;
    color: #eee;
    padding: 10px;
    max-width: 700px;
    margin: 0 auto;
}
h1 { color: #0f8; text-align: center; padding: 8px; font-size: 22px; }
label { display: block; color: #0f8; font-size: 12px; margin: 10px 0 4px; font-weight: bold; }

#code {
    width: 100%;
    height: 140px;
    padding: 10px;
    font-family: "Courier New", monospace;
    font-size: 14px;
    line-height: 1.3;
    background: #000;
    color: #fff;
    border: 2px solid #333;
    border-radius: 6px;
    resize: vertical;
}
#code:focus { outline: none; border-color: #0f8; }

#run {
    width: 100%;
    padding: 14px;
    margin: 10px 0;
    font-size: 18px;
    font-weight: bold;
    background: #0f8;
    color: #000;
    border: none;
    border-radius: 6px;
    cursor: pointer;
}
#run:disabled { background: #444; color: #888; }
#run.stop { background: #e55; color: #fff; }

#terminal {
    background: #000;
    border: 2px solid #333;
    border-radius: 6px;
    padding: 10px;
    min-height: 180px;
    max-height: 350px;
    overflow-y: auto;
    font-family: "Courier New", monospace;
    font-size: 14px;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
    cursor: text;
}
#terminal:focus { outline: none; border-color: #0f8; }
#terminal .out { color: #fff; }
#terminal .sys { color: #888; }
#terminal #cursor {
    display: inline;
    border-left: 2px solid #0f8;
    margin-left: 1px;
    animation: blink 1s step-end infinite;
}
@keyframes blink { 50% { opacity: 0; } }

.hint { font-size: 11px; color: #666; margin-top: 4px; }
</style>
</head>
<body>

<h1>🐍 PyLearn</h1>

<label>CÓDIGO:</label>
<textarea id="code" spellcheck="false">nome = input("Seu nome: ")
print("Ola,", nome + "!")

idade = input("Sua idade: ")
print("Voce tem", idade, "anos")

for i in range(3):
    n = input("Digite um numero: ")
    print(n, "x 2 =", int(n) * 2)</textarea>

<button id="run" onclick="toggle()">▶ EXECUTAR</button>

<label>TERMINAL:</label>
<div id="terminal" tabindex="0"><span class="sys">Clique em EXECUTAR e depois clique aqui para digitar.</span></div>
<div class="hint">Clique no terminal para digitar. Enter envia a entrada.</div>

<script>
var ws = null;
var running = false;

var term = document.getElementById("terminal");
var runBtn = document.getElementById("run");

function appendText(text) {
    var cursor = document.getElementById("cursor");
    if (cursor) cursor.remove();
    
    var span = document.createElement("span");
    span.className = "out";
    span.textContent = text;
    term.appendChild(span);
    
    if (running) {
        var c = document.createElement("span");
        c.id = "cursor";
        c.innerHTML = "&nbsp;";
        term.appendChild(c);
    }
    
    term.scrollTop = term.scrollHeight;
}

function appendSystem(text) {
    var cursor = document.getElementById("cursor");
    if (cursor) cursor.remove();
    
    var span = document.createElement("span");
    span.className = "sys";
    span.textContent = text;
    term.appendChild(span);
    
    term.scrollTop = term.scrollHeight;
}

function clearTerminal() {
    term.innerHTML = "";
}

function toggle() {
    if (running) stop(); else start();
}

function start() {
    var code = document.getElementById("code").value;
    
    clearTerminal();
    appendSystem("Iniciando...\\n");
    
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(proto + "//" + location.host + "/ws");
    
    ws.onopen = function() {
        running = true;
        runBtn.textContent = "⏹ PARAR";
        runBtn.className = "stop";
        clearTerminal();
        
        var c = document.createElement("span");
        c.id = "cursor";
        c.innerHTML = "&nbsp;";
        term.appendChild(c);
        
        ws.send(JSON.stringify({code: code}));
        term.focus();
    };
    
    ws.onmessage = function(e) {
        var msg = JSON.parse(e.data);
        if (msg.t === "out") {
            appendText(msg.d);
        } else if (msg.t === "end") {
            appendSystem("\\n[Programa finalizado: " + msg.c + "]\\n");
            stop();
        }
    };
    
    ws.onerror = function() {
        appendSystem("\\nErro de conexão\\n");
        stop();
    };
    
    ws.onclose = function() {
        if (running) {
            appendSystem("\\n[Conexão fechada]\\n");
            stop();
        }
    };
}

function stop() {
    running = false;
    runBtn.textContent = "▶ EXECUTAR";
    runBtn.className = "";
    
    var cursor = document.getElementById("cursor");
    if (cursor) cursor.remove();
    
    if (ws) { ws.close(); ws = null; }
}

function sendChar(ch) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({t: "in", d: ch}));
    }
}

// Handle keyboard - just send characters, PTY will echo them
term.addEventListener("keydown", function(e) {
    if (!running) return;
    
    e.preventDefault();
    
    if (e.key === "Enter") {
        sendChar("\\n");
    } else if (e.key === "Backspace") {
        sendChar("\\x7f");  // DEL character
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
        sendChar(e.key);
    } else if (e.ctrlKey && e.key === "c") {
        sendChar("\\x03");  // Ctrl+C
    }
});

term.addEventListener("click", function() {
    if (running) term.focus();
});

document.addEventListener("keydown", function(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        if (!running) start();
    }
});
</script>

</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


if __name__ == "__main__":
    import uvicorn
    print(f"PyLearn Terminal running at http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)