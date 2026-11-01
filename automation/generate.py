#!/usr/bin/env python3
"""Auto-publisher for دليل التقنية (https://wynm.github.io/agenhq).

Picks a topic (fresh tech news from RSS feeds, or an evergreen topic from
topics.json), asks Google Gemini for an original Arabic article as JSON,
checks it, renders it with the site's template, and updates index.html,
articles.html, sitemap.xml and feed.xml. The GitHub Actions workflow
(.github/workflows/auto-publish.yml) then commits and pushes.

Usage:
  python automation/generate.py                # real run (needs GEMINI_API_KEY)
  python automation/generate.py --mock         # no API: fake article, to test rendering
  python automation/generate.py --site DIR     # work on another checkout (tests)
Env: GEMINI_API_KEY (required for real runs), GEMINI_MODEL (optional override),
     POSTS_PER_RUN (optional), MODE = auto|news|evergreen (optional)
"""
import argparse
import datetime as dt
import email.utils
import html as H
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import site_template as T  # noqa: E402

UA = "Mozilla/5.0 (compatible; DaleelTechBot/1.0; +https://wynm.github.io/agenhq/)"
CATEGORIES = ["الربح من الإنترنت", "الذكاء الاصطناعي", "أخبار التقنية", "الهاتف", "الحاسوب", "الأمان الرقمي", "تطبيقات وأدوات"]
KICKER = {"الذكاء الاصطناعي": "AI", "أخبار التقنية": "NEWS", "الهاتف": "Mobile", "الحاسوب": "PC",
          "الأمان الرقمي": "Security", "الربح من الإنترنت": "Online", "تطبيقات وأدوات": "Apps"}
BANNED = ["ربح مضمون", "أرباح مضمونة", "دخل مضمون", "اغتنِ بسرعة", "اغتن بسرعة", "الثراء السريع بسهولة", "بدون أي مجهود",
          "دون أي مجهود", "بدون مجهود", "100% مضمون", "مضمون 100", "ستصبح مليونير", "ستصبح غنيًا", "اربح آلاف الدولارات يوميًا"]
