import os
import shutil
import tempfile
import asyncio
import logging
import threading
import queue
import socket
import docker
from concurrent.futures import ThreadPoolExecutor
from fastapi import WebSocket
from core.config import (
    DOCKER_IMAGE, MEM_LIMIT, DISK_LIMIT,
    CPU_LIMIT_NANO, active_sessions
)
from core import config  
 
logger = logging.getLogger("pypam")
 
bg_executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="sandbox_io")
 
# --- DOCKER ENGINE CONNECTIVITY ---
try:
    client = docker.from_env()
    try:
        client.images.get(DOCKER_IMAGE)
    except docker.errors.ImageNotFound:
        logger.info(f"Pulling image {DOCKER_IMAGE}...")
        client.images.pull(DOCKER_IMAGE)
except Exception as e:
    logger.critical(f"Docker is not ready: {e}")
    exit(1)
 
 
def _extract_raw_socket(log_stream) -> socket.socket | None:
    """
    Caminho real confirmado por inspeção do objeto em runtime:
      stream._response                         → requests.models.Response
        .raw                                   → urllib3.response.HTTPResponse
          ._connection                         → docker.transport.unixconn.UnixHTTPConnection
            .sock                              → socket.socket  ← esse
    
    Fechar esse socket de outra thread é garantido de desbloquear
    qualquer recv_into em andamento (comportamento do kernel).
    """
    try:
        conn = log_stream._response.raw._connection
        return getattr(conn, "sock", None)
    except Exception:
        return None
 
 
def _read_logs_into_queue(
    log_stream,
    out_q: queue.Queue,
    stop_event: threading.Event,
):
    """
    Roda em thread daemon dedicada.
    Recebe o log_stream já criado (e socket já extraído) pela coroutine principal.
    Empurra chunks para out_q; coloca None ao terminar.
    """
    try:
        for chunk in log_stream:
            if stop_event.is_set():
                break
            out_q.put(chunk)
    except Exception as e:
        logger.debug(f"Log reader thread exiting: {e}")
    finally:
        try:
            log_stream.close()
        except Exception:
            pass
        out_q.put(None) 
 
 
def _force_close_socket(sock_holder: list):
    """
    Desbloqueia recv_into na thread leitora via shutdown(SHUT_RDWR) + close().
    shutdown() acorda imediatamente qualquer recv bloqueado no kernel;
    close() sozinho pode demorar se houver buffer interno (BufferedReader).
    """
    try:
        sock = sock_holder[0]
        if sock is not None:
            sock_holder[0] = None
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
    except Exception:
        pass
 
 
