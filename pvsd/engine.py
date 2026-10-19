"""
engine.py — Simulation Engine ของ "สถาบันนักผจญภัยฝึกหัด" (Supply Chain Management Simulation)
ใช้ SimPy (Discrete-Event), Pandas/NumPy (ประมวลผลข้อมูล) และ SQLite (ฐานข้อมูล 6 ตารางตามหัวข้อ 3.3)
1 รอบเวลา (tick) = 1 สัปดาห์ ภายในสัปดาห์: เก็บวัตถุดิบ -> แปรรูป 3 สาย -> คลังวัสดุกลาง -> กระจายไปร้านค้า/หอพัก -> บริโภคปลายทาง
"""
import json
import random
import sqlite3

import numpy as np
import pandas as pd
import simpy

TIERS = ["นักเรียนใหม่", "นักเรียนกลาง", "อาจารย์", "อาจารย์อาวุโส"]
SKILL = [30, 60, 80, 90]
JOBS = {"forest": "🌲 ป่า", "lab": "🔬 ห้องทดลอง", "dungeon": "⚔ ดันเจี้ยน",
        "potion": "🧪 ห้องปรุงยา", "craft": "🔨 เวิร์กชอปคราฟต์",
        "tool": "🛠 โรงงานเครื่องมือ", "rest": "💤 พัก"}
# สายการผลิต 3 สาย (Midstream) และสินค้าที่ผลิตได้
LINES = {"potion": ("🧪 ห้องปรุงยา", ["potion"]),
         "craft": ("🔨 เวิร์กชอปคราฟต์", ["repair", "supply"]),
         "tool": ("🛠 โรงงานเครื่องมือ", ["study"])}
# สินค้าสำเร็จรูป: (ชื่อ, จุดบริโภคปลายทางที่เก็บ, สัดส่วนความต้องการ)
PRODUCTS = {"potion": ("ยาพกพา", "dorm", .7), "supply": ("เสบียงลงดันเจี้ยน", "dorm", .5),
            "repair": ("วัสดุซ่อมแซม", "shop", .6), "study": ("อุปกรณ์เรียน", "shop", .5)}
NODES = {"central": "คลังวัสดุกลาง", "shop": "ร้านค้าสถาบัน", "dorm": "หอพัก"}
TRUCK = 30      # ขีดความสามารถในการกระจายสินค้าจากคลังกลางต่อสัปดาห์
MAX_NPC, RECRUIT_COST = 10, 10
RUSH_QUOTA, RUSH_RAW, RUSH_COST = 2, 2, 5   # สั่งด่วน: โควตา/สัปดาห์, วัตถุดิบต่อสินค้า 1 หน่วย, ต้นทุนเพิ่มต่อหน่วย
# พ่อค้าในย่านการค้า: key -> (ชื่อพ่อค้า, ร้าน, สินค้าที่มักสั่ง)
MERCH = {"t_weapon": ("ช่างอาวุธ กรอมม์", "ร้านอาวุธ", ["repair", "supply"]),
         "t_armor": ("ช่างเกราะ เซรีน", "ร้านชุดเกราะ", ["repair", "study"]),
         "t_magic": ("พ่อค้าเครื่องราง ลูน่า", "ร้านเวทมนตร์ / เครื่องราง", ["potion", "study"]),
         "t_jewel": ("ช่างเครื่องประดับ ไอริส", "ร้านเครื่องประดับ", ["study", "repair"]),
         "t_inn": ("เจ้าของโรงเตี้ยม บาร์ตัน", "โรงเตี้ยม", ["supply", "potion"]),
         "t_gen": ("พ่อค้าทั่วไป โมรา", "ร้านทั่วไป", ["supply", "repair", "study", "potion"]),
         "t_food": ("เชฟ ทาเลีย", "ร้านอาหาร & เครื่องดื่ม", ["potion", "supply"]),
         "t_rare": ("พ่อค้าของหายาก วาเลน", "ร้านค้าพิเศษ (ของหายาก)", ["potion", "supply", "repair", "study"])}

