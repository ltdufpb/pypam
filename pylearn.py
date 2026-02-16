#!/usr/bin/env python3
"""
PyLearn - Final Production Version
"""

import os
import asyncio
import docker
import tempfile
import shutil
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

# --- CONFIGURATION ---
PORT = int(os.getenv("PORT", 8000))

# Lightweight Alpine Linux Image
DOCKER_IMAGE = "python:3.14-alpine"

# LIMITS
MAX_CONCURRENT_USERS = 10
MEM_LIMIT = "48m"
CPU_LIMIT_NANO = int(0.20 * 1e9)

# --- AUTHENTICATION ---
ALLOWLIST_FILE = "allowlist.txt"
active_sessions = set()


def get_allowlist():
    if not os.path.exists(ALLOWLIST_FILE):
        return None
    with open(ALLOWLIST_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


user_lock = asyncio.Semaphore(MAX_CONCURRENT_USERS)

try:
    client = docker.from_env()
    try:
        client.images.get(DOCKER_IMAGE)
    except docker.errors.ImageNotFound:
        print(f"Pulling {DOCKER_IMAGE}...")
        client.images.pull(DOCKER_IMAGE)
except Exception as e:
    print(f"CRITICAL ERROR: Docker is not ready.\n{e}")
    exit(1)

app = FastAPI()


@app.post("/login")
async def login(data: dict):
    student_code = data.get("student_code", "").strip()
    allowlist = get_allowlist()
    if allowlist is None:
        return {"success": True}  # No allowlist means public access
    if student_code in allowlist:
        return {"success": True}
    return {"success": False}


@app.websocket("/ws")
async def run_code(ws: WebSocket):
    await ws.accept()

    if user_lock.locked():
        await ws.send_json({"t": "out", "d": "\n[Server Busy] Please wait...\n"})
        await ws.send_json({"t": "end", "c": 1})
        await ws.close()
        return

    student_code = None
    allowlist = get_allowlist()

    async with user_lock:
        container = None
        temp_dir = tempfile.mkdtemp(prefix="pylearn_")
        os.chmod(temp_dir, 0o755)  # Allow container user to access directory
        has_sent_output = False

        try:
            data = await ws.receive_json()
            student_code = data.get("student_code", "").strip()
            code = data.get("code", "")

            if allowlist is not None:
                if not student_code or student_code not in allowlist:
                    await ws.send_json(
                        {"t": "out", "d": "\n[Access Denied] Invalid student code.\n"}
                    )
                    await ws.send_json({"t": "end", "c": 1})
                    return

                if student_code in active_sessions:
                    await ws.send_json(
                        {"t": "out", "d": "\n[Access Denied] Code already in use.\n"}
                    )
                    await ws.send_json({"t": "end", "c": 1})
                    return

                active_sessions.add(student_code)

            if not code:
                return

            safe_code = "import time; time.sleep(0.5)\n" + code

            script_path = os.path.join(temp_dir, "script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(safe_code)
            os.chmod(script_path, 0o644)  # Allow container user to read script

            container = client.containers.create(
                DOCKER_IMAGE,
                command=["python3", "-u", "/app/script.py"],
                working_dir="/app",
                stdin_open=True,
                tty=True,
                detach=True,
                network_disabled=True,
                mem_limit=MEM_LIMIT,
                nano_cpus=CPU_LIMIT_NANO,
                pids_limit=15,
                read_only=True,
                volumes={temp_dir: {"bind": "/app", "mode": "rw"}},
                tmpfs={"/tmp": ""},
                # Run as a non-root user (nobody/nogroup in alpine is usually 65534:65534)
                user="65534:65534",
                environment={"PYTHONIOENCODING": "utf-8", "PYTHON_COLORS": "0"},
            )

            container.start()

            socket = container.attach_socket(
                params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
            )

            async def forward_output():
                nonlocal has_sent_output
                loop = asyncio.get_event_loop()
                while True:
                    try:
                        data = await loop.run_in_executor(None, socket.read, 1024)
                        if not data:
                            break
                        has_sent_output = True
                        await ws.send_json(
                            {"t": "out", "d": data.decode(errors="replace")}
                        )
                    except:
                        break

            output_task = asyncio.create_task(forward_output())

            try:
                while True:
                    container.reload()
                    if container.status != "running":
                        break
                    try:
                        msg = await asyncio.wait_for(ws.receive_json(), timeout=0.2)
                        if msg.get("t") == "in":
                            char = msg.get("d")
                            os.write(socket.fileno(), char.encode())
                    except asyncio.TimeoutError:
                        pass
                    except:
                        break
            except:
                pass

            try:
                await asyncio.wait_for(output_task, timeout=2.0)
            except:
                output_task.cancel()

            container.reload()
            exit_code = container.attrs["State"]["ExitCode"]

            if not has_sent_output and exit_code != 0:
                try:
                    logs = container.logs().decode(errors="replace")
                    if logs:
                        await ws.send_json({"t": "out", "d": logs})
                except:
                    pass

            await ws.send_json({"t": "end", "c": exit_code})

        except Exception as e:
            await ws.send_json({"t": "out", "d": f"\nSystem Error: {e}\n"})
            await ws.send_json({"t": "end", "c": 1})
        finally:
            if student_code and student_code in active_sessions:
                active_sessions.remove(student_code)
            if container:
                try:
                    container.remove(force=True)
                except:
                    pass
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass


HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>PyLearn</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden;background:#1e1e1e;font-family:monospace}
#login-view, #editor-view, #terminal-view {
    position:absolute;top:0;left:0;right:0;bottom:0;
    display:none;flex-direction:column;
}
#login-view { align-items:center;justify-content:center; }
#login-box {
    background:#252526;padding:30px;border-radius:8px;
    box-shadow:0 4px 15px rgba(0,0,0,0.5);text-align:center;width:90%;max-width:400px;color:#fff;
}
input, .btn {
    font-family:monospace;padding:12px;margin:10px 0;border-radius:4px;border:none;outline:none;
}
input[type="text"] {
    width:100%;background:#3c3c3c;color:#fff;border:1px solid #555;
}
.btn {
    cursor:pointer;font-weight:bold;transition:opacity 0.2s;text-align:center;
}
#btn-login { background:#007acc;color:#fff;width:100%; }
#code{flex:1;width:100%;padding:15px;font-family:"Fira Code", "Courier New", monospace;font-size:14px;line-height:1.5;background:#1e1e1e;color:#d4d4d4;border:none;resize:none;outline:none}
#run{padding:15px;font-size:16px;font-weight:bold;background:#007acc;color:#fff;border:none;cursor:pointer}
#terminal{flex:1;padding:15px;overflow-y:auto;font-family:"Fira Code", "Courier New", monospace;font-size:14px;line-height:1.5;color:#fff;background:#000;white-space:pre-wrap;word-break:break-word;outline:none}
#back{padding:12px;font-size:14px;font-weight:bold;background:#333;color:#fff;border:none;cursor:pointer}
.cursor{display:inline-block;width:8px;height:1em;background:#fff;vertical-align:middle;animation:b 1s step-end infinite}
@keyframes b{50%{opacity:0}}
#error-msg { color:#ff5f56;font-size:13px;margin-top:10px;height:1.2em; }
</style>
</head>
<body>

<div id="login-view">
    <div id="login-box">
        <h2 style="margin-bottom:20px">PyLearn Access</h2>
        <input type="text" id="input_student_code" placeholder="Enter Student Code" onkeydown="if(event.key==='Enter') doLogin()"/>
        <button class="btn" id="btn-login" onclick="doLogin()">ENTER</button>
        <div id="error-msg"></div>
    </div>
</div>

<div id="editor-view">
<textarea id="code" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off"></textarea>
<button id="run" onclick="start()">▶ EXECUTAR</button>
</div>

<div id="terminal-view">
<div id="terminal" tabindex="0"></div>
<button id="back" onclick="back()">← Voltar</button>
</div>

<script>
var ws, term=document.getElementById("terminal");

function showView(id) {
    document.getElementById("login-view").style.display = id === "login-view" ? "flex" : "none";
    document.getElementById("editor-view").style.display = id === "editor-view" ? "flex" : "none";
    document.getElementById("terminal-view").style.display = id === "terminal-view" ? "flex" : "none";
}

async function doLogin() {
    var code = document.getElementById("input_student_code").value.trim();
    if(!code) return;
    
    try {
        var res = await fetch("/login", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({student_code: code})
        });
        var data = await res.json();
        if(data.success) {
            localStorage.setItem("student_code", code);
            initApp();
        } else {
            document.getElementById("error-msg").innerText = "Invalid student code.";
        }
    } catch(e) {
        document.getElementById("error-msg").innerText = "Connection error.";
    }
}

