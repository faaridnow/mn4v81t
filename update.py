import requests
import re

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
            
            print(f'[{kanal["ad"]}] Ana sayfa yüklənir: {kanal["url"]}')
            response = requests.get(kanal["url"], headers=headers, timeout=15)
            
            iframe_link = re.search(r'src=["\'](https?://[^"\']+?(?:yodaplayer|embed|player|cdn)[^"\']*?)["\']', response.text)
            
            if iframe_link:
                gizli_url = iframe_link.group(1)
                print(f'[{kanal["ad"]}] Gizli player linki tapıldı: {gizli_url}')
                
                iframe_headers = headers.copy()
                iframe_headers['Referer'] = kanal["url"]
                iframe_response = requests.get(gizli_url, headers=iframe_headers, timeout=15)
                
                # --- CASUS VƏ DETEKTİV HİSSƏ ---
                # Pleyer səhifəsindəki şübhəli sətirləri loqa çıxarırıq ki, gözümüzlə görək
                print(f'===> [{kanal["ad"]}] PLEYER KODUNUN TƏHLİLİ BAŞLADI <===')
                for line in iframe_response.text.split('\n'):
                    if any(x in line.lower() for x in ['m3u8', 'token', 'file:', 'source', 'play']):
                        print(f"[İPUCU] Tapılan Sətir: {line.strip()[:180]}")
                print(f'===> [{kanal["ad"]}] TƏHLİL BİTDİ <===')
                # --------------------------------
                
                # Təkmilləşdirilmiş yeni axtarış: əvvəlində http olmasa belə .m3u8 hissəsini tutmağa çalışır
                m3u8_tapildi = re.search(r'(https?://[^\s"\'<>]+?)?\.m3u8[^\s"\'<>]*', iframe_response.text)
                
                if m3u8_tapildi:
                    canli_link = m3u8_tapildi.group(0)
                    
                    # Əgər link tam deyil rəqəmsaldırsa (məsələn /tracks... ilə başlayırsa) əvvəlinə serveri yapışdırırıq
                    if not canli_link.startswith('http'):
                        canli_link = f"https://str.yodacdn.net{canli_link}"
                    
                    if kanal.get("qorunma"):
                        canli_link = f"{canli_link}|Origin={kanal['referer']}&Referer={kanal['referer']}&User-Agent=Mozilla/5.0"
                    
                    f.write(f'#EXTINF:-1, {kanal["ad"]}\n')
                    f.write(f'{canli_link}\n')
                    print(f'[UĞURLU] {kanal["ad"]} linki uğurla fayla yazıldı!')
                else:
                    print(f'[XƏTA] Yeni metodla da m3u8 tapılmadı.')
            else:
                print(f'[XƏTA] İframe linki tapılmadı.')
                
        except Exception as e:
            print(f'[XƏTA] {kanal["ad"]} xətası: {e}')
