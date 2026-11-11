import colorsys
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


WIDTH = 1280
HEIGHT = 720
FPS = 20
DURATION_SECONDS = 60
LIVE_FPS = 8
SCENE_TITLES = (
    "ATG  /  CYBER GRID",
    "CODE  /  IN MOTION",
    "HOLO CORE  /  3D",
    "SIGNAL  /  OVERDRIVE",
)
VIDEO_FORMATS = {
    "horizontal": (1280, 720),
    "vertical": (720, 1280),
}
PALETTES = [
    ((7, 12, 31), (22, 20, 66), (79, 237, 215), (146, 115, 255)),
    ((11, 12, 27), (52, 18, 54), (255, 111, 145), (255, 190, 96)),
    ((5, 18, 26), (10, 52, 65), (85, 235, 184), (93, 161, 255)),
    ((18, 12, 36), (49, 25, 82), (198, 129, 255), (90, 222, 255)),
    ((14, 18, 25), (41, 47, 61), (255, 205, 95), (126, 234, 176)),
    ((18, 10, 26), (64, 21, 41), (255, 131, 103), (255, 197, 118)),
]
_DESIGN_COUNTER_LOCK = threading.Lock()


def next_design_seed():
    sequence_path = Path(
        os.environ.get(
            "ATG_DESIGN_SEQUENCE_PATH",
            str(Path(tempfile.gettempdir()) / "asif-tech-global-design-sequence.txt"),
        )
    )
    sequence_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = sequence_path.with_suffix(sequence_path.suffix + ".lock")

    with _DESIGN_COUNTER_LOCK, lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        temp_path = None
        try:
            try:
                previous = int(sequence_path.read_text(encoding="ascii").strip())
            except FileNotFoundError:
                previous = 0
            except ValueError as error:
                raise RuntimeError(f"Design sequence file is invalid: {sequence_path}") from error

            design_seed = max(time.time_ns(), previous + 1)
            handle, temp_name = tempfile.mkstemp(
                prefix=f".{sequence_path.name}-",
                dir=sequence_path.parent,
                text=True,
            )
            temp_path = Path(temp_name)
            with os.fdopen(handle, "w", encoding="ascii") as sequence_file:
                sequence_file.write(f"{design_seed}\n")
                sequence_file.flush()
                os.fsync(sequence_file.fileno())
            os.replace(temp_path, sequence_path)
            return design_seed
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def find_ffmpeg():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    return imageio_ffmpeg.get_ffmpeg_exe()


def video_dimensions(video_format):
    try:
        return VIDEO_FORMATS[video_format]
    except KeyError as error:
        raise ValueError("Video format must be horizontal or vertical.") from error


@lru_cache(maxsize=16)
def _font(size, bold=False):
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold
        else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    candidates.extend(
        [
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        ]
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _mix(start, end, amount):
    return tuple(round(left + (right - left) * amount) for left, right in zip(start, end))


@lru_cache(maxsize=24)
def _make_background(top, bottom, width, height):
    gradient = Image.new("RGB", (1, height))
    pixels = gradient.load()
    for y in range(height):
        blend = y / max(height - 1, 1)
        blend = blend * blend * (3 - 2 * blend)
        pixels[0, y] = _mix(top, bottom, blend)
    return gradient.resize((width, height), Image.Resampling.BICUBIC)


@lru_cache(maxsize=32)
def _animation_assets(seed, width, height):
    rng = random.Random(seed)
    hue = rng.random()
    accent = tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, rng.uniform(0.58, 0.88), 1))
    secondary = tuple(
        round(channel * 255)
        for channel in colorsys.hsv_to_rgb((hue + rng.uniform(0.18, 0.42)) % 1, rng.uniform(0.55, 0.9), 1)
    )
    top = tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb((hue + 0.58) % 1, 0.62, 0.10))
    bottom = tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb((hue + 0.58) % 1, 0.72, 0.22))
    palette = (top, bottom, accent, secondary)
    particles = tuple(
        (
            rng.uniform(0.03, 0.97) * width,
            rng.uniform(0.05, 0.95) * height,
            rng.choice((1, 1, 1.5, 2.2, 2.8)),
            rng.choice((1, 2, 3)),
            rng.random() * math.tau,
        )
        for _ in range(68)
    )
    code_columns = tuple(
        (
            rng.uniform(0.35, 0.98),
            rng.uniform(0.55, 1.65),
            rng.random(),
            "".join(rng.choice("01ABCDEF") for _ in range(13)),
        )
        for _ in range(27)
    )
    logo_angle = rng.random() * math.tau
    logo_speed = rng.uniform(1.2, 2.4)
    return palette, particles, code_columns, logo_angle, logo_speed


