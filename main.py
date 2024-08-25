import io
import asyncio
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder, Quality
from picamera2.outputs import FileOutput
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from threading import Condition, Thread
from contextlib import asynccontextmanager
from pathlib import Path

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = Path(__file__).parent / "index.html"
    return HTMLResponse(index_file.read_text())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    jpeg_stream.connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        jpeg_stream.connections.remove(websocket)
        if not jpeg_stream.connections:
            jpeg_stream.stop()

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

    def read(self):
        with self.condition:
            self.condition.wait()
            return self.frame

class JpegStream:
    def __init__(self, loop):
        self.active = False
        self.connections = set()
        self.picam2 = None
        self.loop = loop  # Main event loop

    async def send_frame(self, jpeg_data):
        tasks = [
            websocket.send_bytes(jpeg_data)
            for websocket in self.connections.copy()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    def stream_jpeg(self):
        self.picam2 = Picamera2()
        video_config = self.picam2.create_video_configuration(
            main={"size": (1920, 1080)}
        )
        self.picam2.configure(video_config)
        output = StreamingOutput()
        self.picam2.start_recording(MJPEGEncoder(), FileOutput(output), Quality.MEDIUM)

        try:
            while self.active:
                jpeg_data = output.read()
                asyncio.run_coroutine_threadsafe(self.send_frame(jpeg_data), self.loop)
        finally:
            self.picam2.stop_recording()
            self.picam2.close()
            self.picam2 = None

    def start(self):
        if not self.active:
            self.active = True
            Thread(target=self.stream_jpeg).start()

    def stop(self):
        if self.active:
            self.active = False

# Obtain the event loop in the main thread
loop = asyncio.get_event_loop()

jpeg_stream = JpegStream(loop)

@app.post("/start")
async def start_stream():
    jpeg_stream.start()
    return {"message": "Stream started"}

@app.post("/stop")
async def stop_stream():
    jpeg_stream.stop()
    return {"message": "Stream stopped"}
