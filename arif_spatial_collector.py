# arif_spatial_collector.py
import sys
from pathlib import Path
import pandas as pd
import requests
from datetime import datetime
import time
import logging
import re

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

ARIF_BASE_URL = "http://www.agrometeopuglia.it"

DATA_DIR = Path("data/arif")
LOGS_DIR = Path("logs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SPATIAL_CSV = DATA_DIR / "arif_spatial_timeseries.csv"

# Variables espaciales activas en el servidor
VARIABLES = {
    "T": "tair_hourly",
    "H": "rh_hourly",
    "P": "precip_10min_interval",
    "PC": "precip_today_accum",
    "R": "rs_hourly",
    "W": "wind_speed",
    "WDV": "wdir_hourly"
}

# Mapeo dinámico (api_code, COD_GRANDEZZA) -> (variable_name, unit)
MAPPING = {
    ("T", "TC"): ("tair_hourly", "°C"),
    ("H", "UC"): ("rh_hourly", "%"),
    ("P", "PC"): ("precip_10min_interval", "mm"),
    ("PC", "PC"): ("precip_today_accum", "mm"),
    ("R", "RG"): ("rs_hourly", "W/m²"),
    ("W", "VA"): ("ws_10m_hourly", "m/s"),
    ("W", "VB"): ("ws_2m_hourly", "m/s"),
    ("WDV", "DV"): ("wdir_hourly", "°")
}

# ============================================================================
# LOGGING
# ============================================================================

log_file = LOGS_DIR / f"arif_spatial_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# ============================================================================
# CAPTURA DE API KEY (PLAYWRIGHT)
# ============================================================================

def capturar_api_key_autonomo() -> str:
    """Inicia un navegador headless, emula clics e intercepta la API Key activa"""
    logger.info("Iniciando navegador Playwright para capturar clave...")
    from playwright.sync_api import sync_playwright
    api_key_capturada = None
    
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            # Inyectar cookies de consentimiento
            context.add_cookies([
                {
                    'name': 'nibirumail_cookie_advice',
                    'value': '1',
                    'domain': 'www.agrometeopuglia.it',
                    'path': '/'
                },
                {
                    'name': 'nibirumail_cookie_advice',
                    'value': '1',
                    'domain': 'agrometeopuglia.it',
                    'path': '/'
                }
            ])
            
            page = context.new_page()
            
            # Interceptor de red
            def interceptar(request):
                nonlocal api_key_capturada
                url = request.url
                if "api=" in url:
                    match = re.search(r'api=([a-f0-9]{90,110})', url, re.IGNORECASE)
                    if match and not api_key_capturada:
                        api_key_capturada = match.group(1)
            
            page.on("request", interceptar)
            
            logger.info("Navegando a la página del mapa...")
            response = page.goto(f"{ARIF_BASE_URL}/osservazioni/mappa-stazioni-meteo", timeout=60000)
            
            logger.info(f"Respuesta HTTP recibida: {response.status if response else 'No response'}")
            logger.info(f"Título de la página cargada: '{page.title()}'")
            
            # Forzar espera a que la red se estabilice
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except:
                pass
            
            # GUARDAR CAPTURA DE PANTALLA Y HTML DE DIAGNÓSTICO EN CASO DE BLOQUEO
            screenshot_path = LOGS_DIR / "debug_github.png"
            html_path = LOGS_DIR / "debug_github.html"
            
            page.screenshot(path=str(screenshot_path))
            html_path.write_text(page.content(), encoding='utf-8')
            
            logger.info(f"[DEBUG] Captura guardada en: {screenshot_path}")
            logger.info(f"[DEBUG] Código HTML guardado en: {html_path}")
            
            # Esperar marcadores
            logger.info("Esperando que aparezcan los marcadores en el mapa...")
            page.wait_for_selector(".leaflet-marker-icon", timeout=30000)
            
            # Hacer clic en un marcador
            logger.info("Marcadores detectados. Realizando clic en el primer elemento...")
            page.locator(".leaflet-marker-icon").first.click(force=True)
            page.wait_for_timeout(3000)
            
            # Hacer clic en Dettagli
            enlaces_detalles = page.locator("a:has-text('Dettagli'), a:has-text('dettagli')")
            if enlaces_detalles.count() > 0:
                logger.info("Haciendo clic en el enlace 'Dettagli' del popup...")
                enlaces_detalles.first.click(force=True)
                page.wait_for_timeout(5000)
                
            browser.close()
        except Exception as e:
            logger.error(f"Fallo en la navegación de Playwright: {e}")
            
    return api_key_capturada

# ============================================================================
# DESCARGA DE DATOS
# ============================================================================

def download_variable(session: requests.Session, api_key: str, api_code: str, fallback_name: str) -> list:
    """Descargar datos de UNA variable para TODAS las estaciones"""
    url = f"{ARIF_BASE_URL}/api/osservazioni/stazioni"
    params = {
        'api': api_key,
        'variable': api_code
    }
    
    logger.info(f"Descargando {fallback_name:<25} (API: {api_code})...")
    
    try:
        response = session.get(url, params=params, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"  HTTP {response.status_code}")
            return []
        
        data = response.json()
        
        if not isinstance(data, list) or len(data) == 0:
            logger.warning(f"  Vacío")
            return []
        
        logger.info(f"  ✓ {len(data)} registros obtenidos")
        
        records = []
        
        for item in data:
            raw_val = item.get('VALORE')
            value = None
            if raw_val is not None:
                try:
                    value = float(str(raw_val).strip())
                except:
                    value = str(raw_val).strip()
            
            raw_date = item.get('DATA', '').strip()
            clean_date = raw_date
            
            if raw_date and '-' in raw_date[:10]:
                parts = raw_date.split(' ')
                date_part = parts[0]
                time_part = parts[1] if len(parts) > 1 else ""
                
                date_parts = date_part.split('-')
                if len(date_parts) == 3 and len(date_parts[2]) == 4:
                    clean_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
                    if time_part:
                        clean_date = f"{clean_date} {time_part}"
            
            grandezza = item.get('COD_GRANDEZZA', '')
            mapping_key = (api_code, grandezza)
            
            if mapping_key in MAPPING:
                var_name, unit = MAPPING[mapping_key]
            else:
                var_name = fallback_name
                unit = ""
            
            record = {
                'station_id': item.get('COD_STAZIONE'),
                'station_name': item.get('NOME_STAZIONE'),
                'latitude': float(item.get('LAT')) if item.get('LAT') else None,
                'longitude': float(item.get('LON')) if item.get('LON') else None,
                'datetime_local': clean_date,
                'variable': var_name,
                'value': value,
                'unit': unit,
                'timestamp_utc_extraction': datetime.utcnow().isoformat()
            }
            
            records.append(record)
        
        return records
    
    except Exception as e:
        logger.error(f"  Error: {e}")
        return []

def collect_all_data():
    """Función principal de recolección"""
    logger.info("="*70)
    logger.info("INICIO RECOLECCIÓN ARIF SPATIAL")
    logger.info("="*70)
    
    api_key = capturar_api_key_autonomo()
    if not api_key:
        logger.error("No se pudo obtener una API Key válida. Cancelando ejecución.")
        sys.exit(1)
        
    logger.info(f"API Key activa establecida para descargas directas: {api_key[:15]}...")
    
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "es-419,es;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{ARIF_BASE_URL}/osservazioni/mappa-stazioni-meteo",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    for dom in ["www.agrometeopuglia.it", "agrometeopuglia.it", ".agrometeopuglia.it"]:
        session.cookies.set('nibirumail_cookie_advice', '1', domain=dom, path='/')
    
    all_records = []
    for api_code, fallback_name in VARIABLES.items():
        records = download_variable(session, api_key, api_code, fallback_name)
        all_records.extend(records)
        time.sleep(0.5)
    
    if not all_records:
        logger.error("No se obtuvieron registros de ninguna variable.")
        sys.exit(1)
    
    df_new = pd.DataFrame(all_records)
    logger.info(f"Nuevos registros obtenidos en esta corrida: {len(df_new)}")
    
    if SPATIAL_CSV.exists():
        try:
            df_prev = pd.read_csv(SPATIAL_CSV)
            df_combined = pd.concat([df_prev, df_new], ignore_index=True)
            df_combined = df_combined.drop_duplicates(
                subset=['station_id', 'datetime_local', 'variable'],
                keep='last'
            )
            logger.info(f"Registros previos cargados: {len(df_prev)}")
            logger.info(f"Total registros únicos consolidados: {len(df_combined)}")
        except Exception as e:
            logger.warning(f"Error procesando CSV histórico: {e}")
            df_combined = df_new
    else:
        df_combined = df_new
    
    df_combined = df_combined.sort_values(
        by=['datetime_local', 'station_id', 'variable']
    )
    
    df_combined.to_csv(SPATIAL_CSV, index=False, encoding='utf-8-sig')
    
    logger.info("="*70)
    logger.info("RECOLECCIÓN COMPLETADA CON ÉXITO")
    print(f"  Total registros: {len(df_combined)}")
    print(f"  Estaciones únicas: {df_combined['station_id'].nunique()}")
    print(f"  Variables únicas: {df_combined['variable'].nunique()}")
    logger.info("="*70)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        collect_all_data()
    except Exception as e:
        logger.error(f"Error fatal en la ejecución: {e}", exc_info=True)
        sys.exit(1)