function initApp() {
    var savedCode = localStorage.getItem("student_code");
    if(savedCode) {
        showView("editor-view");
    } else {
        showView("login-view");
    }
}

function start(){
    var code=document.getElementById("code").value;
    var studentCode=localStorage.getItem("student_code");
    
    showView("terminal-view");
    term.innerHTML="";
    term.focus();
    
    var proto=location.protocol==="https:"?"wss:":"ws:";
    ws=new WebSocket(proto+"//"+location.host+"/ws");
    
    ws.onopen=function(){
        addCursor();
        ws.send(JSON.stringify({code:code, student_code:studentCode}));
    };
    ws.onmessage=function(e){
        var m=JSON.parse(e.data);
        if(m.t==="out"){
            processTermData(m.d);
        }
        else if(m.t==="end"){
            append("\\n\\n[Exited with status "+m.c+"]");
            removeCursor();
            ws.close();
        }
    };
    ws.onerror=function(){append("\\n[Connection Error]");removeCursor();};
}

function processTermData(text) {
    text = text.replace(/\\r/g, "");
    for (var i = 0; i < text.length; i++) {
        var char = text[i];
        if (char === "\\b" || char === "\\x08" || char === "\\x7f") {
            removeLast();
        } else {
            appendChar(char);
        }
    }
    term.scrollTop = term.scrollHeight;
}