STATIC_PAGES = ["about.html", "contact.html", "privacy.html", "terms.html"]


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- utils
def http_get(url, timeout=20, limit=3_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", H.unescape(s)).strip()


def norm_tokens(s):
    s = re.sub(r"[\u064B-\u0652\u0640]", "", s.lower())  # tashkeel / tatweel
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")
    return {w for w in re.findall(r"\w+", s) if len(w) > 2}


def similar(a, b):
    x, y = norm_tokens(a), norm_tokens(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return "-".join(s.split("-")[:7])[:60].strip("-")


def word_count(text):
    return len(re.sub(r"<[^>]+>", " ", text).split())


# ---------------------------------------------------------------- topics
def parse_feed(raw, name):
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        def get(*names):
            for n in names:
                for c in el:
                    if c.tag.split("}")[-1] == n:
                        return c
            return None
        title = strip_tags((get("title").text if get("title") is not None else "") or "")
        link_el = get("link")
        link = ""
        if link_el is not None:
            link = (link_el.text or "").strip() or link_el.get("href", "")
        summ_el = get("description", "summary", "content", "encoded")
        summary = strip_tags(summ_el.text if summ_el is not None else "")[:1500]
        d_el = get("pubDate", "published", "updated", "date")
        when = None
        if d_el is not None and d_el.text:
            try:
                when = email.utils.parsedate_to_datetime(d_el.text.strip())
            except Exception:
                try:
                    when = dt.datetime.fromisoformat(d_el.text.strip().replace("Z", "+00:00"))
                except Exception:
                    when = None
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        if title and link.startswith("http"):
            items.append({"title": title, "link": link, "summary": summary, "date": when, "source": name})
    return items


def pick_news(cfg, state):
    now = dt.datetime.now(dt.timezone.utc)
    max_age = dt.timedelta(hours=cfg.get("news_max_age_hours", 72))
    kws = [k.lower() for k in cfg.get("news_keywords", [])]
    skip = [k.lower() for k in cfg.get("news_skip_keywords", [])]
    used = set(state.get("used_sources", []))
    past_titles = [a["title"] for a in state["articles"]] + state.get("used_source_titles", [])
    cands = []
    for f in cfg.get("feeds", []):
        try:
            items = parse_feed(http_get(f["url"]), f["name"])
            log(f"  feed ok   {f['name']}: {len(items)} items")
        except Exception as ex:  # one broken feed must not stop the run
            log(f"  feed fail {f['name']}: {ex}")
            continue
        for it in items[:40]:
            if it["link"] in used or (it["date"] and now - it["date"] > max_age):
                continue
            text = f' {it["title"]} {it["summary"]} '.lower()
            if any(k in text for k in skip):
                continue
            score = sum(1 for k in kws if re.search(r"(?<!\w)" + re.escape(k), text))
            if score == 0:
                continue
            if any(similar(it["title"], t) > 0.5 for t in past_titles):
                continue
            age_h = (now - it["date"]).total_seconds() / 3600 if it["date"] else 48
            cands.append((score - age_h / 48, it))
    cands.sort(key=lambda x: -x[0])
    log(f"  {len(cands)} usable news items")
    return [c[1] for c in cands[:5]]


def pick_evergreen(topics, state):
    used = set(state.get("used_topics", []))
    free = [t for t in topics if t["key"] not in used]
    if not free:
        log("  all evergreen topics used — add new ones to automation/topics.json")
        return None
    # rotate categories: avoid the category of the last published article if possible
    last_tag = state["articles"][0]["tag"] if state["articles"] else None
    for t in free:
        if t["category"] != last_tag:
            return t
    return free[0]


def fetch_source_text(url):
    try:
        raw = http_get(url, timeout=20).decode("utf-8", "ignore")
    except Exception as ex:
        log(f"  could not fetch source page ({ex}); using feed summary only")
        return ""
    paras = [strip_tags(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", raw)]
    paras = [p for p in paras if len(p.split()) > 8]
    return "\n".join(paras)[:7000]


# ---------------------------------------------------------------- Gemini
SYSTEM = """أنت محرر محترف في موقع عربي اسمه «دليل التقنية» يكتب شروحات تقنية مبسطة ومفيدة للقارئ العربي (خصوصًا في المغرب والعالم العربي).
اكتب بالعربية الفصحى البسيطة والواضحة. اكتب محتوى أصليًا ومفيدًا عمليًا، لا حشو فيه.
قواعد صارمة:
- لا تخترع أي إحصائيات أو أرقام أو نسب أو أسعار أو تواريخ أو أسماء أو تصريحات. إذا لم تكن متأكدًا من رقم فلا تذكره، واكتب بصيغة عامة ونصح القارئ بالتحقق من المصدر الرسمي.
- في الأخبار: اعتمد فقط على الوقائع الموجودة في نص المصدر المعطى، وانسبها إلى المصدر بالاسم («بحسب ...»). لا تنسخ جملًا من المصدر، بل أعد الصياغة بأسلوبك واشرح السياق وماذا يعني ذلك للمستخدم العادي.
- في مواضيع الربح من الإنترنت: كن واقعيًا وصريحًا؛ لا وعود بالثراء السريع ولا أرباح مضمونة؛ اذكر الجهد والوقت المطلوبين، وحذّر من طرق النصب، ولا تروّج لأي منصة مقابل مال أو لطرق مخالفة للقانون أو لشروط المنصات.
- لا تكتب روابط داخل النص. يمكنك استعمال **نص** للتغميق فقط، بدون أي HTML أو Markdown آخر.
- أعد JSON صالحًا فقط، بدون أي نص قبله أو بعده."""

SCHEMA = """أعد كائن JSON بهذا الشكل بالضبط:
{
  "title": "عنوان جذاب وصادق (50-90 حرفًا)، بدون مبالغة",
  "slug": "short-english-url-slug-3-to-6-words",
  "short": "وصف قصير جدًا للصورة (أقل من 45 حرفًا)",
  "description": "وصف ميتا للمقال 130-160 حرفًا",
  "category": "واحد فقط من: %s",
  "lead": "فقرة افتتاحية من 60-90 كلمة",
  "sections": [ {"heading": "عنوان فرعي", "paragraphs": ["فقرة", "فقرة"], "bullets": ["نقطة اختيارية"]} ],
  "faq": [ {"q": "سؤال", "a": "جواب من 30-70 كلمة"} ],
  "outro": "خاتمة عملية من 30-60 كلمة"
}
المطلوب: من 5 إلى 7 أقسام، و3 إلى 4 أسئلة شائعة، ومجموع المقال كله بين %d و%d كلمة."""


def build_prompt(job, cfg):
    schema = SCHEMA % ("، ".join(CATEGORIES), cfg["min_words"], max(cfg["min_words"] + 300, 1200))
    if job["kind"] == "news":
        it = job["item"]
        when = it["date"].strftime("%Y-%m-%d") if it["date"] else "غير محدد"
        return f"""اكتب مقالًا إخباريًا تحليليًا أصليًا بالعربية عن الخبر التالي، يشرح ما حدث (بالاعتماد على المصدر فقط)، والسياق، وماذا يعني للمستخدم العادي، ونصائح عملية مرتبطة به إن وجدت.
اذكر اسم المصدر «{it['source']}» داخل النص عند نقل الوقائع. اجعل التصنيف «أخبار التقنية» أو تصنيفًا أدق من القائمة إذا كان أنسب.

المصدر: {it['source']}
تاريخ النشر: {when}
عنوان الخبر الأصلي: {it['title']}
ملخص الخبر: {it['summary'][:1200]}
نص المصدر (للاطلاع فقط، لا تنسخه):
{job.get('source_text', '')[:6000]}

{schema}"""
    t = job["topic"]
    return f"""اكتب دليلًا عمليًا أصليًا بالعربية عن الموضوع التالي:
«{t['topic']}»
التصنيف: {t['category']}
اجعل المقال خطوات وأمثلة قابلة للتطبيق، واذكر الأخطاء الشائعة وحدود كل طريقة بصراحة. لا تذكر أسعارًا أو أرقامًا دقيقة متغيرة؛ انصح بالتحقق من الصفحات الرسمية.

{schema}"""


def gemini_call(model, prompt, key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16384, "responseMimeType": "application/json"}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def list_flash_models(key):
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                     headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=30) as r:
            ms = json.loads(r.read()).get("models", [])
        names = [m["name"].split("/")[-1] for m in ms if "generateContent" in m.get("supportedGenerationMethods", [])]
        flash = [n for n in names if "flash" in n and not any(x in n for x in ("image", "tts", "audio", "live", "embedding"))]
        return sorted(flash, reverse=True)[:3]
    except Exception as ex:
        log(f"  could not list models: {ex}")
        return []


def parse_json(txt):
    txt = txt.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0) if m else txt)


def article_text(d):
    parts = [d.get("lead", "")]
    for s in d.get("sections", []):
        parts += [s.get("heading", "")] + s.get("paragraphs", []) + s.get("bullets", [])
    for f in d.get("faq", []):
        parts += [f.get("q", ""), f.get("a", "")]
    parts.append(d.get("outro", ""))
    return "\n".join(p for p in parts if isinstance(p, str))


def shingles(text, n=6):
    w = list(re.findall(r"\w+", text.lower()))
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def validate(d, job, cfg, state):
    problems = []
    for k in ("title", "description", "lead", "sections", "faq", "outro"):
        if not d.get(k):
            problems.append(f"missing field {k}")
    if problems:
        return problems
    text = article_text(d)
    n = len(text.split())
    if n < cfg["min_words"]:
        problems.append(f"too short: {n} words (min {cfg['min_words']})")
    if n > cfg.get("max_words", 1500) * 1.3:
        problems.append(f"too long: {n} words")
    if len(d["sections"]) < 4:
        problems.append("fewer than 4 sections")
    if len(d["faq"]) < 3:
        problems.append("fewer than 3 FAQ items")
    low = text.lower()
    for b in BANNED:
        if b in low:
            problems.append(f"banned get-rich-quick phrase: {b}")
    if re.search(r"https?://|www\.|<[a-z/]", text):
        problems.append("contains links or HTML in text")
    for a in state["articles"]:
        if similar(d["title"], a["title"]) > 0.6:
            problems.append(f"title too similar to existing article {a['slug']}")
    if job["kind"] == "news":
        src = shingles(job["item"]["summary"] + " " + job.get("source_text", ""))
        mine = shingles(text)
        if src and mine:
            overlap = len(src & mine) / len(mine)
            if overlap > 0.06:
                problems.append(f"copies source text ({overlap:.0%} 6-word overlap)")
        if job["item"]["source"] not in text and not re.search(r"بحسب|وفقًا|وفقا|حسب", text):
            problems.append("news article does not attribute the source")
    return problems


def generate(job, cfg, key, models):
    prompt = build_prompt(job, cfg)
    last = "no model tried"
    for model in models:
        feedback = ""
        for attempt in range(2):
            p = prompt + (f"\n\nالمحاولة السابقة رُفضت للأسباب التالية، صحّحها:\n{feedback}" if feedback else "")
            for net_try in range(3):
                try:
                    log(f"  calling {model} (attempt {attempt + 1})")
                    raw = gemini_call(model, p, key)
                    break
                except urllib.error.HTTPError as ex:
                    msg = ex.read().decode("utf-8", "ignore")[:300]
                    last = f"{model}: HTTP {ex.code} {msg}"
                    log("  " + last)
                    if ex.code in (429, 500, 503) and net_try < 2:
                        time.sleep(20 * (net_try + 1))
                        continue
                    raw = None
                    break
                except Exception as ex:
                    last = f"{model}: {ex}"
                    log("  " + last)
                    raw = None
                    if net_try < 2:
                        time.sleep(10)
                        continue
                    break
            if raw is None:
                break  # next model
            try:
                d = parse_json(raw)
            except Exception as ex:
                feedback = f"- الرد لم يكن JSON صالحًا ({ex})"
                last = f"{model}: invalid JSON"
                continue
            problems = validate(d, job, cfg, STATE)
            if not problems:
                d["_model"] = model
                return d
            last = f"{model}: " + "; ".join(problems)
            log("  rejected: " + "; ".join(problems))
            feedback = "\n".join("- " + x for x in problems)
    raise RuntimeError(f"generation failed: {last}")


# ---------------------------------------------------------------- mock (tests without API key)
def mock_article(job):
    title = job["item"]["title"] if job["kind"] == "news" else job["topic"]["topic"]
    cat = "أخبار التقنية" if job["kind"] == "news" else job["topic"]["category"]
    para = ("هذه فقرة تجريبية لاختبار القالب فقط، تشرح الفكرة بخطوات واضحة وأمثلة عملية يمكن للقارئ تطبيقها في حياته اليومية "
            "دون تعقيد، مع التنبيه إلى الأخطاء الشائعة وحدود كل طريقة، والتذكير بالتحقق من المصادر الرسمية قبل أي قرار مهم.")
    src = f"بحسب {job['item']['source']}، " if job["kind"] == "news" else ""
    return {
        "title": f"[تجربة] {title}"[:90], "slug": "mock-" + (job.get("topic", {}).get("key") or "news-item"),
        "short": "مقال تجريبي", "description": "مقال تجريبي لاختبار النشر التلقائي في موقع دليل التقنية دون استعمال واجهة Gemini. " * 2,
        "category": cat, "lead": src + para + " " + para,
        "sections": [{"heading": f"القسم {i}: خطوات عملية", "paragraphs": [para, para, para],
                      "bullets": ["**نقطة أولى:** " + para[:60], "نقطة ثانية للتجربة"] if i % 2 else []} for i in range(1, 7)],
        "faq": [{"q": f"سؤال تجريبي رقم {i}؟", "a": para} for i in range(1, 4)],
        "outro": para, "_model": "mock"}


# ---------------------------------------------------------------- rendering
def inline(s):
    s = H.escape(s.strip())
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s).replace("**", "")


