import requests
import re
import sys

# ==============================================================================
# HƏR TİP ÜÇÜN XÜSUSİ METODLAR (HANDLERS)
# ==============================================================================

def handle_direct(kanal):
    """Tip 1: Heç bir qoruması olmayan sabit statik linklər (Məsələn: ATV)"""
    return kanal["stream_url"]

def handle_token_yoda(kanal, headers):
    """Tip 2: YodaCDN işlədən dinamik kanallar (AzTV, İdman TV və s.)"""
    print(f'   [YodaCDN] Səhifə yüklənir: {kanal["url"]}')
    
    # İlk sorğu üçün local headers kopyalayırıq
    req_headers = headers.copy()
    if "url_referer" in kanal:
        req_headers['Referer'] = kanal["url_referer"]
        
    try:
        res = requests.get(kanal["url"], headers=req_headers, timeout=15)
        res.raise_for_status()
        
        # Səhifədən player və ya iframe linkini tapırıq
        iframe_link = re.search(r'src=["\'](https?://[^"\']+?(?:yodaplayer|embed|player|cdn)[^"\']*?)["\']', res.text)
        if not iframe_link:
            # Əgər iframe tapılmasa, birbaşa url-in özünü player hesab etməyə çalışaq (Məs: yoda.az birbaşa embed ola bilər)
            if "yodaplayer" in kanal["url"] or "yoda.az" in kanal["url"]:
                gizli_url = kanal["url"]
            else:
                return None
        else:
            gizli_url = iframe_link.group(1)
        
        # İframe-ə müraciət edirik
        iframe_headers = headers.copy()
        iframe_headers['Referer'] = kanal.get("url_referer", kanal["url"])
        
        print(f'   [YodaCDN] Token axtarılır: {gizli_url}')
        iframe_res = requests.get(gizli_url, headers=iframe_headers, timeout=15)
        iframe_res.raise_for_status()
        
        # data-token tapılması (dırnaqlı və ya dırnaqsız halları nəzərə alaraq)
        token_tapildi = re.search(r'data-token\s*=\s*["\']?([^"\'\s>]+)["\']?', iframe_res.text)
        
        if token_tapildi:
            token = token_tapildi.group(1)
            # IPTV Playerlərin (VLC, Perfect Player və s.) tanıması üçün User-Agent və Referer inject formatı
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
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "Xezer TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/xazartv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "ARB", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/arb/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "İctimai TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/ictimaitv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "Baku TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/bakutv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "Real TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/real/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "ARB 24", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/arb24/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "APA TV", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/apatv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "TMB Azerbaycan", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/tmbaz/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "ARB Gunes", 
        "url": "https://yoda.az/",
        "stream_base": "https://str.yodacdn.net/arbgunesh/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
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
        "referer": "https://yodaplayer.yodacdn.net/"
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
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    
    # ---- SABİT VƏ STATİK KANALLAR (Birbaşa linklər) ----
    {
        "type": "direct",
        "ad": "ATV",
        "stream_url": "https://lives.atv.az:5443/ATV_TV_STREAM/streams/atvcanli.m3u8"
    }
    {
        "type": "direct",
        "ad": "CBC Sport",
        "stream_url": "https://cbcsports-live.lg.mncdn.com/cbcsports_live/cbcsports/chunklist.m3u8"
    }
    {
        "type": "generic_scraper",
        "ad": "TRT 1",
        "url": "https://www.trtizle.com/canli/tv/trt-1",
        "stream_base": "https://trt.daioncdn.net/trt-1/master_1440p.m3u8",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/e/e4/TRT_1_logo_%282021%29.png"
    }
    {
        "type": "generic_scraper",
        "ad": "Show TV",
        "url": "https://www.showtv.com.tr/canli-yayin", # Show TV-nin rəsmi canlı yayım səhifəsi
        "stream_base": "https://ciner.daioncdn.net/showtv/showtv_1080p.m3u8",
        "logo": "https://upload.wikimedia.org/wikipedia/commons/e/e0/Show_TV_logo_2014.png"
    }
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
                if kanal["type"] == "direct":
                    canli_link = handle_direct(kanal)
                elif kanal["type"] == "token_yoda":
                    canli_link = handle_token_yoda(kanal, headers)
                elif kanal["type"] == "generic_scraper":
                    canli_link = handle_generic_scraper(kanal, headers)
                
                if canli_link:
                    # Boşluq xətası düzəldildi və standartlaşdırıldı
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
