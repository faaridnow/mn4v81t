import nest_asyncio
import asyncio
from playwright.async_api import async_playwright

# Playwright-ın sinxron əsas dövrlərlə toqquşmaması üçün Colab-da etdiyimiz kimi aktivləşdiririk
nest_asyncio.apply()

import requests
import re
import sys
import urllib.parse

# ==============================================================================
# PLAYWRIGHT ÜMUMİ AYARLARI (CI mühitində sabitlik üçün optimallaşdırılıb)
# ==============================================================================
# Əvvəllər hər kanal üçün ayrı-ayrı brauzer açılırdı (~200+ dəfə chromium
# başlatmaq). Bu, GitHub Actions runner-də CPU/RAM tükənməsinə və nəticədə
# page.goto() timeout-larına səbəb olurdu. İndi BİR brauzer instansı bütün
# playwright əsaslı kanallar üçün paylaşılır, hər kanal üçün yalnız yeni
# context/page açılır - bu, həm sürəti, həm də etibarlılığı artırır.
PW_GOTO_TIMEOUT = 60000     # 60s -> 90s: CI-da şəbəkə gecikmələrinə tolerantlıq
PW_NAV_RETRIES = 2          # goto uğursuz olarsa, yenidən cəhd sayı
PW_RETRY_BACKOFF = 3        # cəhdlər arası gözləmə (saniyə)

# Şəkil/font/media/css yüklənməsini bloklamaq səhifə açılışını sürətləndirir
# və CI-da lazımsız trafiki azaldaraq timeout riskini aşağı salır.
BLOCK_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}


