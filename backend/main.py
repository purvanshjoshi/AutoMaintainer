from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    BackgroundTasks,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from agents import run_agent_loop
import asyncio
import json
import os
import subprocess
import re
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

gitnexus_process = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gitnexus_process
    # Start the GitNexus MCP server in the background
    try:
        gitnexus_process = subprocess.Popen(
            ["gitnexus", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("GitNexus server started on port 4747")
    except Exception as e:
        print(f"Failed to start GitNexus: {e}")

    yield

    if gitnexus_process:
        gitnexus_process.terminate()
        print("GitNexus server stopped")


app = FastAPI(title="AutoMaintainer Backend", lifespan=lifespan)

# Allow the Next.js frontend to connect to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                pass


manager = ConnectionManager()


class StartRequest(BaseModel):
    repo_name: str
    target_issue: Optional[int] = None


@app.post("/start")
async def start_agents(req: StartRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_agent_loop, req.repo_name, manager, req.target_issue)
    return {"status": "started"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # For now, just listen and keep the connection open
            data = await websocket.receive_text()
            await manager.broadcast({"type": "system", "msg": f"Received: {data}"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/repo/{repo_name:path}/tree")
def get_repo_tree(repo_name: str):
    base_tmp = os.path.abspath("/tmp")
    clean_name = repo_name.replace("/", "_").replace("\\", "_")
    repo_dir = os.path.abspath(os.path.join(base_tmp, clean_name))

    if not repo_dir.startswith(base_tmp + os.sep):
        raise HTTPException(status_code=400, detail="Invalid repository name")

    if not os.path.exists(repo_dir):
        raise HTTPException(
            status_code=404,
            detail="Repository not cloned yet. Start an agent loop first.",
        )

    ignored_dirs = {
        ".git",
        "node_modules",
        "__pycache__",
        "venv",
        "env",
        "build",
        "dist",
    }

    def build_tree(path):
        tree = []
        try:
            for item in os.listdir(path):
                if item in ignored_dirs:
                    continue
                item_path = os.path.join(path, item)

                # Prevent symlink loops
                if os.path.islink(item_path):
                    continue

                is_dir = os.path.isdir(item_path)
                node = {
                    "name": item,
                    "path": os.path.relpath(item_path, repo_dir).replace("\\", "/"),
                    "type": "directory" if is_dir else "file",
                }
                if is_dir:
                    node["children"] = build_tree(item_path)
                tree.append(node)
        except (OSError, PermissionError) as e:
            logger.warning(f"Error accessing path {path}: {e}")
            raise HTTPException(status_code=500, detail=f"Error accessing file system")

        # Sort directories first, then files
        tree.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))
        return tree

    return {"name": repo_name, "type": "directory", "children": build_tree(repo_dir)}


@app.get("/repo/{repo_name:path}/file")
def get_repo_file(repo_name: str, file_path: str):
    base_tmp = os.path.abspath("/tmp")
    clean_name = repo_name.replace("/", "_").replace("\\", "_")
    repo_dir = os.path.abspath(os.path.join(base_tmp, clean_name))

    if not repo_dir.startswith(base_tmp + os.sep):
        raise HTTPException(status_code=400, detail="Invalid repository name")

    target_path = os.path.abspath(os.path.join(repo_dir, file_path))

    # Strict path traversal security check for CodeQL
    if not target_path.startswith(repo_dir + os.sep) and target_path != repo_dir:
        raise HTTPException(status_code=403, detail="Invalid file path")

    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="Cannot read binary file")
    except Exception as e:
        logger.exception(f"Failed to read file {target_path}: {e}")
        raise HTTPException(
            status_code=500, detail="An internal error occurred while reading the file"
        )

# Serve the static Next.js frontend if the out directory exists
if os.path.exists("../dashboard/out"):
    app.mount(
        "/", StaticFiles(directory="../dashboard/out", html=True), name="dashboard"
    )
elif os.path.exists("dashboard/out"):  # In docker container
    app.mount("/", StaticFiles(directory="dashboard/out", html=True), name="dashboard")
