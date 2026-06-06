"""
kanobot — B50 成绩表图片生成器
素材拼接模式：卡片/徽标/评级 → 预渲染 PNG 素材 · 运行时只写动态文字
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from models import PlayerProfile, ScoreRecord

# ══════════════════════════════════════════════════════════════
# 布局常数 — 整图
# ══════════════════════════════════════════════════════════════

COLS        = 5
CARD_W      = 400
CARD_H      = 160
CARD_GAP    = 12          # 卡片水平间距
ROW_GAP     = 30          # 卡片垂直行间距
MARGIN      = 75          # 左右页边距
SECTION_H   = 40          # B35/B15 分段标题栏高度
HEADER_H    = 396         # 头部高度（昵称、Rating等）
FOOTER_H    = 60          # 页脚高度

WIDTH = 2200
# 行数动态计算高度
# B35: 7 rows, B15: 3 rows

# ══════════════════════════════════════════════════════════════
# 卡片内部坐标（基于 Figma 设计: 卡片 400×160）
# ══════════════════════════════════════════════════════════════

# 曲绘封面
COVER_X     = 4
COVER_Y     = 4
COVER_SIZE  = 133

# 曲名
TITLE_X     = 144
TITLE_Y     = 4
TITLE_W     = 251
TITLE_H     = 21

# 达成率（数字素材）
ACH_X       = 144
ACH_Y       = 33
ACH_W       = 166
ACH_H       = 40

# 难度标签
DIFF_X      = 144
DIFF_Y      = 77
DIFF_W      = 166
DIFF_H      = 30

# DX 分数
DX_SCORE_X  = 316
DX_SCORE_Y  = 33
DX_SCORE_W  = 80
DX_SCORE_H  = 30

# DX 星数图标
DX_STAR_X   = 336
DX_STAR_Y   = 66
DX_STAR_W   = 40
DX_STAR_H   = 40

# ST/DX 类型图标
TYPE_ICON_X = 88
TYPE_ICON_Y = 141
TYPE_ICON_W = 48
TYPE_ICON_H = 15

# 歌曲 ID
ID_X        = 4
ID_Y        = 141
ID_W        = 72
ID_H        = 15

# ── 完成度示意框（卡片内右下区域）──
COMP_X      = 143
COMP_Y      = 109
COMP_W      = 253
COMP_H      = 45

# 评级标（在完成度框内的坐标，相对于 COMP_X/Y）
GRADE_X     = 10
GRADE_Y     = 3
GRADE_W     = 100
GRADE_H     = 40

# FC 徽标（在完成度框内）
FC_ICON_X   = 135
FC_ICON_Y   = 3
FC_ICON_W   = 40
FC_ICON_H   = 40

# FS 徽标（在完成度框内）
FS_ICON_X   = 200
FS_ICON_Y   = 3
FS_ICON_W   = 40
FS_ICON_H   = 40

# ══════════════════════════════════════════════════════════════
# 配色
# ══════════════════════════════════════════════════════════════

SKY_BLUE    = (210, 228, 245)
HEADER_BG   = (188, 210, 232)
SECTION_BG  = (175, 200, 228)

TEXT        = (28,  32,  48)
TEXT_DIM    = (100, 105, 130)
TEXT_MUTED  = (155, 158, 175)
ACCENT      = (210, 140, 30)
WHITE       = (255, 255, 255)

B15_ACCENT  = (220, 80,  80)
B35_ACCENT  = (60,  130, 210)
TYPE_DX     = (230, 120, 50)
TYPE_SD     = (50,  140, 220)

# ══════════════════════════════════════════════════════════════
# 字体 — M PLUS Rounded 1c 圆角字族引擎
# ══════════════════════════════════════════════════════════════
# 主字体: M PLUS Rounded 1c (开源圆角日系黑体, 中日英全支持)
#   4 字重: Regular(r) / Medium(m) / Bold(b) / ExtraBold(xb)
# 数字字体: Impact (游戏风格压缩粗体, 仅大 Rating 用)
#
# 使用: _font(size, family="mplus", bold=False/True)
#       bold=False → Medium(适中圆滑) / bold=True → Bold(强调)

_FONT_DB = {
    "mplus": {
        "r":  "assets/fonts/MPLUSRounded1c-Regular.ttf",
        "m":  "assets/fonts/MPLUSRounded1c-Medium.ttf",
        "b":  "assets/fonts/MPLUSRounded1c-Bold.ttf",
        "xb": "assets/fonts/MPLUSRounded1c-ExtraBold.ttf",
    },
    "impact": {
        "r": "C:/Windows/Fonts/impact.ttf",
        "b": "C:/Windows/Fonts/impact.ttf",
    },
}

FONT_DIR = "C:/Windows/Fonts"

_font_cache: dict[tuple[str, int, str], ImageFont.FreeTypeFont] = {}

def _font(size: int, family: str = "mplus", bold: bool = False) -> ImageFont.FreeTypeFont:
    """加载字族 · family: 'mplus'|'impact' · bold→MplusBold/Impact"""
    info = _FONT_DB.get(family, _FONT_DB["mplus"])
    weight = "b" if bold else "m"
    wpaths = info.get(weight, info.get("m"))
    if isinstance(wpaths, str):
        wpaths = [wpaths]
    key = (family, size, weight)
    if key in _font_cache:
        return _font_cache[key]
    # 路径列表: 字族优先 → 系统 fallback
    paths = []
    for p in wpaths:
        paths.append(p)
    paths += [
        os.path.join(FONT_DIR, "segoeui.ttf"),
        os.path.join(FONT_DIR, "Deng.ttf"),
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for p in paths:
        try:
            if os.path.exists(p):
                f = ImageFont.truetype(p, size)
                _font_cache[key] = f
                return f
        except (OSError, IOError):
            continue
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _font_xb(size: int, family: str = "mplus") -> ImageFont.FreeTypeFont:
    """ExtraBold 字重 — 用于达成率等需要粗壮数字处"""
    info = _FONT_DB.get(family, _FONT_DB["mplus"])
    wpath = info.get("xb", info.get("b"))
    if isinstance(wpath, str):
        wpath = [wpath]
    key = (family, size, "xb")
    if key in _font_cache:
        return _font_cache[key]
    paths = list(wpath) + [
        os.path.join(FONT_DIR, "segoeuib.ttf"),
        os.path.join(FONT_DIR, "Dengb.ttf"),
    ]
    for p in paths:
        try:
            if os.path.exists(p):
                f = ImageFont.truetype(p, size)
                _font_cache[key] = f
                return f
        except (OSError, IOError):
            continue
    return _font(size, family=family, bold=True)


# ══════════════════════════════════════════════════════════════
# 素材加载
# ══════════════════════════════════════════════════════════════

ASSETS = os.path.join(os.path.dirname(__file__), "assets")

# 难度 → 卡片模板文件
DIFF_CARD: dict[int, str] = {
    0: "card_bas", 1: "card_adv", 2: "card_exp", 3: "card_mas", 4: "card_rem",
}

# FC/FS → 徽标文件（用户 Figma 素材）
BADGE_MAP: dict[str, str] = {
    "fc": "music_icon_fc", "fcp": "music_icon_fcp",
    "ap": "music_icon_ap", "app": "music_icon_app",
    "fs": "music_icon_fs", "fsp": "music_icon_fsp",
    "fdx": "music_icon_fdx", "fdxp": "music_icon_fdxp",
    "sync": "music_icon_sync", "clear": "music_icon_clear",
}

# rate → 评级标（用户 Figma 素材）
GRADE_MAP: dict[str, str] = {
    "sssp": "music_icon_sssp", "sss": "music_icon_sss",
    "ssp": "music_icon_ssp", "ss": "music_icon_ss",
    "sp": "music_icon_sp", "s": "music_icon_s",
    "aaa": "music_icon_aaa", "aa": "music_icon_aa",
    "a": "music_icon_a", "bbb": "music_icon_bbb",
    "bb": "music_icon_bb", "b": "music_icon_b",
    "c": "music_icon_c", "d": "music_icon_d",
}

# 歌曲版本图标
TYPE_ICON: dict[str, str] = {
    "dx": "music_dx", "sd": "music_standard",
}

# 难度索引 → 难度标签图片
DIFF_TAG: dict[int, str] = {
    0: "diff_basic", 1: "diff_advanced", 2: "diff_expert",
    3: "diff_master", 4: "diff_remaster",
}

# 数字素材: 字符 → 文件名
NUM_SPRITE: dict[str, str] = {
    "0": "UI_NUM_Drating_0", "1": "UI_NUM_Drating_1",
    "2": "UI_NUM_Drating_2", "3": "UI_NUM_Drating_3",
    "4": "UI_NUM_Drating_4", "5": "UI_NUM_Drating_5",
    "6": "UI_NUM_Drating_6", "7": "UI_NUM_Drating_7",
    "8": "UI_NUM_Drating_8", "9": "UI_NUM_Drating_9",
    "+": "UI_NUM_Drating_10", "-": "UI_NUM_Drating_11",
    ".": "UI_NUM_Drating_12", ",": "UI_NUM_Drating_13",
}

# 头部 Rating 数字高度
HEADER_NUM_H = 50


def _card_text_color(level_index: int) -> tuple[int, int, int]:
    """卡片文字色: ReM(同卡片背景#8D2BD5，因ReM有白底框) / 其余WHITE"""
    return (141, 43, 213) if level_index == 4 else WHITE


def _load_asset(name: str) -> Optional[Image.Image]:
    path = os.path.join(ASSETS, f"{name}.png")
    if os.path.exists(path):
        return Image.open(path).convert("RGBA")
    return None


def _load_assets() -> dict[str, Image.Image]:
    cache: dict[str, Image.Image] = {}
    all_names: set[str] = set()
    for m in (DIFF_CARD, BADGE_MAP, GRADE_MAP, DIFF_TAG, NUM_SPRITE, TYPE_ICON):
        for name in m.values():
            all_names.add(name)
    for name in all_names:
        img = _load_asset(name)
        if img:
            cache[name] = img
    return cache


# 全局素材缓存（模块加载时填充）
_ASSET_CACHE: Optional[dict[str, Image.Image]] = None


def _get_asset(name: str) -> Optional[Image.Image]:
    global _ASSET_CACHE
    if _ASSET_CACHE is None:
        _ASSET_CACHE = _load_assets()
    return _ASSET_CACHE.get(name)


def _scale_to_h(img: Image.Image, target_h: int) -> Image.Image:
    """等比缩放素材到目标高度"""
    if img.height == target_h:
        return img
    scale = target_h / img.height
    new_w = max(1, int(img.width * scale))
    return img.resize((new_w, target_h), Image.LANCZOS)


def _paste_sprite_number(
    canvas: Image.Image, x: int, y: int, text: str, sprite_h: int,
) -> int:
    """粘贴数字素材拼接字符串，返回结束 x 坐标（用于后续绘制 "%" 等文字）"""
    for ch in text:
        name = NUM_SPRITE.get(ch)
        if name:
            sprite = _get_asset(name)
            if sprite:
                sprite = _scale_to_h(sprite, sprite_h)
                canvas.paste(sprite, (x, y), sprite)
                x += sprite.width
    return x


# ══════════════════════════════════════════════════════════════
# 共享卡片绘制（B50Renderer 和 FilteredScoreRenderer 共用）
# ══════════════════════════════════════════════════════════════

def draw_score_card(
    img: Image.Image, draw: ImageDraw.ImageDraw,
    thumbs: dict[str, Image.Image],
    cx: int, cy: int, rec: ScoreRecord,
) -> None:
    """拼接一张成绩卡片（Figma 布局: 400×160）"""
    tc = _card_text_color(rec.level_index)

    # ── 1. 卡片模板（按难度背景色）──
    card_name = DIFF_CARD.get(rec.level_index, "card_bas")
    card_img = _get_asset(card_name)
    if card_img:
        img.paste(card_img, (cx, cy), card_img)

    # ── 2. 曲绘封面 ──
    thumb = thumbs.get(str(rec.song_id))
    if thumb:
        scaled = thumb.resize((COVER_SIZE, COVER_SIZE), Image.LANCZOS)
        img.paste(scaled, (cx + COVER_X, cy + COVER_Y), scaled)

    # ── 3. 曲名 ──
    title_font = _font(14)
    draw.text(
        (cx + TITLE_X, cy + TITLE_Y),
        _truncate(rec.title, title_font, TITLE_W),
        fill=tc, font=title_font, stroke_width=1,
    )

    # ── 4. DX/SD 版本图标 ──
    type_name = TYPE_ICON.get(rec.type)
    if type_name:
        type_icon = _get_asset(type_name)
        if type_icon:
            type_icon = _scale_to_h(type_icon, TYPE_ICON_H)
            img.paste(type_icon, (cx + TYPE_ICON_X, cy + TYPE_ICON_Y), type_icon)

    # ── 5. 达成率 ──
    ach_str = f"{rec.achievements:.4f}%"
    ach_font = _font_xb(22)
    ach_w = int(ach_font.getlength(ach_str))
    draw.text(
        (cx + ACH_X + (ACH_W - ach_w) // 2, cy + ACH_Y + 6),
        ach_str, fill=tc, font=ach_font, stroke_width=1,
    )

    # ── 6. 难度 (定数→RA) ──
    diff_text = f"{rec.ds:.1f}→{rec.ra:.0f}"
    diff_font = _font(16)
    diff_w = int(diff_font.getlength(diff_text))
    draw.text(
        (cx + DIFF_X + (DIFF_W - diff_w) // 2, cy + DIFF_Y + (DIFF_H - 22) // 2),
        diff_text, fill=tc, font=diff_font, stroke_width=1,
    )

    # ── 7. DX 分数（居中）──
    if rec.dx_score > 0:
        score_str = str(rec.dx_score)
        score_font = _font(14)
        score_w = int(score_font.getlength(score_str))
        draw.text(
            (cx + DX_SCORE_X + (DX_SCORE_W - score_w) // 2, cy + DX_SCORE_Y + 6),
            score_str, fill=tc, font=score_font, stroke_width=1,
        )

    # ── 8. 完成度示意框 ──
    bx = cx + COMP_X
    by = cy + COMP_Y

    # 评级标
    rank_name = GRADE_MAP.get(rec.rate)
    if rank_name:
        rank_img = _get_asset(rank_name)
        if rank_img:
            rank_img = _scale_to_h(rank_img, GRADE_H)
            img.paste(rank_img, (bx + GRADE_X, by + GRADE_Y), rank_img)

    # FC 徽标（空则用空圈框）
    fc_name = BADGE_MAP.get(rec.fc)
    if fc_name:
        fc_img = _get_asset(fc_name)
        if fc_img:
            fc_img = _scale_to_h(fc_img, FC_ICON_H)
            img.paste(fc_img, (bx + FC_ICON_X, by + FC_ICON_Y), fc_img)
    else:
        blank = _load_asset("UI_MSS_MBase_Icon_Blank")
        if blank:
            blank = _scale_to_h(blank, FC_ICON_H)
            img.paste(blank, (bx + FC_ICON_X, by + FC_ICON_Y), blank)

    # FS 徽标（空则用空圈框）
    fs_name = BADGE_MAP.get(rec.fs)
    if fs_name:
        fs_img = _get_asset(fs_name)
        if fs_img:
            fs_img = _scale_to_h(fs_img, FS_ICON_H)
            img.paste(fs_img, (bx + FS_ICON_X, by + FS_ICON_Y), fs_img)
    else:
        blank = _load_asset("UI_MSS_MBase_Icon_Blank")
        if blank:
            blank = _scale_to_h(blank, FS_ICON_H)
            img.paste(blank, (bx + FS_ICON_X, by + FS_ICON_Y), blank)

    # ── 9. 歌曲 ID（永远白色）──
    draw.text(
        (cx + ID_X, cy + ID_Y),
        f"ID {rec.song_id}", fill=WHITE, font=_font(13), stroke_width=1,
    )


# ══════════════════════════════════════════════════════════════
# B50 渲染器（素材拼接）
# ══════════════════════════════════════════════════════════════

class B50Renderer:
    """B50 成绩表 — 素材拼接 · 仅动态文字实时渲染"""

    def __init__(
        self,
        profile: PlayerProfile,
        covers: Optional[dict[str, bytes]] = None,
    ):
        self.profile = profile
        self.b50 = profile.get_best_n(50)
        self.b35 = [r for r in self.b50 if r.bucket == "sd"][:35]
        self.b15 = [r for r in self.b50 if r.bucket == "dx"][:15]
        self.dx_rating = int(sum(r.ra for r in self.b15))
        self.sd_rating = int(sum(r.ra for r in self.b35))

        # 预加载封面缩略图
        self._thumbs: dict[str, Image.Image] = {}
        if covers:
            for sid, data in covers.items():
                if data:
                    try:
                        img = Image.open(io.BytesIO(data)).convert("RGBA")
                        img = _fill_crop(img, COVER_SIZE, COVER_SIZE)
                        img = _round_mask(img, 6)
                        self._thumbs[sid] = img
                    except Exception:
                        pass
            # 收集所有涉及但缺封面的歌曲 id
            all_ids = {str(r.song_id) for r in self.b50}
            for sid in all_ids:
                if sid not in self._thumbs:
                    self._thumbs[sid] = _get_placeholder_cover()

        # 总高度 — 固定 2400 画幅
        self.height = 2400

        # 全幅背景图
        bg = _load_asset("orangestar")
        if bg:
            bg = _fill_crop(bg, WIDTH, self.height)
            self.img = bg.convert("RGB")
        else:
            self.img = Image.new("RGB", (WIDTH, self.height), SKY_BLUE)
        self.draw = ImageDraw.Draw(self.img)

    # ── 公开 API ──

    def render(self) -> Image.Image:
        y = self._draw_header(0)
        y = self._draw_section(y, "B35 \xb7 旧版本曲", B35_ACCENT)
        y = self._draw_grid(y, self.b35)
        y = self._draw_section(y, "B15 \xb7 新版本曲", B15_ACCENT)
        y = self._draw_grid(y, self.b15)
        self._draw_footer(y)
        return self.img

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.img.save(buf, format="PNG")
        return buf.getvalue()

    # ── 头部 ──

    def _draw_header(self, y: int) -> int:
        h = HEADER_H
        p = self.profile

        # 昵称
        self.draw.text(
            (MARGIN, y + 80), p.nickname,
            fill=TEXT, font=_font(48, bold=True), stroke_width=1,
        )

        # 总 Rating
        ra_text = str(p.rating)
        ra_x = WIDTH - MARGIN
        if _get_asset("UI_NUM_Drating_0"):
            ra_num_w = 0
            for ch in reversed(ra_text):
                name = NUM_SPRITE.get(ch)
                if name:
                    s = _get_asset(name)
                    if s:
                        s = _scale_to_h(s, HEADER_NUM_H)
                        ra_num_w += s.width
            ra_x -= ra_num_w
            _paste_sprite_number(self.img, ra_x, y + 80, ra_text, HEADER_NUM_H)
        else:
            ra_font = _font(50, bold=True, family="impact")
            ra_w = int(ra_font.getlength(ra_text))
            ra_x -= ra_w
            self.draw.text((ra_x, y + 80), ra_text, fill=ACCENT, font=ra_font)
        self.draw.text((ra_x - 80, y + 100), "RATING", fill=TEXT_DIM, font=_font(16), stroke_width=1)

        # B15 / B35 子 Rating
        detail_y = y + 180
        x = MARGIN
        for label, val, color in [
            ("B15", self.dx_rating, B15_ACCENT),
            ("B35", self.sd_rating, B35_ACCENT),
        ]:
            self.draw.text(
                (x, detail_y), label, fill=color, font=_font(22, bold=True), stroke_width=1,
            )
            x += 80
            val_str = str(val)
            self.draw.text(
                (x, detail_y + 4), val_str, fill=TEXT, font=_font(22), stroke_width=1,
            )
            x += int(_font(22).getlength(val_str)) + 40

        if p.plate:
            self.draw.text(
                (x, detail_y + 4), p.plate, fill=TEXT_DIM, font=_font(20), stroke_width=1,
            )
            x += int(_font(20).getlength(p.plate)) + 24

        self.draw.text(
            (x, detail_y + 4),
            f"\xb7 共 {len(self.b50)} 首", fill=TEXT_MUTED, font=_font(18), stroke_width=1,
        )
        return y + h

    # ── 分段 ──

    def _draw_section(self, y: int, label: str, accent: tuple) -> int:
        h = SECTION_H
        self.draw.line(
            [MARGIN, y + 8, MARGIN, y + h - 8], fill=accent, width=4,
        )
        self.draw.text(
            (MARGIN + 16, y + 6),
            label, fill=accent, font=_font(18, bold=True), stroke_width=1,
        )
        return y + h

    # ── 卡片网格 ──

    def _draw_grid(self, y: int, records: list[ScoreRecord]) -> int:
        rows = (len(records) + COLS - 1) // COLS
        for row in range(rows):
            row_y = y + row * (CARD_H + ROW_GAP)
            for col in range(COLS):
                idx = row * COLS + col
                if idx >= len(records):
                    break
                self._draw_card(
                    MARGIN + col * (CARD_W + CARD_GAP),
                    row_y,
                    records[idx],
                )
        return y + rows * CARD_H + (rows - 1) * ROW_GAP

    def _draw_card(self, cx: int, cy: int, rec: ScoreRecord) -> None:
        """拼接一张卡片（委托共享函数）"""
        draw_score_card(self.img, self.draw, self._thumbs, cx, cy, rec)

    # ── 页脚 ──

    def _draw_footer(self, y: int) -> None:
        self.draw.text(
            (MARGIN, y + 16),
            "kanobot  \xb7  数据来源 diving-fish  \xb7  "
            + datetime.now().strftime("%Y-%m-%d %H:%M"),
            fill=TEXT_MUTED, font=_font(14), stroke_width=1,
        )


# ══════════════════════════════════════════════════════════════
# 辅助工具
# ══════════════════════════════════════════════════════════════

_RATE_COLORS: dict[str, tuple[int, int, int]] = {
    "sssp": (230, 60,  130), "sss": (210, 160, 40), "ssp": (220, 120, 40),
    "ss":   (140, 140, 165), "sp":  (170, 120, 60), "s":   (180, 140, 70),
}


def _rate_color(rate: str) -> tuple[int, int, int]:
    return _RATE_COLORS.get(rate, (155, 155, 175))


def _fill_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比缩放填满目标区域，居中裁剪（不压扁）"""
    orig_w, orig_h = img.size
    scale = max(target_w / orig_w, target_h / orig_h)
    new_w = int(orig_w * scale)
    new_h = int(orig_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


COVER_PLACEHOLDER: Image.Image | None = None


def _get_placeholder_cover() -> Image.Image:
    """无封面时的占位图 — 深灰底 + 简单音符图标"""
    global COVER_PLACEHOLDER
    if COVER_PLACEHOLDER is not None:
        return COVER_PLACEHOLDER
    w, h = COVER_SIZE, COVER_SIZE
    img = Image.new("RGBA", (w, h), (30, 30, 35, 255))
    draw = ImageDraw.Draw(img)
    cx, cy = w // 2, h // 2
    r = 16
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(80, 80, 85), width=2)
    draw.line((cx + r - 2, cy - r // 2, cx + r - 2, cy + r // 2 + 8), fill=(80, 80, 85), width=2)
    draw.ellipse((cx + r - 8, cy + r // 2 + 4, cx + r + 4, cy + r // 2 + 16), fill=(80, 80, 85))
    COVER_PLACEHOLDER = _round_mask(img, 6)
    return COVER_PLACEHOLDER


def _round_mask(img: Image.Image, radius: int) -> Image.Image:
    """给 RGBA 图片加圆角遮罩"""
    size = img.size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    out = Image.new("RGBA", size)
    out.paste(img, mask=mask)
    return out


def _truncate(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> str:
    """截断文本到指定宽度，超出加 …"""
    if font.getlength(text) <= max_w:
        return text
    lo, hi = 1, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.getlength(text[:mid] + "…") <= max_w:
            lo = mid
        else:
            hi = mid - 1
    if lo <= 1:
        return text[:1] + "…"
    return text[:lo] + "…"


# ══════════════════════════════════════════════════════════════
# 筛选成绩 / 分数列表 渲染器
# ══════════════════════════════════════════════════════════════

SCORE_LIST_ROWS = 10   # 分数列表模式: 5×10 满格
SCORE_HEADER_H  = 300  # 分数列表头部（含筛选条件摘要）

class FilteredScoreRenderer:
    """筛选成绩图 / 分数列表 — 支持 B50 模式(分b15/b35) 和 Score 模式(5×10统一网格)"""

    LAYOUT_B50   = "b50"
    LAYOUT_SCORE = "score"

    def __init__(
        self,
        records: list[ScoreRecord],
        nickname: str,
        covers: dict[str, bytes] | None = None,
        *,
        layout: str = LAYOUT_B50,
        title: str = "",
        page: int = 1,
        total_pages: int = 1,
        sort_by: str = "ra",
        b35: list[ScoreRecord] | None = None,
        b15: list[ScoreRecord] | None = None,
    ):
        self.records = records
        self.nickname = nickname
        self.layout = layout
        self.title = title
        self.page = page
        self.total_pages = total_pages
        self.sort_by = sort_by

        # 分割 b15/b35 (仅 B50 模式)
        if layout == self.LAYOUT_B50:
            if b35 is not None and b15 is not None:
                self.b35 = b35
                self.b15 = b15
            else:
                # 回退：按 bucket 分（公开 B50 接口已设 bucket）
                self.b35 = [r for r in records if r.bucket == "sd"][:35]
                self.b15 = [r for r in records if r.bucket == "dx"][:15]
            self.dx_rating = int(sum(r.ra for r in self.b15))
            self.sd_rating = int(sum(r.ra for r in self.b35))
            self.total_rating = self.dx_rating + self.sd_rating
        else:
            self.b35 = []
            self.b15 = []
            self.dx_rating = 0
            self.sd_rating = 0
            self.total_rating = 0

        # 预加载封面
        self._thumbs: dict[str, Image.Image] = {}
        if covers:
            for sid, data in covers.items():
                if data:
                    try:
                        img = Image.open(io.BytesIO(data)).convert("RGBA")
                        img = _fill_crop(img, COVER_SIZE, COVER_SIZE)
                        img = _round_mask(img, 6)
                        self._thumbs[sid] = img
                    except Exception:
                        pass
            all_ids = {str(r.song_id) for r in self.records}
            for sid in all_ids:
                if sid not in self._thumbs:
                    self._thumbs[sid] = _get_placeholder_cover()

        # 计算画幅高度
        if layout == self.LAYOUT_B50:
            rows_b35 = (len(self.b35) + COLS - 1) // COLS
            rows_b15 = (len(self.b15) + COLS - 1) // COLS
            self.height = (
                HEADER_H + 2 * SECTION_H + FOOTER_H
                + rows_b35 * (CARD_H + ROW_GAP)
                + rows_b15 * (CARD_H + ROW_GAP)
            )
            # 限制最小高度
            self.height = max(self.height, 2400)
        else:
            rows = (min(len(records), 50) + COLS - 1) // COLS
            self.height = (
                SCORE_HEADER_H + FOOTER_H
                + rows * (CARD_H + ROW_GAP)
            )
            self.height = max(self.height, 1200)

        # 全幅背景
        bg = _load_asset("orangestar")
        if bg:
            bg = _fill_crop(bg, WIDTH, self.height)
            self.img = bg.convert("RGB")
        else:
            self.img = Image.new("RGB", (WIDTH, self.height), SKY_BLUE)
        self.draw = ImageDraw.Draw(self.img)

    # ── 公开 API ──

    def render(self) -> Image.Image:
        if self.layout == self.LAYOUT_B50:
            return self._render_b50()
        else:
            return self._render_score_list()

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.img.save(buf, format="PNG")
        return buf.getvalue()

    def _render_b50(self) -> Image.Image:
        y = self._draw_filter_header(0)
        y = self._draw_section(y, "B35 \xb7 旧版本曲", B35_ACCENT)
        y = self._draw_grid(y, self.b35)
        y = self._draw_section(y, "B15 \xb7 新版本曲", B15_ACCENT)
        y = self._draw_grid(y, self.b15)
        self._draw_footer(y)
        return self.img

    def _render_score_list(self) -> Image.Image:
        y = self._draw_filter_header(0)
        page_records = self.records[:50]
        y = self._draw_grid(y, page_records)
        # 页码提示
        if self.total_pages > 1:
            self.draw.text(
                (MARGIN, y + 6),
                f"(第 {self.page}/{self.total_pages} 张) 在原命令后加数字翻页",
                fill=TEXT_DIM, font=_font(16), stroke_width=1,
            )
            y += 24
        self._draw_footer(y)
        return self.img

    # ── 头部（筛选摘要）──

    def _draw_filter_header(self, y: int) -> int:
        h = HEADER_H if self.layout == self.LAYOUT_B50 else SCORE_HEADER_H
        if self.layout == self.LAYOUT_SCORE:
            h = SCORE_HEADER_H

        # 昵称
        self.draw.text(
            (MARGIN, y + 40), self.nickname,
            fill=TEXT, font=_font(40, bold=True), stroke_width=1,
        )

        # 筛选条件
        if self.title:
            self.draw.text(
                (MARGIN, y + 92), self.title,
                fill=TEXT_DIM, font=_font(18), stroke_width=1,
            )

        # 统计信息
        if self.layout == self.LAYOUT_B50:
            # 总 Rating
            ra_text = str(self.total_rating)
            ra_x = WIDTH - MARGIN
            if _get_asset("UI_NUM_Drating_0"):
                ra_num_w = 0
                for ch in reversed(ra_text):
                    name = NUM_SPRITE.get(ch)
                    if name:
                        s = _get_asset(name)
                        if s:
                            s = _scale_to_h(s, HEADER_NUM_H)
                            ra_num_w += s.width
                ra_x -= ra_num_w
                _paste_sprite_number(self.img, ra_x, y + 40, ra_text, HEADER_NUM_H)
            else:
                ra_font = _font(50, bold=True, family="impact")
                ra_w = int(ra_font.getlength(ra_text))
                ra_x -= ra_w
                self.draw.text((ra_x, y + 40), ra_text, fill=ACCENT, font=ra_font)
            self.draw.text((ra_x - 80, y + 60), "RATING", fill=TEXT_DIM, font=_font(16), stroke_width=1)

            # B15 / B35 子 Rating
            detail_y = y + 140
            x = MARGIN
            for label, val, color in [
                ("B15", self.dx_rating, B15_ACCENT),
                ("B35", self.sd_rating, B35_ACCENT),
            ]:
                self.draw.text((x, detail_y), label, fill=color, font=_font(22, bold=True), stroke_width=1)
                x += 80
                self.draw.text((x, detail_y + 4), str(val), fill=TEXT, font=_font(22), stroke_width=1)
                x += int(_font(22).getlength(str(val))) + 40

            self.draw.text(
                (x, detail_y + 4),
                f"\xb7 共 {len(self.records)} 首", fill=TEXT_MUTED, font=_font(18), stroke_width=1,
            )
        else:
            # Score list 模式的统计信息
            detail_y = y + 125
            sort_label = "按达成率排序" if self.sort_by == "achievements" else "按RA排序"
            self.draw.text(
                (MARGIN, detail_y),
                f"共 {len(self.records)} 首  |  {sort_label}",
                fill=TEXT, font=_font(18), stroke_width=1,
            )
            if self.total_pages > 1:
                self.draw.text(
                    (MARGIN, detail_y + 28),
                    f"第 {self.page}/{self.total_pages} 页（共 {len(self.records)} 首）",
                    fill=TEXT_DIM, font=_font(16), stroke_width=1,
                )

        return y + h

    # ── 分段标题 ──

    def _draw_section(self, y: int, label: str, accent: tuple) -> int:
        h = SECTION_H
        self.draw.line(
            [MARGIN, y + 8, MARGIN, y + h - 8], fill=accent, width=4,
        )
        self.draw.text(
            (MARGIN + 16, y + 6),
            label, fill=accent, font=_font(18, bold=True), stroke_width=1,
        )
        return y + h

    # ── 卡片网格 ──

    def _draw_grid(self, y: int, records: list[ScoreRecord]) -> int:
        rows = (len(records) + COLS - 1) // COLS
        for row in range(rows):
            row_y = y + row * (CARD_H + ROW_GAP)
            for col in range(COLS):
                idx = row * COLS + col
                if idx >= len(records):
                    break
                draw_score_card(
                    self.img, self.draw, self._thumbs,
                    MARGIN + col * (CARD_W + CARD_GAP),
                    row_y,
                    records[idx],
                )
        return y + rows * CARD_H + (rows - 1) * ROW_GAP if rows > 0 else y

    # ── 页脚 ──

    def _draw_footer(self, y: int) -> None:
        if self.total_pages > 1 and self.layout == self.LAYOUT_SCORE:
            hint = f"(第 {self.page}/{self.total_pages} 张) 在原命令后加数字翻页"
        else:
            hint = ""
        self.draw.text(
            (MARGIN, y + 16),
            "kanobot  \xb7  数据来源 diving-fish  \xb7  "
            + datetime.now().strftime("%Y-%m-%d %H:%M"),
            fill=TEXT_MUTED, font=_font(14), stroke_width=1,
        )


# ══════════════════════════════════════════════════════════════
# 导入结果对比渲染器
# ══════════════════════════════════════════════════════════════

from models import ImportDiff, RATE_LABEL, FC_LABEL, FS_LABEL  # noqa: E402

GREEN    = (100, 220, 120)
RED      = (255, 90, 90)
GOLD     = (255, 200, 60)
NEW_BG   = (220, 245, 220)
UPD_BG   = (255, 245, 220)

IR_WIDTH  = 820
IR_MARGIN = 20
IR_ROW_H  = 36


class ImportDiffRenderer:
    """导入成绩变动对比图（天蓝主题）"""

    def __init__(self, diff: ImportDiff):
        self.diff = diff
        changed = diff.changed_records
        header_h  = 66
        rating_h  = 40
        section_h = 30
        col_h     = 26
        rows_h    = len(changed) * IR_ROW_H
        footer_h  = 22
        self.height = header_h + rating_h + section_h + col_h + rows_h + footer_h + 12
        self.img = Image.new("RGB", (IR_WIDTH, self.height), SKY_BLUE)
        self.draw = ImageDraw.Draw(self.img)

    def render(self) -> Image.Image:
        y = 0
        y = self._draw_header(y)
        y = self._draw_rating_changes(y)
        y = self._draw_section_title(y)
        y = self._draw_column_headers(y)
        y = self._draw_records(y)
        self._draw_footer(y)
        return self.img

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.img.save(buf, format="PNG")
        return buf.getvalue()

    def _draw_header(self, y: int) -> int:
        self.draw.rectangle([0, y, IR_WIDTH, y + 66], fill=HEADER_BG)
        d = self.diff
        self.draw.text(
            (IR_MARGIN, y + 10), "成绩导入报告",
            fill=TEXT, font=_font(22, bold=True),
        )
        info = (
            f"{d.nickname}  |  导入 {len(d.records)} 首"
            f"  |  变动 {len(d.changed_records)} 首"
        )
        self.draw.text((IR_MARGIN, y + 42), info, fill=TEXT_DIM, font=_font(13))
        return y + 66

    def _draw_rating_changes(self, y: int) -> int:
        self.draw.rectangle([0, y, IR_WIDTH, y + 40], fill=SECTION_BG)
        d = self.diff

        def _d(v: int) -> str:
            return f"+{v}" if v > 0 else str(v) if v < 0 else "0"

        items = [
            (f"B15: {d.new_dx_rating}", d.dx_rating_delta, B15_ACCENT),
            (f"B35: {d.new_sd_rating}", d.sd_rating_delta, B35_ACCENT),
            (f"总RT: {d.new_rating}", d.rating_delta, ACCENT),
        ]
        x = IR_MARGIN
        for label, delta, color in items:
            self.draw.text((x, y + 10), label, fill=color, font=_font(15, bold=True))
            x += 120
            dc = GREEN if delta > 0 else (RED if delta < 0 else TEXT_DIM)
            self.draw.text((x, y + 12), _d(delta), fill=dc, font=_font(13, bold=True))
            x += 100
        return y + 40

    def _draw_section_title(self, y: int) -> int:
        self.draw.rectangle([0, y, IR_WIDTH, y + 30], fill=SECTION_BG)
        n = len(self.diff.changed_records)
        n_new = len(self.diff.new_songs)
        n_upd = len(self.diff.updated_songs)
        self.draw.text(
            (IR_MARGIN, y + 6),
            f"变动明细（新曲 {n_new} / 更新 {n_upd} / 共 {n} 首）",
            fill=ACCENT, font=_font(13, bold=True),
        )
        return y + 30

    def _draw_column_headers(self, y: int) -> int:
        self.draw.rectangle([0, y, IR_WIDTH, y + 26], fill=SKY_BLUE)
        font = _font(11, bold=True)
        for text, cx in [
            ("曲名", IR_MARGIN),
            ("难度", IR_MARGIN + 230),
            ("旧达成率", IR_MARGIN + 285),
            ("新达成率", IR_MARGIN + 410),
            ("FC/FS", IR_MARGIN + 545),
            ("RA变动", IR_MARGIN + 660),
        ]:
            self.draw.text((cx, y + 5), text, fill=TEXT_DIM, font=font)
        return y + 26

    def _draw_records(self, y: int) -> int:
        records = self.diff.changed_records
        rc = (IR_ROW_H - 22) // 2
        for i, rec in enumerate(records):
            ry = y + i * IR_ROW_H
            bg_c = NEW_BG if rec.is_new else UPD_BG
            self.draw.rectangle([0, ry, IR_WIDTH, ry + IR_ROW_H], fill=bg_c)
            bf = _font(12)

            tc = TYPE_DX if rec.type == "dx" else TYPE_SD
            tt = "DX" if rec.type == "dx" else "SD"
            self.draw.text((IR_MARGIN, ry + rc + 3), tt, fill=tc, font=_font(10, bold=True))

            title = rec.title
            nf = _font(12)
            mw = 170
            while nf.getlength(title) > mw and len(title) > 3:
                title = title[:-1]
            if title != rec.title:
                title = title[:-2] + "…"
            self.draw.text((IR_MARGIN + 28, ry + rc + 2), title, fill=TEXT, font=nf)

            self.draw.text(
                (IR_MARGIN + 230, ry + rc + 3),
                f"{rec.level} ({rec.ds:.1f})", fill=TEXT_DIM, font=bf,
            )

            if rec.is_new:
                self.draw.text(
                    (IR_MARGIN + 285, ry + rc + 3), "NEW",
                    fill=GOLD, font=_font(12, bold=True),
                )
            else:
                self.draw.text(
                    (IR_MARGIN + 285, ry + rc + 3),
                    f"{rec.old_achievements:.4f}%", fill=TEXT_DIM, font=bf,
                )

            ac = GREEN if rec.achievements_delta > 0 else TEXT
            self.draw.text(
                (IR_MARGIN + 410, ry + rc + 3),
                f"{rec.new_achievements:.4f}%", fill=ac, font=bf,
            )

            if not rec.is_new and rec.achievement_changed:
                ds = "+" if rec.achievements_delta > 0 else ""
                self.draw.text(
                    (IR_MARGIN + 490, ry + rc + 2),
                    f"{ds}{rec.achievements_delta:.4f}", fill=ac, font=_font(10),
                )

            fc_parts = []
            if rec.fc_changed:
                fc_parts.append(f"FC: {FC_LABEL.get(rec.old_fc, rec.old_fc)}→{FC_LABEL.get(rec.new_fc, rec.new_fc)}")
            if rec.fs_changed:
                fc_parts.append(f"FS: {FS_LABEL.get(rec.old_fs, rec.old_fs)}→{FS_LABEL.get(rec.new_fs, rec.new_fs)}")
            if fc_parts:
                self.draw.text(
                    (IR_MARGIN + 545, ry + rc + 2),
                    "  ".join(fc_parts), fill=GOLD, font=_font(11, bold=True),
                )

            rd = rec.ra_delta
            rc2 = GREEN if rd > 0 else (RED if rd < 0 else TEXT_DIM)
            rt2 = f"+{rd:.0f}" if rd > 0 else str(int(rd)) if rd < 0 else "0"
            self.draw.text(
                (IR_MARGIN + 660, ry + rc + 3),
                rt2, fill=rc2, font=_font(13, bold=True),
            )
            if rec.is_new:
                self.draw.text(
                    (IR_MARGIN + 720, ry + rc + 3),
                    "NEW!", fill=GOLD, font=_font(11, bold=True),
                )
        return y + len(records) * IR_ROW_H

    def _draw_footer(self, y: int) -> None:
        self.draw.text(
            (IR_MARGIN, y + 4),
            "kanobot \xb7 diving-fish  \xb7  "
            + datetime.now().strftime("%Y-%m-%d %H:%M"),
            fill=TEXT_MUTED, font=_font(10),
        )