async def _block_heavy_resources(route):
    if route.request.resource_type in BLOCK_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def _goto_with_retry(page, url, timeout=PW_GOTO_TIMEOUT, retries=PW_NAV_RETRIES):
    """page.goto() üçün retry məntiqi - müvəqqəti şəbəkə problemlərinə qarşı."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return True
        except Exception as e:
            last_exc = e
            print(f"   [Naviqasiya cəhdi {attempt}/{retries} uğursuz]: {e}")
            if attempt < retries:
                await asyncio.sleep(PW_RETRY_BACKOFF)
    if last_exc:
        raise last_exc
    return False


async def _capture_m3u8(browser, url, capture="response", click=False, wait_after=10000):
    """
    Paylaşılan brauzer instansı üzərində yeni context/page açıb m3u8
    linklərini tutan ortaq funksiya. Bütün playwright handler-ləri bunu
    istifadə edir ki, hər kanal üçün yenidən brauzer başlatmasın.
    """
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()
    await page.route("**/*", _block_heavy_resources)

    links = []

    def on_traffic(obj):
        u = obj.url
        if ".m3u8" in u:
            links.append(u)

    if capture == "response":
        page.on("response", on_traffic)
    else:
        page.on("request", on_traffic)

    try:
        await _goto_with_retry(page, url)
        if click:
            try:
                await page.mouse.click(500, 500)
            except Exception:
                pass
        await page.wait_for_timeout(wait_after)
    except Exception as e:
        print(f"   [Playwright Xətası]: {e}")
    finally:
        await context.close()

    return list(set(links))

# ==============================================================================
# HƏR TİP ÜÇÜN XÜSUSİ METODLAR (HANDLERS)
# ==============================================================================

def handle_direct(kanal):
    """Tip 1: Heç bir qoruması olmayan sabit statik linklər (Məsələn: ATV)"""
    return kanal["stream_url"]

def handle_token_yoda(kanal, headers):
    """Tip 2: YodaCDN işlədən dinamik kanallar (AzTV, İdman TV və s.)"""
    print(f'   [YodaCDN] Səhifə yüklənir: {kanal["url"]}')
    
    req_headers = headers.copy()
    if "url_referer" in kanal:
        req_headers['Referer'] = kanal["url_referer"]
        
    try:
        res = requests.get(kanal["url"], headers=req_headers, timeout=15)
        res.raise_for_status()
        
        iframe_link = re.search(r'src=["\'](https?://[^"\']+?(?:yodaplayer|embed|player|cdn)[^"\']*?)["\']', res.text)
        if not iframe_link:
            if "yodaplayer" in kanal["url"] or "yoda.az" in kanal["url"]:
                gizli_url = kanal["url"]
            else:
                return None
        else:
            gizli_url = iframe_link.group(1)
        
        iframe_headers = headers.copy()
        iframe_headers['Referer'] = kanal.get("url_referer", kanal["url"])
        
        print(f'   [YodaCDN] Token axtarılır: {gizli_url}')
        iframe_res = requests.get(gizli_url, headers=iframe_headers, timeout=15)
        iframe_res.raise_for_status()
        
        token_tapildi = re.search(r'data-token\s*=\s*["\']?([^"\'\s>]+)["\']?', iframe_res.text)
        
        if token_tapildi:
            token = token_tapildi.group(1)
            return f"{kanal['stream_base']}?token={token}|Origin={kanal['referer']}&Referer={kanal['referer']}&User-Agent=Mozilla/5.0"
            
    except Exception as e:
        print(f'   [YodaCDN Xətası]: {e}')
        
    return None

def handle_generic_scraper(kanal, headers):
    """Tip 3: Səhifənin kodunda birbaşa .m3u8 axtaran metod"""
    print(f'   [Scraper] Səhifə kodu analiz edilir: {kanal["url"]}')
    try:
        res = requests.get(kanal["url"], headers=headers, timeout=15)
        res.raise_for_status()
        
        m3u8_tapildi = re.search(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', res.text)
        if m3u8_tapildi:
            link = m3u8_tapildi.group(0)
            if "referer_req" in kanal:
                link = f"{link}|Referer={kanal['referer_req']}&User-Agent=Mozilla/5.0"
            return link
    except Exception as e:
        print(f'   [Scraper Xətası]: {e}')
    return None

def handle_trt(kanal, headers):
    """TRT kanalları üçün tamamilə təcrid olunmuş xüsusi bilet ovçusu"""
    print(f'   [TRT] Xüsusi kəşfiyyat başladılıb: {kanal["ad"]}')
    
    local_headers = headers.copy()
    local_headers['Referer'] = 'https://www.trtizle.com/'
    
    try:
        res = requests.get(kanal["url"], headers=local_headers, timeout=12)
        res.raise_for_status()
        
        # Sənin kəşf etdiyin uğurlu aqressiv pattern
        pattern = r'(https?://[^\s"\'<>]+?\.m3u8\?[^\s"\'<>]+)'
        m3u8_tapildi = re.search(pattern, res.text)
        if m3u8_tapildi:
            return m3u8_tapildi.group(1)
        
        # B Planı
        pattern_b = r'(https?://[^\s"\'<>]+?\.m3u8)'
        m3u8_b = re.search(pattern_b, res.text)
        if m3u8_b:
            return m3u8_b.group(1)
            
    except Exception as e:
        print(f'   [TRT Xətası]: {e}')
    return None
    
async def handle_playwright_async(browser, kanal):
    """
    İstənilən kanalı paylaşılan brauzer instansı üzərində skan edib
    m3u8 linkini qoparan universal funksiya. (Əvvəlki versiya hər kanal
    üçün yeni chromium prosesi açırdı - bu, ən böyük gecikmə mənbəyi idi.)
    """
    print(f'   [Playwright] hədəf: {kanal["ad"]}')
    url = kanal["url"]

    m3u8_links = await _capture_m3u8(browser, url, capture="response", click=False, wait_after=15000)

    if m3u8_links:
        # Tapılan linklərdən birincisini götürürük
        return list(m3u8_links)[0].replace('\\', '')

    return None

# ==============================================================================
# YENİ METOD: UNIVERSAL M3U8 SCRAPER (check_link_alive ilə)
# ==============================================================================
import aiohttp

def clean_link(raw):
    """URL-i təmizləyir."""
    if "?file=" in raw:
        raw = raw.split("?file=")[1]
    raw = urllib.parse.unquote(raw)
    if " or " in raw:
        raw = raw.split(" or ")[0]
    return raw.strip()

def is_stable(link):
    """Token olmayan stabil linki müəyyən edir."""
    unstable_patterns = ["bpk-token", "beetv.kz"]
    return not any(p in link for p in unstable_patterns)

async def check_link_alive(link, timeout=10, referer=None):
    """HEAD request ilə linkin işlək olduğunu yoxlayır."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; StreamChecker/1.0)",
        }
        if referer:
            headers["Referer"] = referer
        async with aiohttp.ClientSession() as session:
            async with session.head(link, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as resp:
                return resp.status in (200, 206)
    except Exception:
        return False

async def _get_best_stream_async(browser, url):
    raw_links = await _capture_m3u8(browser, url, capture="response", click=True, wait_after=8000)
    cleaned = list(set(clean_link(l) for l in raw_links))
    stable_links = [l for l in cleaned if is_stable(l)]
    fallback_links = [l for l in cleaned if not is_stable(l)]

    # Referer kimi kanalın öz URL-i istifadə olunur
    parsed = urllib.parse.urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    for link in stable_links:
        if await check_link_alive(link, referer=referer):
            return link
    for link in fallback_links:
        if await check_link_alive(link, referer=referer):
            return link
    return None

async def handle_universal_scraper_async(browser, kanal):
    """
    Tip 7: Stabil linki tapmaq üçün check_link_alive yoxlaması ilə
    universal Playwright scraper. Referer avtomatik olaraq kanalın
    öz saytından götürülür. Paylaşılan brauzer instansından istifadə edir.
    """
    print(f'   [Universal Scraper] Başladı: {kanal["ad"]}')
    try:
        return await _get_best_stream_async(browser, kanal["url"])
    except Exception as e:
        print(f"   [Universal Scraper Xətası]: {e}")
        return None

async def extract_m3u8_smart(browser, video_page_url):
    raw_links = await _capture_m3u8(browser, video_page_url, capture="request", click=True, wait_after=10000)
    mono_links = [l for l in raw_links if "mono.m3u8" in l]
    master_links = [l for l in raw_links if "mono.m3u8" not in l]
    return mono_links if mono_links else master_links

async def handle_playwright_smart(browser, kanal):
    """Yeni 'ağıllı' filtrləmə ilə işləyən Playwright handler (paylaşılan brauzer istifadə edir)"""
    print(f'   [Smart Playwright] hədəf: {kanal["ad"]}')
    try:
        netice = await extract_m3u8_smart(browser, kanal["url"])
        if netice:
            return netice[0]
    except Exception as e:
        print(f'   [Smart Playwright Xətası]: {e}')
    return None
# ==============================================================================
# MƏRKƏZİ KANAL BAZASI
# ==============================================================================
kanallar = [
    # ---- YODACDN QORUMALI KANALLAR (Dinamik) ----
    {
        "type": "token_yoda",
        "ad": "AzTV", 
        "url": "https://aztv.az/az/live",
        "url_referer": "https://aztv.az/",
        "stream_base": "https://str.yodacdn.net/azertv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "Idman TV", 
        "url": "https://aztv.az/az/live-idman",
        "url_referer": "https://aztv.az/",
        "stream_base": "https://str.yodacdn.net/idmantele/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2b/%C4%B0dman_TV_%282019%29.png/250px-%C4%B0dman_TV_%282019%29.png"
    },
    {
        "type": "token_yoda",
        "ad": "Xezer TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/xazartv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/0/05/Xazar_TV_Logo.png/250px-Xazar_TV_Logo.png"
    },
    {
        "type": "token_yoda",
        "ad": "ARB", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/arb/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://images.weserv.nl/?url=https://www.arbtv.az/assets/uploads/events/1.arbtv.png&w=250"
    },
    {
        "type": "direct",
        "ad": "İctimai TV",
        "stream_url": "https://live.itv.az/itv.m3u8?bandwidth=3900&shift=0",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/34/%C4%B0ctimai_TV_%282019-2020%29.png/250px-%C4%B0ctimai_TV_%282019-2020%29.png"
    },
    {
        "type": "token_yoda",
        "ad": "Baku TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/bakutv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3b/Baku_TV_%282018%29.png/250px-Baku_TV_%282018%29.png"
    },
    {
        "type": "token_yoda",
        "ad": "Real TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/real/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ad/Real_TV_loqosu_%282018%29.png/250px-Real_TV_loqosu_%282018%29.png"
    },
    {
        "type": "token_yoda",
        "ad": "ARB 24", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/arb24/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/74/ARB_24_logo1.png/250px-ARB_24_logo1.png"
    },
    {
        "type": "token_yoda",
        "ad": "APA TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/apatv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://images.weserv.nl/?url=https://apagroup.az/storage/news/one/65e5c5252a1fc65e5c5252a1fd170955702965e5c5252a1fa65e5c5252a1fb.jpg&w=250"
    },
    {
        "type": "token_yoda",
        "ad": "TMB Azerbaycan", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/tmbaz/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://images.weserv.nl/?url=https://tmb.tv/wp-content/uploads/2025/01/tmbtaz-png.png&w=250"
    },
    {
        "type": "token_yoda",
        "ad": "ARB Gunes", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/arbgunesh/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/f/fe/ARB_G%C3%BCn%C9%99%C5%9F_logo.png/250px-ARB_G%C3%BCn%C9%99%C5%9F_logo.png"
    },
    {
        "type": "token_yoda",
        "ad": "CBC", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/cbc/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "Medeniyyet Tv", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/medeniyyettele/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/25/M%C9%99d%C9%99niyy%C9%99t_TV_%282019%29.png/250px-M%C9%99d%C9%99niyy%C9%99t_TV_%282019%29.png"
    },
    {
        "type": "token_yoda",
        "ad": "Space TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/space/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "MTV Azerbaijan", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/mtvaz/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/",
        "logo": "https://upload.wikimedia.org/wikipedia/en/thumb/a/a1/MTV_Azerbaijan_Logo.png/250px-MTV_Azerbaijan_Logo.png"
    },
    
    # ---- SABİT VƏ STATİK KANALLAR (Birbaşa linklər) ----
    {
        "type": "direct",
        "ad": "ATV",
        "stream_url": "https://lives.atv.az:5443/ATV_TV_STREAM/streams/atvcanli.m3u8"
    },
    {
        "type": "direct",
        "ad": "CBC Sport",
        "stream_url": "https://cbcsports-live.lg.mncdn.com/cbcsports_live/cbcsports/chunklist.m3u8",
        "logo": "https://upload.wikimedia.org/wikipedia/az/thumb/0/04/CBC_Sport_TV_loqo.png/250px-CBC_Sport_TV_loqo.png"
    },
    
    # ---- DAİONCDN / API QRUPU KANALLARI ----
    {
        "type": "trt",
        "ad": "TRT 1",
        "url": "https://www.trtizle.com/canli-yayin/trt-1",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6c/TRT_1_logo_%282012-2021%29.png/250px-TRT_1_logo_%282012-2021%29.png"
    },
    {
        "type": "trt",
        "ad": "Kanal D",
        "url": "https://www.kanald.com.tr/canli-yayin",
        "logo": "https://upload.wikimedia.org/wikipedia/tr/thumb/4/4e/Kanal_D.png/250px-Kanal_D.png"
    },
    {
        "type": "playwright",
        "ad": "Euro D",
        "url": "https://www.eurod.net.tr/canli-yayin",
        "logo": "https://static.wikia.nocookie.net/logopedia/images/1/1d/Euro_D.svg/revision/latest/scale-to-width-down/250?cb=20220108144208"
    },
    {
        "type": "playwright",
        "ad": "TV 2",
        "url": "https://www.tv2.com.tr/canli-yayin",
        "logo": "https://upload.wikimedia.org/wikipedia/tr/thumb/a/ae/Tv2_logo_%282026%29.png/250px-Tv2_logo_%282026%29.png"
    },
    {
        "type": "playwright",
        "ad": "TV8",
        "url": "https://www.tv8.com.tr/canli-yayin",
        "logo": "https://upload.wikimedia.org/wikipedia/tr/thumb/6/68/Tv8_Yeni_Logo.png/250px-Tv8_Yeni_Logo.png"
    },
    {
        "type": "playwright",
        "ad": "TV8,5",
        "url": "https://canlitv.com/tv85-izle",
        "logo": "https://images.weserv.nl/?url=https://image.pngaaa.com/643/774643-middle.png&w=250"
    },
    {
        "type": "direct",
        "ad": "Show TV",
        "stream_url": "https://yayin2.canlitv.fun/live/showtv.stream/chunklist_w2090257707.m3u8?hash=4b1c6f1b26a8f5c00d2f4cc01662890f", 
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f1/Logo_of_Show_TV.png/250px-Logo_of_Show_TV.png"
    },
    {
        "type": "playwright",
        "ad": "Show Türk",
        "url": "https://www.showturk.com.tr/canli-yayin/",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/fd/Show_Turk_logo.svg/250px-Show_Turk_logo.svg.png"
    },
    {
        "type": "playwright",
        "ad": "Show Max",
        "url": "https://www.showmax.com.tr/canliyayin"
    },
    {
        "type": "playwright",
        "ad": "Star TV",
        "url": "https://puhutv.com/star-tv-canli-yayin",
        "logo": "https://upload.wikimedia.org/wikipedia/tr/thumb/9/92/Star_TV_.png/250px-Star_TV_.png"
    },
    {
        "type": "playwright",
        "ad": "Euro Star",
        "url": "https://www.eurostartv.com.tr/canli-izle",
        "logo": "https://upload.wikimedia.org/wikipedia/tr/thumb/3/37/Euro_Star_logosu.png/250px-Euro_Star_logosu.png"
    },
    {
        "type": "playwright",
        "ad": "ATV",
        "url": "https://canlitv.com/atv-canli",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/Atv_logo_2010.svg/250px-Atv_logo_2010.svg.png"
    },
    {
        "type": "playwright",
        "ad": "A2 TV",
        "url": "https://canlitv.com/canli-a2-izle",
        "logo": "https://upload.wikimedia.org/wikipedia/tr/thumb/e/e8/A2_logosu.jpg/250px-A2_logosu.jpg"
    },
    {
        "type": "generic_scraper",
        "ad": "NOW TV",
        "url": "https://www.nowtv.com.tr/canli-yayin",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/db/NOW_TV_%28Turkey%29_wordmark-red.svg/250px-NOW_TV_%28Turkey%29_wordmark-red.svg.png"
    },
    {
        "type": "playwright",
        "ad": "Kanal 7",
        "url": "https://canlitv.com/kanal7",
        "logo": "https://cdn.kanal7.com/kanal7/wp-content/themes/kanal7/v2/images/kanal7-logo-gray.svg"
    },
    {
        "type": "direct",
        "ad": "Kanal 7 Avrupa",
        "stream_url": "https://livetv.radyotvonline.net/kanal7live/kanal7avr/chunklist.m3u8"
    },
    {
        "type": "playwright",
        "ad": "Beyaz TV",
        "url": "https://beyaztv.com.tr/canli-yayin",
        "logo": "https://images.weserv.nl/?url=https://beyaztv.com.tr/images/logo.png&w=250"
    },  
    {
        "type": "generic_scraper",
        "ad": "NTV",
        "url": "https://puhutv.com/ntv-canli-yayin",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0c/NTV_%28Turkey%29_logo.svg/250px-NTV_%28Turkey%29_logo.svg.png"
    },  
    {
        "type": "generic_scraper",
        "ad": "Haberturk",
        "url": "https://www.haberturk.com/canliyayin",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/78/Haberturk_logo.png/250px-Haberturk_logo.png"
    }, 
    {
        "type": "generic_scraper",
        "ad": "360 TV",
        "url": "https://www.tv360.com.tr/canli-yayin",
        "logo": "https://images.weserv.nl/?url=https://static.wikia.nocookie.net/logopedia/images/3/37/360-tr.png/revision/latest?cb=20220108145523&w=250"
    },   
    {
        "type": "playwright",
        "ad": "TRT Haber",
        "url": "https://www.tabii.com/tr/watch/live/trthaber?trackId=150017",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/fe/TRT_Haber_kurumsal_logo_%282013-2020%29.png/250px-TRT_Haber_kurumsal_logo_%282013-2020%29.png"
    },
    {
        "type": "playwright",
        "ad": "TRT Türk",
        "url": "https://canlitv.com/trt-turk",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e8/TRT_T%C3%BCrk_logosu.png/250px-TRT_T%C3%BCrk_logosu.png"
    },
    {
        "type": "playwright",
        "ad": "Cine 1",
        "url": "https://canlitv.com/cine-1",
        "logo": "https://images.weserv.nl/?url=https://www.digiturkburada.com.tr/kanal3/kanal-buyuk/cine-1-buyuk.png?rkt=DfS6Tgv6Hjr93k3&w=250"
    },
    {
        "type": "playwright",
        "ad": "Kanal 26",
        "url": "https://canlitv.com/kanal-26",
        "logo": "https://images.weserv.nl/?url=https://www.digiturkburada.com.tr/kanal3/kanal-buyuk/kanal-26-buyuk.png?rkt=DfS6Tgv6Hjr93k3&w=250"
    },
    {
        "type": "direct",
        "ad": "TLC",
        "stream_url": "https://yayin2.canlitv.fun/live/tlc.stream/chunklist_w942289914.m3u8?hash=4b1c6f1b26a8f5c00d2f4cc01662890f",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fthumb%2F3%2F3c%2FTLC_logo_%25282023%2529.svg%2F250px-TLC_logo_%25282023%2529.svg.png&w=250&output=webp"
    },
    {
        "type": "direct",
        "ad": "Yaban TV HD",
        "stream_url": "https://yayin1.canlitv.fun/canlitv/yabantv.stream/chunklist_w1077003743.m3u8?hash=4b1c6f1b26a8f5c00d2f4cc01662890f",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Ftr%2F3%2F38%2FYabantv_logo.png&w=250&output=webp"
    },
    {
        "type": "direct",
        "ad": "DMAX Türkiye",
        "stream_url": "https://yayin2.canlitv.fun/live/dmax.stream/chunklist_w2095133286.m3u8?hash=4b1c6f1b26a8f5c00d2f4cc01662890f",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F2%2F25%2FDMAX_Logo_16_05_2011.jpg&w=250&output=webp"
    },
    {
        "type": "playwright",
        "ad": "Dream Turk",
        "url": "https://www.dreamturk.com.tr/canli-yayin-izle",
        "logo": "https://images.weserv.nl/?url=https://static.dreamturk.com.tr/assets/dist/images/dream-turk-logoV2.png&w=250"
    },
    {
        "type": "playwright",
        "ad": "NR 1 Turk",
        "url": "https://www.numberone.com.tr/2015/12/20/number1-turk-tv-canli-yayin/",
        "logo": "https://images.weserv.nl/?url=https://www.numberone.com.tr/wp-content/uploads/2021/10/yeni-logonr1.png&w=250"
    },  
    {
        "type": "playwright",
        "ad": "Kral POP TV",
        "url": "https://puhutv.com/kral-pop-tv-canli-yayin",
        "logo": "https://images.weserv.nl/?url=https://www.kralmuzik.com.tr/app/themes/kral/assets/images/kralmuzik_logo.png?v=2&w=250"
    },  
    {
        "type": "playwright",
        "ad": "TRT Müzik",
        "url": "https://canlitv.com/trt-muzik",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/TRT_M%C3%BCzik.svg/250px-TRT_M%C3%BCzik.svg.png"
    },
    {
        "type": "direct",
        "ad": "TRT Cocuk",
        "stream_url": "https://tv-trtcocuk.medya.trt.com.tr/master_1440.m3u8",
        "logo": "https://images.weserv.nl/?url=https://cdn-i.pr.trt.com.tr/trtcocuk/trtcocuk-logo.svg&w=250&format=png"
    },
    {
        "type": "direct",
        "ad": "Cartoon Network Türkiye",
        "stream_url": "https://yayin2.canlitv.fun/live/cartoon-network.stream/chunklist_w972762167.m3u8?hash=4b1c6f1b26a8f5c00d2f4cc01662890f",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fb%2Fba%2FCartoon_Network.svg&w=250&output=webp"
    },
    {
    "type": "playwright",
    "ad": "Россия 1",
    "url": "https://smotret.tv/rossiya-1",
    "logo": "https://images.weserv.nl/?url=https://upload.wikimedia.org/wikipedia/commons/thumb/d/d2/Rossiya-1_Logo.svg/1280px-Rossiya-1_Logo.svg.png&w=250"
    },
    {
    "type": "universal_scraper",
    "ad": "Первый канал",
    "url": "https://ritsatv.ru/movie-id300104-pervyi-kanal",
    "logo": "https://images.weserv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTwEh1y_xdq4L0p_s4f6olTcplgRn4Jl4ReFLYbQOaWIg&s&w=250"
    },
    {
    "type": "playwright",
    "ad": "Россия РТР",
    "url": "https://smotret.tv/rossiya-rtr",
    "logo": "https://images.weserv.nl/?url=https://telekanaly.com/images/rossiya-rtr.webp&w=250"
    },
    {
    "type": "universal_scraper",
    "ad": "МИР 24",
    "url": "https://ritsatv.ru/movie-id901232-mir-tv",
    "logo": "https://images.weserv.nl/?url=https://upload.wikimedia.org/wikipedia/ru/thumb/6/68/%D0%9C%D0%B8%D1%80_%D0%A2%D0%92_logo.png/250px-%D0%9C%D0%B8%D1%80_%D0%A2%D0%92_logo.png&w=250"
    },
    {
    "type": "generic_scraper",
    "ad": "РБК",
    "url": "http://rutv.pw/rbc",
    "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fthumb%2Ff%2Ff1%2FRBK_logo.svg%2F3840px-RBK_logo.svg.png&w=250&output=webp"
    },
    {
    "type": "playwright",
    "ad": "НТВ",
    "url": "https://rutube.ru/live/video/c37cd74192c6bc3d6cd6077c0c4fd686/",
    "logo": "https://pic.rtbcdn.ru/user/1f/ec/1fec36d83dfaabbf819c84a5cb044858.jpg?size=s"
    },
    {
    "type": "generic_scraper",
    "ad": "НТВ Мир",
    "url": "http://rutv.pw/ntvmir",
    "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F7%2F71%2F%25D0%259D%25D0%25A2%25D0%2592_%25D0%259C%25D0%25B8%25D1%2580_%2528%25D1%2581_2010%252C_%25D0%25B7%25D0%25B5%25D0%25BB%25D1%2591%25D0%25BD%25D1%258B%25D0%25B9_%25D1%2584%25D0%25BE%25D0%25BD%2529.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F200%3Fcb%3D20201214164414%26path-prefix%3Dru&w=250&output=webp"
    },
    {
    "type": "playwright",
    "ad": "Суббота",
    "url": "https://rutube.ru/live/video/310744c10a5809da38aa445c952976da/",
    "logo": "https://pic.rtbcdn.ru/user/dd/0e/dd0e078410ed58d778b38164b3ccdc0d.jpg?size=s"
    },
    # ===== ПОЗНАВАТЕЛЬНЫЕ =====
    {
        "type": "generic_scraper",
        "ad": "Animal Planet HD",
        "url": "http://rutv.pw/animalplanethd",
        "logo": "https://images.weserv.nl/?url=https://upload.wikimedia.org/wikipedia/commons/2/20/2018_Animal_Planet_logo.svg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Da Vinci Learning",
        "url": "http://rutv.pw/davincilearning",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F7%2F7d%2FDa_vinci_learning_logo.jpg&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Discovery Channel HD",
        "url": "http://rutv.pw/discoveryhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Ftr%2F6%2F69%2FDiscovery_HD_logo.PNG&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "English Club TV",
        "url": "http://rutv.pw/englishclub",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcSwOse__aFgHz4dm1lZPy9RhNj4jVujoCOjOXgaE_hCjg%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Galaxy TV",
        "url": "http://rutv.pw/galaxy",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fthumb%2F1%2F10%2FGalaxy_TV.png%2F1280px-Galaxy_TV.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "History 2 HD",
        "url": "http://rutv.pw/h2hd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fa%2Fa3%2FHistory2Logo2019.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Nat Geo Wild HD",
        "url": "http://rutv.pw/natgeowildhd",
        "logo": "https://images.weserv.nl/?url=https://ritsatv.ru/files/poster/original/300230.webp&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "National Geographic HD",
        "url": "http://rutv.pw/natgeohd",
        "logo": "https://images.weserv.nl/?url=https://ritsatv.ru/files/poster/medium/901184.webp&w=250"
    },
   {
        "type": "generic_scraper",
        "ad": "Ocean-TV",
        "url": "http://rutv.pw/ocean",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcTaVpSkpk_I83E4bKv8u2T6e9irUtxESnxbiwmNglpWOQ%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "RTG HD",
        "url": "http://rutv.pw/rtghd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcS6bfpwo9wNzvxSzWNV5kBe6X-NmDQRmsopwZPkbyspew%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Russia Today Doc",
        "url": "http://rutv.pw/rtdoc",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F6%2F6e%2F%25D0%259B%25D0%25BE%25D0%25B3%25D0%25BE%25D1%2582%25D0%25B8%25D0%25BF_%25D1%2582%25D0%25B5%25D0%25BB%25D0%25B5%25D0%25BA%25D0%25B0%25D0%25BD%25D0%25B0%25D0%25BB%25D0%25B0_RT_Doc.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Travel + adventure HD",
        "url": "http://rutv.pw/traveladventurehd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F6%2F6c%2FTravel_Adventure_HD.png%2Frevision%2Flatest%3Fcb%3D20250222153041%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "viju Explore",
        "url": "http://rutv.pw/viasatexplore",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fthumb%2F7%2F78%2FViju_Explore_logo.svg%2F1280px-Viju_Explore_logo.svg.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "viju Nature",
        "url": "http://rutv.pw/viasatnature",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Flogopedia%2Fimages%2F3%2F34%2FViju_Nature.svg%2Frevision%2Flatest%3Fcb%3D20230301225542&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "ZooПарк",
        "url": "http://rutv.pw/zoopark",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F4%2F40%2FZoo%25D0%25BF%25D0%25B0%25D1%2580%25D0%25BA_%25282006-2008%2529.png%2Frevision%2Flatest%2Fscale-to-width-down%2F1200%3Fcb%3D20210626182821%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Бобёр",
        "url": "http://rutv.pw/bober",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2Fa%2Fad%2F%25D0%2591%25D0%25BE%25D0%25B1%25D1%2591%25D1%2580_%2528%25D1%2581_2017%252C_%25D1%258D%25D1%2584%25D0%25B8%25D1%2580%2529.png%2Frevision%2Flatest%2Fscale-to-width-down%2F1200%3Fcb%3D20211121194820%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "В мире животных HD",
        "url": "http://rutv.pw/animalfamilyhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F3%2F36%2F%25D0%2592_%25D0%259C%25D0%25B8%25D1%2580%25D0%25B5_%25D0%2596%25D0%25B8%25D0%25B2%25D0%25BE%25D1%2582%25D0%25BD%25D1%258B%25D1%2585_%25282017%2529.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F220%3Fcb%3D20180612130805%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Время",
        "url": "http://rutv.pw/timetv",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQT218sVsFdHrcNA98ylKI6abC3Tk0OsxvabQSRy2d03Q%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Глазами туриста",
        "url": "http://rutv.pw/glazamiturista",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2Ff%2Ffd%2F%25D0%2593%25D0%25BB%25D0%25B0%25D0%25B7%25D0%25B0%25D0%25BC%25D0%25B8_%25D1%2582%25D1%2583%25D1%2580%25D0%25B8%25D1%2581%25D1%2582%25D0%25B0_%2528%25D0%25B1%25D0%25B5%25D0%25BB%25D1%258B%25D0%25B9_%25D1%2584%25D0%25BE%25D0%25BD%2529.jpg%2Frevision%2Flatest%3Fcb%3D20170419124147%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Доктор",
        "url": "http://rutv.pw/doctv",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2Fe%2Fe8%2F%25D0%2594%25D0%25BE%25D0%25BA%25D1%2582%25D0%25BE%25D1%2580_%25282020%2529.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20210107202221%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Домашние животные",
        "url": "http://rutv.pw/domashniezhivotnye",
        "logo": "https://static.wikia.nocookie.net/tvpedia/images/c/c4/%D0%94%D0%BE%D0%BC%D0%B0%D1%88%D0%BD%D0%B8%D0%B5_%D0%B6%D0%B8%D0%B2%D0%BE%D1%82%D0%BD%D1%8B%D0%B5_%282009%29.png/revision/latest/scale-to-width-down/1200?cb=20161201130502&path-prefix=ru"
    },
    {
        "type": "generic_scraper",
        "ad": "Живая Планета",
        "url": "http://rutv.pw/liveplanet",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F7%2F78%2FZhivaya_Planeta.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20210122185541%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Живая природа HD",
        "url": "http://rutv.pw/wildnaturehd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F4%2F41%2F%25D0%2596%25D0%25B8%25D0%25B2%25D0%25B0%25D1%258F_%25D0%25BF%25D1%2580%25D0%25B8%25D1%2580%25D0%25BE%25D0%25B4%25D0%25B0_%2528%25D0%25B7%25D0%25B5%25D0%25BB%25D1%2591%25D0%25BD%25D1%258B%25D0%25B9%2529.png%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20210717095049%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Загородная жизнь",
        "url": "http://rutv.pw/zagorod",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2Fe%2Feb%2FZagorosnaya_zhizn.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20240814130314%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Загородный",
        "url": "http://rutv.pw/zagorodniy",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcTyOpspUq1q0vQJazgkvj3nYHSqjdI33RfQqj2ilkSTLw%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Здоровое ТВ",
        "url": "http://rutv.pw/zdorovoe",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQugm0Eegtjplx8O1gusfs10itwdBOU-njM7q4_bAMUsA%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Зоо ТВ",
        "url": "http://rutv.pw/telezoo",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F3%2F3c%2FZoo_TV_%25282008%2529.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20201226174710%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "История",
        "url": "http://rutv.pw/istoriya",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcTwhnoj6hb76yTBPcQ7zK4aEzo8SNjDgFMmCVhsx9Pv4g%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Нано ТВ",
        "url": "http://rutv.pw/nano",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRvl0kt9M_fZuNoz_i2-LVSusO9XJbDVkWMxJkIupYz6A%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Наука 2.0",
        "url": "http://rutv.pw/nauka",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fe%2Fe4%2FNauka_2.0_logo.jpg&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Планета HD",
        "url": "http://rutv.pw/planetahd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F2%2F2a%2F%25D0%259F%25D0%25BB%25D0%25B0%25D0%25BD%25D0%25B5%25D1%2582%25D0%25B0_HD_%25282016%2529.png%2Frevision%2Flatest%3Fcb%3D20160410080855%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Поехали!",
        "url": "http://rutv.pw/poehali",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcSLez2cMGFdqoiQudM-kFFOPhzwwTk_r08rhEBH4adaqw%26s%3D10&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Приключения HD",
        "url": "http://rutv.pw/adventurehd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2Ff%2Ff5%2F%25D0%259F%25D1%2580%25D0%25B8%25D0%25BA%25D0%25BB%25D1%258E%25D1%2587%25D0%25B5%25D0%25BD%25D0%25B8%25D1%258F_HD_%25282017-2020%2529.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20180612115135%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Психология 21",
        "url": "http://rutv.pw/psihologiya21",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F3%2F3d%2F%25D0%259F%25D1%2581%25D0%25B8%25D1%2585%25D0%25BE%25D0%25BB%25D0%25BE%25D0%25B3%25D0%25B8%25D1%258F21_%25282009%2529.png%2Frevision%2Flatest%2Fscale-to-width-down%2F1200%3Fcb%3D20161201130924%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "РЖД ТВ",
        "url": "http://rutv.pw/rzd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F7%2F71%2F%25D0%25A0%25D0%2596%25D0%2594.svg%2Frevision%2Flatest%3Fcb%3D20121028185415%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Совершенно секретно",
        "url": "http://rutv.pw/sovsek",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fru%2Fa%2Fa9%2F%25D0%25A1%25D0%25BE%25D0%25B2%25D0%25B5%25D1%2580%25D1%2588%25D0%25B5%25D0%25BD%25D0%25BD%25D0%25BE_%25D1%2581%25D0%25B5%25D0%25BA%25D1%2580%25D0%25B5%25D1%2582%25D0%25BD%25D0%25BE.gif&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Телекафе",
        "url": "http://rutv.pw/telecafe",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Ftoplogos.ru%2Fimages%2Flogo-telekafe.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Телепутешествия",
        "url": "http://rutv.pw/teletravel",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F0%2F08%2F%25D0%25A2%25D0%25B5%25D0%25BB%25D0%25B5%25D0%25BF%25D1%2583%25D1%2582%25D0%25B5%25D1%2588%25D0%25B5%25D1%2581%25D1%2582%25D0%25B2%25D0%25B8%25D1%258F_%25282020%2529.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20201226183115%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Усадьба",
        "url": "http://rutv.pw/usadba",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Ftoplogos.ru%2Fimages%2Flogo-usadba.jpg&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Успех",
        "url": "http://rutv.pw/uspeh",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRu90KGLThsoHkQmJsBMiTKEDBvBB9GGmVs0_wAwtYyDQ%26s%3D10&w=250&output=webp"
    },
    {
    "type": "universal_scraper",
    "ad": "Match TV",
    "url": "https://ritsatv.ru/movie-id900973-match",
    "logo": "https://images.weserv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSbNRDrjRDKEJdaypnmg-uVj5CwXqGj1zCFtv36pp669w&s=10&w=250"
    },
    {
    "type": "generic_scraper",
    "ad": "Eurosport 1",
    "url": "http://rutv.pw/eurosport1hd",
    "logo": "https://wsrv.nl/?url=https://images.seeklogo.com/logo-png/40/1/eurosport-logo-png_seeklogo-407861.png&w=250"
    },
    {
    "type": "generic_scraper",
    "ad": "Eurosport 2",
    "url": "http://rutv.pw/eurosport2hd",
    "logo": "https://images.weserv.nl/?url=https://ritsatv.ru/files/poster/original/900968.jpg&w=250"
    },
    {
    "type": "universal_scraper",
    "ad": "TNT Sports Premium",
    "url": "https://ritsatv.ru/movie-id901490-tnt-sports-premium",
    "logo": "https://wsrv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTHJ23KApagRAkNj-zS1Q9nhxox2bTwTx12WWRLT03EJg&s&w=250"
    },
    # ===== ФИЛЬМЫ =====
    {
        "type": "generic_scraper",
        "ad": "Amedia 1 HD",
        "url": "http://rutv.pw/amedia1hd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F0%2F04%2FAmedia_1.png%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20180421091818%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Amedia 2",
        "url": "http://rutv.pw/amedia2",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F4%2F44%2FAMEDIA_2_2022.png%2Frevision%2Flatest%2Fscale-to-width-down%2F220%3Fcb%3D20230206094526%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Amedia Hit",
        "url": "http://rutv.pw/amediahit",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Favatars.mds.yandex.net%2Fget-ott%2F2419418%2F2a00000185ab20036b43f57cd237ea036557%2F764x430&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Amedia Premium HD",
        "url": "http://rutv.pw/amediapremiumhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F1%2F14%2FAMEDIA_Premium_HD_%2528%25D0%25B2%25D0%25B0%25D1%2580%25D0%25B8%25D0%25B0%25D0%25BD%25D1%2582_2%2529.png%2Frevision%2Flatest%3Fcb%3D20140627112135%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Black",
        "url": "http://rutv.pw/sonyturbo",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F9%2F9f%2FSONY_TURBO_logo.jpg&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Bollywood HD",
        "url": "http://rutv.pw/bollywoodhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Flogopedia%2Fimages%2Fd%2Fd5%2FBollywood_HD_%25282018%2529.svg%2Frevision%2Flatest%3Fcb%3D20181116224348&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Cinema",
        "url": "http://rutv.pw/cinema",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcSHX5KFRqN8_wv6Wnj-UzVkwWA4vRn-mm_Aug%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "FilmBox",
        "url": "http://rutv.pw/filmbox",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F4%2F45%2FFilmbox_pl.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "FilmBox Arthouse",
        "url": "http://rutv.pw/filmboxarthouse",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Ftr%2F3%2F3a%2FFilmBox_Arthouse.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Fox HD",
        "url": "http://rutv.pw/foxhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fthumb%2F2%2F2e%2FFOX_HD.svg%2F960px-FOX_HD.svg.png%3F_%3D20150718014654&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Fox Life HD",
        "url": "http://rutv.pw/foxlifehd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fc%2Fcf%2FFOX_life_HD.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Red",
        "url": "http://rutv.pw/set",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Fdreamlogos%2Fimages%2Fd%2Fd1%2FSony_Red.svg%2Frevision%2Flatest%3Fcb%3D20210301232903&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Sci-Fi",
        "url": "http://rutv.pw/sonyscifi",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fru%2F7%2F7f%2F%25D0%259B%25D0%25BE%25D0%25B3%25D0%25BE%25D1%2582%25D0%25B8%25D0%25BF_%25D1%2582%25D0%25B5%25D0%25BB%25D0%25B5%25D0%25BA%25D0%25B0%25D0%25BD%25D0%25B0%25D0%25BB_Sony_Sci-Fi.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "TV21",
        "url": "http://rutv.pw/tv21",
        "logo": "https://upload.wikimedia.org/wikipedia/en/a/a7/TV21Logo.jpg"
    },
    {
        "type": "generic_scraper",
        "ad": "viju TV1000",
        "url": "http://rutv.pw/tv1000",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fthumb%2F9%2F98%2FViju_TV1000_logo.svg%2F1280px-Viju_TV1000_logo.svg.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "viju TV1000 action",
        "url": "http://rutv.pw/tv1000action",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fimages.iptv.rt.ru%2Fimages%2Fckmlhmjir4sqiatavdvg.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "viju TV1000 Русское кино",
        "url": "http://rutv.pw/tv1000rus",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fimages.iptv.rt.ru%2Fimages%2Fckmlki3ir4sqiatave3g.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "viju+ Comedy HD",
        "url": "http://rutv.pw/vipcomedyhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fwww.cableman.ru%2Fsites%2Fdefault%2Ffiles%2Fviju_comedy_color_rgb_02_1.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "viju+ Megahit HD",
        "url": "http://rutv.pw/vipmegahithd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQZo__cBJj6i9qg-KgKI4K1s8lcHojYILJeMA%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "viju+ Premiere HD",
        "url": "http://rutv.pw/vippremiumhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fwww.cableman.ru%2Fsites%2Fdefault%2Ffiles%2Fviju_premiere_color_rgb_02_1.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "viju+ Serial HD",
        "url": "http://rutv.pw/vipserialhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fimages.iptv.rt.ru%2Fimages%2Fch4dk23mhk079s3jpn40.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Дом кино",
        "url": "http://rutv.pw/domkino",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F4%2F4f%2F%25D0%259F%25D0%25B5%25D1%2580%25D0%25B2%25D1%258B%25D0%25B9_%25D0%25BB%25D0%25BE%25D0%25B3%25D0%25BE%25D1%2582%25D0%25B8%25D0%25BF_%25D0%25BA%25D0%25B0%25D0%25BD%25D0%25B0%25D0%25BB%25D0%25B0_%25D0%2594%25D0%25BE%25D0%25BC_%25D0%259A%25D0%25B8%25D0%25BD%25D0%25BE.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Дом кино Премиум HD",
        "url": "http://rutv.pw/domkinopremiumhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRYYgwHfqPenqzdo_XVaR8afO6y5s9Ya0Ajqg%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Дорама",
        "url": "http://rutv.pw/dorama",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fwww.cableman.ru%2Fsites%2Fdefault%2Ffiles%2Fdorama.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Еврокино",
        "url": "http://rutv.pw/eurokino",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F4%2F4e%2F%25D0%2595%25D0%25B2%25D1%2580%25D0%25BE%25D0%25BA%25D0%25B8%25D0%25BD%25D0%25BE_%25282010%2529.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20210107133719%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Иллюзион+",
        "url": "http://rutv.pw/illusionplus",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcSDTiOPTndeciPs1QhADdXYc2-Sih5kEeF5MA%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Кино ТВ HD",
        "url": "http://rutv.pw/kinotvhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F2%2F2e%2F%25D0%259A%25D0%25B8%25D0%25BD%25D0%25BE_%25D0%25A2%25D0%2592_%25282019%252C_%25D0%25B3%25D0%25BE%25D1%2580%25D0%25B8%25D0%25B7%25D0%25BE%25D0%25BD%25D1%2582%25D0%25B0%25D0%25BB%25D1%258C%2529.png%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20200826092043%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Кинопоказ",
        "url": "http://rutv.pw/kinopokaz",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F1%2F13%2FKinopokazLogo.jpg&w=250&output=webp0"
    },
    {
        "type": "generic_scraper",
        "ad": "Мосфильм. Золотая коллекция",
        "url": "http://rutv.pw/mosfilm",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fmosfilmgold.ru%2Fassets%2Fcached%2F2023%2F04%2Fresize%2F1140__q100_174452c5-3950-4000-8939-33cdc8397800.jpeg&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Наше любимое кино",
        "url": "http://rutv.pw/nashelyubimoekino",
        "logo": "https://static.wikia.nocookie.net/tvpedia/images/c/c0/%D0%9D%D0%B0%D1%88%D0%B5_%D0%BB%D1%8E%D0%B1%D0%B8%D0%BC%D0%BE%D0%B5_%D0%BA%D0%B8%D0%BD%D0%BE_%282009-2012%29_%28%D0%B8%D1%81%D0%BF%D0%BE%D0%BB%D1%8C%D0%B7%D0%BE%D0%B2%D0%B0%D0%BB%D1%81%D1%8F_%D0%B2_%D1%8D%D1%84%D0%B8%D1%80%D0%B5%29.png/revision/latest/scale-to-width-down/250?cb=20210317165926&path-prefix=ru"
    },
    {
        "type": "generic_scraper",
        "ad": "НСТ",
        "url": "http://rutv.pw/strashnoe",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQV-MfSlGCF8OfWeq8hGKYmnrY62nWCish6cw%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Победа",
        "url": "http://rutv.pw/pobeda",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRTSfbev26wqSQK-wzn-ncAUoWjRZ2hguMbog%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Русская комедия",
        "url": "http://rutv.pw/russkayakomediya",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQVfbj-HG5gaDDXh-QWYgI8-Ah4mo6WeOn22w%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Русский бестселлер",
        "url": "http://rutv.pw/bestrussia",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRMr4xvBFILbFRwVqpgbUhmqUq--Ar8Q2gg2w%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Русский детектив",
        "url": "http://rutv.pw/rudetective",
        "logo": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQwpYv89gP0Mvz6dry6KKKd4gGcde2QGVYrSg&s"
    },
    {
        "type": "generic_scraper",
        "ad": "Русский иллюзион",
        "url": "http://rutv.pw/russkiyillusion",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRDK2WhWvwIaTg0MEZTthBRrVBcF9N5d8QMLw%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Русский роман HD",
        "url": "http://rutv.pw/rusromanhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F8%2F80%2F%25D0%25A0%25D1%2583%25D1%2581%25D1%2581%25D0%25BA%25D0%25B8%25D0%25B9_%25D1%2580%25D0%25BE%25D0%25BC%25D0%25B0%25D0%25BD.png%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20120218024953%26path-prefix%3Dru&w=250&output=webp"
    },
     # ===== ДЕТСКИЕ =====
    {
        "type": "generic_scraper",
        "ad": "Cartoon Network",
        "url": "http://rutv.pw/cartoonnetwork",
        "logo": "https://images.weserv.nl/?url=https://upload.wikimedia.org/wikipedia/commons/b/ba/Cartoon_Network.svg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Ani",
        "url": "http://rutv.pw/ani",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRO5K4UTfpz0hIhq3kwN39w4M81NZ-BYJTsiw%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Gulli Girl",
        "url": "http://rutv.pw/gulli",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F4%2F47%2FGulli_Girl_%25282018-%25D0%25BD.%25D0%25B2.%2529.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "JimJam",
        "url": "http://rutv.pw/jimjam",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Fdreamlogos%2Fimages%2F2%2F2d%2F2017._10._29._-_3.png%2Frevision%2Flatest%3Fcb%3D20190726164700&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Nick Jr.",
        "url": "http://rutv.pw/nickjr",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fc%2Fc5%2FNick_Jr._logo_2023_%2528outline%2529.svg&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Nickelodeon HD",
        "url": "http://rutv.pw/nickelodeonhd",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQ2FFubyh9txTNIzgb-e07jEmf-WUJbHdE2uw%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "TiJi",
        "url": "http://rutv.pw/tiji",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fen%2F1%2F13%2FTiJi_logo.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "В гостях у сказки",
        "url": "http://rutv.pw/skazka",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F3%2F38%2FV_gostyah_y_skazki.svg%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20240819082832%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Детский мир",
        "url": "http://rutv.pw/detskiymir",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fru%2F6%2F62%2FDetskiymir_logo.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Мама",
        "url": "http://rutv.pw/mama",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fwww.cableman.ru%2Fsites%2Fdefault%2Ffiles%2Fmama.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Мульт",
        "url": "http://rutv.pw/mult",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fb%2Fbd%2F%25D0%259C%25D1%2583%25D0%25BB%25D1%258C%25D1%2582_logo.jpg&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Мультиландия",
        "url": "http://rutv.pw/multimania",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F7%2F7d%2F%25D0%259C%25D1%2583%25D0%25BB%25D1%258C%25D1%2582%25D0%25B8%25D0%25BB%25D0%25B0%25D0%25BD%25D0%25B4%25D0%25B8%25D1%258F_%25282019%2529.png%2Frevision%2Flatest%2Fscale-to-width-down%2F250%3Fcb%3D20190811092710%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Мультимузыка",
        "url": "http://rutv.pw/multimuzyka",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F6%2F6c%2F%25D0%259C%25D1%2583%25D0%25BB%25D1%258C%25D1%2582_%25D0%25B8_%25D0%25BC%25D1%2583%25D0%25B7%25D1%258B%25D0%25BA%25D0%25B0.png%2Frevision%2Flatest%3Fcb%3D20171201061648%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Радость моя",
        "url": "http://rutv.pw/radostmoya",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.wikia.nocookie.net%2Ftvpedia%2Fimages%2F2%2F27%2F%25D0%25A0%25D0%25B0%25D0%25B4%25D0%25BE%25D1%2581%25D1%2582%25D1%258C_%25D0%25BC%25D0%25BE%25D1%258F.jpg%2Frevision%2Flatest%3Fcb%3D20120723164900%26path-prefix%3Dru&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Рыжий",
        "url": "http://rutv.pw/ryzhiy",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQ-f0xHltDt1TA-YSjejfbsM7gIm7UUJTQhcw%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Уникум",
        "url": "http://rutv.pw/detskiy",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcTjVeRu12WCbng3tsF9oRnw3T7Tzpf3eV_agA%26s&w=250&output=webp"
    },
    # ===== МУЗЫКА =====
    {
        "type": "generic_scraper",
        "ad": "Bridge TV",
        "url": "http://rutv.pw/bridge",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fimages.seeklogo.com%2Flogo-png%2F32%2F1%2Fbridge-tv-logo-png_seeklogo-323609.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Europa Plus TV",
        "url": "http://rutv.pw/europaplus",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRGPtmhPcAuYzkKQqXwKJqXwyiGzHbOnLfNiw%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "FON Music",
        "url": "http://rutv.pw/tntmusic",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcT8JhkUTakm3hiPNqu566-JgFgG5V1KCo8M8g%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Mezzo",
        "url": "http://rutv.pw/mezzo",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcTGU_z52bBAcWno3W2E0F2d8uw85-wQ4m64Pg%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "RU TV",
        "url": "http://rutv.pw/rutv",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fd%2Fd2%2FRu_tv_%25D0%25BB%25D0%25BE%25D0%25B3%25D0%25BE%25D1%2582%25D0%25B8%25D0%25BF.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Жара",
        "url": "http://rutv.pw/stv",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQXkDBpjgA9qlmT0XA-l5auQnu5yCIjq_wRGQ%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Музыка Первого",
        "url": "http://rutv.pw/muz1",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcSfHVzyk0XuOoNt-q23tgerqOzsE5U-cIOztA%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Шансон ТВ",
        "url": "http://rutv.pw/shanson",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fshanson.tv%2Fwp-content%2Fuploads%2F2025%2F12%2Fpics.31.png&w=250&output=webp"
    },
    # ===== МУЖСКИЕ =====
    {
        "type": "generic_scraper",
        "ad": "Авто 24",
        "url": "http://rutv.pw/avto24",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcQmUHL0eFaHvN4B9o2a37Y61qHcmOZy22SEBQ%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Драйв ТВ",
        "url": "http://rutv.pw/drive",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcStlvL10n34yOhbpg9_GxKjPmEkN8eF0uocag%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Мужской",
        "url": "http://rutv.pw/mans",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fstatic.tildacdn.com%2Ftild6439-6233-4263-b738-373666653565%2F_logo___.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Охота и рыбалка",
        "url": "http://rutv.pw/ohotairybalka",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcSZOqEWJHbMyEwx-9IR8iD0mqHki2ZWgq_ffg%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Охотник и рыболов",
        "url": "http://rutv.pw/ohotnikirybolov",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fencrypted-tbn0.gstatic.com%2Fimages%3Fq%3Dtbn%3AANd9GcRYjIHcNo5lIxVjWhb0kNwpe9bfqxXkQrnGkA%26s&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Техно 24",
        "url": "http://rutv.pw/techno24",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2Fd%2Fdc%2FT24_Logo_vertical_main.png&w=250&output=webp"
    },
    {
        "type": "generic_scraper",
        "ad": "Точка Отрыва",
        "url": "http://rutv.pw/tochkaotryva",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fwww.cableman.ru%2Fsites%2Fdefault%2Ffiles%2Ftochka_otryva_1.png&w=250&output=webp"
    },
    {
        "type": "playwright_smart",
        "ad": "TBS USA",
        "url": "https://streamsports99.ru/live-tv/TBS__us",
        "logo": "https://images.weserv.nl/?url=https%3A%2F%2Fupload.wikimedia.org%2Fwikipedia%2Fcommons%2F2%2F2c%2FTBS_2020.svg&w=250&output=webp"
    },
]

# ==============================================================================
# ƏSAS İCRA PROSESİ (MAIN)
# ==============================================================================
# Playwright tələb edən tiplər - bunlar üçün ortaq brauzer instansı istifadə olunur
PLAYWRIGHT_TYPES = {"playwright", "universal_scraper", "playwright_smart"}


async def main():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0',
        'Accept-Language': 'az,en-US;q=0.9,en;q=0.8'
    }

    output_file = "channels.m3u"
    netice_sirasi = [None] * len(kanallar)

    # Brauzer YALNIZ BİR DƏFƏ açılır və bütün playwright-əsaslı kanallar
    # üçün paylaşılır (əvvəlki versiyada hər kanal üçün ayrıca açılırdı).
    need_browser = any(k["type"] in PLAYWRIGHT_TYPES for k in kanallar)
    browser = None
    pw_ctx = None

    if need_browser:
        pw_ctx = async_playwright()
        p = await pw_ctx.__aenter__()
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )

    try:
        for index, kanal in enumerate(kanallar, start=1):
            print(f'[{index}/{len(kanallar)}] [{kanal["ad"]}] İşlənir...')
            canli_link = None

            try:
                tip = kanal["type"]
                if tip == "direct":
                    canli_link = handle_direct(kanal)
                elif tip == "token_yoda":
                    canli_link = handle_token_yoda(kanal, headers)
                elif tip == "generic_scraper":
                    canli_link = handle_generic_scraper(kanal, headers)
                elif tip == "trt":
                    canli_link = handle_trt(kanal, headers)
                elif tip == "playwright":
                    canli_link = await handle_playwright_async(browser, kanal)
                elif tip == "universal_scraper":
                    canli_link = await handle_universal_scraper_async(browser, kanal)
                elif tip == "playwright_smart":
                    canli_link = await handle_playwright_smart(browser, kanal)

                netice_sirasi[index - 1] = canli_link

                if canli_link:
                    print(f'   => [UĞURLU] {kanal["ad"]} tapıldı.\n')
                else:
                    print(f'   => [XƏTA] {kanal["ad"]} üçün token və ya link generasiya edilə bilmədi.\n')

            except Exception as e:
                print(f'   => [SİSTEM XƏTASI] {kanal["ad"]} icra edilərkən gözlənilməz problem: {e}\n')
    finally:
        if browser:
            await browser.close()
        if pw_ctx:
            await pw_ctx.__aexit__(None, None, None)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for kanal, canli_link in zip(kanallar, netice_sirasi):
            if not canli_link:
                continue
            if kanal.get("logo"):
                f.write(f'#EXTINF:-1 tvg-logo="{kanal["logo"]}",{kanal["ad"]}\n')
            else:
                f.write(f'#EXTINF:-1,{kanal["ad"]}\n')
            f.write(f'{canli_link}\n')

    print(f"Siyahı uğurla '{output_file}' faylına yadda saxlanıldı.")

if __name__ == "__main__":
    asyncio.run(main())
