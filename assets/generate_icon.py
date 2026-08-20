"""Script to generate Windows .ico and .png application icons."""

import os
from pathlib import Path
from PIL import Image, ImageDraw


def generate_app_icon(output_dir: str = "assets") -> str:
    """Generate high-res multi-layer Windows .ico and .png icon."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Base squircle background with cyan-blue gradient
    margin = 12
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=54,
        fill=(10, 14, 23, 255),
        outline=(0, 212, 255, 255),
        width=6,
    )

    # 2. Inner glow border
    draw.rounded_rectangle(
        [margin + 6, margin + 6, size - margin - 6, size - margin - 6],
        radius=48,
        outline=(37, 99, 235, 180),
        width=4,
    )

    # 3. Monitor Screen Frame
    screen_left = 42
    screen_top = 46
    screen_right = size - 42
    screen_bottom = 172

    draw.rounded_rectangle(
        [screen_left, screen_top, screen_right, screen_bottom],
        radius=18,
        fill=(18, 26, 44, 255),
        outline=(0, 212, 255, 220),
        width=4,
    )

    # 4. Monitor Screen Content (Grid + Remote Arrows)
    cx, cy = size // 2, (screen_top + screen_bottom) // 2
    # Draw double arrow symbol ↔
    arrow_color = (0, 212, 255, 255)
    # Left arrow
    draw.polygon([(cx - 36, cy), (cx - 16, cy - 18), (cx - 16, cy - 8), (cx + 8, cy - 8), (cx + 8, cy + 8), (cx - 16, cy + 8), (cx - 16, cy + 18)], fill=arrow_color)
    # Right arrow
    draw.polygon([(cx + 36, cy), (cx + 16, cy - 18), (cx + 16, cy - 8), (cx - 8, cy - 8), (cx - 8, cy + 8), (cx + 16, cy + 8), (cx + 16, cy + 18)], fill=(37, 99, 235, 255))

    # 5. Monitor Stand & Base
    stand_w = 20
    stand_top = screen_bottom
    stand_bot = screen_bottom + 26
    draw.rectangle([cx - stand_w // 2, stand_top, cx + stand_w // 2, stand_bot], fill=(0, 212, 255, 220))

    base_w = 80
    draw.rounded_rectangle([cx - base_w // 2, stand_bot, cx + base_w // 2, stand_bot + 12], radius=6, fill=(0, 212, 255, 255))

    # Save PNG
    png_path = out_path / "icon.png"
    img.save(png_path, format="PNG")

    # Save multi-size ICO
    ico_path = out_path / "icon.ico"
    icon_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=icon_sizes)

    print(f"[+] Successfully generated {ico_path} and {png_path}")
    return str(ico_path)


if __name__ == "__main__":
    generate_app_icon()