def _draw_grid(draw, width, height, accent, phase):
    spacing = max(30, round(width * 0.052))
    offset_x = round(math.sin(phase) * spacing * 0.7)
    offset_y = round(math.sin(phase * 2) * spacing * 0.5)
    color = (*accent, 22)
    for x in range(-spacing + offset_x, width + spacing, spacing):
        draw.line((x, 0, x, height), fill=color, width=1)
    for y in range(-spacing + offset_y, height + spacing, spacing):
        draw.line((0, y, width, y), fill=color, width=1)


def _draw_orbit(draw, center, radius, accent, secondary, phase, scale):
    x, y = center
    for ring in range(8):
        spread = radius + ring * round(15 * scale)
        box = (x - spread, y - spread, x + spread, y + spread)
        rotation = math.degrees(phase * (1 if ring % 2 == 0 else -0.65)) + ring * 37
        color = accent if ring % 2 == 0 else secondary
        start = (rotation + ring * 53) % 360
        extent = 205 if ring % 3 else 280
        draw.arc(box, start=start, end=start + extent, fill=(*color, 155 - ring * 11), width=max(2, round(3 * scale)))

    inner_radius = radius + 24 * scale
    for satellite in range(4):
        angle = phase * (1 + satellite % 2) + satellite * math.tau / 4
        distance = inner_radius + satellite * 15 * scale
        px = x + math.cos(angle) * distance
        py = y + math.sin(angle) * distance
        dot = max(3, round((4 + satellite % 2) * scale))
        draw.ellipse((px - dot - 3, py - dot - 3, px + dot + 3, py + dot + 3), fill=(*accent, 38))
        draw.ellipse((px - dot, py - dot, px + dot, py + dot), fill=(*accent, 225))


def _draw_waves(draw, width, height, accent, secondary, phase, scale):
    for wave in range(5):
        baseline = height * (0.46 + wave * 0.055)
        amplitude = height * (0.035 + wave * 0.004)
        points = []
        for x in range(-10, width + 11, max(5, round(width / 100))):
            y = baseline + math.sin(x / (width * 0.12) + phase + wave * 0.58) * amplitude
            y += math.sin(x / (width * 0.062) - phase * 1.4) * amplitude * 0.18
            points.append((x, round(y)))
        color = accent if wave % 2 == 0 else secondary
        draw.line(points, fill=(*color, max(28, 100 - wave * 14)), width=max(1, round(2 * scale)))


def _draw_particles(draw, particles, width, height, phase, accent, secondary, scale):
    for index, (base_x, base_y, radius, speed, offset) in enumerate(particles):
        angle = phase * speed + offset
        x = base_x + math.sin(angle) * width * 0.025
        y = base_y + math.cos(angle * 1.7) * height * 0.032
        pulse = 0.5 + 0.5 * math.sin(angle * 2)
        color = accent if index % 3 else secondary
        dot = max(1, round(radius * scale))
        alpha = round(95 + pulse * 140)
        draw.ellipse((x - dot * 3, y - dot * 3, x + dot * 3, y + dot * 3), fill=(*color, round(alpha * 0.22)))
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(*color, alpha))


def _draw_equalizer(draw, width, height, accent, secondary, phase, scale):
    count = 36
    total_width = width * 0.30
    gap = max(1, min(round(width * 0.006), round(total_width / (count * 3))))
    bar_width = (total_width - gap * (count - 1)) / count
    left = width * 0.65
    baseline = height * 0.88
    for index in range(count):
        motion = (
            0.5
            + 0.28 * math.sin(phase * (1 + index % 3) + index * 0.53)
            + 0.12 * math.sin(phase * 2 - index * 0.27)
        )
        bar_height = (height * 0.09 + height * 0.13 * motion) * (0.58 if index % 4 == 0 else 1)
        x = left + index * (bar_width + gap)
        color = accent if index % 3 else secondary
        draw.rounded_rectangle(
            (x, baseline - bar_height, x + bar_width, baseline),
            radius=max(1, round(bar_width / 2)),
            fill=(*color, round(92 + motion * 110)),
        )


