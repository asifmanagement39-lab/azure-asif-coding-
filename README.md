# Asif Tech Global 24/7 loop stream

Press **Start Live** in the dashboard to generate and send animated graphics
directly to YouTube. The server draws live video frames continuously, chooses
a fresh random design every minute, and keeps streaming until **Stop Live** is
pressed—there is no fixed clip being looped during generated live broadcasts.
Each minute features four original ATG scenes: cyber code rain, a moving
perspective grid, a rotating 3D holographic core, and an overdrive signal HUD.
Orbiting light trails, particles, waveform lines, an equalizer, and scanning
effects animate the scenes. The random palette and visual layouts change for
each new minute. A rotating, extruded **ATG** hologram stays branded on-screen,
and each design displays a unique sequential ID. The sequence is stored on the
server and survives Docker restarts as long as its state volume is kept. Press
**Stop Live** to stop the encoder and
disable reconnects. If the running process disconnects unexpectedly, FFmpeg
retries with increasing delays. Docker Compose restarts the service after a
server or container restart. Set `AUTO_START_STREAM=true` on an always-on server
to resume broadcasting after an app/container restart when
`YOUTUBE_STREAM_KEY` is configured. A restart can still briefly interrupt the
YouTube broadcast.

Choose **Horizontal (16:9, 1280 × 720)** or **Vertical (9:16, 720 × 1280)**
in the control room before starting. The selection applies to the generated
graphics and to the outgoing FFmpeg video, including custom videos. Stop the
broadcast before changing formats. This runs one format per broadcast; use a
separate YouTube event/stream key if you need simultaneous horizontal and
vertical broadcasts.

This is best-effort uptime, not a promise of a literally uninterrupted,
permanent broadcast: server/network failures and YouTube interruptions or
policies can stop a stream. Use video/audio that you own or are licensed to
broadcast, and check the YouTube Live dashboard after going live.

The live-generated stream is original abstract graphic art without audio. The
dashboard creates a new 12-second preview design whenever the preview ends;
the live broadcaster independently creates new designs every minute. Neither
uses the previous MP4 as the continuous generated live source. Original
visuals do not guarantee copyright clearance, monetization, or acceptance under
YouTube's reused/repetitious content policies; follow YouTube's current rules
and only stream content you have rights to use. To use
your own video instead, place it at `media/stream.mp4` and set
`VIDEO_PATH=/media/stream.mp4` in the Compose service environment; a custom
video takes precedence over the generated designs and will loop as supplied.
Leave `VIDEO_PATH` unset to use continuously generated ATG designs.

## Linux server deployment with Docker Compose

The `atg-design-state` Docker volume stores the unique-design sequence. Keep
this volume to avoid reusing design IDs after recreating the container; deleting
it resets the sequence.

1. Install Docker Engine and the Docker Compose plugin on your server.
2. (Optional) To use your own video, place a video you have permission to
   stream at `media/stream.mp4` and configure `VIDEO_PATH=/media/stream.mp4`.
   Otherwise the service generates its own graphic loop.
3. Copy `.env.example` to `.env` and set `YOUTUBE_STREAM_KEY` to the stream key
   from YouTube Studio. Set `AUTO_START_STREAM=true` to resume after server
   restarts. Set a unique `DASHBOARD_PASSWORD` (at least 16 characters) before
   exposing the dashboard. Keep this secret file private:

   ```sh
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```

4. Start the dashboard:

   ```sh
   docker compose up --build -d
   docker compose logs -f dashboard
   ```

   The environment key stays on the server and is not returned by the status
   API. Alternatively, leave it empty and enter the key in the dashboard.
5. On the server, open `http://127.0.0.1:8765`; or use an HTTPS reverse proxy.
   The configured dashboard username/password protects the dashboard and every
   API route. Do not expose the stream-key form over plain HTTP or publish port
   8765 directly to the internet.
6. Paste your YouTube stream key into **Before you go live → YouTube stream
   key**, choose **Save key**, then press **Start Live**. Press **Stop Live**
   to stop broadcasting. A dashboard-entered key stays only in server memory
   until removed or the service restarts.

The dashboard reports whether the worker is generating, starting, streaming,
reconnecting, or stopped. “Streaming” means FFmpeg is running; confirm the
actual broadcast and stream health in YouTube Studio. If your YouTube event
requires manual start, enable its Auto-start setting or start the event in
YouTube Studio; sending RTMP video does not override event settings.

The dashboard date, greeting, and clock use the viewer's local time zone and
update automatically. Viewer, peak-viewer, and chat analytics are shown as
unavailable until a YouTube Data API integration is configured; encoder status
and selected output resolution are reported from the local streaming service.

## Azure App Service (Linux container)

The Web App must run this repository's Docker image; a static-site container
does not run the encoder. Configure the Web App container to use an image built
from this repository's `Dockerfile`, set **Always On** to **On**, and keep the
instance count at **1** so multiple app replicas do not broadcast to the same
YouTube event. The Basic (B1) plan supports Always On.

Under **Settings → Environment variables → App settings**, add these settings
and save/restart the Web App:

| Setting | Value |
|---------|-------|
| `WEBSITES_PORT` | `8765` |
| `PORT` | `8765` |
| `AUTO_START_STREAM` | `true` |
| `YOUTUBE_STREAM_KEY` | The key from YouTube Studio; enter it as a secret Azure App Setting |
| `DASHBOARD_USERNAME` | Your private dashboard username |
| `DASHBOARD_PASSWORD` | A unique password with at least 16 characters |

Azure App Service requires `DASHBOARD_PASSWORD`; the dashboard and all API
routes except the health probe use HTTP Basic authentication over the Web
App's HTTPS endpoint. Your browser will show a sign-in prompt. Do not put the
stream key or dashboard password in GitHub, source files, a Docker image, or
screenshots. Rotate a key/password immediately if exposed.

After saving app settings, restart the Web App and verify the encoder status
and YouTube Studio. A running Azure Web App keeps streaming when your PC or
phone is off, but deployments, platform maintenance, network failures, and
YouTube interruptions can still disconnect the broadcast. `AUTO_START_STREAM`
restarts it when the app process comes back; it does not prevent downtime.
The default generated broadcast is ATG motion graphics, not a camera feed
from your powered-off device. To broadcast your own video, provide a licensed
video to the server and configure `VIDEO_PATH` as described above.

## Change or remove the server stream key

For an environment-managed key, edit `YOUTUBE_STREAM_KEY` in `.env`, then
recreate the container so it receives the updated environment:

```sh
docker compose up -d --force-recreate dashboard
```

To remove a key saved from the dashboard, use **Remove key**. To stop just the
stream, press **Stop Live**. To stop the service:

```sh
docker compose down
```

If a key has been exposed, rotate it in YouTube Studio as well.

## Run without Docker

Install Python 3.12 and the requirements; set `YOUTUBE_STREAM_KEY` (and
optionally `VIDEO_PATH`) in the server environment, then run:

```sh
python3 -m pip install -r requirements.txt
YOUTUBE_STREAM_KEY='your-private-key' python3 server.py
```

With no `VIDEO_PATH`, Python generates a fresh graphic loop in the system temp
directory when a broadcast starts. Separate horizontal and vertical files are
created for the selected format. Set `VIDEO_PATH=./media/stream.mp4` to use your
own video; the selected format is applied to its output.
The requirements include Pillow for graphics and a bundled FFmpeg build for
supported platforms. Install system FFmpeg separately if the bundled build is
not available on your platform. Docker also installs system FFmpeg.

## Tests

```sh
python3 -m unittest -v
```