SCHEMA = """
CREATE TABLE IF NOT EXISTS npcs(id INTEGER PRIMARY KEY, name TEXT, tier INTEGER, atk INT, def INT, spd INT, acc INT, hp INT, injured INT, job TEXT);
CREATE TABLE IF NOT EXISTS resources(id INTEGER PRIMARY KEY, name TEXT, source TEXT, base_drop REAL);
CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY, line TEXT, product TEXT, input_qty INT, output_qty INT, duration REAL);
CREATE TABLE IF NOT EXISTS storages(id INTEGER PRIMARY KEY, node TEXT, item TEXT, capacity INT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, name TEXT, prob REAL, effect TEXT, description TEXT);
CREATE TABLE IF NOT EXISTS simulation_logs(week INT, raw INT, central_goods INT, shop INT, dorm INT, gathered INT,
  shipped INT, demand INT, fill_rate REAL, efficiency REAL, event TEXT, overflow INT, cost INT);
"""
RESOURCES = [("สมุนไพรป่า", "forest", 8), ("ผลึกห้องทดลอง", "lab", 6), ("แก่นดันเจี้ยน", "dungeon", 14)]
RECIPES = [("potion", "potion", 1, 1, 0.25), ("craft", "repair", 1, 1, 0.25),
           ("craft", "supply", 1, 1, 0.25), ("tool", "study", 1, 1, 0.25)]
STORAGES = [("central", "raw", 100)] + [("central", p, 30) for p in PRODUCTS] + \
           [(v[1], p, 30) for p, v in PRODUCTS.items()]
EVENTS = [
    ("สภาพอากาศแปรปรวน", 0.15, {"drop": {"forest": 0.5, "lab": 0.6}}, "วัตถุดิบจากป่าและห้องทดลองลดลง"),
    ("ดันเจี้ยนคลั่ง", 0.10, {"drop": {"dungeon": 1.6}, "risk": 1.6}, "ดันเจี้ยนดรอปสูงขึ้น แต่เสี่ยงบาดเจ็บมากขึ้น"),
    ("วัตถุดิบขาดแคลน", 0.10, {"drop": {"*": 0.6}}, "ผลผลิตวัตถุดิบทุกแหล่งลดลง"),
    ("ความต้องการผันผวน", 0.15, {"dem": 1.5}, "ความต้องการปลายทางเพิ่มขึ้นฉับพลัน"),
]


class NPC:
    def __init__(self, id, name, tier, atk, dfn, spd, acc, job="rest", hp=100, injured=0):
        self.id, self.name, self.tier = id, name, tier
        self.atk, self.dfn, self.spd, self.acc = atk, dfn, spd, acc
        self.job, self.hp, self.injured = job, hp, injured
        self.xp = 0                   # ความรู้สะสมจากอุปกรณ์เรียน (1.3.4)

    @property
    def skill(self):
        return SKILL[self.tier]

    @property
    def active_job(self):          # NPC ที่บาดเจ็บจะทำงานไม่ได้
        return "rest" if self.injured > 0 else self.job


def base_stat(n, job):
    """ความเหมาะสมของ NPC กับงาน (0-1) — ถ้าจัดคนไม่ตรงงานจะเกิดคอขวด"""
    v = {"forest": (n.spd + n.acc) / 2, "lab": (n.acc + n.skill) / 2,
         "dungeon": (n.atk + n.dfn) / 2}.get(job, (2 * n.skill + n.acc) / 3)
    return v / 100


def power(n, job):
    return base_stat(n, job) * (0.4 + 0.6 * n.hp / 100)   # แรงต่ำ -> ผลงานลดลง


