import requests
import re

# Kanalların siyahısı
kanallar = [
    {
        "ad": "AzTV", 
        "url": "https://aztv.az/az/live",
        "qorunma": True,
        "referer": "https://yodaplayer.yodacdn.net/"
    }
]

with open("channels.m3u", "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    
    for kanal in kanallar:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0'}
            
            # ADDIM 1: Ana sayta daxil oluruq
            print(f'[{kanal["ad"]}] Ana sayfa yüklənir: {kanal["url"]}')
            response = requests.get(kanal["url"], headers=headers, timeout=15)
            
            # Səhifədə birbaşa m3u8 axtarırıq
            m3u8_tapildi = re.search(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', response.text)
            
            # ADDIM 2: Əgər birbaşa tapılmadısa, deməli iframe (player) daxilində gizlənib
            if not m3u8_tapildi:
                print(f'[{kanal["ad"]}] Birbaşa link tapılmadı. İframe/Player axtarılır...')
                
                # Səhifənin içində yodaplayer, embed və ya player keçidlərini axtarırıq
                iframe_link = re.search(r'src=["\'](https?://[^"\']+?(?:yodaplayer|embed|player|cdn)[^"\']*?)["\']', response.text)
                
                if iframe_link:
                    gizli_url = iframe_link.group(1)
                    print(f'[{kanal["ad"]}] Gizli player linki tapıldı: {gizli_url}')
                    
                    # İframe-in öz içinə sorğu göndəririk (Referer olaraq ana saytı göstəririk ki, bizi bloklamasın)
                    iframe_headers = headers.copy()
                    iframe_headers['Referer'] = kanal["url"]
                    iframe_response = requests.get(gizli_url, headers=iframe_headers, timeout=15)
                    
                    # İndi isə playerin kodlarının içindən m3u8-i axtarırıq
                    m3u8_tapildi = re.search(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', iframe_response.text)
            
            # YEKUN: Əgər m3u8 tapıldısa fayla yazırıq
            if m3u8_tapildi:
                canli_link = m3u8_tapildi.group(0)
                
                # Əgər kanalın Referer qoruması varsa, player linkini sonuna əlavə edirik
                if kanal.get("qorunma"):
                    canli_link = f"{canli_link}|Origin={kanal['referer']}&Referer={kanal['referer']}&User-Agent=Mozilla/5.0"
                
                f.write(f'#EXTINF:-1, {kanal["ad"]}\n')
                f.write(f'{canli_link}\n')
                print(f'[UĞURLU] {kanal["ad"]} linki tapıldı və fayla yazıldı!')
            else:
                print(f'[XƏTA] {kanal["ad"]} üçün heç bir metodla m3u8 tapılmadı.')
                
        except Exception as e:
            print(f'[XƏTA] {kanal["ad"]} icra edilərkən problem yarandı: {e}')
