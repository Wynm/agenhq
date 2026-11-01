"""Page template shared with the original site build (site/src/build.py).

Same <head> (canonical, OpenGraph, Clickaine meta), same ad placeholders
(ADSTERRA_*, CLICKAINE_*), same footer with the HilltopAds sponsored link.
If you change the look of the site, change it here too.
"""
import html as H
import re
from urllib.parse import quote

CONFIG = {
    "site_name": "دليل التقنية",
    "site_url": "https://wynm.github.io/agenhq",
    "hilltop_link": "https://motionless-bus.com/OZwdbp",
    "clickaine": "f5ba98b6756eb503f321cc8fbdf05f2835de8df2d45303060dd83e6c0fe429079fd2e927cec5ce80ad86c2e3172e13b832f202ecb557f3a18af8d8fcc2b6a148",
}
C = CONFIG
e = H.escape

# Ad codes for NEW auto-published pages: paste each network code in automation/ads.json
# (same names as the placeholders). Empty = keep the visible placeholder like the other pages.
try:
    import json as _json, pathlib as _pl
    ADS = {k: v.strip() for k, v in _json.loads((_pl.Path(__file__).parent / "ads.json").read_text(encoding="utf-8")).items()
           if not k.startswith("_") and isinstance(v, str)}
except FileNotFoundError:
    ADS = {}

MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "ماي", "يونيو", "يوليوز", "غشت", "شتنبر", "أكتوبر", "نونبر", "دجنبر"]


def date_ar(iso):
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{d} {MONTHS_AR[m - 1]} {y}"


def ad(kind, code_name, hint):
    sizes = {"320x50": "ad-320x50", "300x250": "ad-300x250", "native": "ad-native", "sidebar": "ad-sidebar"}
    label = "إعلان" if kind != "native" else "إعلانات مقترحة (Sponsored)"
    ph = {"320x50": "مساحة إعلانية <bdi dir=\"ltr\">320×50</bdi>", "300x250": "مساحة إعلانية <bdi dir=\"ltr\">300×250</bdi>",
          "native": "مساحة Native Banner", "sidebar": "مساحة إعلانية <bdi dir=\"ltr\">300×250</bdi>"}[kind]
    if ADS.get(code_name):
        return f'''<div class="ad-slot {sizes[kind]}">
  <span class="ad-label">{label}</span>
  <!-- {code_name} -->
{ADS[code_name]}
</div>'''
    return f'''<div class="ad-slot {sizes[kind]}">
  <span class="ad-label">{label}</span>
  <!-- {code_name}: {hint} -->
  <!-- احذف السطر الموالي (ad-placeholder) بعد لصق الكود -->
  <div class="ad-placeholder">{ph}</div>
</div>'''


AD_TOP = ad("320x50", "ADSTERRA_BANNER_320x50", "الصق هنا كود Banner 320x50 من Adsterra")
AD_MID = ad("300x250", "ADSTERRA_BANNER_300x250", "الصق هنا كود Banner 300x250 من Adsterra")
AD_NATIVE = ad("native", "ADSTERRA_NATIVE_BANNER", "الصق هنا كود Native Banner من Adsterra (السكريبت + div)")
AD_SIDE = ad("sidebar", "CLICKAINE_BANNER_OR_NATIVE", "الصق هنا كود زون Clickaine (Banner أو Native) — يظهر فالحاسوب فقط")

BODY_END = '''
<!-- ADSTERRA_SOCIAL_BAR: الصق هنا كود Social Bar (سكريبت واحد فقط).
     لا تضف معه In-Page Push ديال Clickaine فنفس الصفحة. -->
<!-- ADSTERRA_POPUNDER (اختياري، معطّل): لا تفعّله إذا كان الزوار جايين من فيسبوك/إنستغرام.
     سياسة Meta كتعتبر pop-ups/unders واجهة مضللة وتقدر تبلوكي روابط الدومين ديالك. -->
__SOCIAL__<script>
document.querySelectorAll('.cp').forEach(function(b){b.addEventListener('click',function(){
  navigator.clipboard&&navigator.clipboard.writeText(location.href).then(function(){b.textContent='تم نسخ الرابط ✓'});});});
</script>'''

BODY_END = BODY_END.replace("__SOCIAL__", (ADS.get("ADSTERRA_SOCIAL_BAR", "") + "\n") if ADS.get("ADSTERRA_SOCIAL_BAR") else "")

RSS_LINK = f'<link rel="alternate" type="application/rss+xml" title="{C["site_name"]}" href="feed.xml">'


def head(title, desc, path, og_img, og_type="website", extra=""):
    url = f'{C["site_url"]}/{path}'.rstrip("/") if path else C["site_url"] + "/"
    full_title = title if title == C["site_name"] else f"{title} | {C['site_name']}"
    img = f'{C["site_url"]}/img/{og_img}'
    return f'''<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(full_title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{url}">
<meta name="clickaine-site-verification" content="{C["clickaine"]}" />
<meta name="robots" content="index, follow">
<meta name="theme-color" content="#0f766e">
<meta property="og:locale" content="ar_AR">
<meta property="og:site_name" content="{e(C["site_name"])}">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{img}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="img/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="style.css">
{extra}
<!-- ADSTERRA_HEAD_CODES: إلا عطاتك شي شبكة كود خاصو يتحط فـ head، حطو هنا -->{("\n" + ADS["ADSTERRA_HEAD_CODES"]) if ADS.get("ADSTERRA_HEAD_CODES") else ""}
</head>
<body>
<header class="site-header"><div class="wrap">
  <a class="logo" href="index.html"><span class="logo-mark">د</span>{e(C["site_name"])}</a>
  <nav class="nav"><a href="index.html">الرئيسية</a><a class="hide-sm" href="index.html#articles">المقالات</a><a href="about.html">من نحن</a><a href="contact.html">اتصل بنا</a></nav>
</div></header>
'''


