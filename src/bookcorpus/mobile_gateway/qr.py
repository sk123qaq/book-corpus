"""纯 Python 标准库零依赖 QR 码生成器（ISO/IEC 18004 Byte-mode Version 1–12，ECC-M）。"""

from __future__ import annotations

import html
from dataclasses import dataclass

# Version -> (total_data_cw, ec_cw_per_block, g1_blocks, g1_data_cw, g2_blocks, g2_data_cw)
_ECC_M_SPECS: dict[int, tuple[int, int, int, int, int, int]] = {
    1: (16, 10, 1, 16, 0, 0),
    2: (28, 16, 1, 28, 0, 0),
    3: (44, 26, 1, 44, 0, 0),
    4: (64, 18, 2, 32, 0, 0),
    5: (86, 24, 2, 43, 0, 0),
    6: (108, 16, 4, 27, 0, 0),
    7: (124, 18, 4, 31, 0, 0),
    8: (154, 22, 2, 38, 2, 39),
    9: (182, 22, 3, 36, 2, 37),
    10: (216, 26, 4, 43, 1, 44),
    11: (254, 30, 1, 50, 4, 51),
    12: (290, 22, 6, 36, 2, 37),
}

_ALIGNMENT_COORDS: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
    11: (6, 30, 54),
    12: (6, 32, 58),
}

_REMAINDER_BITS: dict[int, int] = {
    1: 0,
    2: 7,
    3: 7,
    4: 7,
    5: 7,
    6: 7,
    7: 0,
    8: 0,
    9: 0,
    10: 0,
    11: 0,
    12: 0,
}

# ECC Level M 的 2-bit 格式指示符（ISO/IEC 18004 Table 25: M = 00）
_EC_LEVEL_M_BITS = 0b00

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _init_gf() -> None:
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_init_gf()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        root = _GF_EXP[i]
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, root)
        poly = nxt
    return poly


def _rs_remainder(data: list[int], ec_len: int) -> list[int]:
    gen = _rs_generator(ec_len)
    buf = [*data, *([0] * ec_len)]
    for i in range(len(data)):
        factor = buf[i]
        if factor != 0:
            for j in range(1, len(gen)):
                buf[i + j] ^= _gf_mul(gen[j], factor)
    return buf[len(data) :]


def _format_bits(mask: int) -> int:
    data = (_EC_LEVEL_M_BITS << 3) | (mask & 0x7)
    rem = data << 10
    for shift in range(4, -1, -1):
        if rem & (1 << (shift + 10)):
            rem ^= 0x537 << shift
    return ((data << 10) | rem) ^ 0x5412


def _version_bits(version: int) -> int:
    rem = version << 12
    for shift in range(5, -1, -1):
        if rem & (1 << (shift + 12)):
            rem ^= 0x1F25 << shift
    return (version << 12) | rem


def _select_version(byte_len: int) -> int:
    for version in range(1, 13):
        total_data_cw = _ECC_M_SPECS[version][0]
        count_bits = 8 if version <= 9 else 16
        # Mode (4 bits) + count_bits + data bits must fit in total_data_cw * 8
        if 4 + count_bits + byte_len * 8 <= total_data_cw * 8:
            return version
    raise ValueError(f"QR 内容过长（{byte_len} 字节），超出 Version 12 ECC-M 上限")


