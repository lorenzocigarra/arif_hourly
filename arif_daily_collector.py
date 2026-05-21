# arif_daily_collector.py
import sys
from pathlib import Path
import pandas as pd
import requests
from datetime import datetime, timedelta
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

DAILY_CSV = DATA_DIR / "arif_spatial_daily.csv"

# Variables diarias de ayer con cadence=1D (Código_API: Nombre_Estandar, Unidad)
VARIABLES_DIARIAS = {
    "TDL": ("tair_min_daily", "°C"),
    "TDA": ("tair_mean_daily", "°C"),
    "TDH": ("tair_max_daily", "°C"),
    "HDL": ("rh_min_daily", "%"),
    "HDA": ("rh_mean_daily", "%"),
    "HDH": ("rh_max_daily", "%"),
    "P": ("precip_sum_daily", "mm"),  # Lluvia total diaria
    "R": ("rs_sum_daily", "W/m²"),    # Radiación total diaria
    "WDA": ("ws_mean_daily", "m/s"),
    "WDH": ("ws_max_daily", "m/s")
}

# ============================================================================
# LOGGING
# ============================================================================

log_file = LOGS_DIR / f"arif_daily_{datetime.now().strftime('%Y%m%d')}.log"

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
# CAPTURA DE API KEY (PLAYWRIGHT EN ESTADO ATTACHED)
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
            screenshot_path = LOGS_DIR / "debug_github_daily.png"
            html_path = LOGS_DIR / "debug_github_daily.html"
            
            page.screenshot(path=str(screenshot_path))
            html_path.write_text(page.content(), encoding='utf-8')
            
            logger.info(f"[DEBUG] Captura guardada en: {screenshot_path}")
            logger.info(f"[DEBUG] Código HTML guardado en: {html_path}")
            
            # CORREGIDO: Esperar a que los marcadores estén acoplados (attached) al HTML, sin verificar visibilidad
            logger.info("Esperando que los marcadores estén acoplados en el HTML...")
            page.wait_for_selector(".leaflet-marker-icon", state="attached", timeout=30000)
            
            # Hacer clic forzado en un marcador
            logger.info("Marcadores detectados en el DOM. Realizando clic forzado...")
            page.locator(".leaflet-marker-icon").first.click(force=True)
            page.wait_for_timeout(3000)
            
            # Hacer clic en Dettagli
            enlaces_detalles = page.locator("a:has-text('Dettagli'), a:has-text('dettagli')")
            if enlaces_detalles.count() > 0:
                logger.info("Haciendo clic en el enlace 'Dettagli' del popup...")
                enlaces_detalles.first.click(force=True)
                page.wait_for_timeout(5000)
            else:
                logger.warning("No se localizó el enlace 'Dettagli' en el popup emergente.")
                
            browser.close()
        except Exception as e:
            logger.error(f"Fallo en la navegación de Playwright: {e}")
            
    return api_key_capturada

# ============================================================================
# DESCARGA DE DATOS DIARIOS
# ============================================================================

def download_daily_variable(session: requests.Session, api_key: str, api_code: str, std_name: str, unit: str, fecha_str: str) -> list:
    """Descargar datos de una variable diaria de ayer para todas las estaciones"""
    url = f"{ARIF_BASE_URL}/api/osservazioni/stazioni"
    params = {
        'api': api_key,
        'variable': api_code,
        'cadence': '1D',
        'data': fecha_str
    }
    
    logger.info(f"Descargando variable diaria: {std_name:<20} (API: {api_code})...")
    
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
            
            # Normalizar fecha
            if raw_date and '-' in raw_date[:10]:
                parts = raw_date.split(' ')
                date_part = parts[0]
                date_parts = date_part.split('-')
                if len(date_parts) == 3 and len(date_parts[2]) == 4:
                    clean_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
            
            record = {
                'station_id': item.get('COD_STAZIONE'),
                'station_name': item.get('NOME_STAZIONE'),
                'latitude': float(item.get('LAT')) if item.get('LAT') else None,
                'longitude': float(item.get('LON')) if item.get('LON') else None,
                'date': clean_date,
                'variable': std_name,
                'value': value,
                'unit': unit,
                'timestamp_extraction': datetime.utcnow().isoformat()
            }
            records.append(record)
            
        return records
    except Exception as e:
        logger.error(f"  Error: {e}")
        return []

