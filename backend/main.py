from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from agents import run_agent_loop
import asyncio
import json
import os
import subprocess
from contextlib import asynccontextmanager

gitnexus_process = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global gitnexus_process
    # Start the GitNexus MCP server in the background
    try:
        gitnexus_process = subprocess.Popen(
            ["npx", "-y", "gitnexus@latest", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
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


# Serve the static Next.js frontend if the out directory exists
if os.path.exists("../dashboard/out"):
    app.mount(
        "/", StaticFiles(directory="../dashboard/out", html=True), name="dashboard"
    )
elif os.path.exists("dashboard/out"):  # In docker container
    app.mount("/", StaticFiles(directory="dashboard/out", html=True), name="dashboard")