def footer():
    return f'''<footer class="site-footer"><div class="wrap">
  <div class="foot-grid">
    <div><strong>{e(C["site_name"])}</strong><br>شروحات تقنية مبسّطة بالعربية: الذكاء الاصطناعي، الهاتف، والأمان الرقمي.
      <div class="foot-links" style="margin-top:10px"><a href="about.html">من نحن</a><a href="contact.html">اتصل بنا</a><a href="privacy.html">سياسة الخصوصية</a><a href="terms.html">شروط الاستخدام</a></div></div>
    <div class="sponsored-box">
      <span class="ad-label">إعلان · Sponsored</span>
      <!-- HILLTOPADS_DIRECTLINK: رابط إعلاني اختياري وواضح. لا تغيّر النص لشيء مضلل ولا تحوّلو لزر "تحميل". -->
      <a href="{C["hilltop_link"]}" target="_blank" rel="sponsored nofollow noopener">عروض مختارة من المعلنين ←</a>
      <div style="font-size:13px;color:#8fa1ad">رابط إعلاني لطرف ثالث، يُفتح في نافذة جديدة.</div>
    </div>
  </div>
  <div class="copy">© 2026 {e(C["site_name"])}. المحتوى لأغراض تعليمية. هذا الموقع يعرض إعلانات من شبكات إعلانية خارجية.</div>
</div></footer>
{BODY_END}
</body></html>'''


def words(s):
    return len(re.sub(r"<[^>]+>", " ", s).split())


def card(a, h="h3"):
    img = a.get("image") or f'og-{a["slug"]}.png'
    return f'''<a class="card" href="{a["slug"]}.html">
  <img src="img/{img}" alt="{e(a["short"])}" loading="lazy" width="1200" height="630">
  <div class="body"><span class="tag">{e(a["tag"])}</span><{h}>{e(a["title"])}</{h}><p>{e(a["desc"])}</p></div></a>'''


def article_page(a, body, others):
    """a: manifest entry (slug,title,short,desc,tag,date,image). body: inner HTML with {{AD_MID}}."""
    import json
    n_words = words(body)
    mins = max(4, round(n_words / 180))
    body = body.replace("{{AD_MID}}", AD_MID)
    img = a.get("image") or f'og-{a["slug"]}.png'
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": a["title"],
          "description": a["desc"], "inLanguage": "ar", "datePublished": a["date"], "dateModified": a["date"],
          "image": f'{C["site_url"]}/img/{img}',
          "author": {"@type": "Organization", "name": C["site_name"]},
          "publisher": {"@type": "Organization", "name": C["site_name"]},
          "mainEntityOfPage": f'{C["site_url"]}/{a["slug"]}.html'}
    extra = f'<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>\n{RSS_LINK}'
    share_url = f'{C["site_url"]}/{a["slug"]}.html'
    html = head(a["title"], a["desc"], f'{a["slug"]}.html', img, "article", extra)
    toc = "".join(f'<li><a href="#{m[0]}">{m[1]}</a></li>' for m in re.findall(r'<h2 id="([^"]+)">([^<]+)</h2>', body)[:8])
    html += f'''<div class="wrap">{AD_TOP}
<div class="layout">
<article>
  <div class="crumbs"><a href="index.html">الرئيسية</a> › {e(a["tag"])}</div>
  <h1>{e(a["title"])}</h1>
  <div class="meta">آخر تحديث: {date_ar(a["date"])} · مدة القراءة: {mins} دقائق</div>
  <img class="cover" src="img/{img}" alt="{e(a["short"])}" width="1200" height="630">
  {body}
  <div class="share">
    <a class="fb" href="https://www.facebook.com/sharer/sharer.php?u={quote(share_url)}" target="_blank" rel="noopener">شارك على فيسبوك</a>
    <a class="wa" href="https://wa.me/?text={quote(a["title"] + " " + share_url)}" target="_blank" rel="noopener">أرسل في واتساب</a>
    <button class="cp" type="button">انسخ الرابط</button>
  </div>
  {AD_NATIVE}
  <h2 class="section-title">اقرأ أيضًا</h2>
  <div class="grid">{"".join(card(o) for o in others)}</div>
</article>
<aside class="sidebar">
  <div class="box"><h3>محتوى المقال</h3><ul>{toc}</ul></div>
  {AD_SIDE}
</aside>
</div></div>
''' + footer()
    return html, n_words


def archive_page(articles):
    html = head("كل المقالات", f"جميع مقالات ودلائل {C['site_name']}: الذكاء الاصطناعي، الربح من الإنترنت، أخبار التقنية، الهاتف والحاسوب.",
                "articles.html", "og-home.png", extra=RSS_LINK)
    html += f'''<div class="wrap">
{AD_TOP}
<h1 class="section-title">كل المقالات</h1>
<div class="grid">{"".join(card(a, "h2") for a in articles)}</div>
{AD_NATIVE}
</div>
''' + footer()
    return html