class World:
    def __init__(self, db_path=":memory:", seed=None, weeks=12):
        self.rng, self.weeks, self.week = random.Random(seed), weeks, 0
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        self.env = simpy.Environment()
        self.cap = {(r[0], r[1]): r[2] for r in self.db.execute("SELECT node,item,capacity FROM storages")}
        self.base_drop = {r[0]: r[1] for r in self.db.execute("SELECT source,base_drop FROM resources")}
        self.lead = {r[0]: r[1] for r in self.db.execute("SELECT line,duration FROM recipes")}
        self.events = [dict(name=r[0], prob=r[1], effect=json.loads(r[2]), desc=r[3])
                       for r in self.db.execute("SELECT name,prob,effect,description FROM events")]
        init = {("central", "raw"): 50}
        init.update({("central", p): 5 for p in PRODUCTS})
        init.update({(PRODUCTS[p][1], p): v for p, v in {"potion": 10, "supply": 8, "repair": 10, "study": 8}.items()})
        self.st = {k: simpy.Container(self.env, capacity=self.cap[k], init=v) for k, v in init.items()}
        self.raw = self.st[("central", "raw")]
        self.npcs, self._next_id = [], 1
        for name, tier, job in [("รุ่นกลาง A", 1, "forest"), ("นักเรียนใหม่", 0, "tool"), ("รุ่นกลาง B", 1, "lab"),
                                ("คาเอล", 1, "dungeon"), ("อาจารย์เวิร์กชอป", 2, "craft"), ("อาจารย์อาวุโส", 3, "potion")]:
            self._new_npc(name, tier, job)
        self.npcs[3].atk = min(100, self.npcs[3].atk + 20)
        self.fs = self.stab = self.cost = self.overflow = self.eff_sum = 0
        self.bn = {p: 0 for p in PRODUCTS}
        self.last = None
        self.trust, self.mystery, self.eff_adj, self.ok, self.stall = 40, 0, 0, 0, set()   # ตัวแปรของโหมดเนื้อเรื่อง
        self.last_fill = {p: 1.0 for p in PRODUCTS}   # อัตราตอบสนองสัปดาห์ก่อน -> ส่งผลต่อสัปดาห์ถัดไป
        self.last_dem = None
        self.rush_left, self.rush_week, self.rush_n = RUSH_QUOTA, {}, 0
        self.orders, self._oid, self.orders_done, self.orders_missed = [], 1, 0, 0
        self._save_npcs()
        self.gen_orders()

    # ---------- ฐานข้อมูล ----------
    def _init_db(self):
        self.db.executescript(SCHEMA)
        if not self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
            self.db.executemany("INSERT INTO resources(name,source,base_drop) VALUES(?,?,?)", RESOURCES)
            self.db.executemany("INSERT INTO recipes(line,product,input_qty,output_qty,duration) VALUES(?,?,?,?,?)", RECIPES)
            self.db.executemany("INSERT INTO storages(node,item,capacity) VALUES(?,?,?)", STORAGES)
            self.db.executemany("INSERT INTO events(name,prob,effect,description) VALUES(?,?,?,?)",
                                [(a, b, json.dumps(c), d) for a, b, c, d in EVENTS])
            self.db.commit()

    def _save_npcs(self):
        self.db.executemany("INSERT OR REPLACE INTO npcs VALUES(?,?,?,?,?,?,?,?,?,?)",
                            [(n.id, n.name, n.tier, n.atk, n.dfn, n.spd, n.acc, n.hp, n.injured, n.job) for n in self.npcs])
        self.db.commit()

    def log_df(self):
        return pd.read_sql("SELECT * FROM simulation_logs ORDER BY week", self.db)

    def npc_df(self):
        return pd.DataFrame([{"ชื่อ": n.name, "ระดับ": TIERS[n.tier], "ATK": n.atk, "DEF": n.dfn, "SPD": n.spd,
                              "ACC": n.acc, "แรง": n.hp, "งาน": "🤕 พักฟื้น" if n.injured else JOBS[n.job]} for n in self.npcs])

    # ---------- Procurement: สุ่มรับสมัคร NPC ----------
    def _new_npc(self, name, tier, job="rest"):
        lo, hi = 20 + 8 * tier, 55 + 10 * tier
        n = NPC(self._next_id, name, tier, *[self.rng.randint(lo, hi) for _ in range(4)], job=job)
        self._next_id += 1
        self.npcs.append(n)
        return n

    def recruit(self):
        if len(self.npcs) >= MAX_NPC:
            return False, f"สถาบันรับได้สูงสุด {MAX_NPC} คน"
        if self.raw.level < RECRUIT_COST:
            return False, f"วัตถุดิบไม่พอ (ต้องใช้ {RECRUIT_COST})"
        self.raw.get(RECRUIT_COST)
        x = self.rng.random()
        n = self._new_npc(f"ผู้สมัคร {self._next_id}", 0 if x < .5 else 1 if x < .85 else 2)
        self._save_npcs()
        return True, f"รับ {n.name}: {TIERS[n.tier]} [ATK {n.atk} DEF {n.dfn} SPD {n.spd} ACC {n.acc}]"

    # ---------- Random Event System ----------
    def _roll_event(self):
        r, c = self.rng.random(), 0
        for e in self.events:
            c += e["prob"]
            if r < c:
                return e
        return None

    # ---------- กระบวนการ SimPy ----------
    def _on(self, job):
        return [n for n in self.npcs if n.active_job == job]

    def _gather(self, n):                                    # 1) Upstream: ป่า / ห้องทดลอง / ดันเจี้ยน
        job = n.active_job
        yield self.env.timeout(0.5 * (1 - n.spd / 200))       # NPC เร็ว = กลับมาเร็ว
        d = self.m["drop"]
        mult = d.get(job, 1.0) * d.get("*", 1.0)
        amt = max(0, round(power(n, job) * self.base_drop[job] * mult * (.7 + .3 * self.last_fill["repair"]) + self.rng.uniform(-.5, .5)))
        raw = self.raw
        put = min(amt, int(raw.capacity - raw.level))
        self.w["overflow"] += amt - put
        self.w["gathered"] += put
        if put:
            yield raw.put(put)
        if job == "dungeon":                                  # Auto-simulation: เสี่ยงบาดเจ็บตามค่าพลัง
            p = min(.8, (.05 + .35 * (1 - base_stat(n, job))) * self.m["risk"] * (1 + (1 - self.last_fill["supply"]) * .8))
            if self.rng.random() < p:
                n.hp, n.injured = max(5, n.hp - 30), 3
                self.w["hurt"].append(n.name)

    def _station(self, line):                                # 2) Midstream: แปรรูป 3 สาย -> คลังวัสดุกลาง
        yield self.env.timeout(0.55)
        cap = 0 if line in self.stall else round(sum(power(n, line) for n in self._on(line)) * 10 * (.7 + .3 * self.last_fill["repair"]))   # stall = สายหยุดชะงักจากเหตุการณ์
        take = int(min(cap, self.raw.level))
        if take:
            yield self.raw.get(take)
        yield self.env.timeout(self.lead[line])               # Lead time ของสาย
        prods = LINES[line][1]
        for i, p in enumerate(prods):                         # แบ่งผลผลิตให้สินค้าแต่ละชนิดของสาย
            q = take // len(prods) + (1 if i < take % len(prods) else 0)
            g = self.st[("central", p)]
            put = min(q, int(g.capacity - g.level))
            self.w["overflow"] += q - put
            self.w["made"][p] = put
            if put:
                yield g.put(put)
        self.w["cap"][line], self.w["used_raw"] = cap, self.w["used_raw"] + take

    def _distribute(self):                                   # 3) Storage & Distribution: คลังกลาง -> ร้านค้า/หอพัก
        yield self.env.timeout(0.85)
        truck = TRUCK
        for p in sorted(PRODUCTS, key=lambda p: self.st[(PRODUCTS[p][1], p)].level / self.cap[(PRODUCTS[p][1], p)]):
            src, dst = self.st[("central", p)], self.st[(PRODUCTS[p][1], p)]   # ส่งให้จุดที่พร่องที่สุดก่อน
            q = int(min(src.level, dst.capacity - dst.level, truck))
            if q:
                yield src.get(q)
                yield dst.put(q)
                truck -= q
            self.w["ship"][p] = q

    def _consume(self):                                      # 4) Downstream: NPC ดึงสินค้าไปใช้จริง
        yield self.env.timeout(0.95)
        D = self.demand(self.m["dem"], self.week)           # 1.3.4: ความต้องการมาจาก NPC แต่ละคนจริง
        self.w["by"] = D
        for p, (_, node, ratio) in PRODUCTS.items():
            d, g = D[p]["total"], self.st[(node, p)]
            used = min(d, int(g.level))
            if used:
                yield g.get(used)
            self.w["dem"][p], self.w["used"][p] = d, used

    # ---------- 1.3.4 ความต้องการปลายทาง (Downstream Consumption) ----------
    def demand(self, dem=1.0, wk=None):
        """ความต้องการของ NPC ต่อสินค้า: ยาตามแรง/บาดเจ็บ, เสบียงตามงาน (ดันเจี้ยนมากสุด), วัสดุซ่อมตามภาระงาน, อุปกรณ์เรียนตามระดับ"""
        wk = wk or self.week + 1
        m = dem * (1 + .25 * ((wk - 1) // 4))
        D = {p: {"total": 0.0, "by": []} for p in PRODUCTS}
        for n in self.npcs:
            j = n.active_job
            q = {"potion": 2 if n.injured else 1.5 if n.hp < 60 else .7,
                 "supply": 3 if j == "dungeon" else 1.5 if j == "forest" else .3,
                 "repair": .2 if j == "rest" else 1 if j in LINES else .8,
                 "study": 1 if n.tier <= 1 else .4}
            for p, v in q.items():
                D[p]["total"] += v
                D[p]["by"].append((n.name, v))
        for p, d in D.items():
            d["total"] = int(round(d["total"] * m))
            d["by"] = sorted(((a, round(v * m, 1)) for a, v in d["by"]), key=lambda x: -x[1])
        return D

    # ---------- สั่งด่วน (Express Order) ----------
    def rush(self, p, q):
        """ส่งสินค้าถึงปลายทางทันที ข้ามสายผลิต/รถขนส่ง: ใช้วัตถุดิบ 2 หน่วย/ชิ้น และต้นทุน +5/ชิ้น (โควตา 2 ครั้ง/สัปดาห์)"""
        if self.rush_left < 1:
            return False, f"โควตาสั่งด่วนสัปดาห์นี้หมดแล้ว ({RUSH_QUOTA} ครั้ง/สัปดาห์)"
        node = PRODUCTS[p][1]
        dst = self.st[(node, p)]
        space = int(dst.capacity - dst.level)
        if space < 1:
            return False, f"{PRODUCTS[p][0]}: ปลายทางเต็มแล้ว"
        u = min(q, space, self.level("central", "raw") // RUSH_RAW)
        if u < 1:
            return False, f"วัตถุดิบไม่พอ (ต้องใช้ {RUSH_RAW} หน่วยต่อสินค้า 1 หน่วย)"
        self.raw.get(RUSH_RAW * u)
        dst.put(u)
        self.cost += RUSH_COST * u
        self.rush_left -= 1
        self.rush_n += 1
        self.rush_week[p] = self.rush_week.get(p, 0) + u
        return True, f"ส่งด่วน {PRODUCTS[p][0]} {u} หน่วย ถึง{NODES[node]}ทันที (ใช้วัตถุดิบ {RUSH_RAW * u} · ต้นทุน +{RUSH_COST * u})"

    # ---------- คำสั่งซื้อจากพ่อค้า ----------
    def avail(self, p):
        return self.level("central", p) + self.level(PRODUCTS[p][1], p)

    def gen_orders(self):
        n = 2 if self.week == 0 else (1 if self.rng.random() < .55 else 2)
        m = 1 + .25 * (self.week // 4)
        for _ in range(n):
            if len(self.orders) >= 8:
                break
            k = self.rng.choice(list(MERCH))
            p = self.rng.choice(MERCH[k][2]) if self.rng.random() < .75 else self.rng.choice(list(PRODUCTS))
            rush = self.rng.random() < .25
            q = max(3, round(self.rng.randint(4, 9) * m * (1.2 if rush else 1)))
            self.orders.append(dict(id=self._oid, k=k, p=p, q=q, left=q, rush=rush,
                                    due=self.week + (1 if rush else self.rng.randint(2, 3)),
                                    rw=round(q * (3 if rush else 2) * (1.3 if k == "t_rare" else 1))))
            self._oid += 1

    def deliver(self, oid):
        o = next((x for x in self.orders if x["id"] == oid), None)
        if not o:
            return False, "ไม่พบคำสั่งซื้อ"
        p, a = o["p"], self.avail(o["p"])
        if a < 1:
            return False, f"{PRODUCTS[p][0]} ไม่มีในคลังเลย"
        u = min(a, o["left"])
        c = min(self.level("central", p), u)           # ดึงจากคลังกลางก่อน แล้วค่อยเอาจากสต็อกปลายทาง
        if c:
            self.st[("central", p)].get(c)
        if u - c:
            self.st[(PRODUCTS[p][1], p)].get(u - c)
        o["left"] -= u
        if o["left"] <= 0:
            self.orders.remove(o)
            self.orders_done += 1
            put = min(o["rw"], int(self.raw.capacity - self.raw.level))
            if put:
                self.raw.put(put)
            self.trust = int(np.clip(self.trust + (4 if o["rush"] else 2), 0, 100))
            self.eff_adj += 2
            return True, f"ส่งครบแล้ว! {MERCH[o['k']][0]} จ่ายค่าตอบแทน วัตถุดิบ +{o['rw']} · ไว้ใจ +{4 if o['rush'] else 2}"
        return True, f"ส่ง {PRODUCTS[p][0]} {u} หน่วย · ยังขาดอีก {o['left']}"

    # ---------- 1 สัปดาห์ ----------
    def step(self):
        self.week += 1
        rush_w, self.rush_week, self.rush_left = dict(self.rush_week), {}, RUSH_QUOTA
        ev = self._roll_event()
        eff = ev["effect"] if ev else {}
        self.m = {"drop": eff.get("drop", {}), "risk": eff.get("risk", 1.0), "dem": eff.get("dem", 1.0)}
        self.w = {"gathered": 0, "overflow": 0, "hurt": [], "made": {}, "cap": {}, "dem": {}, "used": {}, "ship": {}, "used_raw": 0}
        t0 = self.env.now
        for n in self.npcs:
            if n.active_job in self.base_drop:
                self.env.process(self._gather(n))
        for line in LINES:
            self.env.process(self._station(line))
        self.env.process(self._distribute())
        self.env.process(self._consume())
        self.env.run(until=t0 + 1)
        self.stall = set()

        w = self.w
        fills = {p: (w["used"][p] / w["dem"][p] if w["dem"][p] else 1.0) for p in PRODUCTS}
        for p, f in fills.items():
            if f < 1:
                self.bn[p] += 1
        af = float(np.mean(list(fills.values())))
        eff_now = 100 * af - (10 if self.raw.level < 8 else 0) - (10 if w["overflow"] else 0)
        eff_now = float(np.clip(eff_now, 20, 100))
        lv = self.snapshot()
        if 10 <= lv["raw"] <= 90 and all(3 <= self.level(PRODUCTS[p][1], p) <= .9 * self.cap[(PRODUCTS[p][1], p)] for p in PRODUCTS):
            self.stab += 1
        self.fs += af
        self.eff_sum += eff_now
        self.overflow += w["overflow"]
        self.cost += w["used_raw"] + round(sum(lv.values()) * .1)
        fx = {"healed": 0, "faster": 0, "up": []}               # ผลของการบริโภคปลายทาง (1.3.4)
        for n in self.npcs:
            need = n.injured > 0 or n.hp < 70
            if n.injured > 0:
                n.injured -= 1
                n.hp = min(100, n.hp + 10)
                if fills["potion"] >= .8 and n.injured > 0:
                    n.injured -= 1
                    fx["faster"] += 1
            elif n.job == "rest":
                n.hp = min(100, n.hp + 12)
            else:
                n.hp = max(0, n.hp - 5)
            if need and fills["potion"] > 0:
                n.hp = min(100, n.hp + round(8 * fills["potion"]))
                fx["healed"] += 1
            elif fills["potion"] < 1 and n.hp < 60 and n.job != "rest" and not n.injured:
                n.hp = max(0, n.hp - 3)
            if n.tier <= 1:                                         # อุปกรณ์เรียน -> เลื่อนระดับ
                n.xp += round(10 * fills["study"])
                if n.xp >= (90 if n.tier else 50):
                    n.xp, n.tier = 0, n.tier + 1
                    n.atk, n.dfn, n.spd, n.acc = (min(100, v + 4) for v in (n.atk, n.dfn, n.spd, n.acc))
                    fx["up"].append(n.name)
        self.last_fill = dict(fills)
        self.last_dem = {"dem": dict(w["dem"]), "used": dict(w["used"])}
        missed = [o for o in self.orders if o["due"] <= self.week]    # คำสั่งซื้อพ่อค้าหมดอายุ
        self.orders = [o for o in self.orders if o["due"] > self.week]
        for _ in missed:
            self.orders_missed += 1
            self.trust = int(np.clip(self.trust - 2, 0, 100))
            self.eff_adj -= 1
        before = len(self.orders)
        self.gen_orders()
        fresh = len(self.orders) - before
        self.db.execute("INSERT INTO simulation_logs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (self.week, lv["raw"], lv["central_goods"], lv["shop"], lv["dorm"], w["gathered"],
                         sum(w["ship"].values()), sum(w["dem"].values()), round(af, 3), round(eff_now, 1),
                         ev["name"] if ev else "", w["overflow"], self.cost))
        self._save_npcs()
        self.last = {"week": self.week, "event": ev, "gathered": w["gathered"], "hurt": w["hurt"],
                     "overflow": w["overflow"], "efficiency": eff_now, "raw": lv["raw"], "fx": fx, "rush": rush_w, "missed": missed, "fresh": fresh,
                     "lines": [{"label": PRODUCTS[p][0], "node": NODES[PRODUCTS[p][1]], "made": w["made"].get(p, 0),
                                "shipped": w["ship"].get(p, 0), "demand": w["dem"][p], "fill": fills[p],
                                "by": w["by"][p]["by"]} for p in PRODUCTS]}
        return self.last

    def level(self, node, item):
        return int(self.st[(node, item)].level)

    def snapshot(self):
        return {"raw": self.level("central", "raw"),
                "central_goods": sum(self.level("central", p) for p in PRODUCTS),
                "shop": sum(self.level("shop", p) for p, v in PRODUCTS.items() if v[1] == "shop"),
                "dorm": sum(self.level("dorm", p) for p, v in PRODUCTS.items() if v[1] == "dorm")}

    # ---------- KPI ----------
    def kpis(self):
        n = max(self.week, 1)
        return {"ofr": 100 * self.fs / n, "bottleneck": 100 * sum(self.bn.values()) / (len(PRODUCTS) * n),
                "stability": 100 * self.stab / n, "cost": self.cost, "overflow": self.overflow,
                "efficiency": self.eff_sum / n, "orders_done": self.orders_done,
                "orders_missed": self.orders_missed, "rush_n": self.rush_n}

    def grade_detail(self):
        n = max(self.week, 1)
        eff = float(np.clip(self.eff_sum / n + self.eff_adj / 2, 0, 100))
        wf = round(100 * sum(1 for x in self.npcs if not x.injured and x.hp >= 50) / len(self.npcs))
        sc = round(.4 * eff + .3 * (self.ok / 6 * 100) + .2 * wf + .1 * self.mystery)
        g = next(g for t, g in [(90, "S"), (75, "A"), (60, "B"), (45, "C"), (30, "D"), (0, "F")] if sc >= t)
        return {"score": sc, "grade": g, "eff": eff, "wf": wf}

    def grade(self):
        d = self.grade_detail()
        return d["score"], d["grade"]

    # ---------- ผลของการตัดสินใจ/เหตุการณ์เนื้อเรื่อง ----------
    def apply_fx(self, fx, who=None):
        """fx: s=วัตถุดิบ, w=แรง NPC, t=ความไว้วางใจ, m=ปม, e=ประสิทธิภาพ, risk=เสี่ยงบาดเจ็บ, stall=สายหยุด, ok=รับมือสำเร็จ"""
        if callable(fx):
            fx = fx(self)
        ok, hurt = fx.get("ok"), None
        if fx.get("risk"):
            gs = [n for n in self.npcs if n.active_job == "dungeon"]
            pw = float(np.mean([power(n, "dungeon") for n in gs])) if gs else 0
            if self.rng.random() < float(np.clip(.15 + .5 * (1 - pw), .1, .8)):
                v = self.rng.choice(gs or self.npcs[:6])
                v.hp, v.injured, ok, hurt = max(5, v.hp - 40), 3, 0, v.name
        if "w" in fx:
            for n in ([self.npcs[who]] if who is not None else self.npcs):
                n.hp = int(np.clip(n.hp + fx["w"], 0, 100))
        self.stall |= {{"med": "potion"}.get(x, x) for x in fx.get("stall", [])}
        s = fx.get("s", 0)
        if s > 0:
            put = min(s, int(self.raw.capacity - self.raw.level))
            if put:
                self.raw.put(put)
        elif s < 0 and int(self.raw.level):
            self.raw.get(min(int(self.raw.level), -s))
        self.trust = int(np.clip(self.trust + fx.get("t", 0), 0, 100))
        self.mystery = int(np.clip(self.mystery + fx.get("m", 0), 0, 100))
        self.eff_adj += fx.get("e", 0)
        if ok:
            self.ok += 1
        self._save_npcs()
        return bool(ok), hurt


if __name__ == "__main__":            # ทดสอบแบบไม่มี UI: python engine.py
    w = World(seed=42)
    print(w.npc_df().to_string(index=False))
    while w.week < w.weeks:
        r = w.step()
        print(f"สัปดาห์ {r['week']:>2} | เก็บได้ {r['gathered']:>2} | คงเหลือ {r['raw']:>3} | "
              f"ส่งมอบ {r['lines'] and sum(x['shipped'] for x in r['lines']):>2} | ตอบสนอง {[round(x['fill'] * 100) for x in r['lines']]} | {r['event']['name'] if r['event'] else '-'}")
    print({k: round(v, 1) for k, v in w.kpis().items()}, "เกรด", w.grade())
