"""
kanobot 数据模型
基于 diving-fish 舞萌 DX API 返回结构
"""

from dataclasses import dataclass, field
from typing import Optional, Any

# ══════════════════════════════════════════════════════════════
# 歌曲 & 谱面
# ══════════════════════════════════════════════════════════════

@dataclass
class ChartInfo:
    """单张谱面信息（来自 music_data）"""
    cid: int = 0
    level_index: int = 0          # 0-4 (Basic → Re:Master)
    level_label: str = ""         # Basic / Advanced / Expert / Master / Re:Master
    level: str = ""               # 显示难度 "13+"
    ds: float = 0.0               # 定数
    notes: list = field(default_factory=list)    # 谱师列表
    charter: str = ""             # 谱师名


@dataclass
class SongInfo:
    """歌曲信息（来自 music_data）"""
    song_id: str = ""              # 歌曲 ID
    title: str = ""                # 曲名
    type: str = ""                 # dx / sd
    artist: str = ""               # 艺术家
    genre: str = ""                # 分类 (POPS&ANIME / ゲーム / etc.)
    bpm: int = 0                   # BPM
    version: str = ""              # 收录版本
    release_date: str = ""         # 发布日期
    is_new: bool = False           # 是否新曲

    # 五张谱面 (idx 0-4: basic advanced expert master remaster)
    charts: dict[int, ChartInfo] = field(default_factory=dict)
    ds_list: list[float] = field(default_factory=list)   # [1.0, 6.0, 8.4, 11.7, 13.2]
    level_list: list[str] = field(default_factory=list)  # ["1", "6", "8+", "11+", "13+"]

    @classmethod
    def from_api(cls, entry: dict) -> "SongInfo":
        """从 API 返回的歌曲条目构造"""
        basic = entry.get("basic_info", {})
        song = cls(
            song_id=str(entry.get("id", "")),
            title=entry.get("title", ""),
            type=entry.get("type", "").lower(),
            artist=basic.get("artist", ""),
            genre=basic.get("genre", ""),
            bpm=basic.get("bpm", 0),
            version=basic.get("from", ""),
            release_date=basic.get("release_date", ""),
            is_new=basic.get("is_new", False),
            ds_list=entry.get("ds", []),
            level_list=entry.get("level", []),
        )

        level_labels = ["Basic", "Advanced", "Expert", "Master", "Re:Master"]
        charts_data = entry.get("charts", [])
        cids = entry.get("cids", [])

        for i in range(5):
            chart = ChartInfo(
                cid=cids[i] if i < len(cids) else 0,
                level_index=i,
                level_label=level_labels[i] if i < len(level_labels) else str(i),
                level=song.level_list[i] if i < len(song.level_list) else "?",
                ds=song.ds_list[i] if i < len(song.ds_list) else 0.0,
                notes=charts_data[i].get("notes", []) if i < len(charts_data) else [],
                charter=charts_data[i].get("charter", "") if i < len(charts_data) else "",
            )
            song.charts[i] = chart

        return song


# ══════════════════════════════════════════════════════════════
# 成绩
# ══════════════════════════════════════════════════════════════

# 评级映射
RATE_RANK = {
    "sssp": 0, "sss": 1, "ssp": 2, "ss": 3,
    "sp": 4, "s": 5, "aaa": 6, "aa": 7, "a": 8,
    "bbb": 9, "bb": 10, "b": 11, "c": 12, "d": 13,
}
RATE_LABEL = {
    "sssp": "SSS+", "sss": "SSS", "ssp": "SS+", "ss": "SS",
    "sp": "S+", "s": "S", "aaa": "AAA", "aa": "AA", "a": "A",
    "bbb": "BBB", "bb": "BB", "b": "B", "c": "C", "d": "D",
}
FC_LABEL = {
    "app": "AP+", "ap": "AP", "fcp": "FC+", "fc": "FC",
    "none": "",
}
FS_LABEL = {
    "fsdp": "FSD+", "fsd": "FSD", "fsp": "FS+", "fs": "FS",
    "none": "",
}