def _draw_code_rain(draw, columns, width, height, phase, accent, scale):
    font = _font(max(8, round(13 * scale)))
    for column_x, speed, offset, glyphs in columns:
        x = round(column_x * width)
        head = (phase / math.tau * height * speed + offset * height) % (height * 1.35)
        for index, glyph in enumerate(glyphs):
            y = round(head - index * 19 * scale)
            if -20 * scale < y < height + 10 * scale:
                alpha = max(18, 115 - index * 13)
                draw.text((x, y), glyph, font=font, fill=(*accent, alpha))


def _project_cube(draw, width, height, phase, accent, secondary, scale):
    center_x = width * 0.73
    center_y = height * 0.53
    size = min(width, height) * 0.19
    spin = phase * 1.3
    tilt = phase * 0.7 + 0.35
    vertices = []
    for z in (-1, 1):
        for y in (-1, 1):
            for x in (-1, 1):
                rotated_x = x * math.cos(spin) - z * math.sin(spin)
                rotated_z = x * math.sin(spin) + z * math.cos(spin)
                rotated_y = y * math.cos(tilt) - rotated_z * math.sin(tilt)
                depth = y * math.sin(tilt) + rotated_z * math.cos(tilt)
                perspective = 3.3 / (3.3 - depth * 0.48)
                vertices.append(
                    (
                        center_x + rotated_x * size * perspective,
                        center_y + rotated_y * size * perspective,
                        depth,
                    )
                )

    for left in range(8):
        for bit in (1, 2, 4):
            right = left ^ bit
            if left < right:
                start = vertices[left]
                end = vertices[right]
                color = accent if (left + right) % 2 else secondary
                draw.line(
                    (start[0], start[1], end[0], end[1]),
                    fill=(*color, 210 if bit == 1 else 120),
                    width=max(1, round((2.5 if bit == 1 else 1.5) * scale)),
                )

    for index, (x, y, depth) in enumerate(vertices):
        radius = max(2, round((3 + (depth + 1) * 2) * scale))
        color = accent if index % 2 else secondary
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(*color, 225),
        )

    pulse = 0.5 + 0.5 * math.sin(phase * 2)
    halo = size * (1.45 + pulse * 0.08)
    draw.ellipse(
        (center_x - halo, center_y - halo, center_x + halo, center_y + halo),
        outline=(*accent, 55),
        width=max(1, round(scale)),
    )


def _draw_perspective_grid(draw, width, height, phase, accent, scale):
    horizon = height * 0.52
    bottom = height * 1.18
    center_x = width * 0.72
    for index in range(-10, 11):
        bottom_x = center_x + index * width * 0.11
        draw.line(
            (center_x, horizon, bottom_x, bottom),
            fill=(*accent, 38),
            width=max(1, round(scale)),
        )
    travel = (phase / math.tau) % 1
    for index in range(13):
        progress = ((index / 12) + travel / 12) % 1
        y = horizon + (progress * progress) * (bottom - horizon)
        draw.line(
            (0, round(y), width, round(y)),
            fill=(*accent, round(16 + progress * 45)),
            width=max(1, round(scale)),
        )