def collect_daily_data():
    """Función principal de recolección diaria incremental"""
    logger.info("="*70)
    logger.info("INICIO RECOLECCIÓN DIARIA INCREMENTAL (ARIF)")
    logger.info("="*70)
    
    # Calcular fecha de ayer en formato YYYY-MM-DD
    fecha_ayer_obj = datetime.now() - timedelta(days=1)
    fecha_ayer_str = fecha_ayer_obj.strftime('%Y-%m-%d')
    
    # 1. Comprobar si ayer ya fue descargado de manera incremental
    if DAILY_CSV.exists():
        try:
            df_prev = pd.read_csv(DAILY_CSV)
            if 'date' in df_prev.columns and 'variable' in df_prev.columns:
                # Normalización preventiva de fechas para compatibilidad con Excel
                df_norm = df_prev.copy()
                df_norm['date_parsed'] = pd.to_datetime(df_norm['date'], errors='coerce')
                df_norm = df_norm.dropna(subset=['date_parsed'])
                df_norm['date_std'] = df_norm['date_parsed'].dt.strftime('%Y-%m-%d')
                
                fechas_completas = df_norm.groupby('date_std')['variable'].nunique()
                fechas_descargadas = set(fechas_completas[fechas_completas >= 8].index.astype(str))
                
                if fecha_ayer_str in fechas_descargadas:
                    logger.info(f"✅ Los datos diarios de ayer ({fecha_ayer_str}) ya están completos localmente. Omitiendo descarga.")
                    return
        except Exception as e:
            logger.warning(f"Error procesando CSV previo: {e}")
            
    # 2. Obtener clave activa dinámica
    api_key = capturar_api_key_autonomo()
    if not api_key:
        logger.error("No se pudo obtener una API Key válida. Cancelando ejecución.")
        sys.exit(1)
        
    logger.info(f"API Key activa establecida para descargas directas: {api_key[:15]}...")
    
    # 3. Inicializar sesión de solicitudes con cookies
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
    
    # 4. Descargar cada variable para la fecha de ayer
    all_records = []
    for api_code, (std_name, unit) in VARIABLES_DIARIAS.items():
        records = download_daily_variable(session, api_key, api_code, std_name, unit, fecha_ayer_str)
        all_records.extend(records)
        time.sleep(0.5)
        
    if not all_records:
        logger.error("No se descargaron registros.")
        sys.exit(1)
        
    df_new = pd.DataFrame(all_records)
    # Filtrar rigurosamente registros que coincidan con la fecha de ayer
    df_new = df_new[df_new['date'] == fecha_ayer_str]
    
    logger.info(f"Nuevos registros válidos obtenidos de ayer: {len(df_new)}")
    
    # 5. Consolidar con el historial existente
    if DAILY_CSV.exists():
        try:
            df_prev = pd.read_csv(DAILY_CSV)
            df_combined = pd.concat([df_prev, df_new], ignore_index=True)
            df_combined = df_combined.drop_duplicates(
                subset=['station_id', 'date', 'variable'],
                keep='last'
            )
            logger.info(f"Registros previos cargados: {len(df_prev)}")
            logger.info(f"Total registros únicos consolidados: {len(df_combined)}")
        except Exception as e:
            logger.warning(f"Error procesando el histórico: {e}")
            df_combined = df_new
    else:
        df_combined = df_new
        
    df_combined = df_combined.sort_values(by=['date', 'station_id', 'variable'])
    df_combined.to_csv(DAILY_CSV, index=False, encoding='utf-8-sig')
    
    logger.info("="*70)
    logger.info("RECOLECCIÓN DIARIA FINALIZADA CON ÉXITO")
    print(f"  Día procesado: {fecha_ayer_str}")
    print(f"  Total registros acumulados: {len(df_combined)}")
    print(f"  Archivo guardado en: {DAILY_CSV}")
    logger.info("="*70)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    try:
        collect_daily_data()
    except Exception as e:
        logger.error(f"Error fatal en la ejecución: {e}", exc_info=True)
        sys.exit(1)