@dataclass
class ScoreRecord:
    """单曲成绩"""
    title: str = ""                # 歌曲名
    song_id: int = 0               # 歌曲 ID
    type: str = ""                 # dx / sd

    level: str = ""                # 显示难度 "13+"
    level_index: int = 0           # 难度索引 (0-4)
    level_label: str = ""          # Basic / Advanced / Expert / Master / Re:Master
    ds: float = 0.0                # 定数

    achievements: float = 0.0      # 达成率 (0-101)
    rate: str = ""                 # 评级 (sssp / sss / ss ...)
    dx_score: int = 0              # DX 分数
    fc: str = ""                   # FC 状态 (none/fc/fcp/ap/app)
    fs: str = ""                   # FS 状态 (none/fs/fsp/fsd/fsdp)

    ra: float = 0.0                # 单曲 Rating
    bucket: str = ""               # 来自 API 哪个分桶: "dx"(新版本/B15) 或 "sd"(旧版本/B35)

    # 可选：关联的歌曲详情
    song_info: Optional[SongInfo] = None

    def get_rate_label(self) -> str:
        return RATE_LABEL.get(self.rate, self.rate.upper())

    def get_fc_label(self) -> str:
        return FC_LABEL.get(self.fc, self.fc)

    def get_fs_label(self) -> str:
        return FS_LABEL.get(self.fs, self.fs)

    def is_ap(self) -> bool:
        return self.fc in ("ap", "app")

    def is_fc(self) -> bool:
        return self.fc in ("fc", "fcp", "ap", "app")

    def is_full_combo_plus(self) -> bool:
        return self.fc in ("fcp", "app")

    def is_sync(self) -> bool:
        return self.fs in ("fs", "fsp", "fsd", "fsdp")

    @classmethod
    def from_api(cls, entry: dict) -> "ScoreRecord":
        return cls(
            title=entry.get("title", ""),
            song_id=entry.get("song_id", 0),
            type=entry.get("type", "").lower(),
            level=entry.get("level", ""),
            level_index=entry.get("level_index", 0),
            level_label=entry.get("level_label", ""),
            ds=entry.get("ds", 0.0),
            achievements=entry.get("achievements", 0.0),
            rate=entry.get("rate", ""),
            dx_score=entry.get("dxScore", 0),
            fc=entry.get("fc", ""),
            fs=entry.get("fs", ""),
            ra=entry.get("ra", 0.0),
        )


# ══════════════════════════════════════════════════════════════
# 玩家
# ══════════════════════════════════════════════════════════════

@dataclass
class PlayerProfile:
    """玩家档案"""
    username: str = ""             # 账户名
    nickname: str = ""             # 游戏内昵称

    rating: int = 0                # B50 总 Rating
    dx_rating: int = 0             # B15 DX Rating
    sd_rating: int = 0             # B35 SD Rating
    additional_rating: int = 0     # 段位 ID (0-22)
    plate: str = ""                # 当前佩戴牌子

    records: list[ScoreRecord] = field(default_factory=list)

    # 衍生统计
    record_count: int = 0
    b50_count: int = 0
    b15_count: int = 0
    b35_count: int = 0

    @classmethod
    def from_api(cls, data: dict) -> "PlayerProfile":
        profile = cls(
            username=data.get("username", ""),
            nickname=data.get("nickname", ""),
            rating=data.get("rating", 0),
            dx_rating=data.get("dx_rating", 0) or 0,
            sd_rating=data.get("sd_rating", 0) or 0,
            additional_rating=data.get("additional_rating", 0) or 0,
            plate=data.get("plate", ""),
        )

        # 解析成绩记录，保留 API 分桶信息 (dx=新版本B15, sd=旧版本B35)
        raw_data = data.get("charts") or data.get("records") or {}
        raw_list: list = []
        if isinstance(raw_data, dict):
            for bucket_key in ("dx", "sd"):
                entries = raw_data.get(bucket_key, [])
                if isinstance(entries, list):
                    for item in entries:
                        if isinstance(item, dict):
                            rec = ScoreRecord.from_api(item)
                            rec.bucket = bucket_key  # 标记分桶
                            raw_list.append(rec)
        elif isinstance(raw_data, list):
            raw_list = [ScoreRecord.from_api(item) for item in raw_data if isinstance(item, dict)]

        profile.records = raw_list
        profile.record_count = len(profile.records)

        return profile

    # ── 分类查询 ──

    def get_best_n(self, n: int = 50) -> list[ScoreRecord]:
        """获取 RA 最高的 N 首"""
        return sorted(
            self.records, key=lambda r: r.ra, reverse=True
        )[:n]

    def filter_by_type(self, song_type: str) -> list[ScoreRecord]:
        """按类型筛选: 'dx' 或 'sd'"""
        return [r for r in self.records if r.type == song_type]

    def filter_by_level(self, level_index: int) -> list[ScoreRecord]:
        """按难度索引筛选 (0-4)"""
        return [r for r in self.records if r.level_index == level_index]

    def filter_by_rate(self, min_rate: str) -> list[ScoreRecord]:
        """按最低评级筛选"""
        min_rank = RATE_RANK.get(min_rate, 99)
        return [r for r in self.records if RATE_RANK.get(r.rate, 99) <= min_rank]

    def filter_by_song_id(self, song_id: int) -> list[ScoreRecord]:
        """按歌曲 ID 查询所有难度的成绩"""
        return [r for r in self.records if r.song_id == song_id]

    def statistics(self) -> dict:
        """返回玩家成绩统计数据"""
        records = self.records
        if not records:
            return {}

        rates = {}
        for r in records:
            key = r.rate or "none"
            rates[key] = rates.get(key, 0) + 1

        fcs = {}
        for r in records:
            key = r.fc or "none"
            fcs[key] = fcs.get(key, 0) + 1

        return {
            "total": len(records),
            "dx_count": len([r for r in records if r.type == "dx"]),
            "sd_count": len([r for r in records if r.type == "sd"]),
            "max_ra": max((r.ra for r in records), default=0),
            "avg_ra": sum(r.ra for r in records) / len(records) if records else 0,
            "ap_count": fcs.get("ap", 0) + fcs.get("app", 0),
            "fc_count": fcs.get("fc", 0) + fcs.get("fcp", 0),
            "sssp_count": rates.get("sssp", 0),
            "sss_count": rates.get("sss", 0),
            "by_level": {
                lv: len([r for r in records if r.level_index == lv])
                for lv in range(5)
            },
        }


