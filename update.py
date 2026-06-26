import nest_asyncio
import asyncio
from playwright.async_api import async_playwright

# Playwright-ın sinxron əsas dövrlərlə toqquşmaması üçün Colab-da etdiyimiz kimi aktivləşdiririk
nest_asyncio.apply()

import requests
import re
import sys
import urllib.parse
import aiohttp

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
    
def handle_playwright(kanal, headers):
    """
    İstənilən kanalı Playwright ilə skan edib m3u8 linkini qoparan universal funksiya.
    """
    print(f'   [Playwright] Brauzer işə salındı, hədəf: {kanal["ad"]}')
    
    m3u8_links = []
    
    # Hər dəfə kanalın öz URL-i bura gələcək
    url = kanal["url"]

    async def run_browser():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            page = await browser.new_page()

            def handle_response(response):
                # İstənilən m3u8 uzantısını tutur
                if ".m3u8" in response.url:
                    m3u8_links.append(response.url)

            page.on("response", handle_response)

            try:
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(15000)
            except Exception as e:
                print(f"   [Playwright Xətası]: {e}")
            finally:
                await browser.close()

    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(run_browser())
    except Exception as e:
        print(f"   [Playwright Dövr Xətası]: {e}")

    if m3u8_links:
        # Tapılan linklərdən birincisini götürürük
        taze_link = list(set(m3u8_links))[0].replace('\\', '')
        return taze_link

    return None

# ==============================================================================
# YENİ METOD: UNIVERSAL M3U8 SCRAPER (check_link_alive ilə)
# ==============================================================================
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

