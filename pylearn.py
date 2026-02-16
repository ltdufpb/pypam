#!/usr/bin/env python3
"""
PyLearn - Secure Docker-based Mobile Terminal
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
DOCKER_IMAGE = "python:3.14-slim"
MEM_LIMIT = "64m"

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


@app.websocket("/ws")
async def run_code(ws: WebSocket):
    await ws.accept()
    container = None
    temp_dir = tempfile.mkdtemp(prefix="pylearn_")

    # Track if we have sent any data to the user
    # If the program crashes instantly, we might need to fallback to logs
    has_sent_output = False

    try:
        data = await ws.receive_json()
        code = data.get("code", "")
        if not code:
            return

        # Startup delay + unbuffered python (-u)
        # Note: If code has SyntaxError, this sleep never happens!
        safe_code = "import time; time.sleep(0.5)\n" + code

        script_path = os.path.join(temp_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(safe_code)

        # Create Container
        container = client.containers.create(
            DOCKER_IMAGE,
            command=["python3", "-u", "/app/script.py"],
            working_dir="/app",
            stdin_open=True,
            tty=True,
            detach=True,
            network_disabled=True,
            mem_limit=MEM_LIMIT,
            nano_cpus=int(0.5 * 1e9),
            pids_limit=20,
            read_only=True,
            volumes={temp_dir: {"bind": "/app", "mode": "rw"}},
            tmpfs={"/tmp": ""},
        )

        container.start()

        # Attach Socket
        socket = container.attach_socket(
            params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
        )

        async def forward_output():
            nonlocal has_sent_output
            loop = asyncio.get_event_loop()
            while True:
                try:
                    # Read 1KB chunks
                    data = await loop.run_in_executor(None, socket.read, 1024)
                    if not data:
                        break  # EOF
                    has_sent_output = True
                    await ws.send_json({"t": "out", "d": data.decode(errors="replace")})
                except:
                    break

        output_task = asyncio.create_task(forward_output())

        # Main Loop: Handle Input & Monitor Status
        try:
            while True:
                container.reload()
                if container.status != "running":
                    break  # Container died, exit loop

                try:
                    # Quick timeout to keep checking container status
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

        # --- SHUTDOWN SEQUENCE ---

        # 1. Wait for the output task to finish reading whatever is left in the pipe
        #    (This catches the SyntaxError that happened right before exit)
        try:
            await asyncio.wait_for(output_task, timeout=2.0)
        except asyncio.TimeoutError:
            output_task.cancel()
        except:
            pass

        # 2. Get Exit Code
        container.reload()
        exit_code = container.attrs["State"]["ExitCode"]

        # 3. FALLBACK: If we haven't sent ANY output and it failed,
        #    fetch logs directly. This guarantees we see SyntaxErrors.
        if not has_sent_output and exit_code != 0:
            try:
                logs = container.logs().decode(errors="replace")
                if logs:
                    await ws.send_json({"t": "out", "d": logs})
            except:
                pass

        await ws.send_json({"t": "end", "c": exit_code})

    except Exception as e:
        await ws.send_json({"t": "out", "d": f"\nServer Error: {e}\n"})
        await ws.send_json({"t": "end", "c": 1})
    finally:
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
#editor-view,#terminal-view{position:absolute;top:0;left:0;right:0;bottom:0;display:flex;flex-direction:column}
#terminal-view{display:none}
#code{flex:1;width:100%;padding:15px;font-family:"Fira Code", "Courier New", monospace;font-size:14px;line-height:1.5;background:#1e1e1e;color:#d4d4d4;border:none;resize:none;outline:none}
#run{padding:15px;font-size:16px;font-weight:bold;background:#007acc;color:#fff;border:none;cursor:pointer}
#terminal{flex:1;padding:15px;overflow-y:auto;font-family:"Fira Code", "Courier New", monospace;font-size:14px;line-height:1.5;color:#fff;background:#000;white-space:pre-wrap;word-break:break-word;outline:none}
#back{padding:12px;font-size:14px;font-weight:bold;background:#333;color:#fff;border:none;cursor:pointer}
.cursor{display:inline-block;width:8px;height:1em;background:#fff;vertical-align:middle;animation:b 1s step-end infinite}
@keyframes b{50%{opacity:0}}
</style>
</head>
<body>

<div id="editor-view">
<textarea id="code" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off">i = 0
while True:
    print(f"Counting: {i}")
    i += 1
    if i > 5: break
    import time
    time.sleep(1)</textarea>
<button id="run" onclick="start()">▶ EXECUTAR</button>
</div>

<div id="terminal-view">
<div id="terminal" tabindex="0"></div>
<button id="back" onclick="back()">← Voltar</button>
</div>

<script>
var ws, term=document.getElementById("terminal");

function start(){
    var code=document.getElementById("code").value;
    document.getElementById("editor-view").style.display="none";
    document.getElementById("terminal-view").style.display="flex";
    term.innerHTML="";
    term.focus();
    
    var proto=location.protocol==="https:"?"wss:":"ws:";
    ws=new WebSocket(proto+"//"+location.host+"/ws");
    
    ws.onopen=function(){
        addCursor();
        ws.send(JSON.stringify({code:code}));
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
    document.getElementById("terminal-view").style.display="none";
    document.getElementById("editor-view").style.display="flex";
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
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


if __name__ == "__main__":
    import uvicorn

    print(f"http://0.0.0.0:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
