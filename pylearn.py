#!/usr/bin/env python3
"""PyLearn - Minimal Mobile Terminal"""

import os, pty, select, signal, uuid, asyncio, fcntl
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

TIMEOUT = 60
PORT = int(os.getenv("PORT", 8000))
app = FastAPI()

def set_nonblocking(fd):
    fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)

@app.websocket("/ws")
async def run_code(ws: WebSocket):
    await ws.accept()
    master_fd = pid = tmp = None
    try:
        data = await ws.receive_json()
        code = data.get("code", "")
        tmp = f"/tmp/py_{uuid.uuid4().hex[:8]}.py"
        with open(tmp, "w") as f: f.write(code)
        pid, master_fd = pty.fork()
        if pid == 0:
            os.execvp("python3", ["python3", "-u", tmp])
        else:
            set_nonblocking(master_fd)
            await ws.send_json({"t": "start"})
            start = asyncio.get_event_loop().time()
            while True:
                if asyncio.get_event_loop().time() - start > TIMEOUT:
                    await ws.send_json({"t": "out", "d": f"\n[Timeout {TIMEOUT}s]\n"})
                    break
                result = os.waitpid(pid, os.WNOHANG)
                if result[0] != 0:
                    try:
                        while True:
                            d = os.read(master_fd, 1024)
                            if not d: break
                            await ws.send_json({"t": "out", "d": d.decode("utf-8", errors="replace")})
                    except: pass
                    await ws.send_json({"t": "end", "c": os.WEXITSTATUS(result[1]) if os.WIFEXITED(result[1]) else -1})
                    pid = None
                    break
                try:
                    r, _, _ = select.select([master_fd], [], [], 0.01)
                    if master_fd in r:
                        d = os.read(master_fd, 1024)
                        if d: await ws.send_json({"t": "out", "d": d.decode("utf-8", errors="replace")})
                except: pass
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=0.01)
                    if msg.get("t") == "in": os.write(master_fd, msg.get("d", "").encode())
                except asyncio.TimeoutError: pass
                except: break
                await asyncio.sleep(0.01)
    except Exception as e:
        try:
            await ws.send_json({"t": "out", "d": f"\nErro: {e}\n"})
            await ws.send_json({"t": "end", "c": 1})
        except: pass
    finally:
        if pid:
            try: os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            except: pass
        if master_fd:
            try: os.close(master_fd)
            except: pass
        if tmp and os.path.exists(tmp):
            try: os.remove(tmp)
            except: pass

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>PyLearn</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden;background:#000;font-family:monospace}
#editor-view,#terminal-view{position:absolute;top:0;left:0;right:0;bottom:0;display:flex;flex-direction:column}
#terminal-view{display:none}
#code{flex:1;width:100%;padding:10px;font-family:"Courier New",monospace;font-size:14px;line-height:1.4;background:#0a0a0a;color:#0f0;border:none;resize:none;outline:none}
#run{padding:14px;font-size:16px;font-weight:bold;background:#0f0;color:#000;border:none;cursor:pointer}
#terminal{flex:1;padding:10px;overflow-y:auto;font-family:"Courier New",monospace;font-size:14px;line-height:1.4;color:#0f0;white-space:pre-wrap;word-break:break-word;outline:none}
#back{padding:12px;font-size:14px;font-weight:bold;background:#333;color:#0f0;border:none;cursor:pointer}
.cursor{border-left:2px solid #0f0;animation:b 1s step-end infinite}
@keyframes b{50%{opacity:0}}
</style>
</head>
<body>

<div id="editor-view">
<textarea id="code" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off">nome = input("Nome: ")
print("Ola", nome)

for i in range(3):
    x = input("Numero: ")
    print(x, "* 2 =", int(x)*2)</textarea>
<button id="run" onclick="start()">▶ EXECUTAR</button>
</div>

<div id="terminal-view">
<div id="terminal" tabindex="0"></div>
<button id="back" onclick="back()">← VOLTAR</button>
</div>

<script>
var ws,term=document.getElementById("terminal");

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
        if(m.t==="out")append(m.d);
        else if(m.t==="end"){append("\\n[exit "+m.c+"]");removeCursor();}
    };
    ws.onerror=function(){append("\\n[erro]");removeCursor();};
    ws.onclose=function(){removeCursor();};
}

function back(){
    if(ws){ws.close();ws=null;}
    document.getElementById("terminal-view").style.display="none";
    document.getElementById("editor-view").style.display="flex";
}

function append(txt){
    removeCursor();
    term.appendChild(document.createTextNode(txt));
    addCursor();
    term.scrollTop=term.scrollHeight;
}

function addCursor(){
    var c=document.createElement("span");
    c.className="cursor";
    c.id="cur";
    c.innerHTML="&nbsp;";
    term.appendChild(c);
}

function removeCursor(){
    var c=document.getElementById("cur");
    if(c)c.remove();
}

function send(ch){
    if(ws&&ws.readyState===1)ws.send(JSON.stringify({t:"in",d:ch}));
}

term.addEventListener("keydown",function(e){
    e.preventDefault();
    if(e.key==="Enter")send("\\n");
    else if(e.key==="Backspace")send("\\x7f");
    else if(e.key.length===1&&!e.ctrlKey&&!e.metaKey)send(e.key);
    else if(e.ctrlKey&&e.key==="c")send("\\x03");
});

term.addEventListener("click",function(){term.focus();});
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def home(): return HTML

if __name__ == "__main__":
    import uvicorn
    print(f"http://0.0.0.0:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)