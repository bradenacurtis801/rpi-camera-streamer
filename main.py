import uvicorn
import io
import asyncio
from pathlib import Path
from threading import Condition, Thread
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder, Quality
from picamera2.outputs import FileOutput


class StreamingOutput(io.BufferedIOBase):
    """Class to handle the streaming of JPEG frames."""
    def __init__(self):
        super().__init__()
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
    """Singleton class to manage the JPEG stream from the camera."""
    _instance = None

    def __new__(cls, loop=None):
        if cls._instance is None:
            cls._instance = super(JpegStream, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, loop=None):
        if self._initialized:
            return
        self.active = False
        self.connections = set()
        self.picam2 = None
        self.loop = loop  # Main event loop
        self._initialized = True

    async def send_frame(self, jpeg_data):
        """Send JPEG data to all connected WebSocket clients."""
        tasks = [
            websocket.send_bytes(jpeg_data)
            for websocket in self.connections.copy()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    def stream_jpeg(self):
        """Method to continuously stream JPEG data."""
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
        """Start the JPEG stream in a new thread."""
        if not self.active:
            self.active = True
            Thread(target=self.stream_jpeg).start()

    def stop(self):
        """Stop the JPEG stream."""
        if self.active:
            self.active = False


# Initialize FastAPI app
app = FastAPI()

# Obtain the main event loop
loop = asyncio.get_event_loop()

# Initialize the JPEG stream singleton
jpeg_stream = JpegStream(loop)


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main HTML page."""
    index_file = Path(__file__).parent / "index.html"
    return HTMLResponse(index_file.read_text())


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for streaming JPEG data."""
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


@app.post("/start")
async def start_stream():
    """API endpoint to start the JPEG stream."""
    jpeg_stream.start()
    return {"message": "Stream started"}


@app.post("/stop")
async def stop_stream():
    """API endpoint to stop the JPEG stream."""
    jpeg_stream.stop()
    return {"message": "Stream stopped"}


# Run Uvicorn server if the script is executed directly
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
