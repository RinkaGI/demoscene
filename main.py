from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.websockets import WebSocketDisconnect

from database import db
import time
from fastapi.middleware.cors import CORSMiddleware

from bs4 import BeautifulSoup
import markdown
from html import escape

app = FastAPI()

origins = [
    "https://sthings.ml",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request, "messages": db.get_messages()}
    )


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.timeouts = {}
        self.hosts = {}

    def get_timeout(self, websocket: WebSocket):
        if websocket.client.host in self.timeouts:
            return time.time() - self.timeouts[websocket.client.host]
        return 1

    def set_timeout(self, websocket: WebSocket):
        self.timeouts[websocket.client.host] = time.time()

    async def connect(self, websocket: WebSocket):
        if websocket.client.host not in self.hosts:
            self.hosts[websocket.client.host] = 0
        if self.hosts[websocket.client.host] > 3:
            await websocket.close(code=1008, reason="Too many connections")
            return False
        await websocket.accept()
        self.active_connections.append(websocket)
        self.hosts[websocket.client.host] += 1
        return True

    def disconnect(self, websocket: WebSocket):
        self.hosts[websocket.client.host] -= 1
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, json_message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(json_message)
            except RuntimeError:
                self.disconnect(connection)
            except WebSocketDisconnect:
                self.disconnect(connection)


manager = ConnectionManager()


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket):
    success = await manager.connect(websocket)
    while success:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            manager.disconnect(websocket)
            break
        except RuntimeError:
            manager.disconnect(websocket)
            break
        if data["type"] == "message":
            if not data["content"].strip() or manager.get_timeout(websocket) < 1:
                continue
            manager.set_timeout(websocket)
            content = data["content"]
            # max lenght is limit by sqlite
            if len(content) > 1000000000:
                content = content[:1000000000]
            content = escape(BeautifulSoup(content, "html.parser").prettify().strip())
            content = markdown.markdown(content).strip()
            # check if html in content
            message = db.add_message(content)
            await manager.broadcast(
                {
                    "type": "message",
                    "id": message.id,
                    "content": message.content,
                    "created_at": message.created_at,
                }
            )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=80)
