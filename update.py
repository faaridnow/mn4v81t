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

def handle_m3u8_scraper(kanal):
    """
    Tip 5: Playwright ilə şəbəkə trafikini izləyərək ən yüksək keyfiyyətli
    m3u8 linkini avtomatik seçən universal skraper (yepyeni_metod).
    """
    print(f'   [M3U8 Scraper] Brauzer işə salındı, hədəf: {kanal["ad"]}')
    
    url = kanal["url"]
    found_links = set()

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            context = await browser.new_context(viewport={"width": 1280, "height": 720})
            page = await context.new_page()

            def handle_response(response):
                if ".m3u8" in response.url:
                    found_links.add(response.url)

            page.on("response", handle_response)

            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            await page.mouse.click(640, 360)
            await page.wait_for_timeout(3000)
            await browser.close()

    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())
    except Exception as e:
        print(f"   [M3U8 Scraper Xətası]: {e}")
        return None

    if not found_links:
        return None

    # Linkləri təmizlə
    cleaned_links = set()
    for raw_link in found_links:
        clean = raw_link.split("?file=")[1] if "?file=" in raw_link else raw_link
        clean = urllib.parse.unquote(clean)
        if " or " in clean:
            clean = clean.split(" or ")[0]
        cleaned_links.add(clean)

    # Ən yüksək bitreyti seç
    best_link = None
    max_bitrate = -1
    for link in cleaned_links:
        match = re.search(r"video=(\d+)", link)
        if match:
            bitrate = int(match.group(1))
            if bitrate > max_bitrate:
                max_bitrate = bitrate
                best_link = link

    # Fallback: index.m3u8 olan linki seç
    if not best_link:
        index_links = [l for l in cleaned_links if "index.m3u8" in l and "poster" not in l]
        best_link = index_links[0] if index_links else (list(cleaned_links)[0] if cleaned_links else None)

    return best_link

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
        "ad": "TRT Genç",
        "url": "https://canlitv.com/trt-genc",
        "logo": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRbw482VP0KxtIJz0COUGOU7l7pVQrOmsVqww&s"
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
    "type": "m3u8_scraper",
    "ad": "Match TV",
    "url": "https://ritsatv.ru/movie-id900973-match",
    "logo": "https://images.weserv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSbNRDrjRDKEJdaypnmg-uVj5CwXqGj1zCFtv36pp669w&s=10&w=250"
    },
    {
    "type": "m3u8_scraper",
    "ad": "Setanta Sports 1",
    "url": "https://ritsatv.ru/movie-id900982-setanta-1",
    "logo": "https://images.weserv.nl/?url=https://ritsatv.ru/files/poster/original/900982.jpg&w=250"
    },
    {
    "type": "m3u8_scraper",
    "ad": "Setanta Sports 2",
    "url": "https://ritsatv.ru/movie-id900983-setanta-2",
    "logo": "https://wsrv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRFR8wvcpZvK--w75DSnZM0Nloq8Rf7LJQqSSBvWWdTw3F62qT13t6NgDS8&s=10&w=250"
    },
    {
    "type": "m3u8_scraper",
    "ad": "Eurosport 1",
    "url": "https://ritsatv.ru/movie-id900967-eurosport-1",
    "logo": "https://wsrv.nl/?url=https://images.seeklogo.com/logo-png/40/1/eurosport-logo-png_seeklogo-407861.png&w=250"
    },
    {
    "type": "m3u8_scraper",
    "ad": "Eurosport 2",
    "url": "https://ritsatv.ru/movie-id900968-eurosport-2",
    "logo": "https://images.weserv.nl/?url=https://ritsatv.ru/files/poster/original/900968.jpg&w=250"
    },
    {
    "type": "m3u8_scraper",
    "ad": "TNT Sports 1",
    "url": "https://ritsatv.ru/movie-id901126-tnt-sports-1",
    "logo": "https://wsrv.nl/?url=https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/TNT_Sports_%282023%29.svg/960px-TNT_Sports_%282023%29.svg.png&w=250"
    },
    {
    "type": "m3u8_scraper",
    "ad": "TNT Sports 2",
    "url": "https://ritsatv.ru/movie-id901269-tnt-sports-2",
    "logo": "https://wsrv.nl/?url=https://media.info/l/o/1/1540.1690027877.png&w=250"
    },
    {
    "type": "m3u8_scraper",
    "ad": "TNT Sports Premium",
    "url": "https://ritsatv.ru/movie-id901490-tnt-sports-premium",
    "logo": "https://wsrv.nl/?url=https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTHJ23KApagRAkNj-zS1Q9nhxox2bTwTx12WWRLT03EJg&s&w=250"
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
                elif kanal["type"] == "m3u8_scraper":
                    canli_link = handle_m3u8_scraper(kanal)

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
