"""Create a deterministic editor exercise, explicitly NOT an AI character sample."""
from pathlib import Path
from PIL import Image, ImageDraw

target = Path(__file__).resolve().parents[1] / "examples"
target.mkdir(exist_ok=True)
sheet = Image.new("RGBA", (192, 256))
for i in range(4):
    frame = Image.new("RGBA", (96, 128))
    d = ImageDraw.Draw(frame)
    # A small pixel lantern for practicing selection, layers and transparent animation.
    dark, green, gold, light = "#26352f", "#647d72", "#bea184", "#e4e7c6"
    d.rectangle((44, 20, 51, 25), fill=dark)
    d.rectangle((42, 22, 53, 35), outline=dark, width=2)
    d.rectangle((29, 36, 66, 40), fill=dark)
    d.rectangle((33, 41, 62, 76), fill=dark)
    d.rectangle((36, 44, 59, 73), fill=gold)
    d.rectangle((37, 45, 43, 72), fill=light)
    d.rectangle((48, 43, 50, 74), fill=green)
    d.rectangle((55, 44, 57, 72), fill=green)
    d.rectangle((29, 77, 66, 81), fill=dark)
    d.rectangle((34, 79, 61, 83), fill=green)
    d.rectangle((46, 84, 49, 98), fill=dark)
    offset = [0, 1, 0, -1][i]
    d.rectangle((43+offset, 96, 52+offset, 101), fill=gold)
    d.rectangle((44+offset, 102, 45+offset, 111), fill=gold)
    d.rectangle((49+offset, 102, 50+offset, 111), fill=gold)
    sheet.paste(frame, (i%2*96, i//2*128))
sheet.save(target / "lantern-exercise.png")
print(target / "lantern-exercise.png")