# ══════════════════════════════════════════════════════════════
# 导入结果对比
# ══════════════════════════════════════════════════════════════

@dataclass
class RecordDiff:
    """单曲变动"""
    song_id: int = 0
    title: str = ""
    type: str = ""                # dx / sd
    level_index: int = 0
    level_label: str = ""
    level: str = ""
    ds: float = 0.0

    # 旧值
    old_achievements: float = 0.0
    old_rate: str = ""
    old_fc: str = ""
    old_fs: str = ""
    old_ra: float = 0.0
    # 新值
    new_achievements: float = 0.0
    new_rate: str = ""
    new_fc: str = ""
    new_fs: str = ""
    new_ra: float = 0.0

    is_new: bool = False          # 新曲（之前未打过）

    @property
    def achievements_delta(self) -> float:
        return self.new_achievements - self.old_achievements

    @property
    def ra_delta(self) -> float:
        return self.new_ra - self.old_ra

    @property
    def fc_changed(self) -> bool:
        return self.old_fc != self.new_fc

    @property
    def fs_changed(self) -> bool:
        return self.old_fs != self.new_fs

    @property
    def achievement_changed(self) -> bool:
        return abs(self.achievements_delta) > 0.0001

    @property
    def has_meaningful_change(self) -> bool:
        """是否有实质变动"""
        return (
            self.is_new
            or self.achievement_changed
            or self.fc_changed
            or self.fs_changed
        )


@dataclass
class ImportDiff:
    """导入结果对比汇总"""
    records: list[RecordDiff] = field(default_factory=list)
    old_rating: int = 0
    new_rating: int = 0
    old_dx_rating: int = 0
    new_dx_rating: int = 0
    old_sd_rating: int = 0
    new_sd_rating: int = 0
    nickname: str = ""

    @property
    def rating_delta(self) -> int:
        return self.new_rating - self.old_rating

    @property
    def dx_rating_delta(self) -> int:
        return self.new_dx_rating - self.old_dx_rating

    @property
    def sd_rating_delta(self) -> int:
        return self.new_sd_rating - self.old_sd_rating

    @property
    def changed_records(self) -> list[RecordDiff]:
        """只返回有实质变动的记录"""
        return [r for r in self.records if r.has_meaningful_change]

    @property
    def new_songs(self) -> list[RecordDiff]:
        return [r for r in self.records if r.is_new]

    @property
    def updated_songs(self) -> list[RecordDiff]:
        return [r for r in self.records if not r.is_new and r.has_meaningful_change]