function appendChar(char){
    var c = document.getElementById("cur");
    c.parentNode.insertBefore(document.createTextNode(char), c);
}

function append(txt) {
    processTermData(txt);
}

function removeLast(){
    var c = document.getElementById("cur");
    while (c.previousSibling) {
        var node = c.previousSibling;
        if (node.nodeType === 3) { 
            if (node.length > 0) {
                node.deleteData(node.length - 1, 1);
                return; 
            } else {
                node.remove();
            }
        } else {
            return;
        }
    }
}

function back(){
    if(ws){ws.close();ws=null;}
    showView("editor-view");
}

function addCursor(){
    var c=document.createElement("span");
    c.className="cursor";
    c.id="cur";
    term.appendChild(c);
}

function removeCursor(){
    var c=document.getElementById("cur");
    if(c)c.remove();
}

term.addEventListener("keydown",function(e){
    if(!ws || ws.readyState!==1) return;
    e.preventDefault();
    var key = e.key;
    if(key==="Enter") ws.send(JSON.stringify({t:"in",d:"\\n"}));
    else if(key==="Backspace") ws.send(JSON.stringify({t:"in",d:"\\x7f"}));
    else if(key.length===1 && !e.ctrlKey && !e.metaKey) ws.send(JSON.stringify({t:"in",d:key}));
    else if(e.ctrlKey && key==="c") ws.send(JSON.stringify({t:"in",d:"\\x03"}));
});

term.addEventListener("click",function(){term.focus();});

initApp();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