def render_body(d, job, cfg):
    out = [f'<p class="lead">{inline(d["lead"])}</p>']
    tag = d["category"]
    if tag == "الربح من الإنترنت":
        out.append('<div class="note"><strong>تنبيه:</strong> لا توجد طريقة مضمونة للربح السريع من الإنترنت. النتائج تختلف حسب المهارة والوقت والجهد، '
                   'واحذر من أي عرض يطلب منك دفع مال مسبقًا مقابل «أرباح مضمونة».</div>')
    if job["kind"] == "news":
        it = job["item"]
        out.append(f'<div class="note"><strong>مصدر الخبر:</strong> <a href="{H.escape(it["link"])}" target="_blank" rel="noopener nofollow">'
                   f'{H.escape(it["source"])}</a> — نعيد شرح الخبر وتبسيطه بالعربية؛ راجع المصدر الأصلي للتفاصيل الكاملة.</div>')
    secs = d["sections"]
    mid = max(1, len(secs) // 2)
    for i, s in enumerate(secs, 1):
        out.append(f'\n<h2 id="s{i}">{inline(s.get("heading", ""))}</h2>')
        for p in s.get("paragraphs", []):
            if p.strip():
                out.append(f"<p>{inline(p)}</p>")
        bl = [b for b in s.get("bullets", []) or [] if b.strip()]
        if bl:
            out.append("<ul>\n" + "\n".join(f"<li>{inline(b)}</li>" for b in bl) + "\n</ul>")
        if i == mid:
            out.append("\n{{AD_MID}}")
    out.append('\n<h2 id="faq">أسئلة شائعة</h2>')
    for f in d["faq"]:
        out.append(f"<h3>{inline(f['q'])}</h3>\n<p>{inline(f['a'])}</p>")
    if job["kind"] == "news":
        it = job["item"]
        when = f' ({it["date"].strftime("%Y-%m-%d")})' if it["date"] else ""
        out.append(f'\n<h2 id="sources">المصادر</h2>\n<ul><li><a href="{H.escape(it["link"])}" target="_blank" rel="noopener nofollow">'
                   f'{H.escape(it["title"])}</a> — {H.escape(it["source"])}{when}</li></ul>')
    out.append(f'\n<p class="outro">{inline(d["outro"])}</p>')
    if cfg.get("ai_disclosure", True):
        out.append('<p style="font-size:14px;color:#6b7c88">أُعدّ هذا المقال بمساعدة أدوات الذكاء الاصطناعي'
                   + (" اعتمادًا على المصدر المذكور أعلاه" if job["kind"] == "news" else "")
                   + '. إذا لاحظت خطأ، راسلنا من صفحة <a href="contact.html">اتصل بنا</a> لنصحّحه.</p>')
    return "\n".join(out)


def _font(name, size):
    from PIL import ImageFont
    return ImageFont.truetype(str(HERE / "fonts" / name), size, layout_engine=ImageFont.Layout.RAQM)


AR_OK = "\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF«»\\-:,.!؟،؛0-9٪ "
MIRROR = str.maketrans("()[]{}<>", ")(][}{><")


def _pieces(text):
    """Split one line into visual pieces, right to left: (string, 'ar'|'lat', direction)."""
    out = []
    for i, part in enumerate(re.split(r"([A-Za-z](?:[A-Za-z0-9 .\-_+&'/]*[A-Za-z0-9])?)", text)):
        if not part:
            continue
        if i % 2:  # latin run, kept left-to-right
            out.append((part, "lat", "ltr"))
            continue
        for j, ch in enumerate(re.split(f"([^{AR_OK}]+)", part)):
            if not ch:
                continue
            if j % 2:  # punctuation the Arabic font lacks: mirror + reverse, draw with latin font
                out.append((ch.translate(MIRROR)[::-1], "lat", "ltr"))
            else:
                out.append((ch, "ar", "rtl"))
    return out


def _fonts(size, bold):
    return (_font("NotoSansArabic-Bold.ttf" if bold else "NotoSansArabic-Regular.ttf", size),
            _font("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf", size))


def _draw_line(draw, text, right, y, size, bold, color):
    fa, fl = _fonts(size, bold)
    x = right
    for s, kind, d in _pieces(text):
        f = fa if kind == "ar" else fl
        w = f.getlength(s, direction=d)
        if draw is not None and s.strip():
            draw.text((x - w, y), s, font=f, fill=color, direction=d, anchor="ls")
        x -= w
    return right - x


def _wrap(text, size, bold, width):
    lines, cur = [], []
    for w in text.split():
        if cur and _draw_line(None, " ".join(cur + [w]), 0, 0, size, bold, None) > width:
            lines.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    return lines


def make_og(title, category, out_path):
    """1200x630 cover in the site's style. Returns False if Arabic shaping is unavailable."""
    try:
        from PIL import Image, ImageDraw, features
        if not features.check("raqm"):
            log("  Pillow has no raqm (Arabic shaping) — using og-home.png")
            return False
        img = Image.new("RGB", (1200, 630), (15, 118, 110))
        dr = ImageDraw.Draw(img)
        dr.rounded_rectangle((40, 40, 1160, 590), radius=28, fill=(34, 128, 121))
        _draw_line(dr, KICKER.get(category, "Tech"), 1080, 170, 60, False, (240, 250, 248))
        size = 60
        while True:
            lines = _wrap(title, size, True, 1000)
            if (len(lines) <= 3 and len(lines) * size * 1.45 <= 270) or size <= 36:
                break
            size -= 4
        lines = lines[:3]
        y = 175 + int(size * 1.45)
        for ln in lines:
            _draw_line(dr, ln, 1080, y, size, True, (255, 255, 255))
            y += int(size * 1.45)
        _draw_line(dr, category, 1080, 505, 32, False, (220, 238, 235))
        _draw_line(dr, "daleel-tech - دليل التقنية", 1080, 555, 24, False, (190, 220, 215))
        img.save(out_path, optimize=True)
        return True
    except Exception as ex:
        log(f"  OG image failed ({ex}) — using og-home.png")
        return False


def update_index(site, articles, cfg):
    p = site / "index.html"
    html = p.read_text(encoding="utf-8")
    home = articles[: cfg.get("home_max_articles", 12)]
    grid = "".join(T.card(a, "h2") for a in home)
    more = '\n<p style="text-align:center;margin:18px 0"><a class="chip" href="articles.html">كل المقالات ←</a></p>'
    block = f"<!-- AUTO:ARTICLES:START -->{grid}<!-- AUTO:ARTICLES:END -->"
    if "<!-- AUTO:ARTICLES:START -->" in html:
        html = re.sub(r"<!-- AUTO:ARTICLES:START -->.*?<!-- AUTO:ARTICLES:END -->", lambda m: block, html, flags=re.S)
    else:
        pat = re.compile(r'(<h2 class="section-title" id="articles">[^<]*</h2>\s*<div class="grid">)(.*?)(</div>\s*<div class="ad-slot ad-native">)', re.S)
        if not pat.search(html):
            raise RuntimeError("index.html: could not find the articles grid")
        html = pat.sub(lambda m: m.group(1) + block + m.group(3), html, count=1)
    if 'href="articles.html">كل المقالات' not in html:
        html = html.replace("<!-- AUTO:ARTICLES:END --></div>", "<!-- AUTO:ARTICLES:END --></div>" + more, 1)
    if 'type="application/rss+xml"' not in html:
        html = html.replace("<!-- ADSTERRA_HEAD_CODES", T.RSS_LINK + "\n<!-- ADSTERRA_HEAD_CODES", 1)
    p.write_text(html, encoding="utf-8")


def write_sitemap(site, articles):
    base = T.C["site_url"]
    newest = articles[0]["date"] if articles else dt.date.today().isoformat()
    rows = [("", newest, "1.0")] + [(f'{a["slug"]}.html', a["date"], "0.8") for a in articles] + [("articles.html", newest, "0.5")]
    rows += [(p, "2026-11-01", "0.3") for p in STATIC_PAGES if (site / p).exists()]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    sm += [f"  <url><loc>{base}/{p}</loc><lastmod>{d}</lastmod><priority>{pr}</priority></url>" for p, d, pr in rows]
    sm.append("</urlset>")
    (site / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")


def write_feed(site, articles, n):
    base = T.C["site_url"]
    items = []
    for a in articles[:n]:
        d = dt.datetime.fromisoformat(a.get("published_at") or a["date"] + "T08:00:00+00:00")
        items.append(f"""  <item><title>{H.escape(a['title'])}</title><link>{base}/{a['slug']}.html</link>
    <guid isPermaLink="true">{base}/{a['slug']}.html</guid><pubDate>{email.utils.format_datetime(d)}</pubDate>
    <category>{H.escape(a['tag'])}</category><description>{H.escape(a['desc'])}</description></item>""")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>{H.escape(T.C['site_name'])}</title><link>{base}/</link>
  <description>شروحات تقنية مبسطة بالعربية: الذكاء الاصطناعي، الربح من الإنترنت، أخبار التقنية، الهاتف والحاسوب.</description>
  <language>ar</language>
{chr(10).join(items)}
</channel></rss>
"""
    (site / "feed.xml").write_text(xml, encoding="utf-8")


def publish(site, d, job, cfg, state):
    today = dt.datetime.now(dt.timezone.utc)
    cat = d.get("category") if d.get("category") in CATEGORIES else ("أخبار التقنية" if job["kind"] == "news" else job["topic"]["category"])
    d["category"] = cat
    slug = slugify(d.get("slug")) or f"post-{today:%Y%m%d}"
    existing = {a["slug"] for a in state["articles"]} | {p.stem for p in site.glob("*.html")}
    base_slug, i = slug, 2
    while slug in existing:
        slug, i = f"{base_slug}-{i}", i + 1
    entry = {"slug": slug, "title": d["title"].strip(), "short": (d.get("short") or d["title"])[:60].strip(),
             "desc": d["description"].strip(), "tag": cat, "date": today.date().isoformat(),
             "published_at": today.replace(microsecond=0).isoformat(), "auto": True, "model": d.get("_model"),
             "kind": job["kind"]}
    img = f"og-{slug}.png"
    entry["image"] = img if make_og(entry["title"], cat, site / "img" / img) else "og-home.png"
    body = render_body(d, job, cfg)
    others = [a for a in state["articles"]][:3]
    html, n = T.article_page(entry, body, others)
    entry["words"] = n
    (site / f"{slug}.html").write_text(html, encoding="utf-8")
    state["articles"].insert(0, entry)
    if job["kind"] == "news":
        state.setdefault("used_sources", []).append(job["item"]["link"])
        state.setdefault("used_source_titles", []).append(job["item"]["title"])
    else:
        state.setdefault("used_topics", []).append(job["topic"]["key"])
    log(f"  published {slug}.html ({n} words, {cat}, image {entry['image']})")
    return entry


def rebuild_lists(site, state, cfg):
    arts = state["articles"]
    update_index(site, arts, cfg)
    (site / "articles.html").write_text(T.archive_page(arts), encoding="utf-8")
    write_sitemap(site, arts)
    write_feed(site, arts, cfg.get("feed_items", 20))


# ---------------------------------------------------------------- main
STATE = {}


def main():
    global STATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=str(HERE.parent), help="site root (default: repo root)")
    ap.add_argument("--mock", action="store_true", help="no API call: publish a fake test article")
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--mode", choices=["auto", "news", "evergreen"], default=None)
    ap.add_argument("--rebuild-only", action="store_true", help="only rebuild index/articles/sitemap/feed")
    args = ap.parse_args()
    site = pathlib.Path(args.site).resolve()
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    topics = json.loads((HERE / "topics.json").read_text(encoding="utf-8"))["topics"]
    state_path = HERE / "published.json"
    STATE = json.loads(state_path.read_text(encoding="utf-8"))

    if args.rebuild_only:
        rebuild_lists(site, STATE, cfg)
        log("rebuilt lists")
        return 0

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key and not args.mock:
        msg = ("GEMINI_API_KEY is not set — nothing published. Add it in GitHub: Settings → Secrets and variables → "
               "Actions → New repository secret (name: GEMINI_API_KEY).")
        log(f"::warning::{msg}")
        return 0

    count = args.count or int(os.environ.get("POSTS_PER_RUN") or 0) or int(cfg.get("posts_per_run", 1))
    count = max(1, min(count, 5))
    mode = args.mode or (os.environ.get("MODE") or "auto").strip() or "auto"
    models = [m for m in [os.environ.get("GEMINI_MODEL", "").strip()] if m] + cfg.get("models", [])
    models = list(dict.fromkeys(models))
    if key and not args.mock:
        models += [m for m in list_flash_models(key) if m not in models]
    log(f"site={site} count={count} mode={mode} models={models}")

    published, failures = [], []
    for n in range(count):
        auto_count = sum(1 for a in STATE["articles"] if a.get("auto"))
        every = int(cfg.get("news_every", 2) or 0)
        want_news = mode == "news" or (mode == "auto" and every > 0 and auto_count % every == 0)
        jobs = []
        if want_news:
            log("picking a news item…")
            jobs += [{"kind": "news", "item": it} for it in pick_news(cfg, STATE)[:2]]
        if mode != "news":
            t = pick_evergreen(topics, STATE)
            if t:
                jobs.append({"kind": "evergreen", "topic": t})
        if not jobs:
            failures.append("no topic available (no fresh news and no unused evergreen topic)")
            continue
        done = False
        for job in jobs:
            label = job["item"]["title"] if job["kind"] == "news" else job["topic"]["topic"]
            log(f"[{n + 1}/{count}] {job['kind']}: {label}")
            if job["kind"] == "news":
                job["source_text"] = fetch_source_text(job["item"]["link"])
            try:
                d = mock_article(job) if args.mock else generate(job, cfg, key, models)
                if args.mock:
                    log("  mock validation: " + ("; ".join(validate(d, job, cfg, STATE)) or "OK"))
            except Exception as ex:
                log(f"  ✗ {ex}")
                failures.append(str(ex))
                continue
            published.append(publish(site, d, job, cfg, STATE))
            done = True
            break
        if not done:
            log("  nothing published for this slot")

    if published:
        rebuild_lists(site, STATE, cfg)
        state_path.write_text(json.dumps(STATE, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        log("PUBLISHED: " + ", ".join(a["slug"] for a in published))
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"published={' '.join(a['slug'] for a in published)}\n")
    if failures and not published:
        log("::error::No article published. " + " | ".join(failures)[:900])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
