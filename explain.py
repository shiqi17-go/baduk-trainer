"""Structure layer + template language layer.

Turns raw KataGo analysis JSON into a fact sheet, then renders Chinese prose
from it. Everything numeric here comes from the engine; nothing is invented.

Key conventions (all verified empirically against v1.18.1):
  * winrate and scoreLead are reported from BLACK's perspective because we run
    the engine with reportAnalysisWinratesAs=BLACK. The docs say "the current
    side", so do NOT trust the prose -- convert explicitly.
  * ownership / policy arrays are row-major with (0,0) at the TOP-LEFT (A19),
    indexed y * board_size + x.
  * policy / humanPolicy have length size*size + 1, the last entry is pass,
    and illegal moves are -1.

The "surprise" idea comes from the KataGo authors' own open questions in
docs/Analysis_Engine.md, section "Possible metrics that might be interesting".
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goban import BLACK, EMPTY, WHITE, GTP_LETTERS  # noqa: E402

# --------------------------------------------------------------------- regions

REGION_NAMES = ["左上角", "上边", "右上角", "左边", "中腹", "右边",
                "左下角", "下边", "右下角"]
SHORT_REGION = ["左上", "上边", "右上", "左边", "中腹", "右边",
                "左下", "下边", "右下"]


def region_index(x: int, y: int, size: int = 19) -> int:
    """Split the board into a 3x3 grid: corner / side / centre.

    The middle band is 6..12 inclusive. An earlier version tested
    `x < edges[1][0]` (x < 6) for the middle band, so column 6 fell through to
    the third band and every point with 6 <= x <= 12 -- the entire centre and
    all four sides -- was misfiled into the right-hand column. The side and
    centre regions ended up containing zero points, which quietly corrupted
    every direction, ownership and battlefield statement built on top.
    """
    if size == 19:
        edges = [(0, 5), (6, 12), (13, 18)]
    else:
        third = size // 3
        edges = [(0, third - 1), (third, size - third - 1), (size - third, size - 1)]
    cx = 0 if x <= edges[0][1] else (1 if x <= edges[1][1] else 2)
    cy = 0 if y <= edges[0][1] else (1 if y <= edges[1][1] else 2)
    return cy * 3 + cx


def region_of_move(move: str, size: int = 19) -> int | None:
    from goban import from_gtp
    pos = from_gtp(move, size)
    if pos is None:
        return None
    return region_index(pos[0], pos[1], size)


def region_totals(ownership: list[float], size: int = 19) -> list[float]:
    """Signed expected black territory per region (positive = black)."""
    out = [0.0] * 9
    for y in range(size):
        row = y * size
        for x in range(size):
            out[region_index(x, y, size)] += ownership[row + x]
    return out


# ------------------------------------------------------------------ utilities

def norm_policy(policy: list[float], size: int = 19) -> dict[str, float]:
    """policy array -> {move: prob} with illegal entries dropped."""
    out: dict[str, float] = {}
    n = size * size
    for i, v in enumerate(policy[:n]):
        if v > 0:
            x, y = i % size, i // size
            out[f"{GTP_LETTERS[x]}{size - y}"] = v
    if len(policy) > n and policy[n] > 0:
        out["pass"] = policy[n]
    return out


def kl_divergence(p: dict[str, float], q: dict[str, float],
                  floor: float = 1e-6) -> float:
    """KL(p || q) in bits over the union of support."""
    keys = set(p) | set(q)
    sp = sum(p.values()) or 1.0
    sq = sum(q.values()) or 1.0
    total = 0.0
    for k in keys:
        pi = p.get(k, 0.0) / sp
        qi = q.get(k, 0.0) / sq
        if pi <= 0:
            continue
        total += pi * math.log2(pi / max(qi, floor))
    return total


def surprisal_bits(prob: float, floor: float = 1e-6) -> float:
    """-log2(p): how unexpected a move is under a distribution."""
    return -math.log2(max(prob, floor))


# A position this far from even is already decided, and the score "loss" of a
# move played there is noise rather than a lesson.
DECIDED_LEAD = 25.0


def distribution_entropy(dist: dict[str, float]) -> float:
    return -sum(p * math.log2(p) for p in dist.values() if p > 1e-9)


def rank_in(dist: dict[str, float], move: str) -> int | None:
    """1-based rank of `move` inside `dist`, most likely first."""
    for i, (mv, _) in enumerate(sorted(dist.items(), key=lambda kv: -kv[1]), 1):
        if mv == move:
            return i
    return None


def visit_distribution(move_infos: list[dict]) -> dict[str, float]:
    tot = sum(mi.get("visits", 0) for mi in move_infos) or 1
    return {mi["move"]: mi.get("visits", 0) / tot for mi in move_infos}


# ---------------------------------------------------------------- fact sheet

def build_turn_facts(turn: int, resp: dict, played_move: str,
                     human_profile: str, size: int = 19,
                     komi: float = 7.5, grid=None) -> dict:
    """One turn -> one fact record. All score values are from the MOVER's
    perspective (converted from the engine's black-perspective output).

    Pass `grid` (goban.grid) to get a group-by-group read of the board, which is
    what lets the explanation talk about stones instead of only numbers.
    """
    ri = resp.get("rootInfo", {})
    mover = ri.get("currentPlayer", "B")
    sign = 1.0 if mover == "B" else -1.0

    move_infos = resp.get("moveInfos", []) or []
    by_move = {mi["move"]: mi for mi in move_infos}
    best = move_infos[0] if move_infos else None

    def mover_lead(mi: dict | None) -> float | None:
        if not mi or "scoreLead" not in mi:
            return None
        return sign * mi["scoreLead"]

    f: dict = {
        "turn": turn,
        "move_number": turn + 1,
        "mover": mover,
        "played": played_move,
        "human_profile": human_profile,
        "root_lead_mover": sign * ri.get("scoreLead", 0.0),
        "root_winrate_mover": 1.0 - ri.get("winrate", 0.5) if mover == "W"
        else ri.get("winrate", 0.5),
        "best_move": best["move"] if best else None,
        "is_best": bool(best and best["move"] == played_move),
        "loss": None,
        "played_lead_mover": None,
        "visits": sum(mi.get("visits", 0) for mi in move_infos),
        "num_candidates": len(move_infos),
        "region": region_of_move(played_move, size),
        "reasoning_divergence": False,
        "policy_top": None,
        "search_top": best["move"] if best else None,
        "pv": [],
        "human_p_played": None,
        "surprisal_bits": None,
        "kl_human_ai": None,
        "human_top": [],
        "ai_top": [],
        "ownership_delta_regions": None,
        "played_searched": played_move in by_move,
        "pass_expected": False,
    }

    # how the AI ranks its candidates
    for mi in move_infos[:5]:
        f["ai_top"].append({
            "move": mi["move"],
            "visits": mi.get("visits", 0),
            "lead": mover_lead(mi),
            "winrate": (1.0 - mi.get("winrate", 0.5)) if mover == "W"
            else mi.get("winrate", 0.5),
            "human_prior": mi.get("humanPrior"),
            "region": region_of_move(mi["move"], size),
        })

    if best:
        b_lead = mover_lead(best)
        if played_move in by_move:
            p_lead = mover_lead(by_move[played_move])
            f["played_lead_mover"] = p_lead
            if b_lead is not None and p_lead is not None:
                f["loss"] = max(0.0, b_lead - p_lead)

    if best:
        f["pv"] = [m for m in best.get("pv", [])][:6]

    # --- human policy -------------------------------------------------------
    hp = resp.get("humanPolicy")
    if hp:
        hp_map = norm_policy(hp, size)
        # Entropy matters: a typical draw from a 4.5-bit distribution IS about
        # 4.5 bits surprising, so raw surprisal alone flags everything. What we
        # actually want is how much MORE surprising this move is than a typical
        # move for this rank.
        ent = distribution_entropy(hp_map)
        f["human_entropy_bits"] = ent
        if played_move in hp_map:
            f["human_p_played"] = hp_map[played_move]
            f["surprisal_bits"] = surprisal_bits(hp_map[played_move])
            f["surprise_excess_bits"] = f["surprisal_bits"] - ent
        elif played_move.lower() == "pass":
            f["human_p_played"] = hp[-1] if isinstance(hp, list) else 0.0
        f["human_entropy_bits"] = ent
        f["human_rank"] = rank_in(hp_map, played_move)
        f["human_top_share"] = max(hp_map.values()) if hp_map else None
        # top human choices, with the cost AI assigns to them when searched
        for mv, pr in sorted(hp_map.items(), key=lambda kv: -kv[1])[:4]:
            if pr < 0.01:
                break
            mi = by_move.get(mv)
            f["human_top"].append({
                "move": mv,
                "prob": pr,
                "searched": mv in by_move,
                "loss": (max(0.0, mover_lead(best) - mover_lead(mi))
                         if (mi and best and mover_lead(best) is not None
                             and mover_lead(mi) is not None) else None),
                "region": region_of_move(mv, size),
            })

    # --- second, stronger human reference (the study mode's expert contrast) --
    href = resp.get("humanPolicyRef")
    if href:
        rmap = norm_policy(href, size)
        if rmap:
            f["ref_profile"] = resp.get("_ref_profile") or "高手"
            f["ref_top"] = [{"move": mv, "prob": p}
                            for mv, p in sorted(rmap.items(), key=lambda kv: -kv[1])[:3]]
            bm = f.get("best_move")
            if bm:
                f["ref_share_best"] = rmap.get(bm)

    # --- reasoning trace: net intuition vs search result --------------------
    rp = resp.get("policy")
    if rp:
        rp_map = norm_policy(rp, size)
        if rp_map:
            f["policy_top"] = max(rp_map.items(), key=lambda kv: kv[1])[0]
            f["reasoning_divergence"] = bool(
                best and f["policy_top"] and f["policy_top"] != best["move"])
            vd = visit_distribution(move_infos)
            if vd and rp_map:
                f["kl_ai_visits_vs_policy"] = kl_divergence(vd, rp_map)
            hp_map = norm_policy(hp, size) if hp else None
            if hp_map and vd:
                f["kl_human_ai"] = kl_divergence(hp_map, vd)

    # --- ownership: what the best move fights for ---------------------------
    root_own = resp.get("ownership")
    best_mi = by_move.get(f["best_move"]) if f["best_move"] else None
    if root_own:
        # How settled is the position? Points the net considers undecided tell a
        # student whether the game is still wide open or nearly over -- a far
        # more useful thing to know than the raw score alone.
        f["undecided_points"] = sum(1 for v in root_own if abs(v) < 0.4)
        f["settled_points"] = sum(1 for v in root_own if abs(v) >= 0.8)
        f["board_points"] = len(root_own)
    if root_own and best_mi and best_mi.get("ownership"):
        before = region_totals(root_own, size)
        after = region_totals(best_mi["ownership"], size)
        f["ownership_delta_regions"] = [a - b for a, b in zip(after, before)]
        f["root_region_own"] = before
        # point-level delta, so the explanation can name actual points instead of
        # saying "the lower-right moved a bit"
        f["ownership_delta_full"] = [a - b for a, b in
                                     zip(best_mi["ownership"], root_own)]
        # per-region count of points whose ownership is still in the air
        und = [0] * 9
        rsize = [0] * 9
        for y in range(size):
            for x in range(size):
                ri = region_index(x, y, size)
                rsize[ri] += 1
                if abs(root_own[y * size + x]) < 0.4:
                    und[ri] += 1
        f["region_undecided"] = und
        f["region_size"] = rsize
    # group-by-group read of the board, which is what makes an explanation feel
    # like teaching rather than a statistics dump
    if grid is not None and root_own:
        f["groups"] = analyze_groups(grid, root_own, size)
    f["board_size"] = size

    # urgency: if you play the best move of another region instead, what do you lose?
    if best and move_infos:
        first_region = region_of_move(best["move"], size)
        alt = next((mi for mi in move_infos[1:]
                    if region_of_move(mi["move"], size) != first_region
                    and (mover_lead(mi) is not None)), None)
        if alt and mover_lead(best) is not None:
            f["tenuki_cost"] = mover_lead(best) - mover_lead(alt)
            f["alt_region_move"] = alt["move"]
            f["alt_region_lead"] = mover_lead(alt)
    return f


# --------------------------------------------------------- language layer

def fmt_move(mv: str | None) -> str:
    if not mv:
        return "（无）"
    return "停一手" if mv.lower() == "pass" else mv


def describe_lead(lead: float, who: str) -> str:
    """A negative lead is not 'leading by -12' -- it is being behind."""
    if lead >= 0:
        return f"{who}方领先 {lead:.1f} 目"
    return f"{who}方落后 {abs(lead):.1f} 目"


def describe_region_gain(f: dict, threshold: float = 2.0) -> str | None:
    d = f.get("ownership_delta_regions")
    if not d:
        return None
    mover = f["mover"]
    sign = 1.0 if mover == "B" else -1.0
    scored = sorted(((sign * v, i) for i, v in enumerate(d)), reverse=True)
    gain, gi = scored[0]
    loss, li = scored[-1]
    if gain < threshold:
        return None
    who = "黑" if mover == "B" else "白"
    txt = f"{REGION_NAMES[gi]}的预期归属向{who}方移动约 {gain:.1f} 目"
    if loss < -threshold:
        txt += f"，同时{REGION_NAMES[li]}方向让出约 {abs(loss):.1f} 目"
    return txt


def strategic_directions(f: dict, max_groups: int = 3) -> list[dict]:
    """Group the AI's top candidates into the few distinct plans it sees.

    Listing ten candidate points teaches nothing; a human thinks in terms of
    "two or three directions". Candidates are grouped by region.

    Order matters and is taken from the AI's own ranking, NOT from the raw score
    lead: KataGo orders moves by playSelectionValue, a blend of winrate and
    score, so the move with the largest scoreLead is not necessarily the move it
    would play. Sorting by lead produced a "preferred direction" that
    contradicted the stated best move.
    """
    ai_top = f.get("ai_top") or []
    groups: list[dict] = []
    for rank, cand in enumerate(ai_top):          # rank 0 = the AI's own choice
        region = cand.get("region")
        for g in groups:
            if g["region"] == region:
                g["moves"].append(cand)
                break
        else:
            groups.append({"region": region, "moves": [cand], "rank": rank})
    if not groups:
        return []
    for g in groups:
        g["best"] = g["moves"][0]                 # its earliest = its best
        g["size"] = len(g["moves"])
    groups.sort(key=lambda g: g["rank"])
    return groups[:max_groups]


def render_directions(f: dict) -> list[str]:
    groups = strategic_directions(f)
    if len(groups) < 2:
        return []
    top = groups[0]["best"]
    L = ["  · AI 在这个局面看到几条路（按区域归类，顺序即 AI 的偏好）："]
    for i, g in enumerate(groups, 1):
        rn = REGION_NAMES[g["region"]] if g["region"] is not None else "全盘"
        b = g["best"]
        extra = f"，另有 {g['size'] - 1} 个相近点" if g["size"] > 1 else ""
        lead_txt = (f"，领先 {b['lead']:+.1f} 目"
                    if b.get("lead") is not None else "")
        tail = "（首选所在）" if i == 1 else ""
        L.append(f"      {i}. {rn}——代表点 {fmt_move(b['move'])}{extra}"
                 f"{lead_txt}{tail}")
    # Is the choice between directions decisive?
    l0, l1 = groups[0]["best"].get("lead"), groups[1]["best"].get("lead")
    if l0 is not None and l1 is not None:
        gap = abs(l0 - l1)
        if gap < 1.0:
            L.append(f"      前两条路几乎等价（目差 {gap:.1f}），属于风格取舍，"
                     f"不是对错问题。")
        elif gap >= 3.0:
            L.append(f"      首选方向明显更好（与第二方向相差 {gap:.1f} 目），"
                     f"这里是有明确优劣的。")
    return L


def term_hints(f: dict) -> list[str]:
    """Map computed signals onto ordinary Go vocabulary. These are inferences
    from the numbers, not engine output, so they are phrased as such."""
    hints: list[str] = []
    tc = f.get("tenuki_cost")
    if tc is not None:
        if tc >= 5:
            hints.append("急所——脱先代价很大")
        elif tc >= 2:
            hints.append("先手意味较重")
        elif tc < 0.5:
            hints.append("大场——可从容选择，不必急")
    d = f.get("ownership_delta_regions")
    if d:
        mover = f["mover"]
        sign = 1.0 if mover == "B" else -1.0
        vals = sorted(((sign * v, i) for i, v in enumerate(d)), reverse=True)
        gain, gi = vals[0]
        loss, li = vals[-1]
        if gain >= 3:
            hints.append(f"{REGION_NAMES[gi]}一带是这手的主要收益所在")
        if loss <= -3:
            hints.append(f"代价是{REGION_NAMES[li]}方向让出实利")
    if f.get("reasoning_divergence"):
        hints.append("这一带 AI 做过较深的计算")
    if f.get("kl_human_ai") is not None and f["kl_human_ai"] >= 1.5:
        hints.append("此处人与 AI 的判断差距较大，值得重点体会")
    return hints


def render_situation(f: dict) -> list[str]:
    """A one-glance read of the whole position: who leads, and how much of the
    board is still undecided. The second number is what tells a student whether
    they should be steering for a safe finish or looking for complications."""
    L: list[str] = []
    who = "黑" if f["mover"] == "B" else "白"
    lead = f.get("root_lead_mover")
    und = f.get("undecided_points")
    total = f.get("board_points") or 361
    bits = []
    if lead is not None:
        bits.append(describe_lead(lead, who) + f"（轮到{who}走）")
    if und is not None:
        pct = 100.0 * und / total
        if pct >= 55:
            shape = "局面非常开阔，选择很多"
        elif pct >= 30:
            shape = "局面还有相当余地"
        elif pct >= 12:
            shape = "大局已大致成形"
        else:
            shape = "局面基本定型，进入收官"
        bits.append(f"全盘 {total} 个点里 {und} 个归属未定（{pct:.0f}%），{shape}")
    if bits:
        L.append("  · 形势：" + "；".join(bits) + "。")
    return L


def finalize(L: list[str], f: dict) -> str:
    """Shared tail for both renderers: strategic directions + plain-language
    Go vocabulary. Kept in one place so the two renderers cannot drift."""
    L.extend(render_situation(f))
    L.extend(render_directions(f))
    hints = term_hints(f)
    if hints:
        L.append("  · 用围棋的说法：" + "；".join(hints) + "。")
    return "\n".join(L)


def analyze_groups(grid, ownership: list[float], size: int = 19) -> list[dict]:
    """Find every connected group and read its outlook off the ownership map.

    This is the missing bridge between numbers and the board. "Loses 22 points"
    teaches nothing; "your six stones on the right are still unsettled, and the
    net expects black to take the points around them" is something a student can
    picture and act on.

    `grid` is goban.grid: rows of EMPTY/BLACK/WHITE. `outlook` is from the
    group's own point of view: positive means the surrounding area is expected
    to end up friendly, negative means hostile.
    """
    if not grid or not ownership:
        return []
    seen = [[False] * size for _ in range(size)]
    out: list[dict] = []
    for y0 in range(size):
        for x0 in range(size):
            if grid[y0][x0] == EMPTY or seen[y0][x0]:
                continue
            color = grid[y0][x0]
            stack = [(x0, y0)]
            seen[y0][x0] = True
            stones: list[tuple[int, int]] = []
            while stack:
                x, y = stack.pop()
                stones.append((x, y))
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if (0 <= nx < size and 0 <= ny < size and not seen[ny][nx]
                            and grid[ny][nx] == color):
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            libs = set()
            for (x, y) in stones:
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if (0 <= nx < size and 0 <= ny < size
                            and grid[ny][nx] == EMPTY):
                        libs.add((nx, ny))
            if not libs:
                continue
            sign = 1.0 if color == BLACK else -1.0
            vals = [ownership[ny * size + nx] * sign for nx, ny in libs]
            mean = sum(vals) / len(vals)
            cx = sum(s[0] for s in stones) / len(stones)
            cy = sum(s[1] for s in stones) / len(stones)
            out.append({
                "color": color,
                "size": len(stones),
                "liberties": len(libs),
                "outlook": mean,
                "centre": (int(round(cx)), int(round(cy))),
            })
    return out


def group_status(g: dict) -> str:
    o = g["outlook"]
    if o <= -0.55:
        return "很危险"
    if o <= -0.2:
        return "还没安顿"
    if o >= 0.6:
        return "已经安定"
    return "尚未定型"


def loc_name(x: int, y: int, size: int = 19) -> str:
    return f"{GTP_LETTERS[x]}{size - y}"


def describe_board(f: dict) -> list[str]:
    """Say what the board actually looks like: which groups are in trouble, what
    is settled, and where the fight is."""
    groups = f.get("groups") or []
    size = f.get("board_size", 19)
    if not groups:
        return []
    mover = f["mover"]
    theirs = [g for g in groups
              if g["color"] == (BLACK if mover == "B" else WHITE)]
    foes = [g for g in groups
            if g["color"] != (BLACK if mover == "B" else WHITE)]
    risky = sorted([g for g in theirs if g["size"] >= 3 and g["outlook"] < -0.2],
                   key=lambda g: (g["outlook"], -g["size"]))
    attackable = sorted([g for g in foes if g["size"] >= 3 and g["outlook"] < -0.2],
                        key=lambda g: (g["outlook"], -g["size"]))
    safe = sorted([g for g in groups if g["size"] >= 5 and g["outlook"] >= 0.6],
                  key=lambda g: -g["size"])
    bits: list[str] = []
    if risky:
        g = risky[0]
        x, y = g["centre"]
        bits.append(f"自己{loc_name(x, y, size)}一带那块 {g['size']} 子"
                    f"{group_status(g)}——AI 判断周围的地到头来多半是对手的")
        if len(risky) > 1:
            g2 = risky[1]
            x2, y2 = g2["centre"]
            bits.append(f"{loc_name(x2, y2, size)}那块 {g2['size']} 子"
                        f"{group_status(g2)}")
    else:
        bits.append("自己这边没有明显薄弱的棋")
    if attackable:
        g = attackable[0]
        x, y = g["centre"]
        bits.append(f"对方{loc_name(x, y, size)}那块 {g['size']} 子"
                    f"{group_status(g)}，是可攻击的目标")
    if safe:
        g = safe[0]
        x, y = g["centre"]
        bits.append(f"{loc_name(x, y, size)}这块 {g['size']} 子{group_status(g)}，"
                    f"不用再操心")
    return bits


def describe_contested(f: dict) -> str | None:
    """Name the area still genuinely up for grabs.

    "The centre has the most undecided points" is true in every opening and
    therefore useless. What matters is a region that is *mostly* decided but
    still has a live pocket, i.e. a real fight -- or, if the whole board is
    still open, saying so once.
    """
    und = f.get("region_undecided")
    if not und:
        return None
    total = sum(und)
    board = f.get("board_points") or 361
    if total / board > 0.8:
        return "全局绝大多数地方都还没定，现在谈具体地盘为时过早"
    # rank regions by how much of them is contested, ignoring trivial pockets
    region_size = f.get("region_size") or [0] * 9
    best_i, best_val = None, 0
    for i, u in enumerate(und):
        if u < 6:
            continue
        sz = region_size[i] or 1
        frac = u / sz
        if frac > best_val:
            best_i, best_val = i, frac
    if best_i is None:
        return "各处大体都已定型，剩下的是官子"
    return (f"还没定的是{REGION_NAMES[best_i]}一带，"
            f"{und[best_i]} 个点归属未定（占该区域的 {100*best_val:.0f}%）")


def describe_effect(f: dict, delta_key: str = "ownership_delta_full",
                    min_total: float = 3.0) -> str | None:
    """Name the specific points a move converts, not just a whole region.

    Requires a meaningful total change: "4 points moved by 0.25 each, about 1
    point" is noise dressed up as an explanation, so it is suppressed.
    """
    d = f.get(delta_key)
    if not d:
        return None
    mover = f["mover"]
    sign = 1.0 if mover == "B" else -1.0
    size = f.get("board_size", 19)
    scored = sorted(((sign * v, i) for i, v in enumerate(d)), reverse=True)
    top = [(v, i) for v, i in scored if v >= 0.25][:6]
    if len(top) < 2:
        return None
    total = sum(v for v, _ in top)
    if total < min_total:
        return None
    names = [loc_name(i % size, i // size, size) for _, i in top[:4]]
    side = "黑" if mover == "B" else "白"
    return (f"{'、'.join(names)} 一带 {len(top)} 个点由未定转为{side}方领地"
            f"（约 {total:.0f} 目）")


def grid_after(moves: list[list[str]], upto: int, size: int = 19):
    """Replay the first `upto` moves and return the resulting grid, so the
    explanation can be given the actual board at that turn."""
    from goban import Goban, from_gtp
    g = Goban(size)
    for color, mv in moves[:upto]:
        c = BLACK if color == "B" else WHITE
        if str(mv).lower() == "pass":
            g.play_pass(c)
            continue
        pos = from_gtp(mv, size)
        if pos:
            ok, _ = g.is_legal(c, pos[0], pos[1])
            if ok:
                g.play(c, pos[0], pos[1])
    return g.grid


def render_principle(f: dict) -> str | None:
    """State the general principle this position illustrates.

    Earlier versions only reported numbers, and every position produced almost
    identical sentences -- which is why the explanation read as monotonous and
    was hard to learn from. A number tells you what happened here; a principle
    is what a student can carry to the next game.
    """
    und = f.get("undecided_points")
    board = f.get("board_points") or 361
    open_ratio = (und / board) if und is not None else None
    tc = f.get("tenuki_cost")
    rank = f.get("human_rank")
    loss = f.get("loss")
    groups = f.get("groups") or []
    mover = f["mover"]
    theirs = [g for g in groups
              if g["color"] == (BLACK if mover == "B" else WHITE)]
    foes = [g for g in groups
            if g["color"] != (BLACK if mover == "B" else WHITE)]
    weak_own = [g for g in theirs if g["size"] >= 3 and g["outlook"] < -0.2]
    weak_foe = [g for g in foes if g["size"] >= 3 and g["outlook"] < -0.2]

    # the most valuable lesson: a move this level plays out of habit, which loses
    if rank is not None and rank <= 3 and loss is not None and loss >= 3:
        return (f"这个水平的人多数都会下这一手，可它要亏 {loss:.1f} 目——属于"
                f"「习惯性下法」。要改的不是这一盘，是以后碰到类似形状时的第一反应。")
    if weak_own and (tc is None or tc < 3):
        return ("有弱棋先处理弱棋。抢大场之前先看自己哪块还没安顿——"
                "弱棋被人先动手，损失往往比抢一个大场大得多。")
    if weak_foe and not weak_own:
        return ("对方有薄棋时，攻击本身就是最大的便宜：一边攻一边围，"
                "比单纯去抢大场效率高得多。")
    if tc is not None and tc >= 3:
        return ("急所先于大场。这一手脱先要亏不少目，说明这一带是必须应的地方——"
                "大场可以等，急所不能等。")
    if tc is not None and tc < 1 and open_ratio is not None and open_ratio > 0.5:
        return ("布局记住顺序：先角、后边、再中腹。现在角和边大多还空着，"
                "先占哪一处差别不大，但顺序颠倒就会吃亏。")
    if tc is not None and tc < 1:
        return ("两处价值相当时不必纠结，挑一个就行——真正拉开差距的不是这一步选了哪边，"
                "而是后面几十步怎么应对。")
    return None


def render_teaching(f: dict) -> str:
    """Narrative explanation: what the board looks like, where the key point is,
    what the move actually did, and what to play instead.

    The bullet-list version reported numbers without ever describing the
    position, which is why it read like a statistics dump rather than a lesson.
    This one is organised the way a teacher talks.
    """
    size = f.get("board_size", 19)
    mover = f["mover"]
    who = "黑" if mover == "B" else "白"
    opp = "白" if mover == "B" else "黑"
    P: list[str] = []

    # ---- header
    tags = []
    if f.get("is_best"):
        tags.append("AI 首选")
    if f.get("loss") is not None and f["loss"] >= 2:
        tags.append(f"亏 {f['loss']:.1f} 目")
    rank = f.get("human_rank")
    if rank is not None and rank <= 3 and (f.get("loss") or 0) >= 3:
        tags.append("本段位通病")
    if f.get("reasoning_divergence"):
        tags.append("AI 在这里算过")
    head = f"{who}第 {f['move_number']} 手　{fmt_move(f['played'])}"
    if tags:
        head += "　【" + "｜".join(tags) + "】"
    P.append(head)

    # ---- 局面
    board_bits = describe_board(f)
    contested = describe_contested(f)
    if board_bits or contested:
        P.append("")
        P.append("【局面】" + "；".join(board_bits + ([contested] if contested else []))
                 + "。")

    # ---- 要害
    tc = f.get("tenuki_cost")
    alt = fmt_move(f.get("alt_region_move"))
    if tc is not None:
        if tc >= 3:
            P.append(f"【要害】当前最要紧的地方不在这儿——若改下 {alt} 那个方向，"
                     f"要亏约 {tc:.1f} 目。这一带是现在最大的地方。")
        elif tc >= 1:
            P.append(f"【要害】{alt} 那个方向与这里相差约 {tc:.1f} 目，"
                     f"两处都值得下，但这里更急一点。")
        else:
            P.append(f"【要害】这里和 {alt} 那个方向差别不到 {abs(tc):.1f} 目，"
                     f"都属于大场，先下哪边是风格问题。")

    # ---- 这手做了什么
    if f.get("loss") is not None:
        l = f["loss"]
        if l < 0.5:
            verdict = "这一手与 AI 的首选基本一致，没有问题"
        elif l < 2:
            verdict = f"这一手比首选略差（约 {l:.1f} 目），不算错但不够好"
        elif l < 5:
            verdict = f"这一手比首选差约 {l:.1f} 目，是一处失误"
        else:
            verdict = f"这一手比首选差约 {l:.1f} 目，是这盘棋的关键失误之一"
        est = "（这个数字是估算的）" if f.get("loss_estimated") else ""
        line = f"【这手】{verdict}{est}。"
        if f.get("pv"):
            line += " 之后 AI 预计会这样走：" + " → ".join(
                fmt_move(m) for m in f["pv"][:5]) + "。"
        P.append(line)
    elif not f.get("played_searched"):
        P.append("【这手】这一手不在 AI 搜索到的候选点里，无法给出精确的目差。")

    # ---- 该怎么下
    if f.get("best_move") and not f.get("is_best"):
        b = f["ai_top"][0] if f["ai_top"] else None
        line = f"【该怎么下】{fmt_move(f['best_move'])}"
        if b and b.get("lead") is not None:
            line += f"（{describe_lead(b['lead'], who)}）"
        line += "。"
        eff = describe_effect(f)
        if eff:
            line += f" 它的作用是：{eff}。"
        P.append(line)

    # ---- 你和 AI 差在哪
    if f.get("human_top"):
        ht = f["human_top"][0]
        prof = f["human_profile"]
        pct = 100 * ht["prob"]
        seg = [f"【对照】{prof} 这个水平的人 {pct:.0f}% 会下 "
               f"{fmt_move(ht['move'])}"]
        if ht.get("loss") is not None and ht["loss"] >= 1:
            seg.append(f"，而那手 AI 判断要亏 {ht['loss']:.1f} 目")
        seg.append("。")
        if rank is not None and f.get("human_p_played") is not None:
            if rank <= 3:
                seg.append("实战这一手也正是该水平最常见的下法之一——"
                           "说明这是这个水平普遍的看法，值得专门改。")
            elif rank >= 8:
                seg.append(f"实战这一手在该水平里很罕见（排第 {rank} 位）"
                           f"——不是老师教得出来的手。")
        P.append("".join(seg))

    # ---- 棋理与术语
    P.extend(principle_lines(f))
    P.append("")
    P.append("─" * 66)
    return "\n".join(P)


def principle_lines(f: dict) -> list[str]:
    """The takeaway block: the transferable principle, then the terminology.
    Shared so both renderers stay in step."""
    out: list[str] = []
    p = render_principle(f)
    if p:
        out.append("【棋理】" + p)
    hints = term_hints(f)
    if hints:
        out.append("【术语】" + "；".join(hints) + "。")
    return out


def render_teaching_position(f: dict) -> str:
    """Narrative explanation of a position nobody has moved in yet -- the study
    mode's case. Same teaching shape as render_teaching: board, key point,
    recommendation and why."""
    who = "黑" if f["mover"] == "B" else "白"
    P = [f"轮到{who}走"]

    board_bits = describe_board(f)
    contested = describe_contested(f)
    if board_bits or contested:
        P.append("")
        P.append("【局面】" + "；".join(board_bits + ([contested] if contested else []))
                 + "。")

    if f.get("best_move"):
        b = f["ai_top"][0] if f["ai_top"] else None
        line = f"【为什么下这里】AI 选 {fmt_move(f['best_move'])}"
        if b and b.get("lead") is not None:
            line += f"（{describe_lead(b['lead'], who)}）"
        line += "。"
        eff = describe_effect(f)
        if eff:
            line += f" 它办到的事：{eff}。"
        P.append(line)

    alts = [x for x in f["ai_top"][1:4] if x.get("lead") is not None]
    if alts and f["ai_top"][0].get("lead") is not None:
        top_lead = f["ai_top"][0]["lead"]
        parts = "、".join(
            f"{fmt_move(x['move'])}（差 {top_lead - x['lead']:.1f} 目）"
            for x in alts if abs(top_lead - x["lead"]) >= 0.5)
        if parts:
            P.append(f"【其他选择】{parts}。差得越多越说明首选有明确理由，"
                     f"差得少就只是风格问题。")

    tc = f.get("tenuki_cost")
    if tc is not None:
        alt = fmt_move(f.get("alt_region_move"))
        if tc >= 3:
            P.append(f"【紧迫程度】很急。若改下 {alt} 那个方向，要亏约 {tc:.1f} 目"
                     f"——这一带必须应。")
        elif tc >= 1:
            P.append(f"【紧迫程度】中等。改投 {alt} 那个方向约亏 {tc:.1f} 目。")
        else:
            P.append(f"【紧迫程度】不急。这里和 {alt} 那个方向差别不到 "
                     f"{abs(tc):.1f} 目，大场很多，先下哪边都行。")

    if f.get("pv"):
        P.append("【预计后续】" + " → ".join(fmt_move(m) for m in f["pv"][:6]))

    if f.get("reasoning_divergence"):
        P.append(f"【AI 算过】网络第一感是 {fmt_move(f.get('policy_top'))}，"
                 f"搜索之后改成了 {fmt_move(f.get('best_move'))}。"
                 f"这种分歧点往往正是局面要害。")

    if f.get("human_top"):
        ht = f["human_top"][0]
        P.append(f"【对照】{f['human_profile']} 这个水平的人 {100*ht['prob']:.0f}% "
                 f"会下 {fmt_move(ht['move'])}"
                 + (f"，AI 判断那手要亏 {ht['loss']:.1f} 目"
                    if ht.get("loss") is not None and ht["loss"] >= 1 else "")
                 + "。")
        bm = f.get("best_move")
        if bm:
            share = next((h["prob"] for h in f["human_top"] if h["move"] == bm), None)
            if share is None:
                P.append(f"【差距】AI 建议的 {bm} 不在该水平常下的几种里，"
                         f"这一手值得专门记住。")
            elif share >= 0.4:
                # most players of this level already find it -- saying they must
                # "catch up" on it would be nonsense
                P.append(f"【差距】{bm} 本来就是该水平的主流下法"
                         f"（{100*share:.0f}%）——不难想到，难的是每次都想到。")
            elif share >= 0.15:
                P.append(f"【差距】{bm} 在该水平里只有 {100*share:.0f}% 的人会下，"
                         f"属于能想到就赚到的一手。")
            else:
                P.append(f"【差距】AI 建议的 {bm} 在该水平落子倾向里只占 "
                         f"{100*share:.1f}%——这正是你要补上的那部分。")

    # three-way ladder: what a strong human does with the same position
    if f.get("ref_top"):
        rt = f["ref_top"][0]
        line = (f"【高手对照】{f.get('ref_profile')} 这个层次的人 "
                f"{100*rt['prob']:.0f}% 会下 {fmt_move(rt['move'])}")
        bm = f.get("best_move")
        if bm and f.get("ref_share_best") is not None:
            if f["ref_share_best"] >= 0.25:
                line += f"，AI 的首选 {bm} 也在他们的主要选择里" \
                        f"（{100*f['ref_share_best']:.0f}%）"
            else:
                line += (f"；AI 的首选 {bm} 在他们那里只占 "
                         f"{100*f['ref_share_best']:.1f}%，"
                         f"说明这一手连高手也容易忽略")
        line += "。"
        P.append(line)

    P.extend(principle_lines(f))
    return "\n".join(P)


def render_turn(f: dict, show_ai_moves: bool = True) -> str:
    """Template language layer. Every sentence maps to a computed fact."""
    L: list[str] = []
    who = "黑" if f["mover"] == "B" else "白"
    other = "白" if f["mover"] == "B" else "黑"

    head = f"第 {f['move_number']} 手　{who} {fmt_move(f['played'])}"
    tags = []
    if f.get("is_best"):
        tags.append("AI 首选")
    if f.get("loss") is not None and f["loss"] >= 2:
        tags.append(f"亏 {f['loss']:.1f} 目")
    ex = f.get("surprise_excess_bits")
    rank = f.get("human_rank")
    # A rank-2 move is common no matter how flat the distribution is, so rank
    # has to gate the "surprising" tag, not the excess on its own.
    if rank is not None and (rank >= 8 or (ex is not None and ex >= 2.5 and rank >= 4)):
        tags.append("很意外")
    if rank is not None and rank <= 3 and f.get("loss") is not None and f["loss"] >= 3:
        tags.append("本段位通病")
    if f.get("reasoning_divergence"):
        tags.append("有推理痕迹")
    if is_decided(f):
        tags.append("局面已定")
    if tags:
        head += "　【" + "｜".join(tags) + "】"
    L.append(head)

    # what the AI wants here
    if f.get("best_move"):
        b = f["ai_top"][0] if f["ai_top"] else None
        txt = f"AI 认为这里最好的是 {fmt_move(f['best_move'])}"
        if b and b.get("lead") is not None:
            txt += f"（{describe_lead(b['lead'], who)}）"
        if f.get("is_best"):
            txt += "——就是这一手。"
        else:
            txt += f"，实战这手不在 AI 的首选之列。"
        L.append("  · " + txt)

    # the cost of the played move
    if f.get("loss") is not None:
        est = "（估算）" if f.get("loss_estimated") else ""
        if f["loss"] < 0.5:
            L.append(f"  · 代价：与首选基本持平（差 {f['loss']:.1f} 目{est}），属于可下之着。")
        elif f["loss"] < 2:
            L.append(f"  · 代价：比首选差约 {f['loss']:.1f} 目{est}，略亏。")
        elif f["loss"] < 5:
            L.append(f"  · 代价：比首选差约 {f['loss']:.1f} 目{est}，是一处失误。")
        else:
            L.append(f"  · 代价：比首选差约 {f['loss']:.1f} 目{est}，是明显的问题手。")
    elif not f.get("played_searched"):
        # Do NOT guess why it was not searched -- it may simply have fallen
        # outside the top candidates at this visit count.
        L.append("  · 这一手不在 AI 搜索到的候选点里，无法给出精确目差。")

    # what the best move is trying to achieve
    g = describe_region_gain(f)
    if g and show_ai_moves:
        L.append(f"  · 首选的意图（据归属预测推断）：{g}。")

    # urgency
    if f.get("tenuki_cost") is not None:
        tc = f["tenuki_cost"]
        alt = fmt_move(f.get("alt_region_move"))
        if tc >= 3:
            L.append(f"  · 紧迫度：高。若改到别处（如 {alt}），要亏约 {tc:.1f} 目——"
                     f"这一带是当前最大的地方，属于必须应的地方。")
        elif tc >= 1:
            L.append(f"  · 紧迫度：中。改投别处（如 {alt}）约亏 {tc:.1f} 目。")
        else:
            L.append(f"  · 紧迫度：低。别处（如 {alt}）与这里差别不到 {abs(tc):.1f} 目，"
                     f"大场很多，属于风格选择。")

    # follow-up
    if f.get("pv"):
        pv = " → ".join(fmt_move(m) for m in f["pv"][:5])
        L.append(f"  · 后续预期：{pv}")

    # reasoning trace
    if f.get("reasoning_divergence"):
        L.append(f"  · 推理痕迹：网络第一感是 {fmt_move(f.get('policy_top'))}，"
                 f"搜索之后改成了 {fmt_move(f.get('best_move'))}。"
                 f"这类分歧点往往正是局面要害——AI 在这里「算过」。")

    # human contrast -- the teaching payload
    if f.get("human_top"):
        ht = f["human_top"][0]
        prof = f["human_profile"]
        txt = (f"  · 你这个水平（{prof}）最可能下 {fmt_move(ht['move'])}"
               f"（{100*ht['prob']:.0f}% 的人会这样下）")
        if ht.get("loss") is not None:
            txt += f"，AI 判断那手要亏 {ht['loss']:.1f} 目"
        txt += "。"
        if f.get("human_rank") and f.get("human_p_played") is not None:
            rank = f["human_rank"]
            txt += (f"实战这手在该水平的落子倾向里排第 {rank} 位"
                    f"（{100*f['human_p_played']:.1f}%）")
            # keep this consistent with the rank, not with the raw excess
            if rank <= 3:
                txt += "——是该水平最常见的几种下法之一。"
            elif rank <= 8:
                txt += "——在这个水平里已算少见。"
            else:
                txt += "——在该水平的落子里非常罕见。"
        L.append(txt)
        if len(f["human_top"]) > 1:
            others = "、".join(
                f"{fmt_move(h['move'])}（{100*h['prob']:.0f}%"
                + (f"，亏 {h['loss']:.1f} 目" if h.get("loss") is not None else "")
                + "）"
                for h in f["human_top"][1:3]
                if h["prob"] >= 0.03 and h["move"] != f["played"])
            if others:
                L.append(f"  · 该水平的其它常见选择：{others}")

    if f.get("kl_human_ai") is not None:
        kl = f["kl_human_ai"]
        if kl >= 2.0:
            L.append(f"  · 人机分歧：很大（KL {kl:.2f} bit）——"
                     f"这个局面 AI 与人类看到的重点几乎不在同一处。")
        elif kl >= 1.0:
            L.append(f"  · 人机分歧：中等（KL {kl:.2f} bit）。")
    return finalize(L, f)


def fill_missing_losses(facts: list[dict], results: dict) -> None:
    """Recover the cost of moves the search never visited.

    A move that is both bad and very unlikely for a human never gets searched,
    so it has no moveInfo and no direct score loss. But the analysis of the NEXT
    turn evaluates exactly the position that move produced: its rootInfo
    scoreLead is the value after the played move, while this turn's best
    moveInfo scoreLead is the value after the best move. The difference is the
    cost. Marked as estimated because rootInfo is smoothed over all visits.
    """
    for f in facts:
        if f.get("loss") is not None:
            continue
        cur = results.get(f["turn"])
        nxt = results.get(f["turn"] + 1)
        if not cur or not nxt:
            continue
        mis = cur.get("moveInfos") or []
        ri_next = nxt.get("rootInfo") or {}
        if not mis or "scoreLead" not in ri_next:
            continue
        best_black = mis[0].get("scoreLead")
        after_black = ri_next.get("scoreLead")
        if best_black is None or after_black is None:
            continue
        sign = 1.0 if f["mover"] == "B" else -1.0
        f["loss"] = max(0.0, sign * (best_black - after_black))
        f["loss_estimated"] = True
        f["after_lead_mover"] = sign * after_black


def is_decided(f: dict) -> bool:
    lead = f.get("root_lead_mover")
    return lead is not None and abs(lead) > DECIDED_LEAD


def build_position_facts(resp: dict, profile: str, size: int = 19,
                         grid=None) -> dict:
    """Facts about the CURRENT position, where nobody has moved yet. The study
    mode analyses the position after the last stone, so the engine's
    recommendation is for the player about to move -- treating it as "the move
    just played" would describe the wrong thing entirely.

    Pass `grid` (goban.grid) to get the group-by-group read of the board.
    """
    return build_turn_facts(-1, resp, "", profile, size, grid=grid)


def render_position(f: dict) -> str:
    """Explain the position waiting to be played, not a move already played."""
    L: list[str] = []
    who = "黑" if f["mover"] == "B" else "白"
    L.append(f"轮到{who}走")

    if f.get("best_move"):
        b = f["ai_top"][0] if f["ai_top"] else None
        txt = f"  · AI 建议下 {fmt_move(f['best_move'])}"
        if b and b.get("lead") is not None:
            txt += f"（{describe_lead(b['lead'], who)}）"
        L.append(txt)
        alts = [x for x in f["ai_top"][1:4] if x.get("lead") is not None]
        if alts and f["ai_top"][0].get("lead") is not None:
            top_lead = f["ai_top"][0]["lead"]
            parts = "、".join(
                f"{fmt_move(x['move'])}（差 {top_lead - x['lead']:.1f} 目）"
                for x in alts if abs(top_lead - x["lead"]) >= 0.3)
            if parts:
                L.append(f"  · 备选：{parts}")

    g = describe_region_gain(f)
    if g:
        L.append(f"  · 首选意图（据归属预测推断）：{g}。")

    if f.get("tenuki_cost") is not None:
        tc = f["tenuki_cost"]
        alt = fmt_move(f.get("alt_region_move"))
        if tc >= 3:
            L.append(f"  · 紧迫度：高。若改到别处（如 {alt}），要亏约 {tc:.1f} 目——"
                     f"这一带是当前最大的地方。")
        elif tc >= 1:
            L.append(f"  · 紧迫度：中。改投别处（如 {alt}）约亏 {tc:.1f} 目。")
        else:
            L.append(f"  · 紧迫度：低。别处（如 {alt}）与这里差别不到 {abs(tc):.1f} 目，"
                     f"大场很多，属于风格选择。")

    if f.get("pv"):
        L.append("  · 后续预期：" + " → ".join(fmt_move(m) for m in f["pv"][:6]))

    if f.get("reasoning_divergence"):
        L.append(f"  · 推理痕迹：网络第一感是 {fmt_move(f.get('policy_top'))}，"
                 f"搜索之后改成了 {fmt_move(f.get('best_move'))}。"
                 f"这类分歧点往往正是局面要害——AI 在这里「算过」。")

    if f.get("human_top"):
        ht = f["human_top"][0]
        txt = (f"  · 你这个水平（{f['human_profile']}）最可能下 {fmt_move(ht['move'])}"
               f"（{100*ht['prob']:.0f}% 的人会这样下）")
        if ht.get("loss") is not None:
            txt += f"，AI 判断那手要亏 {ht['loss']:.1f} 目"
        txt += "。"
        L.append(txt)
        if len(f["human_top"]) > 1:
            others = "、".join(
                f"{fmt_move(h['move'])}（{100*h['prob']:.0f}%"
                + (f"，亏 {h['loss']:.1f} 目" if h.get("loss") is not None else "")
                + "）"
                for h in f["human_top"][1:3] if h["prob"] >= 0.03)
            if others:
                L.append(f"  · 该水平的其它常见选择：{others}")
        bm = f.get("best_move")
        if bm:
            share = next((h["prob"] for h in f["human_top"] if h["move"] == bm), None)
            if share is not None:
                L.append(f"  · AI 建议的 {bm} 在该水平的落子倾向里占 {100*share:.1f}%。")
            else:
                L.append(f"  · AI 建议的 {bm} 不在该水平最常见的几种下法里。")

    if f.get("kl_human_ai") is not None:
        kl = f["kl_human_ai"]
        if kl >= 2.0:
            L.append(f"  · 人机分歧：很大（KL {kl:.2f} bit）——"
                     f"这个局面 AI 与人类看到的重点几乎不在同一处。")
        elif kl >= 1.0:
            L.append(f"  · 人机分歧：中等（KL {kl:.2f} bit）。")
    return finalize(L, f)


def explain_priority(f: dict) -> float:
    """Rank which turns are worth explaining. Surprise drives the explanation:
    cost matters, but so does how unexpected the turn is *for this rank* and how
    differently human and AI read the position."""
    if is_decided(f):
        # in a decided game further point swings are not a lesson
        return 0.0
    score = 0.0
    if f.get("loss") is not None:
        score += min(f["loss"], 20.0) / 4.0            # up to ~5
    excess = f.get("surprise_excess_bits")
    if excess is not None:
        score += min(max(excess, 0.0), 8.0) / 2.5      # up to ~3.2
    if f.get("kl_human_ai") is not None:
        score += min(f["kl_human_ai"], 4.0) / 2.0      # up to ~2
    if f.get("reasoning_divergence"):
        score += 0.7
    if f.get("tenuki_cost") is not None and f["tenuki_cost"] >= 3:
        score += 0.5
    # The single most valuable teaching signal: a move the student's own level
    # plays most of the time, which is nonetheless a real mistake. That is a
    # systematic misconception, not one bad day.
    rank = f.get("human_rank")
    if rank is not None and rank <= 3 and f.get("loss") is not None and f["loss"] >= 3:
        score += 1.2
    return score


def render_report(facts: list[dict], meta: dict, top_n: int = 8) -> str:
    L: list[str] = []
    L.append("=" * 74)
    L.append("围棋 AI 讲解报告")
    L.append("=" * 74)
    L.append(f"对局　　　: {meta.get('black_name')} (黑) vs {meta.get('white_name')} (白)")
    L.append(f"学生水平　: {meta.get('student_profile')}")
    L.append(f"引擎　　　: KataGo {meta.get('version')}")
    L.append(f"贴目　　　: {meta.get('komi')}　规则: {meta.get('rules')}")
    L.append(f"分析手数　: {len(facts)}")
    L.append("")
    L.append("说明：本报告的判断一律以 AI 的「目差」为准。实测本网络在低搜索量下")
    L.append("      胜率输出校准不佳（贴目差 1 目，胜率会跳 20 个百分点），故不采用。")
    L.append("")

    decided = [f for f in facts if is_decided(f)]
    scored = sorted([f for f in facts if not is_decided(f)],
                    key=explain_priority, reverse=True)
    if not scored:
        scored = sorted(facts, key=explain_priority, reverse=True)

    L.append("-" * 74)
    L.append(f"值得讲的手（按「代价 + 意外度 + 人机分歧」排序，前 {top_n} 手）")
    L.append("-" * 74)
    L.append("意外度 = 该手的意外程度 减去 该水平落子分布的熵。")
    L.append("         也就是说，它衡量的是「比你这个水平的典型落子罕见多少」，")
    L.append("         而不是绝对意外值——后者的基线本来就有 4~5 bit。")
    L.append("")
    L.append(f"{'手数':>5} {'方':>2} {'落子':>6} {'亏目':>7} {'意外(超熵)':>11} "
             f"{'人机KL':>7} {'人类排名':>9}  综合")
    for f in scored[:top_n]:
        loss = f"{f['loss']:.1f}" if f.get("loss") is not None else "-"
        ex = (f"{f['surprise_excess_bits']:+.1f}"
              if f.get("surprise_excess_bits") is not None else "-")
        kl = f"{f['kl_human_ai']:.2f}" if f.get("kl_human_ai") is not None else "-"
        hr = f"#{f['human_rank']}" if f.get("human_rank") else "-"
        L.append(f"{f['move_number']:>5} {f['mover']:>2} {fmt_move(f['played']):>6} "
                 f"{loss:>7} {ex:>11} {kl:>7} {hr:>9}  {explain_priority(f):.2f}")
    L.append("")
    if decided:
        L.append(f"（另有 {len(decided)} 手处于已分出胜负的局面，已从讲解中排除——"
                 f"那里的目差波动不构成教学内容。）")
        L.append("")

    for f in scored[:top_n]:
        L.append("-" * 74)
        L.append(render_turn(f))
        L.append("")

    # --------------------------------------------------- critical & good moves
    # Teaching the best play means showing what a good move accomplishes, not
    # only what went wrong. But "good" alone is not enough to be worth a
    # section: a move that every player of that level finds (human rank #1 at
    # 99%) is a FORCED point, not an insight. The two are labelled separately so
    # a mandatory move is never presented as a clever idea.
    playable = [f for f in facts if not is_decided(f)
                and f.get("loss") is not None and f["loss"] < 1.0]

    critical = [f for f in playable if (f.get("tenuki_cost") or 0) >= 5.0]
    critical.sort(key=lambda x: -(x.get("tenuki_cost") or 0))

    insights = [f for f in playable
                if f.get("human_p_played") is not None
                and f["human_p_played"] < 0.25
                and (f.get("human_rank") or 99) >= 4]
    insights.sort(key=lambda x: -((x.get("tenuki_cost") or 0)
                                  + (x.get("surprise_excess_bits") or 0)))

    if critical:
        L.append("=" * 74)
        L.append(f"必争的要点（AI 首选，且脱先代价很大）前 {min(top_n, len(critical))} 手")
        L.append("=" * 74)
        L.append("这些手当时非走不可——换到别处要亏很多目。")
        L.append("学的是「什么时候必须应」：看脱先代价的数字。")
        L.append("")
        for f in critical[:top_n]:
            L.append("-" * 74)
            L.append(render_turn(f))
            L.append("")

    if insights:
        L.append("=" * 74)
        L.append(f"有想法的一手（下得好，而且不是这个水平通常会走的）"
                 f"前 {min(top_n, len(insights))} 手")
        L.append("=" * 74)
        L.append("这些手走对了，而且不属于该水平的惯常下法——属于真正学到的东西；")
        L.append("值得回想当时是看到了什么才走出来的。")
        L.append("")
        for f in insights[:top_n]:
            L.append("-" * 74)
            L.append(render_turn(f))
            L.append("")

    return "\n".join(L)
