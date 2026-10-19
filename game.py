"""
game.py — เกมเดินสำรวจ "สถาบันนักผจญภัยฝึกหัด" (Tkinter) เหมือนเวอร์ชัน HTML
มี 2 ฉาก (ภาพรวมสถาบัน / อาคารเรียนหลัก), NPC พร้อมบทสนทนา, เหตุการณ์เนื้อเรื่อง 6 เหตุการณ์ และตอนจบ S-F
เครื่องยนต์จำลองโซ่อุปทานคือ engine.py (SimPy + SQLite)
ติดตั้ง: pip install pillow simpy pandas numpy matplotlib   |   รัน: python game.py
"""
import os
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from engine import JOBS, PRODUCTS, RECRUIT_COST, TIERS, World, base_stat, power
from story import END, EV, INTRO, SCENES, TERM_INTRO

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
except Exception:
    FigureCanvasTkAgg = None

HERE = os.path.dirname(os.path.abspath(__file__))
VW, VH = 1000, 552
FONT = ("Tahoma", 10)
GOLD, PANEL, INK = "#d9a441", "#2a1f33", "#f3e7c9"
SPOT = {s["k"]: s for sc in SCENES.values() for s in sc["spots"]}
TERM = lambda w: "เทอมต้น — พิสูจน์ตัวเอง" if w <= 4 else "เทอมกลาง — เงาจางในดันเจี้ยน" if w <= 8 else "เทอมปลาย — ทางเลือกสุดท้าย"