def _encode_data_codewords(payload: bytes, version: int) -> list[int]:
    total_data_cw = _ECC_M_SPECS[version][0]
    capacity_bits = total_data_cw * 8
    count_bits = 8 if version <= 9 else 16

    bits: list[int] = []

    def push_bits(val: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((val >> shift) & 1)

    push_bits(0b0100, 4)  # Byte mode
    push_bits(len(payload), count_bits)
    for byte in payload:
        push_bits(byte, 8)

    terminator = min(4, capacity_bits - len(bits))
    bits.extend([0] * terminator)
    while len(bits) % 8 != 0:
        bits.append(0)

    codewords = [0] * (len(bits) // 8)
    for idx in range(len(codewords)):
        val = 0
        base = idx * 8
        for bit_idx in range(8):
            val = (val << 1) | bits[base + bit_idx]
        codewords[idx] = val

    pad_bytes = (0xEC, 0x11)
    pad_idx = 0
    while len(codewords) < total_data_cw:
        codewords.append(pad_bytes[pad_idx & 1])
        pad_idx += 1
    return codewords


def _interleave_blocks(data_cw: list[int], version: int) -> list[int]:
    _total, ec_len, g1_cnt, g1_len, g2_cnt, g2_len = _ECC_M_SPECS[version]
    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    cursor = 0

    for _ in range(g1_cnt):
        blk = data_cw[cursor : cursor + g1_len]
        cursor += g1_len
        data_blocks.append(blk)
        ec_blocks.append(_rs_remainder(blk, ec_len))
    for _ in range(g2_cnt):
        blk = data_cw[cursor : cursor + g2_len]
        cursor += g2_len
        data_blocks.append(blk)
        ec_blocks.append(_rs_remainder(blk, ec_len))

    final_cw: list[int] = []
    max_data_len = max(len(b) for b in data_blocks)
    for col in range(max_data_len):
        for blk in data_blocks:
            if col < len(blk):
                final_cw.append(blk[col])
    for col in range(ec_len):
        for blk in ec_blocks:
            final_cw.append(blk[col])
    return final_cw


def _mask_condition(mask: int, row: int, col: int) -> bool:
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return ((row // 2) + (col // 3)) % 2 == 0
    if mask == 5:
        return ((row * col) % 2) + ((row * col) % 3) == 0
    if mask == 6:
        return (((row * col) % 2) + ((row * col) % 3)) % 2 == 0
    return (((row + col) % 2) + ((row * col) % 3)) % 2 == 0


def _build_function_grid(version: int) -> tuple[list[list[bool]], list[list[bool]]]:
    size = 17 + 4 * version
    modules = [[False] * size for _ in range(size)]
    is_func = [[False] * size for _ in range(size)]

    def set_func(r: int, c: int, dark: bool) -> None:
        if 0 <= r < size and 0 <= c < size:
            modules[r][c] = dark
            is_func[r][c] = True

    def place_finder(top: int, left: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = top + dr, left + dc
                if not (0 <= r < size and 0 <= c < size):
                    continue
                if 0 <= dr <= 6 and 0 <= dc <= 6:
                    dark = dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)
                else:
                    dark = False
                set_func(r, c, dark)

    place_finder(0, 0)
    place_finder(0, size - 7)
    place_finder(size - 7, 0)

    # Alignment patterns
    coords = _ALIGNMENT_COORDS[version]
    for r_center in coords:
        for c_center in coords:
            if is_func[r_center][c_center]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    dark = max(abs(dr), abs(dc)) != 1
                    set_func(r_center + dr, c_center + dc, dark)

    # Timing patterns
    for idx in range(8, size - 8):
        dark = idx % 2 == 0
        if not is_func[6][idx]:
            set_func(6, idx, dark)
        if not is_func[idx][6]:
            set_func(idx, 6, dark)

    # Dark module
    set_func(size - 8, 8, True)

    # Reserve format info cells
    for idx in range(9):
        if idx != 6:
            is_func[8][idx] = True
            is_func[idx][8] = True
    for idx in range(8):
        is_func[8][size - 1 - idx] = True
    for idx in range(7):
        is_func[size - 1 - idx][8] = True

    # Version info (V7+)
    if version >= 7:
        vbits = _version_bits(version)
        for i in range(18):
            dark = bool((vbits >> i) & 1)
            r = i // 3
            c = size - 11 + (i % 3)
            set_func(r, c, dark)
            set_func(c, r, dark)

    return modules, is_func


def _write_format_info(modules: list[list[bool]], mask: int) -> None:
    size = len(modules)
    fbits = _format_bits(mask)

    top_left_coords = [
        (8, 0),
        (8, 1),
        (8, 2),
        (8, 3),
        (8, 4),
        (8, 5),
        (8, 7),
        (8, 8),
        (7, 8),
        (5, 8),
        (4, 8),
        (3, 8),
        (2, 8),
        (1, 8),
        (0, 8),
    ]
    split_coords = [
        (size - 1, 8),
        (size - 2, 8),
        (size - 3, 8),
        (size - 4, 8),
        (size - 5, 8),
        (size - 6, 8),
        (size - 7, 8),
        (8, size - 8),
        (8, size - 7),
        (8, size - 6),
        (8, size - 5),
        (8, size - 4),
        (8, size - 3),
        (8, size - 2),
        (8, size - 1),
    ]
    for bit_pos in range(15):
        dark = bool((fbits >> (14 - bit_pos)) & 1)
        r1, c1 = top_left_coords[bit_pos]
        r2, c2 = split_coords[bit_pos]
        modules[r1][c1] = dark
        modules[r2][c2] = dark


def _place_codewords(modules: list[list[bool]], is_func: list[list[bool]], codewords: list[int], version: int) -> None:
    size = len(modules)
    bit_stream: list[bool] = []
    for cw in codewords:
        for shift in range(7, -1, -1):
            bit_stream.append(bool((cw >> shift) & 1))
    bit_stream.extend([False] * _REMAINDER_BITS[version])

    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        row_iter = range(size - 1, -1, -1) if upward else range(size)
        for row in row_iter:
            for dc in (0, -1):
                c = col + dc
                if not is_func[row][c]:
                    modules[row][c] = bit_stream[bit_idx] if bit_idx < len(bit_stream) else False
                    bit_idx += 1
        upward = not upward
        col -= 2


def _penalty_score(modules: list[list[bool]]) -> int:
    size = len(modules)
    score = 0

    # Rule 1: runs of 5+ same color in rows and columns
    for r in range(size):
        run_color = modules[r][0]
        run_len = 1
        for c in range(1, size):
            if modules[r][c] == run_color:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_color = modules[r][c]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)

    for c in range(size):
        run_color = modules[0][c]
        run_len = 1
        for r in range(1, size):
            if modules[r][c] == run_color:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_color = modules[r][c]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)

    # Rule 2: 2x2 blocks of same color
    for r in range(size - 1):
        for c in range(size - 1):
            v = modules[r][c]
            if v == modules[r][c + 1] == modules[r + 1][c] == modules[r + 1][c + 1]:
                score += 3

    # Rule 3: finder-like 1:1:3:1:1 patterns with 4-module white guard
    pat_a = (True, False, True, True, True, False, True, False, False, False, False)
    pat_b = (False, False, False, False, True, False, True, True, True, False, True)
    for r in range(size):
        row = modules[r]
        for c in range(size - 10):
            seg = tuple(row[c : c + 11])
            if seg in (pat_a, pat_b):
                score += 40
    for c in range(size):
        for r in range(size - 10):
            seg = tuple(modules[r + k][c] for k in range(11))
            if seg in (pat_a, pat_b):
                score += 40

    # Rule 4: dark/light balance
    dark_count = sum(1 for row in modules for cell in row if cell)
    total = size * size
    deviation = abs(dark_count * 100 - total * 50)
    score += (deviation // (total * 5)) * 10
    return score


@dataclass(frozen=True)
class QrMatrix:
    """编码后的 QR 矩阵与元信息。"""

    version: int
    mask: int
    size: int
    modules: tuple[tuple[bool, ...], ...]


def encode_qr(text: str) -> QrMatrix:
    """将 UTF-8 字符串编码为 QR Code 矩阵（Byte mode，ECC Level M）。"""
    payload = text.encode("utf-8")
    if not payload:
        raise ValueError("QR 内容不能为空")
    version = _select_version(len(payload))
    data_cw = _encode_data_codewords(payload, version)
    final_cw = _interleave_blocks(data_cw, version)

    base_modules, is_func = _build_function_grid(version)
    _place_codewords(base_modules, is_func, final_cw, version)

    size = len(base_modules)
    best_mask = 0
    best_score: int | None = None
    best_grid: list[list[bool]] | None = None

    for mask in range(8):
        cand = [row.copy() for row in base_modules]
        for r in range(size):
            for c in range(size):
                if not is_func[r][c] and _mask_condition(mask, r, c):
                    cand[r][c] = not cand[r][c]
        _write_format_info(cand, mask)
        sc = _penalty_score(cand)
        if best_score is None or sc < best_score:
            best_score = sc
            best_mask = mask
            best_grid = cand

    assert best_grid is not None
    return QrMatrix(
        version=version,
        mask=best_mask,
        size=size,
        modules=tuple(tuple(row) for row in best_grid),
    )


def render_svg(
    text: str,
    *,
    border: int = 4,
    module_px: int = 4,
    fg: str = "#111111",
    bg: str = "#FFFFFF",
    label: str = "配对二维码",
) -> str:
    """将字符串渲染为带白底与 4-module 静区的确定性离线 SVG 二维码。"""
    qr = encode_qr(text)
    quiet = max(4, int(border))
    scale = max(1, int(module_px))
    dim = (qr.size + quiet * 2) * scale

    segments: list[str] = []
    for r, row in enumerate(qr.modules):
        c = 0
        while c < qr.size:
            if not row[c]:
                c += 1
                continue
            start = c
            while c < qr.size and row[c]:
                c += 1
            x = (start + quiet) * scale
            y = (r + quiet) * scale
            w = (c - start) * scale
            segments.append(f"M{x} {y}h{w}v{scale}h-{w}z")

    path_d = "".join(segments)
    esc_label = html.escape(label, quote=True)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
        f'width="{dim}" height="{dim}" role="img" aria-label="{esc_label}" '
        f'shape-rendering="crispEdges">'
        f'<rect width="100%" height="100%" fill="{html.escape(bg, quote=True)}"/>'
        f'<path fill="{html.escape(fg, quote=True)}" d="{path_d}"/>'
        f"</svg>"
    )