def compare_import(before: PlayerProfile, after: PlayerProfile) -> ImportDiff:
    """对比导入前后的玩家数据，生成变动汇总"""
    # 建立旧数据索引: (song_id, level_index) → ScoreRecord
    old_index: dict[tuple[int, int], ScoreRecord] = {}
    for r in before.records:
        key = (r.song_id, r.level_index)
        # 保留 RA 更高的记录（同一谱面可能有多条）
        if key not in old_index or r.ra > old_index[key].ra:
            old_index[key] = r

    diffs: list[RecordDiff] = []

    for new_rec in after.records:
        key = (new_rec.song_id, new_rec.level_index)
        old_rec = old_index.get(key)

        diff = RecordDiff(
            song_id=new_rec.song_id,
            title=new_rec.title,
            type=new_rec.type,
            level_index=new_rec.level_index,
            level_label=new_rec.level_label,
            level=new_rec.level,
            ds=new_rec.ds,
            new_achievements=new_rec.achievements,
            new_rate=new_rec.rate,
            new_fc=new_rec.fc,
            new_fs=new_rec.fs,
            new_ra=new_rec.ra,
        )

        if old_rec is None:
            diff.is_new = True
        else:
            diff.old_achievements = old_rec.achievements
            diff.old_rate = old_rec.rate
            diff.old_fc = old_rec.fc
            diff.old_fs = old_rec.fs
            diff.old_ra = old_rec.ra

        diffs.append(diff)

    # 按 RA 增量排序
    diffs.sort(key=lambda d: d.ra_delta, reverse=True)

    return ImportDiff(
        records=diffs,
        old_rating=before.rating,
        new_rating=after.rating,
        old_dx_rating=before.dx_rating,
        new_dx_rating=after.dx_rating,
        old_sd_rating=before.sd_rating,
        new_sd_rating=after.sd_rating,
        nickname=after.nickname,
    )


# ══════════════════════════════════════════════════════════════
# 版本代号映射（3.1.8 按版本获取成绩）
# ══════════════════════════════════════════════════════════════

VERSION_CODES: dict[str, str] = {
    "真": "maimai PLUS",
    "超": "maimai GreeN",
    "檄": "maimai GreeN PLUS",
    "橙": "maimai ORANGE",
    "暁": "maimai ORANGE PLUS",
    "桃": "maimai PiNK",
    "櫻": "maimai PiNK PLUS",
    "紫": "maimai MURASAKi",
    "菫": "maimai MURASAKi PLUS",
    "白": "maimai MiLK",
    "雪": "MiLK PLUS",
    "輝": "maimai FiNALE",
    "舞": "ALL FiNALE",
    "熊": "maimai でらっくす",
    "華": "maimai でらっくす PLUS",
    "爽": "maimai でらっくす Splash",
    "煌": "maimai でらっくす Splash PLUS",
    "宙": "maimai でらっくす UNiVERSE",
    "星": "maimai でらっくす UNiVERSE PLUS",
    "祭": "maimai でらっくす FESTiVAL",
    "祝": "maimai でらっくす FESTiVAL PLUS",
    "BUDDiES": "maimai でらっくす BUDDiES",
    "PRiSM": "maimai でらっくす PRiSM",
}

# 反向映射（版本名 → 代号）
VERSION_NAME_TO_CODE: dict[str, str] = {
    v: k for k, v in VERSION_CODES.items()
}

# ══════════════════════════════════════════════════════════════
# 分类别名（中文 → API 分类名）
# ══════════════════════════════════════════════════════════════

GENRE_ALIASES: dict[str, str] = {
    # 舞萌 / maimai
    "舞萌": "舞萌", "maimai": "舞萌",
    # Vocaloid / V家 → niconico & VOCALOID
    "vocaloid": "niconico & VOCALOID", "v家": "niconico & VOCALOID",
    "术力口": "niconico & VOCALOID", "ボカロ": "niconico & VOCALOID",
    # niconico
    "niconico": "niconico & VOCALOID",
    # 东方
    "东方": "东方Project", "车万": "东方Project", "东方project": "东方Project",
    # 流行 / 动漫
    "流行": "流行&动漫", "动漫": "流行&动漫", "二次元": "流行&动漫",
    "pops": "流行&动漫", "pop": "流行&动漫", "anime": "流行&动漫",
    # 音击 / 中二
    "音击": "音击&中二节奏", "中二": "音击&中二节奏",
    "音击中二": "音击&中二节奏", "ongeki": "音击&中二节奏",
    "chunithm": "音击&中二节奏", "chu": "音击&中二节奏",
    # 其他游戏
    "其他游戏": "其他游戏", "variety": "其他游戏", "游戏": "其他游戏",
    # 宴会場
    "宴会場": "宴会場", "宴会": "宴会場", "宴": "宴会場",
}

