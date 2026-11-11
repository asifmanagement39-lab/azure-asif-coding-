import os
import threading
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "YouTube Live Stream Runner Active!"

def start_stream():
    # Aapki FFmpeg streaming command (apni Stream Key zaroor check karein)
    os.system("ffmpeg -re -stream_loop -1 -i video.mp4 -c:v libx264 -preset ultrafast -b:v 3000k -maxrate 3000k -bufsize 6000k -pix_fmt yuv420p -g 50 -c:a aac -b:a 128k -ar 44100 -f flv rtmp://a.rtmp.youtube.com/live2/YOUR_STREAM_KEY")

if __name__ == '__main__':
    # Stream background thread me start hogi
    threading.Thread(target=start_stream, daemon=True).start()
    # Azure Web Port listen karega
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
