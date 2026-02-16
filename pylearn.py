#!/usr/bin/env python3
"""
PyLearn - Final Production Version (Advanced Editor & Fully Unified UI)
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
DOCKER_IMAGE = "python:3.14-alpine"
MAX_CONCURRENT_USERS = 10
MEM_LIMIT = "48m"
CPU_LIMIT_NANO = int(0.20 * 1e9)

# --- AUTHENTICATION ---
ALLOWLIST_FILE = "allowlist.txt"
ADMIN_CREDS_FILE = "admin.txt"
active_sessions = set()


def get_allowlist():
    if not os.path.exists(ALLOWLIST_FILE):
        return {}
    users = {}
    with open(ALLOWLIST_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if ":" in line:
                u, p = line.split(":", 1)
                users[u] = p
    return users


def save_allowlist(users):
    with open(ALLOWLIST_FILE, "w") as f:
        for u, p in users.items():
            f.write(f"{u}:{p}\n")


def get_admin_creds():
    if not os.path.exists(ADMIN_CREDS_FILE):
        return "admin", "admin123"
    with open(ADMIN_CREDS_FILE, "r") as f:
        line = f.read().strip()
        if ":" in line:
            return line.split(":", 1)
    return "admin", "admin123"


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

user_lock = asyncio.Semaphore(MAX_CONCURRENT_USERS)
app = FastAPI()


@app.post("/login")
async def login(data: dict):
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    users = get_allowlist()
    if username in users and users[username] == password:
        return {"success": True}
    return {"success": False}


@app.post("/admin/login")
async def admin_login(data: dict):
    u, p = get_admin_creds()
    if data.get("username") == u and data.get("password") == p:
        return {"success": True}
    return {"success": False}


@app.post("/admin/get_users")
async def get_users(data: dict):
    u, p = get_admin_creds()
    if data.get("admin_u") != u or data.get("admin_p") != p:
        return {"success": False}
    users = get_allowlist()
    return {"success": True, "users": sorted(list(users.keys()))}


@app.post("/admin/save_user")
async def save_user(data: dict):
    u, p = get_admin_creds()
    if data.get("admin_u") != u or data.get("admin_p") != p:
        return {"success": False, "msg": "Acesso negado"}

    new_u = (data.get("username") or "").strip()
    new_p = (data.get("password") or "").strip()
    old_u = (data.get("old_username") or "").strip()

    if not new_u or ":" in new_u:
        return {"success": False, "msg": "Usuário inválido"}

    users = get_allowlist()
    if old_u and old_u in users:
        final_p = new_p if new_p else users[old_u]
        if old_u != new_u:
            del users[old_u]
        users[new_u] = final_p
    else:
        if not new_p:
            return {"success": False, "msg": "Senha obrigatória"}
        users[new_u] = new_p

    save_allowlist(users)
    return {"success": True}


@app.post("/admin/delete_user")
async def delete_user(data: dict):
    u, p = get_admin_creds()
    if data.get("admin_u") != u or data.get("admin_p") != p:
        return {"success": False, "msg": "Acesso negado"}

    target = (data.get("username") or "").strip()
    users = get_allowlist()
    if target in users:
        del users[target]
        save_allowlist(users)
    return {"success": True}


@app.websocket("/ws")
async def run_code(ws: WebSocket):
    await ws.accept()
    if user_lock.locked():
        await ws.send_json({"t": "out", "d": "\n[Servidor Ocupado]\n"})
        await ws.send_json({"t": "end", "c": 1})
        await ws.close()
        return

    username = None
    users = get_allowlist()
    async with user_lock:
        container = None
        temp_dir = tempfile.mkdtemp(prefix="pylearn_")
        os.chmod(temp_dir, 0o755)
        has_sent_output = False
        try:
            data = await ws.receive_json()
            username = (data.get("username") or "").strip()
            password = (data.get("password") or "").strip()
            code = data.get("code", "")

            if username not in users or users[username] != password:
                await ws.send_json({"t": "out", "d": "\n[Credenciais Inválidas]\n"})
                await ws.send_json({"t": "end", "c": 1})
                return

            if username in active_sessions:
                await ws.send_json({"t": "out", "d": "\n[Usuário já ativo]\n"})
                await ws.send_json({"t": "end", "c": 1})
                return

            active_sessions.add(username)
            if not code:
                return

            script_path = os.path.join(temp_dir, "script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            os.chmod(script_path, 0o644)

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
                user="65534:65534",
                environment={"PYTHONIOENCODING": "utf-8", "PYTHON_COLORS": "0"},
            )

            socket = container.attach_socket(
                params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
            )
            container.start()

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
                            os.write(socket.fileno(), msg.get("d").encode())
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
            await ws.send_json({"t": "out", "d": f"\nErro: {e}\n"})
            await ws.send_json({"t": "end", "c": 1})
        finally:
            if username and username in active_sessions:
                active_sessions.remove(username)
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


SHARED_CSS = """
:root {
    --primary: #007acc;
    --success: #28a745;
    --danger: #d73a49;
    --secondary: #e9ecef;
    --secondary-text: #495057;
    --bg-gray: #f8f9fa;
    --border: #dee2e6;
    --text: #1c1e21;
    --radius: 12px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{height:100vh;overflow:hidden;background:var(--bg-gray);font-family:system-ui,-apple-system,sans-serif;color:var(--text)}
.view-container {
    position:absolute;top:0;left:0;right:0;bottom:0;
    display:none;flex-direction:column;
}
.login-screen {
    display:flex;align-items:center;justify-content:center;height:100%;
}
.login-box {
    background:#fff;padding:40px;border-radius:16px;
    box-shadow:0 10px 30px rgba(0,0,0,0.08);text-align:center;width:90%;max-width:400px;
}
h2 { margin-bottom:24px;font-size:24px;font-weight:600 }
input, .btn {
    font-family:inherit;padding:12px 16px;margin:8px 0;border-radius:var(--radius);border:1px solid var(--border);outline:none;font-size:16px;
}
input[type="text"], input[type="password"] {
    width:100%;background:#fff;color:var(--text);transition:border-color 0.2s;
}
input:focus { border-color:var(--primary);box-shadow:0 0 0 2px rgba(0,122,204,0.1) }
.btn {
    cursor:pointer;font-weight:600;transition:all 0.2s;text-align:center;border:none;
}
.btn-primary { background:var(--primary);color:#fff;width:100%; }
.btn-primary:hover { opacity:0.9; }
.btn-success { background:var(--success);color:#fff; }
.btn-success:hover { opacity:0.9; }
.btn-danger { background:var(--danger);color:#fff; }
.btn-danger:hover { opacity:0.9; }
.btn-secondary { background:var(--secondary);color:var(--secondary-text); }
.btn-secondary:hover { background:#dde1e3; }

.app-header { display:flex;justify-content:space-between;align-items:center;padding:10px 15px;background:var(--secondary);border-bottom:1px solid var(--border) }
.user-label { color:var(--secondary-text);font-weight:600;font-size:14px }
.logout-btn { background:none;border:none;color:var(--secondary-text);cursor:pointer;font-size:18px;padding:4px;display:flex;align-items:center }
"""

LOGIN_BOX_TEMPLATE = """
    <div class="login-screen">
        <div class="login-box">
            <h2>{title}</h2>
            <input type="text" id="username" name="username" placeholder="Usuário" autocomplete="username" autocapitalize="none" autocorrect="off" onkeydown="if(event.key==='Enter') {login_func}()"/>
            <input type="password" id="password" name="password" placeholder="Senha" autocomplete="current-password" onkeydown="if(event.key==='Enter') {login_func}()"/>
            <button class="btn btn-primary" onclick="{login_func}()">ENTRAR</button>
            <div id="error-msg" style="color:var(--danger);font-size:14px;margin-top:12px;height:1.4em;"></div>
        </div>
    </div>
"""

HEADER_TEMPLATE = """
    <div class="app-header">
        <span class="user-display"></span>
        <button class="logout-btn" title="Sair" onclick="doLogout()">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>
        </button>
    </div>
"""

HTML = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=yes">
<title>PyLearn</title>
<style>
{SHARED_CSS}
#editor-container {{ flex:1; display:flex; background:#fff; overflow:hidden; }}
#line-numbers {{ 
    width:40px; padding:20px 0; background:#f8f9fa; border-right:1px solid #e5e7eb; 
    color:#adb5bd; font-family:"Fira Code",monospace; font-size:14px; line-height:1.6; 
    text-align:right; padding-right:8px; user-select:none; overflow:hidden;
}}
#code {{ 
    flex:1; padding:20px; font-family:"Fira Code",monospace; font-size:14px; 
    line-height:1.6; background:#fff; color:var(--text); border:none; resize:none; 
    outline:none; white-space:pre; overflow-wrap:normal; overflow-x:auto;
}}
#terminal{{flex:1;padding:20px;font-family:"Fira Code",monospace;font-size:14px;line-height:1.6;color:#333;background:#f9fafb;white-space:pre-wrap;word-break:break-word;outline:none;overflow-y:auto;}}
#run, #back {{ width:100%; border-radius:0; margin:0; padding:16px; font-size:16px; }}
.cursor{{display:inline-block;width:8px;height:1.2em;background:#333;vertical-align:middle;animation:b 1s step-end infinite}}
@keyframes b{{50%{{opacity:0}}}}
#error-msg {{ color:var(--danger);font-size:14px;margin-top:12px;height:1.4em; }}
</style>
</head>
<body>
<div id="login-view" class="view-container">
    {LOGIN_BOX_TEMPLATE.format(title="Acesso Estudante", login_func="doLogin")}
</div>
<div id="editor-view" class="view-container">
    {HEADER_TEMPLATE}
    <div id="editor-container">
        <div id="line-numbers">1</div>
        <textarea id="code" spellcheck="false" autocapitalize="none" autocomplete="off" autocorrect="off" placeholder="Escreva seu código Python aqui..."></textarea>
    </div>
    <button id="run" class="btn btn-success" onclick="start()">▶ EXECUTAR</button>
</div>
<div id="terminal-view" class="view-container">
    {HEADER_TEMPLATE}
    <div id="terminal" tabindex="0"></div>
    <button id="back" class="btn btn-secondary" onclick="back()">← Voltar</button>
</div>
<script>
var ws, term=document.getElementById("terminal"), codeArea=document.getElementById("code"), lineNums=document.getElementById("line-numbers");

function updateLineNumbers() {{
    const lines = codeArea.value.split("\\n").length;
    let html = "";
    for(let i=1; i<=lines; i++) html += i + "<br>";
    lineNums.innerHTML = html;
}}

codeArea.addEventListener("input", updateLineNumbers);
codeArea.addEventListener("scroll", () => {{
    lineNums.scrollTop = codeArea.scrollTop;
}});

codeArea.addEventListener("keydown", function(e) {{
    if(e.key === "Enter") {{
        const start = this.selectionStart;
        const end = this.selectionEnd;
        const text = this.value;
        const before = text.substring(0, start);
        const lines = before.split("\\n");
        const currentLine = lines[lines.length - 1];
        const match = currentLine.match(/^\\s*/);
        const indent = match ? match[0] : "";
        const extraIndent = currentLine.trim().endsWith(":") ? "    " : "";
        
        e.preventDefault();
        const insert = "\\n" + indent + extraIndent;
        this.value = text.substring(0, start) + insert + text.substring(end);
        this.selectionStart = this.selectionEnd = start + insert.length;
        updateLineNumbers();
    }}
    if(e.key === "Tab") {{
        e.preventDefault();
        const start = this.selectionStart;
        const end = this.selectionEnd;
        this.value = this.value.substring(0, start) + "    " + this.value.substring(end);
        this.selectionStart = this.selectionEnd = start + 4;
    }}
}});

function showView(id) {{
    document.querySelectorAll(".view-container").forEach(d => d.style.display = "none");
    var target = document.getElementById(id);
    target.style.display = "flex";
    var u = localStorage.getItem("pylearn_u");
    if(u) {{ target.querySelectorAll(".user-display").forEach(el => el.innerText = u); }}
    if(id === "editor-view") updateLineNumbers();
}}

async function doLogin() {{
    var u = document.getElementById("username").value.trim();
    var p = document.getElementById("password").value.trim();
    if(!u || !p) return;
    try {{
        var res = await fetch("/login", {{
            method: "POST", headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{username: u, password: p}})
        }});
        var data = await res.json();
        if(data.success) {{
            localStorage.setItem("pylearn_u", u);
            localStorage.setItem("pylearn_p", p);
            initApp();
        }} else {{ document.getElementById("error-msg").innerText = "Usuário ou senha incorretos."; }}
    }} catch(e) {{ document.getElementById("error-msg").innerText = "Erro ao conectar."; }}
}}
function doLogout() {{
    localStorage.removeItem("pylearn_u");
    localStorage.removeItem("pylearn_p");
    initApp();
}}
function initApp() {{
    var u = localStorage.getItem("pylearn_u"), p = localStorage.getItem("pylearn_p");
    if(u && p) {{ showView("editor-view"); }}
    else {{ document.getElementById("username").value = ""; document.getElementById("password").value = ""; showView("login-view"); }}
}}
function start(){{
    var code=codeArea.value;
    var u=localStorage.getItem("pylearn_u"), p=localStorage.getItem("pylearn_p");
    showView("terminal-view");
    term.innerHTML=""; term.focus();
    var proto=location.protocol==="https:"?"wss:":"ws:";
    ws=new WebSocket(proto+"//"+location.host+"/ws");
    ws.onopen=function(){{ addCursor(); ws.send(JSON.stringify({{code:code, username:u, password:p}})); }};
    ws.onmessage=function(e){{
        var m=JSON.parse(e.data);
        if(m.t==="out") processTermData(m.d);
        else if(m.t==="end"){{ append("\\n\\n[Status: "+m.c+"]"); removeCursor(); ws.close(); }}
    }};
}}
function processTermData(text) {{
    text = text.replace(/\\r/g, "");
    for (var i = 0; i < text.length; i++) {{
        var char = text[i];
        if (char === "\\b" || char === "\\x08" || char === "\\x7f") {{ removeLast(); }} else {{ appendChar(char); }}
    }}
    term.scrollTop = term.scrollHeight;
}}
function appendChar(char){{ var c = document.getElementById("cur"); c.parentNode.insertBefore(document.createTextNode(char), c); }}
function append(txt) {{ processTermData(txt); }}
function removeLast(){{
    var c = document.getElementById("cur");
    while (c.previousSibling) {{
        var node = c.previousSibling;
        if (node.nodeType === 3) {{ 
            if (node.length > 0) {{ node.deleteData(node.length - 1, 1); return; }} else {{ node.remove(); }}
        }} else {{ return; }}
    }}
}}
function back(){{ if(ws){{ws.close();ws=null;}} showView("editor-view"); }}
function addCursor(){{ var c=document.createElement("span"); c.className="cursor"; c.id="cur"; term.appendChild(c); }}
function removeCursor(){{ var c=document.getElementById("cur"); if(c)c.remove(); }}
term.addEventListener("keydown",function(e){{
    if(!ws || ws.readyState!==1) return;
    e.preventDefault();
    var key = e.key;
    if(key==="Enter") ws.send(JSON.stringify({{t:"in",d:"\\n"}}));
    else if(key==="Backspace") ws.send(JSON.stringify({{t:"in",d:"\\x7f"}}));
    else if(key.length===1 && !e.ctrlKey && !e.metaKey) ws.send(JSON.stringify({{t:"in",d:key}}));
    else if(e.ctrlKey && key==="c") ws.send(JSON.stringify({{t:"in",d:"\\x03"}}));
}});
initApp();
</script>
</body>
</html>"""

ADMIN_HTML = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=yes">
<title>PyLearn - Admin</title>
<style>
{SHARED_CSS}
body{{overflow-y:auto;}}
#container {{ max-width:600px;margin:0 auto;padding:20px }}
header {{ display:flex;justify-content:space-between;align-items:center;margin-bottom:20px }}
.user-card {{ background:#fff;padding:16px;border-radius:var(--radius);margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.05);display:flex;justify-content:space-between;align-items:center }}
.user-info {{ font-weight:600;font-size:16px;color:var(--text) }}
.user-actions {{ display:flex;gap:8px }}
#modal {{ position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);display:none;align-items:center;justify-content:center;z-index:100;padding:20px }}
#modal-box {{ background:#fff;padding:24px;border-radius:16px;width:100%;max-width:400px;text-align:center }}
.modal-input {{ width:100%;margin-bottom:12px }}
</style>
</head>
<body>
    <div id="admin-login" class="view-container">
        {LOGIN_BOX_TEMPLATE.format(title="PyLearn Admin", login_func="doAdminLogin")}
    </div>

    <div id="container" style="display:none">
        <header>
            <h2 style="font-size:20px">Estudantes</h2>
            <button class="btn btn-success" onclick="openModal()">+ NOVO</button>
        </header>
        <div id="user-list"></div>
    </div>

    <div id="modal">
        <div id="modal-box">
            <h3 id="modal-title" style="margin-bottom:16px">Novo Estudante</h3>
            <p id="modal-desc" style="color:var(--secondary-text);font-size:14px;margin-bottom:16px"></p>
            <input type="text" id="m_u" class="modal-input" placeholder="Usuário" autocomplete="username" autocapitalize="none" autocorrect="off" />
            <input type="password" id="m_p" class="modal-input" placeholder="Senha" autocomplete="new-password" />
            <div style="display:flex;gap:10px;margin-top:10px">
                <button class="btn btn-secondary" style="flex:1" onclick="closeModal()">CANCELAR</button>
                <button class="btn btn-primary" style="flex:1" onclick="handleModalSave()">SALVAR</button>
            </div>
        </div>
    </div>

<script>
var admin_u, admin_p, editing_u = null;
async function doAdminLogin() {{
    var u = document.getElementById("username").value, p = document.getElementById("password").value;
    var res = await fetch("/admin/login", {{
        method: "POST", headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{username: u, password: p}})
    }});
    var data = await res.json();
    if(data.success) {{
        admin_u = u; admin_p = p;
        document.getElementById("admin-login").style.display = "none";
        document.getElementById("container").style.display = "block";
        loadUsers();
    }} else document.getElementById("error-msg").innerText = "Inválido.";
}}
async function loadUsers() {{
    var res = await fetch("/admin/get_users", {{
        method: "POST", headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{admin_u, admin_p}})
    }});
    var data = await res.json();
    if(data.success) renderUsers(data.users);
}}
function renderUsers(users) {{
    var list = document.getElementById("user-list");
    list.innerHTML = "";
    users.forEach(u => {{
        let div = document.createElement("div");
        div.className = "user-card";
        div.innerHTML = `
            <div class="user-info">${{u}}</div>
            <div class="user-actions">
                <button class="btn btn-primary" style="padding:8px 12px" onclick="openModal('${{u}}')">Editar</button>
                <button class="btn btn-danger" style="padding:8px 12px" onclick="deleteUser('${{u}}')">✕</button>
            </div>
        `;
        list.appendChild(div);
    }});
}}
function openModal(u = null) {{
    editing_u = u;
    document.getElementById("modal").style.display = "flex";
    document.getElementById("m_u").value = u || "";
    document.getElementById("m_p").value = "";
    if(u) {{
        document.getElementById("modal-title").innerText = "Editar Aluno";
        document.getElementById("modal-desc").innerText = "Deixe a senha em branco para manter a atual.";
    }} else {{
        document.getElementById("modal-title").innerText = "Novo Aluno";
        document.getElementById("modal-desc").innerText = "Admin: entregue o aparelho ao aluno.";
    }}
}}
function closeModal() {{ document.getElementById("modal").style.display = "none"; }}
async function handleModalSave() {{
    var u = document.getElementById("m_u").value.trim(), p = document.getElementById("m_p").value.trim();
    if(!u || (!editing_u && !p)) return;
    var res = await fetch("/admin/save_user", {{
        method: "POST", headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{admin_u, admin_p, old_username: editing_u, username: u, password: p}})
    }});
    var data = await res.json();
    if(data.success) {{ closeModal(); loadUsers(); }} else alert(data.msg);
}}
async function deleteUser(u) {{
    if(confirm("Excluir " + u + "?")) {{
        await fetch("/admin/delete_user", {{
            method: "POST", headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{admin_u, admin_p, username: u}})
        }});
        loadUsers();
    }}
}}
document.getElementById("admin-login").style.display = "flex";
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/admin", response_class=HTMLResponse)
def admin():
    return ADMIN_HTML


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