# ══════════════════════════════════════════════════════════════
# 难度颜色 / 标签映射
# ══════════════════════════════════════════════════════════════

LEVEL_COLOR_TO_LABEL: dict[str, str] = {
    "绿": "Basic", "黄": "Advanced", "红": "Expert", "紫": "Master", "白": "Re:Master",
}

LEVEL_LABEL_TO_COLOR: dict[str, str] = {
    "Basic": "绿", "Advanced": "黄", "Expert": "红", "Master": "紫", "Re:Master": "白",
}

LEVEL_LABEL_SHORT: dict[str, str] = {
    "Basic": "B", "Advanced": "A", "Expert": "E", "Master": "M", "Re:Master": "R",
}

# ══════════════════════════════════════════════════════════════
# 简繁汉字映射（谱师搜索用 — 用户输入简体，API 返回日文汉字）
# ══════════════════════════════════════════════════════════════

# 简体 → 日文/繁体
SIMPLIFIED_TO_TRADITIONAL: dict[str, str] = {
    "鸟": "鳥", "游": "遊", "东": "東", "车": "車",
    "门": "門", "马": "馬", "鱼": "魚", "龙": "龍",
    "风": "風", "云": "雲", "电": "電", "飞": "飛",
    "开": "開", "关": "關", "无": "無", "乐": "楽",
    "体": "體", "国": "國", "学": "學", "实": "実",
    "写": "寫", "对": "対", "时": "時", "机": "機",
    "气": "気", "爱": "愛", "点": "點", "长": "長",
    "间": "間", "见": "見", "觉": "覚", "语": "語",
    "话": "話", "读": "読", "谁": "誰", "买": "買",
    "卖": "売", "饭": "飯", "饮": "飲", "铁": "鉄",
    "银": "銀", "钱": "銭", "剑": "剣", "绿": "緑",
    "线": "線", "组": "組", "织": "織", "经": "経",
    "结": "結", "给": "給", "绝": "絶", "统": "統",
    "丝": "糸", "轮": "輪", "转": "転", "轻": "軽",
    "轴": "軸", "辉": "輝", "释": "釈", "轴": "軸",
    "鸡": "鶏", "鸣": "鳴", "鹵": "鹵",
}

# 反向映射：日文/繁体 → 简体
TRADITIONAL_TO_SIMPLIFIED: dict[str, str] = {
    v: k for k, v in SIMPLIFIED_TO_TRADITIONAL.items()
}


def normalize_cjk(text: str, to: str = "traditional") -> str:
    """将文本中的 CJK 字符统一转换为繁体(traditional)或简体(simplified)"""
    if to == "traditional":
        return "".join(SIMPLIFIED_TO_TRADITIONAL.get(c, c) for c in text)
    else:
        return "".join(TRADITIONAL_TO_SIMPLIFIED.get(c, c) for c in text)

# ══════════════════════════════════════════════════════════════
# 牌子 / 版本曲目
# ══════════════════════════════════════════════════════════════

@dataclass
class PlateEntry:
    """牌子/版本内的单曲条目"""
    song_id: int = 0
    title: str = ""
    level_index: int = 0       # 要求的难度
    level_label: str = ""
    # 玩家完成状态（查分后填充）
    achieved: bool = False
    achievements: float = 0.0
    rate: str = ""


@dataclass
class PlateData:
    """牌子/版本曲目数据"""
    plate_name: str = ""           # 版本名 or 段位名
    songs: list[PlateEntry] = field(default_factory=list)
    cleared_count: int = 0
    total_count: int = 0

    @classmethod
    def from_api(cls, data: dict) -> "PlateData":
        plate = cls(
            plate_name=data.get("plate", ""),
            total_count=len(data.get("verlist", [])),
        )
        for entry in data.get("verlist", []):
            plate.songs.append(PlateEntry(
                song_id=entry.get("song_id", 0),
                title=entry.get("title", ""),
                level_index=entry.get("level_index", 0),
                level_label=entry.get("level", ""),
                achieved=entry.get("achieved", False),
                achievements=entry.get("achievements", 0.0),
                rate=entry.get("rate", ""),
            ))
            if entry.get("achieved"):
                plate.cleared_count += 1
        return plate
