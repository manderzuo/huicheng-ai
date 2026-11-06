import subprocess, time, sys, hashlib
import numpy as np
from PIL import Image

CLI = __import__("fishfleet.paths", fromlist=["mobilecli_path"]).mobilecli_path()
DEV = "SERIAL0001"
SHOT = r"C:\Users\ADMINI~1\AppData\Local\Temp\_v8.png"
COLS = 9
L, TOP, BOT, cw = 11.5, 430.0, 1510.0, 116.9
OFFS = list(range(-26, 27, 6))
A = {"RED", "YEL", "GRN", "BLU", "PUR"}
sym = {"RED":"R","YEL":"Y","GRN":"G","BLU":"B","PUR":"U","PINK":"P","ICE":"#","NONE":"."}


def grab():
    subprocess.run([CLI, "screenshot", "--device", DEV, "-o", SHOT], capture_output=True, timeout=60)
    return np.asarray(Image.open(SHOT).convert("RGB"), dtype=np.float32) / 255.0


def rgb_to_hsv(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = a.max(-1); mn = a.min(-1); d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0); v = mx
    h = np.zeros_like(mx); m = d > 1e-6
    rm = m & (mx == r); gm = m & (mx == g) & ~rm; bm = m & (mx == b) & ~rm & ~gm
    h[rm] = ((g[rm]-b[rm])/d[rm]) % 6
    h[gm] = (b[gm]-r[gm])/d[gm] + 2
    h[bm] = (r[bm]-g[bm])/d[bm] + 4
    return h*60, s, v


def _longest_run(row):
    idx = np.flatnonzero(row)
    if idx.size == 0:
        return 0
    d = np.diff(idx)
    br = np.flatnonzero(d > 1)
    starts = np.r_[0, br + 1]
    ends = np.r_[br, idx.size - 1]
    return int((idx[ends] - idx[starts]).max() + 1)