async def _universal_scraper_async(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()
        found_links = set()

        def handle_response(response):
            if ".m3u8" in response.url:
                found_links.add(response.url)

        page.on("response", handle_response)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        await page.mouse.click(640, 360)
        await page.wait_for_timeout(8000)
        await browser.close()
        return list(found_links)

async def _get_best_stream_async(url):
    raw_links = await _universal_scraper_async(url)
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

def handle_universal_scraper(kanal):
    """
    Tip 7: Stabil linki tapmaq üçün check_link_alive yoxlaması ilə
    universal Playwright scraper. Referer avtomatik olaraq kanalın
    öz saytından götürülür.
    """
    print(f'   [Universal Scraper] Başladı: {kanal["ad"]}')
    url = kanal["url"]
    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_get_best_stream_async(url))
        return result
    except Exception as e:
        print(f"   [Universal Scraper Xətası]: {e}")
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
        "type": "trt",
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
        "type": "generic_scraper",
        "ad": "Show TV",
        "url": "https://www.showtv.com.tr/canli-yayin", 
        "stream_base": "https://ciner.daioncdn.net/showtv/showtv_1080p.m3u8",
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
        "url": "https://www.kanal7.com/canli-izle"
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
        "ad": "Tivi 6",
        "url": "https://tivi6.com.tr/sayfa/https-tivi6-com-tr-sayfa-canli-yayin",
        "logo": "https://images.weserv.nl/?url=https://tivi6.com.tr/uploads/settings/2026/03/69b468997c5a5_1773430937.jpeg&w=250"
    },
    {
        "type": "playwright",
        "ad": "Kanal 26",
        "url": "https://canlitv.com/kanal-26",
        "logo": "https://images.weserv.nl/?url=https://www.digiturkburada.com.tr/kanal3/kanal-buyuk/kanal-26-buyuk.png?rkt=DfS6Tgv6Hjr93k3&w=250"
    },
    {
        "type": "playwright",
        "ad": "Yaban TV",
        "url": "https://www.yabantv.com/broadcast",
        "logo": "https://images.weserv.nl/?url=https://www.yabantv.com/public/img/ayar/yabantv_logo.png&w=250"
    },
    {
        "type": "playwright",
        "ad": "DMAX Türkiye",
        "url": "https://canlitv.com/dmax-canli-yayin",
        "logo": "https://images.weserv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSDeVj2mkN2b-YawhB9oyy24nwZUldYlRfnKg&s&w=250"
    },
    {
        "type": "direct",
        "ad": "TRT Belgesel",
        "stream_url": "https://tv-trtbelgesel-dai.medya.trt.com.tr/master_3.m3u8",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/09/TRT_Belgesel_logo_%282019-%29.svg/250px-TRT_Belgesel_logo_%282019-%29.svg.png"
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
    "type": "playwright",
    "ad": "НТВ",
    "url": "https://rutube.ru/live/video/c37cd74192c6bc3d6cd6077c0c4fd686/",
    "logo": "https://pic.rtbcdn.ru/user/1f/ec/1fec36d83dfaabbf819c84a5cb044858.jpg?size=s"
    },
    {
    "type": "generic_scraper",
    "ad": "НТВ Мир",
    "url": "http://rutv.pw/ntvmir",
    "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/ntvmir.jpg&w=250"
    },
    {
    "type": "playwright",
    "ad": "Суббота",
    "url": "https://rutube.ru/live/video/310744c10a5809da38aa445c952976da/",
    "logo": "https://pic.rtbcdn.ru/user/dd/0e/dd0e078410ed58d778b38164b3ccdc0d.jpg?size=s"
    },
    {
    "type": "universal_scraper",
    "ad": "ТНТ",
    "url": "https://ritsatv.ru/movie-id300118-tnt",
    "logo": "https://images.weserv.nl/?url=https://upload.wikimedia.org/wikipedia/commons/6/6b/Logo_tnt.png&w=250"
    },
    {
    "type": "universal_scraper",
    "ad": "Ю ТВ",
    "url": "https://ritsatv.ru/movie-id900226-telekanal-yu",
    "logo": "https://images.weserv.nl/?url=https://upload.wikimedia.org/wikipedia/commons/8/8f/%D0%AE_2020.webp&w=250"
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
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/davincilearning.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Discovery Channel HD",
        "url": "http://rutv.pw/discoveryhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/discoveryhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "English Club TV",
        "url": "http://rutv.pw/englishclub",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/englishclub.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Galaxy TV",
        "url": "http://rutv.pw/galaxy",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/galaxy.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "History 2 HD",
        "url": "http://rutv.pw/h2hd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/h2hd.jpg&w=250"
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
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/ocean.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "RTG HD",
        "url": "http://rutv.pw/rtghd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/rtghd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Russia Today Doc",
        "url": "http://rutv.pw/rtdoc",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/rtdoc.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Travel + adventure HD",
        "url": "http://rutv.pw/traveladventurehd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/traveladventurehd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju Explore",
        "url": "http://rutv.pw/viasatexplore",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/viasatexplore.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju Nature",
        "url": "http://rutv.pw/viasatnature",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/viasatnature.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "ZooПарк",
        "url": "http://rutv.pw/zoopark",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/zoopark.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Бобёр",
        "url": "http://rutv.pw/bober",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/bober.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "В мире животных HD",
        "url": "http://rutv.pw/animalfamilyhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/animalfamilyhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Время",
        "url": "http://rutv.pw/timetv",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/timetv.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Глазами туриста",
        "url": "http://rutv.pw/glazamiturista",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/glazamiturista.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Доктор",
        "url": "http://rutv.pw/doctv",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/doctv.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Домашние животные",
        "url": "http://rutv.pw/domashniezhivotnye",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/domashniezhivotnye.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Живая Планета",
        "url": "http://rutv.pw/liveplanet",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/liveplanet.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Живая природа HD",
        "url": "http://rutv.pw/wildnaturehd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/wildnaturehd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Загородная жизнь",
        "url": "http://rutv.pw/zagorod",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/zagorod.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Загородный",
        "url": "http://rutv.pw/zagorodniy",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/zagorodniy.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Здоровое ТВ",
        "url": "http://rutv.pw/zdorovoe",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/zdorovoe.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Зоо ТВ",
        "url": "http://rutv.pw/telezoo",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/telezoo.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "История",
        "url": "http://rutv.pw/istoriya",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/istoriya.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Моя планета",
        "url": "http://rutv.pw/moyaplaneta",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/moyaplaneta.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Нано ТВ",
        "url": "http://rutv.pw/nano",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/nano.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Наука 2.0",
        "url": "http://rutv.pw/nauka",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/nauka.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Планета HD",
        "url": "http://rutv.pw/planetahd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/planetahd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Поехали!",
        "url": "http://rutv.pw/poehali",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/poehali.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Приключения HD",
        "url": "http://rutv.pw/adventurehd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/adventurehd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Психология 21",
        "url": "http://rutv.pw/psihologiya21",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/psihologiya21.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "РЖД ТВ",
        "url": "http://rutv.pw/rzd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/rzd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Совершенно секретно",
        "url": "http://rutv.pw/sovsek",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/sovsek.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Телекафе",
        "url": "http://rutv.pw/telecafe",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/telecafe.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Телепутешествия",
        "url": "http://rutv.pw/teletravel",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/teletravel.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Усадьба",
        "url": "http://rutv.pw/usadba",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/usadba.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Успех",
        "url": "http://rutv.pw/uspeh",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/uspeh.jpg&w=250"
    },
    {
    "type": "universal_scraper",
    "ad": "Match TV",
    "url": "https://ritsatv.ru/movie-id900973-match",
    "logo": "https://images.weserv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSbNRDrjRDKEJdaypnmg-uVj5CwXqGj1zCFtv36pp669w&s=10&w=250"
    },
    {
    "type": "universal_scraper",
    "ad": "Setanta Sports 1",
    "url": "https://ritsatv.ru/movie-id900982-setanta-1",
    "logo": "https://images.weserv.nl/?url=https://ritsatv.ru/files/poster/original/900982.jpg&w=250"
    },
    {
    "type": "universal_scraper",
    "ad": "Setanta Sports 2",
    "url": "https://ritsatv.ru/movie-id900983-setanta-2",
    "logo": "https://wsrv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRFR8wvcpZvK--w75DSnZM0Nloq8Rf7LJQqSSBvWWdTw3F62qT13t6NgDS8&s=10&w=250"
    },
    {
    "type": "playwright",
    "ad": "Eurosport 1",
    "url": "http://myru.top/online/eurosport",
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
    "ad": "TNT Sports 1",
    "url": "https://ritsatv.ru/movie-id901126-tnt-sports-1",
    "logo": "https://wsrv.nl/?url=https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/TNT_Sports_%282023%29.svg/960px-TNT_Sports_%282023%29.svg.png&w=250"
    },
    {
    "type": "universal_scraper",
    "ad": "TNT Sports 2",
    "url": "https://ritsatv.ru/movie-id901269-tnt-sports-2",
    "logo": "https://wsrv.nl/?url=https://media.info/l/o/1/1540.1690027877.png&w=250"
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
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/amedia1hd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Amedia 2",
        "url": "http://rutv.pw/amedia2",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/amedia2.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Amedia Hit",
        "url": "http://rutv.pw/amediahit",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/amediahit.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Amedia Premium HD",
        "url": "http://rutv.pw/amediapremiumhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/amediapremiumhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Black",
        "url": "http://rutv.pw/sonyturbo",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/sonyturbo.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Bollywood HD",
        "url": "http://rutv.pw/bollywoodhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/bollywoodhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Cinema",
        "url": "http://rutv.pw/cinema",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/cinema.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "FilmBox",
        "url": "http://rutv.pw/filmbox",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/filmbox.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "FilmBox Arthouse",
        "url": "http://rutv.pw/filmboxarthouse",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/filmboxarthouse.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Fox HD",
        "url": "http://rutv.pw/foxhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/foxhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Fox Life HD",
        "url": "http://rutv.pw/foxlifehd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/foxlifehd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Red",
        "url": "http://rutv.pw/set",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/set.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Sci-Fi",
        "url": "http://rutv.pw/sonyscifi",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/sonyscifi.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "TV XXI (TV21)",
        "url": "http://rutv.pw/tv21",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/tv21.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju TV1000",
        "url": "http://rutv.pw/tv1000",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/tv1000.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju TV1000 action",
        "url": "http://rutv.pw/tv1000action",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/tv1000action.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju TV1000 Русское кино",
        "url": "http://rutv.pw/tv1000rus",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/tv1000rus.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju+ Comedy HD",
        "url": "http://rutv.pw/vipcomedyhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/vipcomedyhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju+ Megahit HD",
        "url": "http://rutv.pw/vipmegahithd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/vipmegahithd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju+ Premiere HD",
        "url": "http://rutv.pw/vippremiumhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/vippremiumhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "viju+ Serial HD",
        "url": "http://rutv.pw/vipserialhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/vipserialhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Дом кино",
        "url": "http://rutv.pw/domkino",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/domkino.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Дом кино Премиум HD",
        "url": "http://rutv.pw/domkinopremiumhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/domkinopremiumhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Дорама",
        "url": "http://rutv.pw/dorama",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/dorama.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Еврокино",
        "url": "http://rutv.pw/eurokino",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/eurokino.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Иллюзион+",
        "url": "http://rutv.pw/illusionplus",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/illusionplus.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Кино ТВ HD",
        "url": "http://rutv.pw/kinotvhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/kinotvhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Кинопоказ",
        "url": "http://rutv.pw/kinopokaz",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/kinopokaz.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Мосфильм. Золотая коллекция",
        "url": "http://rutv.pw/mosfilm",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/mosfilm.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Наше любимое кино",
        "url": "http://rutv.pw/nashelyubimoekino",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/nashelyubimoekino.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "НСТ",
        "url": "http://rutv.pw/strashnoe",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/strashnoe.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Победа",
        "url": "http://rutv.pw/pobeda",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/pobeda.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Русская комедия",
        "url": "http://rutv.pw/russkayakomediya",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/russkayakomediya.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Русский бестселлер",
        "url": "http://rutv.pw/bestrussia",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/bestrussia.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Русский детектив",
        "url": "http://rutv.pw/rudetective",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/rudetective.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Русский иллюзион",
        "url": "http://rutv.pw/russkiyillusion",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/russkiyillusion.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Русский роман HD",
        "url": "http://rutv.pw/rusromanhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/rusromanhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Точка ТВ",
        "url": "http://rutv.pw/tochka",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/tochka.jpg&w=250"
    },
     # ===== ДЕТСКИЕ =====
    {
        "type": "playwright",
        "ad": "Cartoon Network",
        "url": "http://myru.top/online/cartnoon-network",
        "logo": "https://images.weserv.nl/?url=https://upload.wikimedia.org/wikipedia/commons/b/ba/Cartoon_Network.svg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Ani",
        "url": "http://rutv.pw/ani",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/ani.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Gulli Girl",
        "url": "http://rutv.pw/gulli",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/gulli.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "JimJam",
        "url": "http://rutv.pw/jimjam",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/jimjam.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Nick Jr.",
        "url": "http://rutv.pw/nickjr",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/nickjr.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Nickelodeon HD",
        "url": "http://rutv.pw/nickelodeonhd",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/nickelodeonhd.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "TiJi",
        "url": "http://rutv.pw/tiji",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/tiji.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "В гостях у сказки",
        "url": "http://rutv.pw/skazka",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/skazka.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Детский мир",
        "url": "http://rutv.pw/detskiymir",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/detskiymir.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Мама",
        "url": "http://rutv.pw/mama",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/mama.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Мульт",
        "url": "http://rutv.pw/mult",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/mult.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Мультиландия",
        "url": "http://rutv.pw/multimania",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/multimania.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Мультимузыка",
        "url": "http://rutv.pw/multimuzyka",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/multimuzyka.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Радость моя",
        "url": "http://rutv.pw/radostmoya",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/radostmoya.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Рыжий",
        "url": "http://rutv.pw/ryzhiy",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/ryzhiy.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Уникум",
        "url": "http://rutv.pw/detskiy",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/detskiy.jpg&w=250"
    },
    # ===== МУЗЫКА =====
     {
        "type": "generic_scraper",
        "ad": "Bridge TV",
        "url": "http://rutv.pw/bridge",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/bridge.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Europa Plus TV",
        "url": "http://rutv.pw/europaplus",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/europaplus.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "FON Music",
        "url": "http://rutv.pw/tntmusic",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/tntmusic.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Mezzo",
        "url": "http://rutv.pw/mezzo",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/mezzo.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "RU TV",
        "url": "http://rutv.pw/rutv",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/rutv.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Жара",
        "url": "http://rutv.pw/stv",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/stv.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Музыка Первого",
        "url": "http://rutv.pw/muz1",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/muz1.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Шансон ТВ",
        "url": "http://rutv.pw/shanson",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/shanson.jpg&w=250"
    },
    # ===== МУЖСКИЕ =====
    {
        "type": "generic_scraper",
        "ad": "Авто 24",
        "url": "http://rutv.pw/avto24",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/avto24.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Драйв ТВ",
        "url": "http://rutv.pw/drive",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/drive.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Мужской",
        "url": "http://rutv.pw/mans",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/mans.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Охота и рыбалка",
        "url": "http://rutv.pw/ohotairybalka",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/ohotairybalka.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Охотник и рыболов",
        "url": "http://rutv.pw/ohotnikirybolov",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/ohotnikirybolov.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Техно 24",
        "url": "http://rutv.pw/techno24",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/techno24.jpg&w=250"
    },
    {
        "type": "generic_scraper",
        "ad": "Точка Отрыва",
        "url": "http://rutv.pw/tochkaotryva",
        "logo": "https://images.weserv.nl/?url=http://rutv.pw/imgs/tochkaotryva.jpg&w=250"
    },
]

