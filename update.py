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
    """Tip 2: YodaCDN işlədən dinamik kanallar (AzTV, İdman TV)"""
    print(f'   [YodaCDN] Səhifə yüklənir: {kanal["url"]}')
    res = requests.get(kanal["url"], headers=headers, timeout=15)
    
    iframe_link = re.search(r'src=["\'](https?://[^"\']+?(?:yodaplayer|embed|player|cdn)[^"\']*?)["\']', res.text)
    if not iframe_link:
        return None
        
    gizli_url = iframe_link.group(1)
    iframe_headers = headers.copy()
    iframe_headers['Referer'] = kanal["url"]
    iframe_res = requests.get(gizli_url, headers=iframe_headers, timeout=15)
    
    token_tapildi = re.search(r'data-token=["\']([^"\']+)["\']', iframe_res.text)
    if token_tapildi:
        token = token_tapildi.group(1)
        return f"{kanal['stream_base']}?token={token}|Origin={kanal['referer']}&Referer={kanal['referer']}&User-Agent=Mozilla/5.0"
    return None

def handle_generic_scraper(kanal, headers):
    """Tip 3: Fərqli qruplar üçün - Səhifənin kodunda birbaşa .m3u8 axtaran metod"""
    print(f'   [Scraper] Səhifə kodu analiz edilir: {kanal["url"]}')
    res = requests.get(kanal["url"], headers=headers, timeout=15)
    
    m3u8_tapildi = re.search(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', res.text)
    if m3u8_tapildi:
        link = m3u8_tapildi.group(0)
        if "referer_req" in kanal:
            link = f"{link}|Referer={kanal['referer_req']}&User-Agent=Mozilla/5.0"
        return link
    return None

# ==============================================================================
# MƏRKƏZİ KANAL BAZASI (Real və aktiv linklər)
# ==============================================================================
kanallar = [
    # ---- YODACDN QORUMALI KANALLAR (Dinamik) ----
    {
        "type": "token_yoda",
        "ad": "AzTV", 
        "url": "https://aztv.az/az/live",
        "stream_base": "https://str.yodacdn.net/azertv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "Idman TV", 
        "url": "https://aztv.az/az/live-idman",
        "stream_base": "https://str.yodacdn.net/idmantele/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "Xezer TV", 
        "url": "https://aztv.az/az/live-idman",
        "stream_base": "https://str.yodacdn.net/xazartv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "ARB", 
        "url": "https://aztv.az/az/live-idman",
        "stream_base": "https://str.yodacdn.net/arb/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "İctimai TV", 
        "url": "https://aztv.az/az/live-idman",
        "stream_base": "https://str.yodacdn.net/ictimaitv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    {
        "type": "token_yoda",
        "ad": "Baku TV", 
        "url": "https://aztv.az/az/live-idman",
        "stream_base": "https://str.yodacdn.net/bakutv/tracks-v1a1/mono.ts.m3u8",
        "referer": "https://yodaplayer.yodacdn.net/"
    },
    
    # ---- SABİT VƏ STATİK KANALLAR (Birbaşa linklər) ----
    {
        "type": "direct",
        "ad": "ATV",
        "stream_url": "https://lives.atv.az:5443/ATV_TV_STREAM/streams/atvcanli.m3u8"
    }
]

# ==============================================================================
# ƏSAS İCRA PROSESİ (MAIN)
# ==============================================================================
def main():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0'}
    
    with open("channels.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        
        for kanal in kanallar:
            print(f'[{kanal["ad"]}] İşlənir...')
            canli_link = None
            
            try:
                if kanal["type"] == "direct":
                    canli_link = handle_direct(kanal)
                elif kanal["type"] == "token_yoda":
                    canli_link = handle_token_yoda(kanal, headers)
                elif kanal["type"] == "generic_scraper":
                    canli_link = handle_generic_scraper(kanal, headers)
                
                if canli_link:
                    f.write(f'#EXTINF:-1, {kanal["ad"]}\n')
                    f.write(f'{canli_link}\n')
                    print(f'   => [UĞURLU] {kanal["ad"]} pleylistə yazıldı.\n')
                else:
                    print(f'   => [XƏTA] {kanal["ad"]} üçün link tapılmadı.\n')
                    
            except Exception as e:
                print(f'   => [SİSTEM XƏTASI] {kanal["ad"]} modulunda problem oldu: {e}\n')

if __name__ == "__main__":
    main()