def find_green_button(a):
    h, s, v = rgb_to_hsv(a)
    m = (h > 85) & (h < 155) & (s > 0.45) & (v > 0.45)
    H = m.shape[0]
    good = np.zeros(H, dtype=bool)
    for y in range(500, min(H, 1700), 2):
        if _longest_run(m[y]) >= 240:
            good[y] = True
            good[y + 1] = True
    ys = np.flatnonzero(good)
    if ys.size < 40:
        return None
    y0, y1 = int(ys.min()), int(ys.max())
    band = m[y0:y1]
    xs = np.flatnonzero(band.any(axis=0))
    cx = int((xs.min() + xs.max()) // 2)
    cy = int((y0 + y1) // 2)
    # 结算/开战页的按钮固定在屏幕中下部；棋盘里的绿色动物不会落在这个位置
    if not (940 <= cy <= 1430 and 360 <= cx <= 720):
        return None
    return cx, cy


def board_bbox(a):
    """用"非天空"像素的外接框自动定位棋盘，适配不同宽度的关卡棋盘"""
    H, W = a.shape[:2]
    h, s, v = rgb_to_hsv(a)
    dark = v < 0.45
    y0s, y1s = 425, min(H, 1560)
    sub = dark[y0s:y1s, :]
    if sub.size == 0:
        return None
    colsum = sub.sum(axis=0)
    rowsum = sub.sum(axis=1)
    cols = np.flatnonzero(colsum > sub.shape[0] * 0.12)
    rows = np.flatnonzero(rowsum > sub.shape[1] * 0.12)
    if cols.size < 20 or rows.size < 20:
        return None
    x0, x1 = int(cols.min()), int(cols.max())
    yb0, yb1 = int(rows.min()) + y0s, int(rows.max()) + y0s
    if x1 - x0 < 400 or yb1 - yb0 < 400:
        return None
    return x0, x1, yb0, yb1


def read_board(a, centers, C=None):
    C = C or COLS
    H, W = a.shape[:2]; R = len(centers)
    xs = np.array([int(L + cw*(i+0.5)) for i in range(C)], dtype=np.int32)
    ys = np.array(centers, dtype=np.int32)
    ox = np.array(OFFS, dtype=np.int32)
    gx = np.clip(xs[None, :, None] + ox, 0, W-1)
    gy = np.clip(ys[:, None, None] + ox, 0, H-1)
    gx = np.broadcast_to(gx, (R, C, len(OFFS)))
    gy = np.broadcast_to(gy, (R, C, len(OFFS)))
    px = a[gy[..., None], gx[..., None], :]
    hh, ss, vv = rgb_to_hsv(px)
    white = (ss < 0.22) & (vv > 0.80)
    wr = white.reshape(R, C, -1).mean(-1)
    satm = (ss > 0.45) & (vv > 0.35)
    hf = hh.reshape(R, C, -1); mf = satm.reshape(R, C, -1)
    vf = vv.reshape(R, C, -1); sf = ss.reshape(R, C, -1)
    std_rgb = px.reshape(R, C, -1, 3).std(axis=2).mean(-1)
    out = []
    for j in range(R):
        row = []
        for i in range(C):
            vals = hf[j, i][mf[j, i]]
            if vals.size == 0:
                row.append("NONE"); continue
            sels = mf[j, i]
            med = float(np.median(vals))
            vmed = float(np.median(vf[j, i][sels]))
            smed = float(np.median(sf[j, i][sels]))
            # 棋盘外天空：几乎纯色 + 天空蓝（与蓝猫同色但蓝猫有轮廓，标准差大）
            if std_rgb[j, i] < 0.06 and 185 <= med <= 206 and smed > 0.65 and vmed > 0.80:
                row.append("NONE"); continue
            if wr[j, i] > 0.62:
                row.append("ICE"); continue
            # 猫脸木板：木色(橙棕)、哑光、无高光；与亮黄小鸡区分
            if 15 <= med <= 40 and 0.50 <= smed <= 0.78 and 0.78 <= vmed <= 0.90:
                row.append("WOOD"); continue
            if 265 <= med < 322: c = "PUR"
            elif 322 <= med < 345: c = "PINK"
            elif med >= 345 or med < 20: c = "RED"
            elif med < 65: c = "YEL"
            elif med < 165: c = "GRN"
            elif med < 265: c = "BLU"
            else: c = "PINK"
            row.append(c)
        out.append(row)
    return out


def run_len(g, i, j, di, dj, C, R):
    t = g[j][i]; n = 1
    x, y = i+di, j+dj
    while 0 <= x < C and 0 <= y < R and g[y][x] == t:
        n += 1; x += di; y += dj
    x, y = i-di, j-dj
    while 0 <= x < C and 0 <= y < R and g[y][x] == t:
        n += 1; x -= di; y -= dj
    return n


def cleared(g, C, R):
    out = set()
    for j in range(R):
        for i in range(C):
            if g[j][i] not in A: continue
            if run_len(g, i, j, 1, 0, C, R) >= 3:
                x = i
                while x >= 0 and g[j][x] == g[j][i]:
                    out.add((x, j)); x -= 1
                x = i+1
                while x < C and g[j][x] == g[j][i]:
                    out.add((x, j)); x += 1
            if run_len(g, i, j, 0, 1, C, R) >= 3:
                y = j
                while y >= 0 and g[y][i] == g[j][i]:
                    out.add((i, y)); y -= 1
                y = j+1
                while y < R and g[y][i] == g[j][i]:
                    out.add((i, y)); y += 1
    return out


def all_moves(g, C, R):
    res = []
    for j in range(R):
        for i in range(C):
            if g[j][i] not in A: continue
            for di, dj in ((1, 0), (0, 1)):
                x, y = i+di, j+dj
                if not (0 <= x < C and 0 <= y < R): continue
                if g[y][x] not in A: continue
                h = [r[:] for r in g]
                h[j][i], h[y][x] = h[y][x], h[j][i]
                cl = cleared(h, C, R)
                if not cl: continue
                res.append((len(cl), i, j, x, y))
    res.sort(reverse=True)
    return res


def board_key(g):
    return hashlib.md5(str(g).encode()).hexdigest()[:8]


def swipe(i, j, x, y, centers, C=9):
    step_x = cw
    subprocess.run([CLI, "io", "swipe", "--device", DEV, "%d,%d,%d,%d" % (
        int(L+step_x*(i+0.5)), centers[j], int(L+step_x*(x+0.5)), centers[y]),
        "--duration", "260"], capture_output=True, timeout=60)


def tap(x, y):
    subprocess.run([CLI, "io", "tap", "--device", DEV, "%d,%d" % (x, y)], capture_output=True, timeout=60)


def build(rows):
    ch = (BOT-TOP)/rows
    return [int(TOP+ch*(j+0.5)) for j in range(rows)]


def board_patch(a):
    y0, y1 = int(TOP), int(BOT)
    x0, x1 = int(L), int(L + cw * COLS)
    y0 = max(0, y0); x0 = max(0, x0)
    return a[y0:y1, x0:x1, :]


def patch_diff(a, b):
    if a.shape != b.shape:
        return 1.0
    return float(np.mean(np.abs(a - b) > 0.10))


CANDIDATES = [(9, 9), (8, 8), (9, 8), (8, 9), (8, 7), (7, 7), (9, 7), (8, 10), (9, 10), (10, 10)]


def layout_darkness(a, C, R, bx0, bx1, by0, by1):
    """正确布局下，格子中心是方块内容；布局错位时中心会压到格子深色缝上"""
    H, W = a.shape[:2]
    h, s, v = rgb_to_hsv(a)
    dark = v < 0.40
    cw_ = (bx1 - bx0) / C
    ch_ = (by1 - by0) / R
    vals = []
    for j in range(R):
        for i in range(C):
            cx = int(bx0 + cw_ * (i + 0.5))
            cy = int(by0 + ch_ * (j + 0.5))
            x0, x1 = max(0, cx-12), min(W, cx+12)
            y0, y1 = max(0, cy-12), min(H, cy+12)
            if x1 <= x0 or y1 <= y0:
                continue
            vals.append(float(dark[y0:y1, x0:x1].mean()))
    return float(np.mean(vals)) if vals else 1.0


def pick_layout(a):
    bb = board_bbox(a)
    if not bb:
        return None
    bx0, bx1, by0, by1 = bb
    w = bx1 - bx0
    h = by1 - by0
    if w < 500 or h < 500:
        return None
    aspect = w / h
    best = None
    for C, R in CANDIDATES:
        if abs((C / R) - aspect) > 0.35:
            continue
        d = layout_darkness(a, C, R, bx0, bx1, by0, by1)
        if best is None or d < best[0]:
            best = (d, C, R, bx0, bx1, by0, by1)
    return best


def main(max_steps):
    global L, TOP, BOT, cw
    t0 = time.time()
    steps = 0
    levels = 0
    idle = 0
    tried = set()
    for _ in range(max_steps * 6):
        a = grab()
        btn = find_green_button(a)
        if btn:
            tap(*btn); time.sleep(2.0); levels += 1; idle = 0; tried.clear()
            print("[%5.1fs] >> button %s (transitions %d, steps %d)"
                  % (time.time()-t0, str(btn), levels, steps))
            time.sleep(0.8)
            continue
        pk = pick_layout(a)
        if not pk:
            tap(540, 960); time.sleep(1.5); idle += 1
            if idle > 15: print(">> stuck"); break
            continue
        _, C, R, bx0, bx1, by0, by1 = pk
        if C == 0 or R == 0:
            continue
        L = float(bx0); TOP = float(by0); BOT = float(by1)
        cw = (bx1 - bx0) / C
        ch = (BOT - TOP) / R
        centers = [int(TOP + ch*(j+0.5)) for j in range(R)]
        g = read_board(a, centers, C)
        animal = np.mean([[t in A for t in row] for row in g]) if R and C else 0
        if animal < 0.08:
            tap(540, 960); time.sleep(1.5); idle += 1
            print("[%5.1fs] ...... no board (animal=%.2f)" % (time.time()-t0, animal))
            if idle > 15: print(">> stuck"); break
            continue
        mv = all_moves(g, C, R)
        if not mv:
            tap(540, 960); time.sleep(1.5); idle += 1
            if idle > 15: print(">> no moves"); break
            continue
        base = board_patch(a)
        acted = False
        for cnt, i, j, x, y in mv[:8]:
            key = (C, R, i, j, x, y)
            if key in tried: continue
            swipe(i, j, x, y, centers, C)
            time.sleep(1.0)
            a2 = grab()
            d = patch_diff(base, board_patch(a2))
            if d > 0.05:
                steps += 1; idle = 0; tried.clear()
                print("[%5.1fs] step %3d | %dx%d | %d,%d->%d,%d expect=%d diff=%.2f"
                      % (time.time()-t0, steps, C, R, i, j, x, y, cnt, d))
                acted = True
                break
            else:
                tried.add(key)
        if not acted:
            tap(540, 960); time.sleep(1.5); idle += 1
            print("[%5.1fs] ...... all candidates ineffective idle=%d" % (time.time()-t0, idle))
            tried.clear()
            if idle > 10: print(">> stuck"); break
        if steps >= max_steps: break
    print("SUMMARY: %d steps, %d transitions, %.1fs (%.2fs/step)"
          % (steps, levels, time.time()-t0, (time.time()-t0)/max(steps, 1)))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