def _draw_rotating_atg(
    effects,
    draw,
    width,
    height,
    angle,
    accent,
    secondary,
    scale,
    design_seed,
):
    center_x = round(width * 0.73)
    center_y = round(height * (0.50 + math.sin(angle * 0.5) * 0.025))
    radius_x = min(width, height) * 0.235
    flattened_radius = radius_x * (0.18 + abs(math.cos(angle)) * 0.30)
    for ring in range(3):
        radius = radius_x * (0.76 + ring * 0.20)
        height_radius = flattened_radius * (0.55 + ring * 0.28)
        rotation = math.degrees(angle * (0.35 if ring % 2 else -0.22))
        color = accent if ring != 1 else secondary
        draw.arc(
            (
                center_x - radius,
                center_y - height_radius,
                center_x + radius,
                center_y + height_radius,
            ),
            start=rotation + 28 + ring * 24,
            end=rotation + 322 + ring * 11,
            fill=(*color, 165 - ring * 28),
            width=max(1, round((3 - ring * 0.5) * scale)),
        )

    font = _font(max(24, round(min(width * 0.14, height * 0.18))), True)
    text_bounds = font.getbbox("ATG", stroke_width=max(1, round(scale)))
    padding = max(5, round(10 * scale))
    logo = Image.new(
        "L",
        (text_bounds[2] - text_bounds[0] + padding * 2, text_bounds[3] - text_bounds[1] + padding * 2),
    )
    ImageDraw.Draw(logo).text(
        (padding - text_bounds[0], padding - text_bounds[1]),
        "ATG",
        font=font,
        fill=255,
        stroke_width=max(1, round(scale)),
        stroke_fill=255,
    )

    cosine = math.cos(angle)
    if cosine < 0:
        logo = logo.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    target_width = max(2, round(logo.width * max(0.10, abs(cosine))))
    logo = logo.resize((target_width, logo.height), Image.Resampling.LANCZOS)
    x = center_x - logo.width // 2
    y = center_y - logo.height // 2
    thickness = max(2, round(8 * scale))

    for depth in range(thickness, 0, -1):
        side = Image.new("RGBA", logo.size, (*secondary, 190))
        side.putalpha(logo)
        effects.alpha_composite(side, (x + round(depth * scale), y + round(depth * scale)))

    outline = logo.filter(ImageFilter.MaxFilter(5 if scale < 1 else 7))
    edge = ImageChops.subtract(outline, logo)
    outline_layer = Image.new("RGBA", logo.size, (*accent, 225))
    outline_layer.putalpha(edge)
    effects.alpha_composite(outline_layer, (x, y))
    face = Image.new("RGBA", logo.size, (239, 247, 255, 255))
    face.putalpha(logo)
    effects.alpha_composite(face, (x, y))

    label = f"ATG DESIGN / {design_seed:016X}"
    label_font = _font(max(7, round(10 * scale)), True)
    label_bounds = draw.textbbox((0, 0), label, font=label_font)
    label_x = max(0, center_x - (label_bounds[2] - label_bounds[0]) // 2)
    label_y = min(height - 1, center_y + logo.height * 0.70)
    draw.text((label_x, label_y), label, font=label_font, fill=(*accent, 220))


def render_animation_frame(
    seed,
    frame_index,
    total_frames,
    width=WIDTH,
    height=HEIGHT,
    palette=None,
):
    if total_frames < 2:
        raise ValueError("An animation needs at least two frames.")
    if not 0 <= frame_index < total_frames:
        raise ValueError("Frame index must be within the animation.")
    if width < 160 or height < 90:
        raise ValueError("Animation frames must be at least 160x90 pixels.")

    if palette is None:
        palette, particles, code_columns, logo_angle, logo_speed = _animation_assets(seed, width, height)
    else:
        _, particles, code_columns, logo_angle, logo_speed = _animation_assets(seed, width, height)
    top, bottom, accent, secondary = palette
    scale = width / WIDTH
    scene_index = min(3, frame_index * len(SCENE_TITLES) // total_frames)
    scene_start = scene_index * total_frames // len(SCENE_TITLES)
    scene_end = (scene_index + 1) * total_frames // len(SCENE_TITLES)
    scene_frame_count = max(2, scene_end - scene_start)
    phase = math.tau * (frame_index - scene_start) / scene_frame_count
    ambient_phase = math.tau * frame_index / total_frames
    background = _make_background(top, bottom, width, height).convert("RGBA")
    effects = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(effects, "RGBA")

    _draw_grid(draw, width, height, accent, ambient_phase)

    center_x = width * (0.72 + 0.025 * math.sin(phase))
    center_y = height * (0.49 + 0.035 * math.cos(phase))
    orbit_size = min(width, height) * 0.18
    glow_pulse = 0.5 + 0.5 * math.sin(phase)
    glow_radius = min(width, height) * (0.30 + glow_pulse * 0.07)
    draw.ellipse(
        (
            center_x - glow_radius,
            center_y - glow_radius,
            center_x + glow_radius,
            center_y + glow_radius,
        ),
        fill=(*accent, round(8 + glow_pulse * 13)),
    )
    if scene_index in (0, 3):
        _draw_orbit(draw, (center_x, center_y), orbit_size, accent, secondary, phase, scale)
        _draw_particles(draw, particles, width, height, phase, accent, secondary, scale)
    if scene_index in (1, 3):
        _draw_waves(draw, width, height, accent, secondary, phase, scale)
        _draw_equalizer(draw, width, height, accent, secondary, phase, scale)
    if scene_index == 0:
        _draw_code_rain(draw, code_columns, width, height, phase, accent, scale)
    elif scene_index == 1:
        _draw_perspective_grid(draw, width, height, phase, accent, scale)
    elif scene_index == 2:
        _project_cube(draw, width, height, phase, accent, secondary, scale)
        _draw_particles(draw, particles, width, height, phase, accent, secondary, scale)
    else:
        _draw_perspective_grid(draw, width, height, phase, accent, scale)

    _draw_rotating_atg(
        effects,
        draw,
        width,
        height,
        ambient_phase * logo_speed + logo_angle,
        accent,
        secondary,
        scale,
        seed,
    )

    sweep_y = ((phase / math.tau) * height * 1.4 - height * 0.2) % height
    draw.line(
        (0, sweep_y, width, sweep_y),
        fill=(*secondary, round(18 + glow_pulse * 22)),
        width=max(1, round(2 * scale)),
    )

    badge_box = (round(width * 0.05), round(height * 0.08), round(width * 0.28), round(height * 0.145))
    draw.rounded_rectangle(badge_box, radius=round(height * 0.03), fill=(255, 255, 255, 16))
    badge_x, badge_y = badge_box[0] + round(14 * scale), badge_box[1] + round(13 * scale)
    blink = 0.5 + 0.5 * math.sin(phase * 2)
    draw.ellipse((badge_x, badge_y, badge_x + round(12 * scale), badge_y + round(12 * scale)), fill=(*accent, round(140 + 115 * blink)))
    draw.text((badge_x + round(23 * scale), badge_box[1] + round(9 * scale)), "LIVE  /  GLOBAL", font=_font(max(10, round(17 * scale)), True), fill=(239, 244, 255, 238))
    draw.text((round(width * 0.05), round(height * 0.22)), "ASIF TECH GLOBAL", font=_font(max(12, round(21 * scale)), True), fill=(*accent, 245))

    title = SCENE_TITLES[scene_index]
    draw.text(
        (round(width * 0.05), round(height * 0.30)),
        title,
        font=_font(max(20, round(47 * scale)), True),
        fill=(255, 255, 255, 248),
        stroke_width=max(1, round(scale)),
        stroke_fill=(255, 255, 255, 45),
    )
    draw.rounded_rectangle(
        (round(width * 0.05), round(height * 0.415), round(width * 0.15), round(height * 0.422)),
        radius=max(1, round(3 * scale)),
        fill=(*accent, round(190 + 60 * glow_pulse)),
    )
    draw.text((round(width * 0.05), round(height * 0.46)), "TECH  /  IDEAS  /  THE FUTURE", font=_font(max(10, round(19 * scale)), True), fill=(190, 201, 226, 232))
    draw.text((round(width * 0.05), round(height * 0.86)), "A CONTINUOUS SIGNAL FROM ASIF TECH GLOBAL", font=_font(max(8, round(14 * scale)), True), fill=(179, 190, 216, 205))
    counter = f"{scene_index + 1:02d} / {len(SCENE_TITLES):02d}"
    counter_box = (round(width * 0.87), round(height * 0.85), round(width * 0.95), round(height * 0.91))
    draw.rounded_rectangle(counter_box, radius=round(height * 0.025), fill=(255, 255, 255, 19))
    draw.text((counter_box[0] + round(11 * scale), counter_box[1] + round(9 * scale)), counter, font=_font(max(8, round(13 * scale)), True), fill=(*accent, 240))

    frame = Image.alpha_composite(background, effects).convert("RGB")
    return frame.tobytes()


def build_video_command(
    ffmpeg,
    output_path,
    width=WIDTH,
    height=HEIGHT,
    fps=FPS,
    duration_seconds=DURATION_SECONDS,
):
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-f",
        "lavfi",
        "-t",
        str(duration_seconds),
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_generated_stream_command(ffmpeg, width, height, rtmp_url, stream_key, fps=LIVE_FPS):
    destination = f"{rtmp_url.rstrip('/')}/{stream_key}"
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        "2500k",
        "-minrate",
        "2500k",
        "-maxrate",
        "2500k",
        "-bufsize",
        "5000k",
        "-x264-params",
        "nal-hrd=cbr:force-cfr=1",
        "-r",
        "30",
        "-g",
        "60",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "44100",
        "-f",
        "flv",
        destination,
    ]


def stream_generated_designs(
    process,
    width,
    height,
    should_continue,
    fps=LIVE_FPS,
    design_seconds=DURATION_SECONDS,
    on_frame=None,
    on_design=None,
):
    if fps < 1 or design_seconds < 1:
        raise ValueError("Live animation FPS and design duration must be positive.")
    frames_per_design = fps * design_seconds

    while should_continue() and process.poll() is None:
        seed = next_design_seed()
        if on_design is not None:
            on_design(seed)
        for frame_index in range(frames_per_design):
            if not should_continue() or process.poll() is not None:
                return
            frame_started = time.monotonic()
            frame = render_animation_frame(
                seed,
                frame_index,
                frames_per_design,
                width=width,
                height=height,
            )
            try:
                process.stdin.write(frame)
            except (BrokenPipeError, OSError):
                return
            if on_frame is not None:
                on_frame()
            remaining = 1 / fps - (time.monotonic() - frame_started)
            if remaining > 0 and should_continue():
                time.sleep(remaining)


def generate_loop_video(
    output_path,
    ffmpeg="ffmpeg",
    seed=None,
    duration_seconds=DURATION_SECONDS,
    fps=FPS,
    width=WIDTH,
    height=HEIGHT,
):
    ffmpeg_path = find_ffmpeg() if ffmpeg == "ffmpeg" else ffmpeg
    if ffmpeg_path is None:
        raise RuntimeError("FFmpeg is required to generate the animated loop video.")
    if duration_seconds < 2 or fps < 1:
        raise ValueError("Animation duration must be at least two seconds and FPS must be positive.")
    if width < 160 or height < 90:
        raise ValueError("Animation frames must be at least 160x90 pixels.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
    total_frames = duration_seconds * fps
    command = build_video_command(
        ffmpeg_path,
        output_path=output,
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration_seconds,
    )

    temp_handle, temp_name = tempfile.mkstemp(
        prefix=f".{output.stem}-",
        suffix=".mp4",
        dir=output.parent,
    )
    os.close(temp_handle)
    temp_output = Path(temp_name)

    try:
        with tempfile.TemporaryFile() as error_log:
            process = subprocess.Popen(
                command[:-1] + [str(temp_output)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=error_log,
                shell=False,
                close_fds=True,
            )
            try:
                for frame_index in range(total_frames):
                    frame = render_animation_frame(
                        seed,
                        frame_index,
                        total_frames,
                        width=width,
                        height=height,
                    )
                    process.stdin.write(frame)
                process.stdin.close()
                return_code = process.wait(timeout=180)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired) as error:
                process.kill()
                process.wait()
                if isinstance(error, subprocess.TimeoutExpired):
                    raise RuntimeError("Animated video encoding timed out.") from error
                raise RuntimeError("FFmpeg stopped while encoding the animation.") from error
            finally:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()

            if return_code != 0:
                error_log.seek(0)
                detail = error_log.read().decode("utf-8", errors="replace").strip()[-1200:]
                raise RuntimeError(
                    f"Could not encode the animated loop: {detail or f'FFmpeg exited with code {return_code}.'}"
                )

        if not temp_output.is_file() or temp_output.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not create the animated loop video.")
        os.replace(temp_output, output)
    finally:
        temp_output.unlink(missing_ok=True)

    return output