class Game(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("สถาบันนักผจญภัยฝึกหัด — Supply Chain Simulation")
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.w, self.vis, self.done_mc = World(), set(), set()
        self.raw = {n: Image.open(os.path.join(HERE, "assets", n)).convert("RGBA")
                    for n in ("overview.jpg", "inner.jpg", "hero.png", "npc.png")}
        self.cv = tk.Canvas(self, width=VW, height=VH, highlightthickness=0, bg="#14101c")
        self.cv.pack()
        self.hero = self.cv.create_image(0, 0, anchor="s", tags="hero")
        self.hud = self.cv.create_text(12, 8, anchor="nw", fill=INK, font=("Tahoma", 10, "bold"))
        self.promptbg = self.cv.create_rectangle(0, 0, 0, 0, fill=GOLD, outline="")
        self.prompt = self.cv.create_text(VW // 2, VH - 24, fill="#2a1a05", font=("Tahoma", 13, "bold"))
        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x")
        for text, cmd in [("⏭ จบสัปดาห์ (T)", self.end_week), ("📋 ทีม NPC", lambda: self.open_node("hr")),
                          ("📊 แดชบอร์ด", lambda: self.open_node("dash")), ("🔄 เริ่มใหม่", self.restart)]:
            tk.Button(bar, text=text, command=cmd, font=FONT, bg=GOLD, relief="flat", padx=10).pack(side="left", padx=6, pady=5)
        tk.Label(bar, text="เดิน: WASD/ลูกศร · โต้ตอบ: E/Space", bg=PANEL, fg=INK, font=FONT).pack(side="right", padx=8)
        self.dir, self.t, self.keys, self.modal, self.near, self.keep = 0, 0.0, set(), None, None, []
        self.bind("<KeyPress>", self.kdown)
        self.bind("<KeyRelease>", lambda e: self.keys.discard(e.keysym.lower()))
        self.load_scene("over")
        self.after(200, lambda: self.seq(INTRO))
        self.after(33, self.tick)

    # ---------- ฉาก ----------
    def P(self, x, y):
        return self.ox + x * self.z, self.oy + y * self.z

    def load_scene(self, key, pos=None):
        sc = SCENES[key]
        self.scene, self.sc = key, sc
        self.z = min(VW / sc["w"], VH / sc["h"])
        self.ox, self.oy = (VW - sc["w"] * self.z) / 2, (VH - sc["h"] * self.z) / 2
        self.px, self.py = pos or sc["start"]
        self.cv.delete("scene")
        self.keep = []
        bg = ImageTk.PhotoImage(self.raw[sc["img"]].convert("RGB").resize((int(sc["w"] * self.z), int(sc["h"] * self.z))))
        self.keep.append(bg)
        self.cv.create_image(self.ox, self.oy, image=bg, anchor="nw", tags="scene")
        self.marks = {}
        r = 30 * self.z
        for s in sc["spots"]:
            x, y = self.P(s["x"], s["y"])
            if s["kind"] == "npc":
                d = max(8, int(2 * r))
                face = self.raw["npc.png"].crop((s["p"] * 96, 0, s["p"] * 96 + 96, 96)).resize((d, d))
                mask = Image.new("L", (d, d), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
                face.putalpha(mask)
                im = ImageTk.PhotoImage(face)
                self.keep.append(im)
                self.cv.create_image(x, y, image=im, tags="scene")
                self.marks[s["k"]] = self.cv.create_oval(x - r, y - r, x + r, y + r, outline=GOLD, width=3, tags="scene")
            elif s["k"] not in ("exit",):
                rr = 9
                self.marks[s["k"]] = self.cv.create_oval(x - rr, y - rr, x + rr, y + rr, fill="#ffd54f", outline="#fff3c4", width=2, tags="scene")
        fw, fh = max(1, int(68 * self.z * 1.25)), max(1, int(77 * self.z * 1.25))
        sheet = self.raw["hero.png"]
        self.frames = [[ImageTk.PhotoImage(sheet.crop((f * 112, d * 128, f * 112 + 112, d * 128 + 128)).resize((fw, fh)))
                        for f in range(9)] for d in range(4)]
        for tag in ("hero",):
            self.cv.tag_raise(tag)
        for i in (self.promptbg, self.prompt, self.hud):
            self.cv.tag_raise(i)
        for k in self.marks:
            if SCENES[key]["name"] + k in self.vis and SPOT[k]["kind"] != "npc":
                self.cv.itemconfig(self.marks[k], fill="#4caf50")

    def goto(self, s):
        self.load_scene(s["to"], s.get("pos"))

    # ---------- วงจรเกม ----------
    def kdown(self, e):
        k = e.keysym.lower()
        if self.modal:
            return
        self.keys.add(k)
        if k in ("e", "space", "return") and self.near:
            self.interact(self.near)
        elif k == "t":
            self.end_week()

    def tick(self):
        self.t += 0.033
        mv = {"w", "a", "s", "d", "up", "down", "left", "right"} & self.keys
        if not self.modal and mv:
            k = self.keys
            dx = (1 if {"d", "right"} & k else 0) - (1 if {"a", "left"} & k else 0)
            dy = (1 if {"s", "down"} & k else 0) - (1 if {"w", "up"} & k else 0)
            if dx or dy:
                n = (dx * dx + dy * dy) ** .5
                b = self.sc["b"]
                self.px = min(b[2], max(b[0], self.px + dx / n * 7))
                self.py = min(b[3], max(b[1], self.py + dy / n * 7))
                self.dir = (2 if dx > 0 else 1) if dx and abs(dx) >= abs(dy) else (0 if dy > 0 else 3)
        moving = bool(mv) and not self.modal
        self.cv.itemconfig(self.hero, image=self.frames[self.dir][int(self.t * 11) % 9 if moving else 0])
        self.cv.coords(self.hero, *self.P(self.px, self.py))
        self.near, best = None, 85
        for s in self.sc["spots"]:
            d = ((s["x"] - self.px) ** 2 + (s["y"] - self.py) ** 2) ** .5
            if d < best:
                self.near, best = s["k"], d
        if self.near and not self.modal:
            s = SPOT[self.near]
            verb = {"go": "🚪 เข้า/ออก: ", "npc": "💬 คุยกับ ", "info": "✨ ดู: "}.get(s["kind"], "🏢 บริหาร: ")
            self.cv.itemconfig(self.prompt, text=f"{verb}{s['n']}  [E]")
            x0, y0, x1, y1 = self.cv.bbox(self.prompt)
            self.cv.coords(self.promptbg, x0 - 12, y0 - 5, x1 + 12, y1 + 5)
        else:
            self.cv.itemconfig(self.prompt, text="")
            self.cv.coords(self.promptbg, 0, 0, 0, 0)
        w, s = self.w, self.w.snapshot()
        wk = min(w.week + 1, w.weeks)
        self.cv.itemconfig(self.hud, text=f"{self.sc['name']} · สัปดาห์ {wk}/{w.weeks} · {TERM(wk)}\n"
                           f"📦 วัตถุดิบ {s['raw']} · 🏭 รอส่ง {s['central_goods']} · 🏪 ร้านค้า {s['shop']} · 🛏 หอพัก {s['dorm']}\n"
                           f"🤝 ไว้ใจ {w.trust} · 🔍 ปม {w.mystery} · 👥 {len(w.npcs)} คน")
        self.after(33, self.tick)

    # ---------- หน้าต่างโต้ตอบ ----------
    def popup(self, title, w=0, h=0):
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=PANEL)
        if w:
            win.geometry(f"{w}x{h}")
        else:
            win.minsize(620, 0)
        win.transient(self)
        win.update_idletasks()
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self.modal = win
        self.keys.clear()

        def close():
            self.modal = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close)
        win.close = close
        return win

    def label(self, parent, text, bold=False, fg=INK, wrap=580):
        l = tk.Label(parent, text=text, bg=PANEL, fg=fg, font=("Tahoma", 11 if bold else 10, "bold" if bold else "normal"),
                     justify="left", wraplength=wrap, anchor="w")
        l.pack(fill="x", padx=12, pady=3)
        return l

    def dialog(self, title, text, buttons=None):
        """buttons = [(ข้อความปุ่ม, ฟังก์ชันที่เรียกหลังปิด)]"""
        win = self.popup(title)
        self.label(win, title, True, GOLD)
        self.label(win, text)
        bs = buttons or [("ปิด", None)]

        def mk(f):
            def c():
                win.close()
                if f:
                    f()
            return c
        for t, f in bs:
            tk.Button(win, text=t, command=mk(f), bg=GOLD, relief="flat", anchor="w", justify="left", wraplength=560,
                      padx=10).pack(fill="x", padx=12, pady=3)
        tk.Label(win, bg=PANEL).pack()
        if len(bs) == 1:
            win.bind("<Escape>", lambda e: mk(bs[0][1])())
            win.protocol("WM_DELETE_WINDOW", mk(bs[0][1]))
        return win

    def seq(self, items, done=None):
        t, d = items[0]
        last = len(items) == 1
        self.dialog(t, d, [("เริ่มเกม" if last else "ต่อไป ▶", done if last else (lambda: self.seq(items[1:], done)))])

    def npc_status(self, p):
        n = self.w.npcs[p]
        return (f"\n\n[{TIERS[n.tier]} · ATK {n.atk} DEF {n.dfn} SPD {n.spd} ACC {n.acc}]\n"
                f"[งาน: {'🤕 พักฟื้น' if n.injured else JOBS[n.job]} · แรง {n.hp}/100]")

    # ---------- โต้ตอบกับจุดบนแผนที่ ----------
    def interact(self, key):
        s = SPOT[key]
        if s["kind"] not in ("npc", "go"):
            self.vis.add(self.sc["name"] + key)
            if key in self.marks:
                self.cv.itemconfig(self.marks[key], fill="#4caf50")
        if s["kind"] == "go":
            return self.goto(s)
        if s["kind"] in ("work", "store", "hr", "dash"):
            return self.open_node(key)
        if s["kind"] == "npc":
            mc = s.get("mc")
            if mc and key not in self.done_mc:
                def pick(o):
                    def f():
                        self.done_mc.add(key)
                        self.w.apply_fx(o[1], s["p"])
                        self.dialog(s["n"], o[2])
                    return f
                return self.dialog(s["n"], mc["q"], [(o[0], pick(o)) for o in mc["o"]])
            return self.dialog(s["n"], s["d"] + self.npc_status(s["p"]))
        self.dialog(s["n"], s["d"])

    def open_node(self, key):
        if self.modal:
            return
        s = SPOT[key]
        win = self.popup(s["n"], 700, 640)
        self.label(win, s["n"], True, GOLD)
        if s["d"]:
            self.label(win, s["d"])
        body = tk.Frame(win, bg=PANEL)
        body.pack(fill="both", expand=True)
        tk.Button(win, text="ปิด (Esc)", command=win.close, bg=GOLD, relief="flat", padx=16).pack(pady=8)
        win.bind("<Escape>", lambda e: win.close())
        {"work": self.node_work, "store": self.node_store, "hr": self.node_hr, "dash": self.node_dash}[s["kind"]](body, key, s.get("jobs", []))

    def clear(self, body):
        for c in body.winfo_children():
            c.destroy()

    def roster(self, body, jobs, refresh):
        for n in self.w.npcs:
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", padx=10, pady=2)
            fit = "  ".join(f"{JOBS[j][:2]}เหมาะ {base_stat(n, j) * 100:.0f}%" for j in jobs)
            tk.Label(row, text=f"{n.name} · {TIERS[n.tier]} · ATK {n.atk} DEF {n.dfn} SPD {n.spd} ACC {n.acc} · แรง {n.hp}\n"
                               f"งานตอนนี้: {'🤕 พักฟื้น' if n.injured else JOBS[n.job]}   {fit}",
                     bg=PANEL, fg=INK, font=("Tahoma", 9), justify="left", anchor="w").pack(side="left", fill="x", expand=True)
            for j in jobs:
                def go(n=n, j=j):
                    n.job = j
                    refresh()
                tk.Button(row, text="✔ อยู่ที่นี่" if n.job == j else f"ส่งมา {JOBS[j]}", command=go, font=("Tahoma", 9),
                          state="disabled" if n.injured or n.job == j else "normal", relief="flat", bg=GOLD).pack(side="right", padx=2)
            if jobs:
                tk.Button(row, text="💤 พัก", font=("Tahoma", 9), relief="flat", bg="#8a7a55",
                          state="disabled" if n.injured or n.job == "rest" else "normal",
                          command=lambda n=n: (setattr(n, "job", "rest"), refresh())).pack(side="right", padx=2)

    def node_work(self, body, key, jobs):
        def draw():
            self.clear(body)
            if key in ("potion", "craft", "tool"):
                cap = round(sum(power(n, key) for n in self.w.npcs if n.active_job == key) * 10)
                self.label(body, f"กำลังผลิตต่อสัปดาห์ (ประมาณ): {cap} หน่วย (ใช้วัตถุดิบ 1 : 1)", True)
            else:
                est = {j: sum(power(n, j) for n in self.w.npcs if n.active_job == j) * self.w.base_drop[j] for j in jobs}
                self.label(body, "ผลผลิตวัตถุดิบโดยประมาณ: " + " · ".join(f"{JOBS[j]} {v:.0f}" for j, v in est.items()), True)
            self.roster(body, jobs, draw)
        draw()

    def node_store(self, body, key, jobs):
        w = self.w
        items = (["raw"] if key == "central" else []) + [p for p, v in PRODUCTS.items() if key == "central" or v[1] == key]
        tv = ttk.Treeview(body, columns=("item", "lv", "cap"), show="headings", height=len(items))
        for c, t in [("item", "รายการ"), ("lv", "คงคลัง"), ("cap", "ความจุ")]:
            tv.heading(c, text=t)
        for it in items:
            tv.insert("", "end", values=("วัตถุดิบ" if it == "raw" else PRODUCTS[it][0], w.level(key, it), w.cap[(key, it)]))
        tv.pack(fill="x", padx=12, pady=8)
        self.label(body, "⚠ สินค้าล้นความจุจะสูญเสีย · สินค้าขาด = ตอบสนองความต้องการไม่ครบ (Stockout)")

    def node_hr(self, body, key, jobs):
        def draw(msg=None):
            self.clear(body)
            tk.Button(body, text=f"🎲 สุ่มรับสมัคร NPC (ใช้วัตถุดิบ {RECRUIT_COST})", bg=GOLD, relief="flat", command=hire).pack(pady=6)
            if msg:
                self.label(body, ("✅ " if msg[0] else "⚠ ") + msg[1], True, "#9be28f" if msg[0] else "#ff9a8a")
            self.label(body, f"ทีมปัจจุบัน {len(self.w.npcs)}/10 คน (จัดเวรได้ที่อาคารสายงานแต่ละแห่ง)", True)
            self.roster(body, [], draw)

        def hire():
            draw(self.w.recruit())
        draw()

    def node_dash(self, body, key, jobs):
        w, k = self.w, self.w.kpis()
        self.label(body, f"Order Fulfillment Rate {k['ofr']:.0f}% · Bottleneck Rate {k['bottleneck']:.0f}% · "
                         f"Inventory Stability {k['stability']:.0f}% · ต้นทุนรวม {k['cost']} · ล้นคลัง {k['overflow']}", True)
        log = w.log_df()
        if log.empty or FigureCanvasTkAgg is None:
            self.label(body, "ยังไม่มีข้อมูล (กด “จบสัปดาห์” ก่อน)" if log.empty else "ติดตั้ง matplotlib เพื่อดูกราฟ")
            return
        fig = Figure(figsize=(6.4, 3.6), dpi=90)
        ax = fig.add_subplot(111)
        for col, lab in [("raw", "วัตถุดิบ"), ("central_goods", "รอส่ง"), ("shop", "ร้านค้า"), ("dorm", "หอพัก")]:
            ax.plot(log["week"], log[col], marker="o", label=lab)
        ax.set_xlabel("week")
        ax.legend()
        FigureCanvasTkAgg(fig, master=body).get_tk_widget().pack(padx=8, pady=4)

    # ---------- จบสัปดาห์ -> รายงาน -> เหตุการณ์เนื้อเรื่อง -> เปลี่ยนเทอม -> ตอนจบ ----------
    def end_week(self):
        if self.modal:
            return
        if self.w.week >= self.w.weeks:
            return self.final()
        r = self.w.step()
        lines = []
        if r["event"]:
            lines.append(f"🎲 เหตุการณ์สุ่ม: {r['event']['name']} — {r['event']['desc']}\n")
        lines.append(f"⛏ เก็บวัตถุดิบได้ {r['gathered']} หน่วย" + (f"  ⚠ บาดเจ็บ: {', '.join(r['hurt'])}" if r["hurt"] else ""))
        for x in r["lines"]:
            lines.append(f"{x['label']}: ผลิต {x['made']} → ส่งไป{x['node']} {x['shipped']} · ต้องการ {x['demand']} · ตอบสนอง {x['fill']:.0%}")
        if r["overflow"]:
            lines.append(f"🗑 ล้นคลัง {r['overflow']} หน่วย")
        lines.append(f"\nประสิทธิภาพโซ่อุปทาน {r['efficiency']:.0f}%")
        wk = r["week"]
        self.dialog(f"รายงานสัปดาห์ที่ {wk}", "\n".join(lines), [("ต่อไป", lambda: self.after_report(wk))])

    def after_report(self, wk):
        if wk % 2 == 0:
            self.run_event(EV[wk // 2 - 1], lambda: self.after_event(wk))
        else:
            self.after_event(wk)

    def after_event(self, wk):
        nxt = self.final if wk >= self.w.weeks else None
        if wk in TERM_INTRO:
            self.dialog(*TERM_INTRO[wk], [("เริ่มเทอมถัดไป", None)])
        elif nxt:
            nxt()

    def run_event(self, ev, then):
        def pick(o):
            def f():
                ok, hurt = self.w.apply_fx(o[1])
                txt = o[2] + (f"\n⚠ {hurt} บาดเจ็บระหว่างภารกิจ (พักฟื้น)" if hurt else "") + f"\n\n📘 บทเรียน: {ev['l']}"
                self.dialog(ev["n"], txt, [("ต่อไป", then)])
            return f
        self.dialog(ev["n"], ev["d"], [(o[0], pick(o)) for o in ev["o"]])

    def final(self):
        d = self.w.grade_detail()
        self.dialog(f"สรุปปีการศึกษา — เกรด {d['grade']}",
                    f"คะแนนรวม {d['score']}/100\n• ประสิทธิภาพโซ่อุปทาน {d['eff']:.0f} (40%)\n• รับมือวิกฤตสำเร็จ {self.w.ok}/6 (30%)\n"
                    f"• NPC ที่ยังพร้อมทำงาน {d['wf']}% (20%)\n• ความคืบหน้าปม {self.w.mystery} (10%)\n\nตอนจบ: {END[d['grade']]}",
                    [("เล่นใหม่", self.restart)])

    def restart(self):
        if self.modal and not getattr(self.modal, "winfo_exists", lambda: 0)():
            return
        if self.modal:
            self.modal.destroy()
            self.modal = None
        self.w, self.vis, self.done_mc = World(), set(), set()
        self.load_scene("over")
        self.seq(INTRO)


if __name__ == "__main__":
    Game().mainloop()