async def execute_user_code(code: str, ws: WebSocket, username: str) -> None:
    container = None
    temp_dir = tempfile.mkdtemp(prefix="pypam_")
    os.chmod(temp_dir, 0o777)
    has_sent_output = False
 
    try:
        if username in active_sessions:
            logger.warning(
                f"Concurrent session attempt: {username}", extra={"user": username}
            )
            await ws.send_json(
                {"t": "out", "d": "\n[Access Denied] User already active.\n"}
            )
            await ws.send_json({"t": "end", "c": 1})
            return
 
        if not code:
            logger.info(
                f"Empty code submitted by {username}", extra={"user": username}
            )
            return
 
        script_path = os.path.join(temp_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)
        os.chmod(script_path, 0o644)
 
        logger.info(f"Spawning container for {username}", extra={"user": username})
 
        container = client.containers.create(
            DOCKER_IMAGE,
            command=["python3", "-u", "/app/script.py"],
            working_dir="/app",
            # tty=False  → framing multiplexado padrão (headers de 8 bytes),
            #              sem truncamento de chunks.
            # stdin_open=False → script não é interativo.
            stdin_open=False,
            tty=False,
            detach=True,
            network_disabled=True,
            mem_limit=MEM_LIMIT,
            memswap_limit=MEM_LIMIT,
            nano_cpus=CPU_LIMIT_NANO,
            pids_limit=15,
            read_only=True,
            tmpfs={
                "/app": f"size={DISK_LIMIT},mode=1777",
                "/tmp": f"size={DISK_LIMIT},mode=1777",
            },
            volumes={script_path: {"bind": "/app/script.py", "mode": "ro"}},
            user="65534:65534",
            security_opt=["no-new-privileges:true"],
            environment={"PYTHONIOENCODING": "utf-8", "PYTHON_COLORS": "0"},
        )
 
        container.start()
 
        log_stream = container.logs(
            stdout=True, stderr=True, stream=True, follow=True
        )
        raw_sock = _extract_raw_socket(log_stream)
 
        out_q: queue.Queue = queue.Queue()
        stop_event = threading.Event()
        sock_holder = [raw_sock]  
 
        reader_thread = threading.Thread(
            target=_read_logs_into_queue,
            args=(log_stream, out_q, stop_event),
            daemon=True,
            name=f"log_reader_{username}",
        )
        reader_thread.start()
 
        async def drain_queue():
            nonlocal has_sent_output
            loop = asyncio.get_event_loop()
            while True:
                try:
                    chunk = await loop.run_in_executor(
                        bg_executor, lambda: out_q.get(timeout=0.1)
                    )
                    if chunk is None:
                        break
                    has_sent_output = True
                    await ws.send_json(
                        {"t": "out", "d": chunk.decode(errors="replace")}
                    )
                except queue.Empty:
                    if not reader_thread.is_alive():
                        break
                except Exception:
                    break
 
        output_task = asyncio.create_task(drain_queue())
 
        start_time = asyncio.get_event_loop().time()
        timed_out = False
        try:
            while True:
                container.reload()
                if container.status != "running":
                    break
 
                if asyncio.get_event_loop().time() - start_time > config.EXECUTION_TIMEOUT:
                    timed_out = True
                    logger.warning(
                        f"MISBEHAVIOR: Execution timeout for {username}",
                        extra={"user": username},
                    )
                    await ws.send_json(
                        {
                            "t": "out",
                            "d": f"\n[Execution Timeout] Script killed after {config.EXECUTION_TIMEOUT}s.\n",
                        }
                    )
                    # Mata o container primeiro, depois desbloqueamos a thread
                    stop_event.set()
                    container.kill()
                    # Fecha o socket BSD diretamente — desbloqueia recv_into imediatamente
                    _force_close_socket(sock_holder)
                    break
 
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=0.2)
                    if msg.get("t") == "in":
                        pass
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    logger.debug(
                        f"Input handling error for {username}: {e}",
                        extra={"user": username},
                    )
                    break
        except Exception as e:
            logger.error(
                f"Container loop error for {username}: {e}",
                extra={"user": username},
            )
 
        # === LIMPEZA ===
        stop_event.set()
        _force_close_socket(sock_holder)   
 
        # Aguarda a task de drenagem — deve encerrar rápido agora que o socket fechou
        try:
            await asyncio.wait_for(output_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            output_task.cancel()
 
        reader_thread.join(timeout=2.0)
 
        container.reload()
        state = container.attrs["State"]
        exit_code = state["ExitCode"]
        oom_killed = state.get("OOMKilled", False)
 
        if oom_killed:
            logger.warning(
                f"MISBEHAVIOR: OOM Killed for {username} (Memory Limit: {MEM_LIMIT})",
                extra={"user": username},
            )
            await ws.send_json(
                {
                    "t": "out",
                    "d": f"\n[Resource Limit] Out of Memory: Script exceeded {MEM_LIMIT}.\n",
                }
            )
        elif exit_code == 137:
            if timed_out:
                pass  
            else:
                logger.warning(
                    f"MISBEHAVIOR: Container killed for {username} (Likely PID limit/Fork Bomb)",
                    extra={"user": username},
                )
                await ws.send_json(
                    {
                        "t": "out",
                        "d": "\n[Resource Limit] Script terminated (Likely hit process limit).\n",
                    }
                )
        elif exit_code != 0:
            logger.info(
                f"Execution finished for {username} with error (Exit Code: {exit_code})",
                extra={"user": username},
            )
        else:
            logger.info(
                f"Execution finished for {username} (Successful)",
                extra={"user": username},
            )
 
        if not has_sent_output and exit_code != 0:
            try:
                logs = container.logs().decode(errors="replace")
                if logs:
                    await ws.send_json({"t": "out", "d": logs})
            except Exception as e:
                logger.error(
                    f"Failed to fetch logs for {username}: {e}",
                    extra={"user": username},
                )
 
        await ws.send_json({"t": "end", "c": exit_code})
 
    except Exception as e:
        logger.exception(
            f"Exception during code execution for {username or 'unknown'}",
            extra={"user": username or "unknown"},
        )
        await ws.send_json({"t": "out", "d": f"\nSystem Error: {e}\n"})
        await ws.send_json({"t": "end", "c": 1})
    finally:
        if username and username in active_sessions:
            active_sessions.remove(username)
        if container:
            try:
                container.remove(force=True)
            except Exception as e:
                logger.error(
                    f"Failed to remove container for {username}: {e}",
                    extra={"user": username},
                )
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.error(
                    f"Failed to remove temp dir for {username}: {e}",
                    extra={"user": username},
                )
