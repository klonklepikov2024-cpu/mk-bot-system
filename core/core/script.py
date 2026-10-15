import os
import re
import shutil
import py_compile
from datetime import datetime

BACKUP_DIR = os.path.join("backups", datetime.now().strftime("%Y%m%d_%H%M%S_v2"))

def make_backup(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Файл не найден: {filepath}")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, os.path.basename(filepath))
    shutil.copy2(filepath, dst)
    print(f"📦 Бэкап создан: {dst}")
    return dst

def verify_python(filepath, backup_path):
    try:
        py_compile.compile(filepath, doraise=True)
        print(f"✅ Проверка синтаксиса пройдена: {filepath}")
    except py_compile.PyCompileError as e:
        shutil.copy2(backup_path, filepath)
        raise RuntimeError(f"❌ Ошибка синтаксиса в {filepath}! Восстановлен из бэкапа.\n{e}")

# =====================================================================
# 1. ОБНОВЛЕНИЕ main.py (Мужские роли в !семья + Древо для WebApp)
# =====================================================================
NEW_FAMILY_FUNCTIONS = '''def compute_male_kinship_roles(uid, gd, partner_of, kids_of):
    """Вычисляет строго мужские роли всех родственников относительно uid."""
    roles = {uid: "вы"}
    my_part = partner_of(uid)
    if my_part:
        roles[my_part] = "муж"

    # 1. Предки вверх (отец / батя -> дед -> прадед -> пращур)
    gen_up = {0: [uid] + ([my_part] if my_part else [])}
    p1 = gd(uid).get("parent_id")
    if p1:
        p2 = partner_of(p1)
        gen_up[1] = [p1] + ([p2] if p2 else [])
        roles[p1] = "отец"
        if p2 and p2 not in roles:
            roles[p2] = "батя"
    else:
        gen_up[1] = []

    # Если у мужа есть родители — подпишем их
    if my_part:
        mp1 = gd(my_part).get("parent_id")
        if mp1 and mp1 not in roles:
            roles[mp1] = "отец мужа"
            mp2 = partner_of(mp1)
            if mp2 and mp2 not in roles:
                roles[mp2] = "батя мужа"
            for bil in kids_of(mp1):
                if bil not in roles:
                    roles[bil] = "брат мужа"

    for lvl in range(2, 7):
        cur_anc = []
        lbl = "дед" if lvl == 2 else ("прадед" if lvl == 3 else "пращур")
        for node in gen_up.get(lvl - 1, []):
            up = gd(node).get("parent_id")
            if up and up not in roles:
                roles[up] = lbl
                cur_anc.append(up)
                up_p = partner_of(up)
                if up_p and up_p not in roles:
                    roles[up_p] = lbl
                    cur_anc.append(up_p)
        gen_up[lvl] = cur_anc

    # 2. Потомки вниз от uid (сын -> внук -> правнук)
    gen_down = {0: [uid]}
    for lvl in range(1, 6):
        cur_desc = []
        lbl = "сын" if lvl == 1 else ("внук" if lvl == 2 else ("правнук" if lvl == 3 else "потомок"))
        sp_lbl = "зять" if lvl == 1 else f"муж {lbl}а"
        for node in gen_down.get(lvl - 1, []):
            for ch in kids_of(node):
                if ch not in roles:
                    roles[ch] = lbl
                cur_desc.append(ch)
                ch_p = partner_of(ch)
                if ch_p and ch_p not in roles:
                    roles[ch_p] = sp_lbl
        gen_down[lvl] = cur_desc

    # 3. Братья и племянники (дети отцов)
    for par in gen_up.get(1, []):
        for sib in kids_of(par):
            if sib not in roles:
                roles[sib] = "брат"
                sib_p = partner_of(sib)
                if sib_p and sib_p not in roles:
                    roles[sib_p] = "муж брата"
                for neph in kids_of(sib):
                    if neph not in roles:
                        roles[neph] = "племянник"
                        neph_p = partner_of(neph)
                        if neph_p and neph_p not in roles:
                            roles[neph_p] = "муж племянника"
                        for gneph in kids_of(neph):
                            if gneph not in roles:
                                roles[gneph] = "внуч. племянник"

    # 4. Дяди и двоюродные братья (дети дедов)
    for gp in gen_up.get(2, []):
        for unc in kids_of(gp):
            if unc not in roles:
                roles[unc] = "дядя"
                unc_p = partner_of(unc)
                if unc_p and unc_p not in roles:
                    roles[unc_p] = "муж дяди"
                for cous in kids_of(unc):
                    if cous not in roles:
                        roles[cous] = "двоюр. брат"

    return roles

def build_family_tree_text(uid, get_data, get_name, max_nodes=45):
    """Полное древо в чате со строго мужскими подписями родства (отец, батя, дед, брат, сын, внук, племянник)."""
    import html
    cache = {}
    def gd(u):
        if u not in cache: cache[u] = get_data(u) or {}
        return cache[u]
    def link(u): return f"<a href='tg://user?id={u}'>{html.escape(str(get_name(u)))}</a>"
    def partner_of(u): return gd(u).get("partner_id")
    def kids_of(u):
        ids = list(gd(u).get("children", []))
        p = partner_of(u)
        if p:
            for c in gd(p).get("children", []):
                if c not in ids: ids.append(c)
        return ids

    roles = compute_male_kinship_roles(uid, gd, partner_of, kids_of)
    def role_tag(u):
        r = roles.get(u, "родня")
        return f" <i>[{r}]</i>"

    seen, cur = {uid}, uid
    for _ in range(6):
        p = gd(cur).get("parent_id")
        if not p:
            pt = partner_of(cur)
            if pt: p = gd(pt).get("parent_id")
        if not p or p in seen: break
        seen.add(p); cur = p
    root = cur

    shown, lines, state = set(), [], {"n": 0, "cut": False}
    def icon_for(u):
        r = roles.get(u, "")
        if u == uid: return "👉"
        if u == root or r in ("дед", "прадед", "пращур"): return "👑"
        if r in ("отец", "батя", "отец мужа", "батя мужа"): return "🧔‍♂️"
        if r in ("сын", "внук", "правнук", "племянник"): return "👦"
        return "🧑"

    def label(u):
        ic = icon_for(u)
        if u == uid:
            s = f"{ic} <b>{html.escape(str(get_name(u)))}</b> <i>[вы]</i>"
        else:
            s = f"{ic} {link(u)}{role_tag(u)}"
        p = partner_of(u)
        if p:
            p_name = f"<b>{html.escape(str(get_name(p)))}</b> <i>[вы]</i>" if p == uid else f"{link(p)}{role_tag(p)}"
            s += f" 💍 {p_name}"
            pp = gd(p).get("parent_id")
            if pp and pp not in shown and pp != u:
                s += f" <i>(род. из семьи {link(pp)}{role_tag(pp)})</i>"
        return s

    def walk(u, prefix, is_last, depth):
        if state["n"] >= max_nodes:
            state["cut"] = True; return
        state["n"] += 1
        shown.add(u)
        p = partner_of(u)
        if p: shown.add(p)
        branch = "" if depth == 0 else ("└─ " if is_last else "├─ ")
        lines.append(f"{prefix}{branch}{label(u)}")
        kids = [c for c in kids_of(u) if c not in shown]
        cp = prefix + ("" if depth == 0 else ("   " if is_last else "│  "))
        for i, c in enumerate(kids):
            walk(c, cp, i == len(kids) - 1, depth + 1)
    walk(root, "", True, 0)

    if uid not in shown:
        lines.append("")
        walk(uid, "", True, 0)
    text = "🌳 <b>ГЕНЕАЛОГИЧЕСКОЕ ДРЕВО</b> 🌳\\n\\n" + "\\n".join(lines)
    if state["cut"] or len(text) > 3900:
        text = text[:3850].rsplit("\\n", 1)[0] + "\\n… <i>(показаны не все родственники)</i>"
    return text

def build_family_webapp_nodes(uid, max_nodes=35):
    """Формирует список узлов родословной с мужскими ролями для карточки Синдиката в Мини-Аппе."""
    cache, name_cache = {}, {}
    def gd(u):
        if u not in cache:
            cache[u] = paid_collection.find_one({"uid": u}) or {}
        return cache[u]
    def gname(u):
        if u not in name_cache:
            st = db['chat_stats'].find_one({"uid": u}) or {}
            us = db['users'].find_one({"_id": u}) or {}
            name_cache[u] = st.get("name") or us.get("first_name") or f"ID {u}"
        return name_cache[u]
    def partner_of(u): return gd(u).get("partner_id")
    def kids_of(u):
        ids = list(gd(u).get("children", []))
        p = partner_of(u)
        if p:
            for c in gd(p).get("children", []):
                if c not in ids: ids.append(c)
        return ids

    u_doc = gd(uid)
    if not u_doc.get("partner_id") and not u_doc.get("parent_id") and not u_doc.get("children"):
        return []

    roles = compute_male_kinship_roles(uid, gd, partner_of, kids_of)
    seen, cur = {uid}, uid
    for _ in range(6):
        p = gd(cur).get("parent_id")
        if not p:
            pt = partner_of(cur)
            if pt: p = gd(pt).get("parent_id")
        if not p or p in seen: break
        seen.add(p); cur = p
    root = cur

    shown, nodes = set(), []
    def walk(u, depth):
        if len(nodes) >= max_nodes: return
        shown.add(u)
        p = partner_of(u)
        if p: shown.add(p)
        r_u = roles.get(u, "родня")
        node_item = {
            "uid": u,
            "name": gname(u),
            "role": r_u,
            "is_me": (u == uid),
            "depth": depth,
            "partner": None
        }
        if p:
            node_item["partner"] = {
                "uid": p,
                "name": gname(p),
                "role": roles.get(p, "муж"),
                "is_me": (p == uid)
            }
        nodes.append(node_item)
        for c in [k for k in kids_of(u) if k not in shown]:
            walk(c, depth + 1)

    walk(root, 0)
    if uid not in shown:
        walk(uid, 0)
    return nodes
'''

def patch_main():
    path = "main.py"
    bak = make_backup(path)
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    # 1. Заменяем build_family_tree_text на новую версию с мужскими ролями + build_family_webapp_nodes
    tree_pat = re.compile(
        r"(?:def compute_male_kinship_roles\(.*?\n)?def build_family_tree_text\(uid,\s*get_data,\s*get_name,\s*max_nodes=45\):.*?(?=def _family_bank_id\(uid,\s*user_data\):)",
        re.DOTALL
    )
    if not tree_pat.search(code):
        raise RuntimeError("Не найдена функция build_family_tree_text в main.py")
    code = tree_pat.sub(lambda _: NEW_FAMILY_FUNCTIONS + "\n", code, count=1)

    # 2. Добавляем передачу "family_tree" в /api/profile
    if '"family_tree":' not in code:
        code = code.replace(
            '"family_balance": family_balance,',
            '"family_balance": family_balance,\n        "family_tree": build_family_webapp_nodes(uid),',
            1
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    verify_python(path, bak)
    print("🎉 main.py обновлён (мужская родословная в !семья и API древа для Мини-Аппа)!")


# =====================================================================
# 2. ОБНОВЛЕНИЕ templates/webapp.html (Автоскрытие «Ещё» + Карточка Родословной)
# =====================================================================
NEW_FAMILY_HTML = """        <!-- 🔥 БЛОК СИНДИКАТА И РОДОСЛОВНОЙ В НОВОМ СТИЛЕ 🔥 -->
        <div id="familyBlock" class="hidden surface rounded-3xl p-4 mb-6 shadow-xl border border-pink-500/30 relative overflow-hidden">
            <div class="flex justify-between items-center mb-3 relative z-10">
                <div class="flex items-center gap-2">
                    <span class="text-xl">💍</span>
                    <div>
                        <h3 class="font-extrabold text-base text-white leading-tight">Ваш Синдикат</h3>
                        <p class="text-[10px] text-gray-400">Мужская родословная & общак</p>
                    </div>
                </div>
                <div class="bg-yellow-500/10 border border-yellow-500/30 px-3 py-1 rounded-full text-xs font-black text-yellow-400">
                    🏦 <span id="uiFamilyBank">0</span> 💎
                </div>
            </div>

            <div class="grid grid-cols-3 gap-2 mb-3 relative z-10">
                <div class="bg-black/35 p-2.5 rounded-2xl border border-white/10 text-center">
                    <div class="text-[9px] text-gray-400 uppercase tracking-wider mb-0.5">Муж / Партнёр</div>
                    <div class="font-extrabold text-xs text-white truncate" id="uiPartnerName">Холост</div>
                </div>
                <div class="bg-black/35 p-2.5 rounded-2xl border border-white/10 text-center">
                    <div class="text-[9px] text-gray-400 uppercase tracking-wider mb-0.5">Сыновья</div>
                    <div class="font-extrabold text-xs text-white"><span id="uiKidsCount" class="text-pink-400">0</span> / 3</div>
                </div>
                <div class="bg-black/35 p-2.5 rounded-2xl border border-white/10 text-center">
                    <div class="text-[9px] text-gray-400 uppercase tracking-wider mb-0.5">В клане</div>
                    <div class="font-extrabold text-xs text-sky-400"><span id="uiClanCount">1</span> чел.</div>
                </div>
            </div>

            <div class="bg-black/40 rounded-2xl p-3 border border-white/10 relative z-10">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-[10px] font-extrabold uppercase tracking-wider text-gray-400">🌳 Генеалогическое древо</span>
                    <span class="text-[9px] text-gray-500">роли относительно вас</span>
                </div>
                <div id="uiFamilyTreeList" class="space-y-1.5 text-xs"></div>
            </div>
        </div>

        """

def patch_webapp():
    path = os.path.join("templates", "webapp.html")
    if not os.path.exists(path):
        path = "webapp.html"
    bak = make_backup(path)
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    # 1. Заменяем карточку Синдиката на новую с родословной
    fam_pat = re.compile(
        r"<!-- 🔥 БЛОК СИНДИКАТА.*?<div class=\"surface rounded-2xl p-4 mb-4 text-center\">\s*<div id=\"slotIcon\"",
        re.DOTALL
    )
    if fam_pat.search(html):
        html = fam_pat.sub(
            lambda _: NEW_FAMILY_HTML + '<div class="surface rounded-2xl p-4 mb-4 text-center">\n            <div id="slotIcon"',
            html,
            count=1
        )

    # 2. Обновляем кнопку «Ещё» в нижнем меню, чтобы она вызывала toggleMoreMenu(this)
    html = html.replace(
        'id="navBtnMore" onclick="document.getElementById(\'moreMenuSheet\').classList.toggle(\'hidden\')"',
        'id="navBtnMore" onclick="toggleMoreMenu(this)"'
    )

    # 3. Обновляем switchTab(), чтобы шторка «Ещё» ВСЕГДА закрывалась при выборе любой вкладки
    switch_pat = re.compile(r"function switchTab\(tabId,\s*btnElement\)\s*\{")
    new_switch_open = """function toggleMoreMenu(btn) {
            safeHaptic('impact', 'light');
            const sheet = document.getElementById('moreMenuSheet');
            if (!sheet) return;
            const willOpen = sheet.classList.contains('hidden');
            sheet.classList.toggle('hidden');
            if (willOpen) {
                document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
                btn.classList.add('active');
            }
        }

        function switchTab(tabId, btnElement) {
            const moreSheet = document.getElementById('moreMenuSheet');
            if (moreSheet) moreSheet.classList.add('hidden');"""

    if "function toggleMoreMenu" not in html:
        html = switch_pat.sub(lambda _: new_switch_open, html, count=1)

    # 4. Обновляем отрисовку семьи и дерева внутри loadProfile()
    old_fam_js = re.compile(
        r"const famBlock = document\.getElementById\('familyBlock'\);.*?famBlock\.classList\.add\('hidden'\);\s*\}",
        re.DOTALL
    )
    new_fam_js = """const famBlock = document.getElementById('familyBlock');
                const treeNodes = data.family_tree || [];
                if (data.partner || data.kids > 0 || treeNodes.length > 0) {
                    famBlock.classList.remove('hidden');
                    document.getElementById('uiPartnerName').innerText = data.partner || "Холост";
                    document.getElementById('uiKidsCount').innerText = data.kids || "0";
                    document.getElementById('uiFamilyBank').innerText = fmtNum(data.family_balance || 0);
                    const clanCnt = treeNodes.reduce((acc, n) => acc + 1 + (n.partner ? 1 : 0), 0) || 1;
                    const clanEl = document.getElementById('uiClanCount');
                    if (clanEl) clanEl.innerText = clanCnt;

                    const badgeStyle = (role) => {
                        if (role === 'вы') return 'bg-sky-500/20 text-sky-300 border-sky-400/50';
                        if (role === 'муж') return 'bg-pink-500/20 text-pink-300 border-pink-400/50';
                        if (['отец', 'батя', 'дед', 'прадед', 'пращур', 'отец мужа', 'батя мужа'].includes(role))
                            return 'bg-amber-500/20 text-amber-300 border-amber-400/50';
                        if (['сын', 'внук', 'правнук', 'племянник', 'зять'].includes(role))
                            return 'bg-emerald-500/20 text-emerald-300 border-emerald-400/50';
                        return 'bg-indigo-500/20 text-indigo-300 border-indigo-400/40';
                    };

                    const treeBox = document.getElementById('uiFamilyTreeList');
                    if (treeBox) {
                        if (treeNodes.length === 0) {
                            treeBox.innerHTML = `<div class="text-gray-400 text-[11px]">Вы и ваш партнёр (${esc(data.partner)}) — основатели ветви.</div>`;
                        } else {
                            treeBox.innerHTML = treeNodes.map(n => {
                                const indent = Math.min(n.depth, 4) * 14;
                                const branch = n.depth > 0 ? '<span class="text-gray-600 mr-1">└─</span>' : '👑 ';
                                const uBadge = `<span class="text-[9px] font-extrabold uppercase px-1.5 py-0.5 rounded border ${badgeStyle(n.role)}">${esc(n.role)}</span>`;
                                let pHtml = '';
                                if (n.partner) {
                                    const pBadge = `<span class="text-[9px] font-extrabold uppercase px-1.5 py-0.5 rounded border ${badgeStyle(n.partner.role)}">${esc(n.partner.role)}</span>`;
                                    pHtml = ` <span class="text-pink-400 mx-0.5">💍</span> <span class="${n.partner.is_me ? 'font-black text-sky-300' : 'font-semibold text-gray-200'}">${esc(n.partner.name)}</span> ${pBadge}`;
                                }
                                return `<div class="flex flex-wrap items-center gap-1 py-1 px-2 rounded-xl bg-white/[0.03] border border-white/[0.05]" style="margin-left:${indent}px">
                                    ${branch}<span class="${n.is_me ? 'font-black text-sky-300' : 'font-semibold text-gray-200'}">${esc(n.name)}</span> ${uBadge}${pHtml}
                                </div>`;
                            }).join('');
                        }
                    }
                } else {
                    famBlock.classList.add('hidden');
                }"""

    if old_fam_js.search(html):
        html = old_fam_js.sub(lambda _: new_fam_js, html, count=1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"🎉 {path} обновлён (автоскрытие меню «Ещё» + неоновое древо Синдиката)!")

if __name__ == "__main__":
    print("🚀 Запуск патча v2 (Меню «Ещё» + Мужская родословная)...")
    patch_main()
    patch_webapp()
    print(f"\n✨ Готово! Бэкапы лежат в: {BACKUP_DIR}")