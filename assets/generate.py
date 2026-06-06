"""
素材生成器 — 一键生成所有 B50 图表静态素材
运行: python assets/generate.py
生成后可替换为你自己手绘的 PNG
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════

CARD_W, CARD_H = 226, 116
CARD_RADIUS = 10
BORDER_W = 3

COVER_SIZE = 82
COVER_X = 10
COVER_Y = (CARD_H - COVER_SIZE) // 2

# 难度主题
DIFF = {
    "bas": {"bg": (225, 248, 225), "border": (120, 210, 130)},
    "adv": {"bg": (255, 246, 215), "border": (235, 195, 55)},
    "exp": {"bg": (255, 215, 215), "border": (200, 55,  55)},
    "mas": {"bg": (232, 215, 252), "border": (145, 70,  210)},
    "rem": {"bg": (250, 245, 255), "border": (200, 185, 230)},
}

# 徽标颜色
BADGES = {
    "fc":   (40,  170, 80),
    "fcp":  (20,  140, 60),
    "ap":   (210, 160, 50),
    "app":  (240, 120, 40),
    "fs":   (50,  150, 210),
    "fsp":  (40,  130, 190),
    "fsd":  (30,  110, 170),
    "fsdp": (20,  90,  150),
}

GRADES = {
    "sssp": (230, 60,  130),
    "sss":  (210, 160, 40),
    "ssp":  (220, 120, 40),
    "ss":   (140, 140, 165),
    "sp":   (170, 120, 60),
    "s":    (180, 140, 70),
}

# ══════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = []
    if bold:
        paths.append("C:/Windows/Fonts/msyhbd.ttc")
    paths.append("C:/Windows/Fonts/SIMYOU.TTF")
    paths.append("C:/Windows/Fonts/msyh.ttc")
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _round_mask(size: tuple[int, int], radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return m


# ══════════════════════════════════════════════════════════
# 卡片模板
# ══════════════════════════════════════════════════════════

def make_card_template(name: str, bg_color: tuple, border_color: tuple):
    """生成一张卡片模板（含圆角背景+边框+封面占位区）"""
    img = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆角背景
    mask = _round_mask((CARD_W, CARD_H), CARD_RADIUS)
    bg_layer = Image.new("RGBA", (CARD_W, CARD_H), bg_color)
    img.paste(bg_layer, mask=mask)

    # 圆角边框
    draw.rounded_rectangle(
        [0, 0, CARD_W - 1, CARD_H - 1],
        radius=CARD_RADIUS, outline=border_color, width=BORDER_W,
    )

    # 封面占位区（浅灰虚线框）
    cx, cy = COVER_X, COVER_Y
    draw.rounded_rectangle(
        [cx, cy, cx + COVER_SIZE, cy + COVER_SIZE],
        radius=6, outline=(0, 0, 0, 30), width=1,
    )

    img.save(f"assets/card_{name}.png")
    print(f"  card_{name}.png")


# ══════════════════════════════════════════════════════════
# 徽标
# ══════════════════════════════════════════════════════════

def make_badge(name: str, color: tuple, label: str, pad: int = 6):
    """生成一个小徽标（FC/AP/FS 等）"""
    font = _font(12, bold=True)
    tw = int(font.getlength(label))
    w, h = tw + pad * 2, 20
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=4, fill=color)
    draw.text((pad, 3), label, fill=(255, 255, 255), font=font)
    img.save(f"assets/badge_{name}.png")
    print(f"  badge_{name}.png  ({w}×{h})")


# ══════════════════════════════════════════════════════════
# 评级标
# ══════════════════════════════════════════════════════════

def make_grade(name: str, color: tuple, label: str):
    """生成评级标签（SSS+ / SSS / SS+ ...）"""
    font = _font(12, bold=True)
    tw = int(font.getlength(label))
    w, h = tw + 12, 20
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=4, fill=color)
    draw.text((6, 3), label, fill=(255, 255, 255), font=font)
    img.save(f"assets/grade_{name}.png")
    print(f"  grade_{name}.png  ({w}×{h})")


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════

def main():
    print("生成卡片模板...")
    for name, colors in DIFF.items():
        make_card_template(name, colors["bg"], colors["border"])

    print("\n生成 FC/AP/FS 徽标...")
    labels = {
        "fc": "FC", "fcp": "FC+",
        "ap": "AP", "app": "AP+",
        "fs": "FS", "fsp": "FS+",
        "fsd": "FSD", "fsdp": "FSD+",
    }
    for name, color in BADGES.items():
        make_badge(name, color, labels.get(name, name.upper()))

    print("\n生成评级标...")
    grade_labels = {
        "sssp": "SSS+", "sss": "SSS", "ssp": "SS+",
        "ss": "SS", "sp": "S+", "s": "S",
    }
    for name, color in GRADES.items():
        make_grade(name, color, grade_labels.get(name, name.upper()))

    print("\n✅ 全部素材生成完毕 → 存放在 assets/ 目录")
    print("   可替换为你自己画的同名 PNG")


if __name__ == "__main__":
    main()