# ==============================================================================
# ƏSAS İCRA PROSESİ (MAIN)
# ==============================================================================
def main():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0',
        'Accept-Language': 'az,en-US;q=0.9,en;q=0.8'
    }
    
    output_file = "channels.m3u"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        
        for index, kanal in enumerate(kanallar, start=1):
            print(f'[{index}/{len(kanallar)}] [{kanal["ad"]}] İşlənir...')
            canli_link = None
            
            try:
                # İF-ELİF BLOKLARI YENİLƏNDİ
                if kanal["type"] == "direct":
                    canli_link = handle_direct(kanal)
                elif kanal["type"] == "token_yoda":
                    canli_link = handle_token_yoda(kanal, headers)
                elif kanal["type"] == "generic_scraper":
                    canli_link = handle_generic_scraper(kanal, headers)
                elif kanal["type"] == "trt":
                    canli_link = handle_trt(kanal, headers)
                elif kanal["type"] == "playwright":
                    canli_link = handle_playwright(kanal, headers)
                elif kanal["type"] == "universal_scraper":
                    canli_link = handle_universal_scraper(kanal)

                if canli_link:
                    # Loqo dəstəyi bura əlavə edildi
                    if "logo" in kanal and kanal["logo"]:
                        f.write(f'#EXTINF:-1 tvg-logo="{kanal["logo"]}",{kanal["ad"]}\n')
                    else:
                        f.write(f'#EXTINF:-1,{kanal["ad"]}\n')
                        
                    f.write(f'{canli_link}\n')
                    print(f'   => [UĞURLU] {kanal["ad"]} pleylistə yazıldı.\n')
                else:
                    print(f'   => [XƏTA] {kanal["ad"]} üçün token və ya link generasiya edilə bilmədi.\n')
                    
            except Exception as e:
                print(f'   => [SİSTEM XƏTASI] {kanal["ad"]} icra edilərkən gözlənilməz problem: {e}\n')

    print(f"Siyahı uğurla '{output_file}' faylına yadda saxlanıldı.")

if __name__ == "__main__":
    main()
